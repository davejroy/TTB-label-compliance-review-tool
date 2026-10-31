"""Shared upload / request-size guards used by sync, batch, and stream paths.

These checks run before file bytes are decoded or sent to the model. Count
and batch validators fail open on internal errors so a broken helper cannot
lock out a legitimate review. The request-body cap is the exception: if the
cap cannot be read, the middleware fails closed (HTTP 413) rather than
accepting an unlimited body. ``GET``/``HEAD`` ``/api/health`` is never
blocked so probes stay up; other methods on that path are still capped.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from fastapi import UploadFile
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import (
    FILE_TOO_LARGE,
    INVALID_REQUEST,
    REQUEST_ID_HEADER,
    ClientError,
    current_request_id,
    error_payload,
    sanitize_request_id,
    set_request_id,
)

_log = logging.getLogger(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image
MAX_IMAGES_PER_LABEL = 4
READ_CHUNK_SIZE = 64 * 1024

MAX_REQUEST_BODY_BYTES_ENV = "MAX_REQUEST_BODY_BYTES"
DEFAULT_MAX_REQUEST_BODY_BYTES = 50 * 1024 * 1024  # 50 MB

MAX_LABELS_PER_BATCH_ENV = "MAX_LABELS_PER_BATCH"
DEFAULT_MAX_LABELS_PER_BATCH = 50

MAX_TEMPLATE_BYTES_ENV = "MAX_TEMPLATE_BYTES"
DEFAULT_MAX_TEMPLATE_BYTES = 1 * 1024 * 1024  # 1 MB

FILE_TOO_LARGE_MESSAGE = "The upload is too large. Please use fewer or smaller photos."
TEMPLATE_TOO_LARGE_MESSAGE = "The template file is too large. Please upload a file under 1 MB."
TOO_MANY_LABELS_MESSAGE = (
    "Too many labels in this batch. Please submit fewer labels and try again."
)
IMAGES_PER_LABEL_MESSAGE = (
    f"A maximum of {MAX_IMAGES_PER_LABEL} images may be uploaded per label. "
    "Please resubmit with 4 or fewer photos."
)
IMAGE_COUNTS_MISMATCH_MESSAGE = "Sum of image_counts must match the number of uploaded files."
IMAGE_COUNTS_LENGTH_MESSAGE = "image_counts length must match the number of applications."
EMPTY_LABEL_MESSAGE = "Each label must include at least one image."
UPLOAD_RANGE_MESSAGE = f"Upload between 1 and {MAX_IMAGES_PER_LABEL} images per label."

HEALTH_PATH = "/api/health"
HEALTH_EXEMPT_METHODS = frozenset({"GET", "HEAD"})


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Parse a positive int from the environment. Fail-open to *default*."""
    raw = os.environ.get(name, str(default))
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        _log.warning("Invalid %s=%r; using default %s (fail-open)", name, raw, default)
        return default
    if value < minimum:
        _log.warning("Invalid %s=%s; using default %s (fail-open)", name, value, default)
        return default
    return value


def get_max_request_body_bytes() -> int:
    return _env_int(MAX_REQUEST_BODY_BYTES_ENV, DEFAULT_MAX_REQUEST_BODY_BYTES, minimum=1)


def get_max_labels_per_batch() -> int:
    return _env_int(MAX_LABELS_PER_BATCH_ENV, DEFAULT_MAX_LABELS_PER_BATCH, minimum=1)


def get_max_template_bytes() -> int:
    return _env_int(MAX_TEMPLATE_BYTES_ENV, DEFAULT_MAX_TEMPLATE_BYTES, minimum=1)


@dataclass
class ValidatedLabelBatch:
    """Normalized per-label file groups after count checks (no bytes read)."""

    label_groups: list[list[UploadFile]] = field(default_factory=list)
    image_counts: list[int] = field(default_factory=list)
    label_count: int = 0
    file_count: int = 0
    error: ClientError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def validate_label_upload_request(
    files: list[UploadFile],
    *,
    image_counts: list[int] | None = None,
    expected_label_count: int | None = None,
    enforce_batch_cap: bool = False,
) -> ValidatedLabelBatch:
    """Shared request validator for sync, batch, and stream review paths.

    Checks label/file counts only — never reads upload bodies. Call this
    before the first ``UploadFile.read`` so oversized batches are rejected
    without buffering images.

    Args:
        files: Multipart files in label order.
        image_counts: Per-label image counts (batch/stream). ``None`` means
            a single-label (sync) request containing every file.
        expected_label_count: When set (applications length), must match
            ``len(image_counts)``.
        enforce_batch_cap: Apply ``MAX_LABELS_PER_BATCH`` (batch/stream only).

    Security:
        Fail-open on unexpected internal errors: log and return a permissive
        grouping so a broken validator cannot block a legitimate review.
    """
    try:
        file_list = list(files)
        if image_counts is None:
            counts = [len(file_list)] if file_list else []
        else:
            counts = [int(item) for item in image_counts]

        if expected_label_count is not None and len(counts) != expected_label_count:
            return ValidatedLabelBatch(error=ClientError(INVALID_REQUEST, IMAGE_COUNTS_LENGTH_MESSAGE))

        if enforce_batch_cap and len(counts) > get_max_labels_per_batch():
            return ValidatedLabelBatch(
                error=ClientError(INVALID_REQUEST, TOO_MANY_LABELS_MESSAGE)
            )

        if not counts or not file_list:
            return ValidatedLabelBatch(error=ClientError(INVALID_REQUEST, UPLOAD_RANGE_MESSAGE))

        if any(count < 1 for count in counts):
            return ValidatedLabelBatch(error=ClientError(INVALID_REQUEST, EMPTY_LABEL_MESSAGE))

        if any(count > MAX_IMAGES_PER_LABEL for count in counts):
            return ValidatedLabelBatch(
                error=ClientError(
                    INVALID_REQUEST,
                    (
                        f"A maximum of {MAX_IMAGES_PER_LABEL} images may be uploaded per label. "
                        f"Please resubmit with 4 or fewer photos."
                    ),
                )
            )

        if image_counts is None and not (1 <= len(file_list) <= MAX_IMAGES_PER_LABEL):
            return ValidatedLabelBatch(
                error=ClientError(
                    INVALID_REQUEST,
                    (
                        f"A maximum of {MAX_IMAGES_PER_LABEL} images may be uploaded per label. "
                        f"You submitted {len(file_list)}. Please resubmit with 4 or fewer photos."
                    ),
                )
            )

        if sum(counts) != len(file_list):
            return ValidatedLabelBatch(
                error=ClientError(INVALID_REQUEST, IMAGE_COUNTS_MISMATCH_MESSAGE)
            )

        groups: list[list[UploadFile]] = []
        offset = 0
        for count in counts:
            groups.append(file_list[offset : offset + count])
            offset += count

        return ValidatedLabelBatch(
            label_groups=groups,
            image_counts=counts,
            label_count=len(groups),
            file_count=len(file_list),
        )
    except Exception:  # noqa: BLE001
        _log.exception("Upload validator failed; allowing request (fail-open)")
        file_list = list(files)
        if image_counts:
            groups = []
            offset = 0
            for count in image_counts:
                groups.append(file_list[offset : offset + int(count)])
                offset += int(count)
            return ValidatedLabelBatch(
                label_groups=groups or [file_list],
                image_counts=list(image_counts),
                label_count=len(groups) or (1 if file_list else 0),
                file_count=len(file_list),
            )
        return ValidatedLabelBatch(
            label_groups=[file_list] if file_list else [],
            image_counts=[len(file_list)] if file_list else [],
            label_count=1 if file_list else 0,
            file_count=len(file_list),
        )


async def read_upload_capped(
    file: UploadFile,
    max_size: int = MAX_FILE_SIZE,
) -> tuple[bytes, bool]:
    """Read an upload in chunks and stop once *max_size* is exceeded.

    Returns ``(bytes_read, exceeded)``. The leftover stream is drained so the
    connection can close cleanly. Internal read errors fail open by returning
    whatever was already buffered with ``exceeded=False``.
    """
    chunks: list[bytes] = []
    total = 0
    exceeded = False
    try:
        while True:
            chunk = await file.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                exceeded = True
                try:
                    while await file.read(READ_CHUNK_SIZE):
                        pass
                except Exception:  # noqa: BLE001
                    _log.exception("Draining oversize upload failed; continuing")
                break
            chunks.append(chunk)
    except Exception:  # noqa: BLE001
        _log.exception("Chunked upload read failed; using buffered bytes (fail-open)")
        return b"".join(chunks), False
    return b"".join(chunks), exceeded


def file_too_large_error(filename: str | None) -> ClientError:
    name = filename or "upload"
    return ClientError(FILE_TOO_LARGE, f"File '{name}' exceeds 10 MB limit.")


def _request_id_from_scope(scope: Scope) -> str:
    """Adopt a safe incoming X-Request-ID before the inner request-id middleware runs."""
    try:
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == "x-request-id":
                rid = sanitize_request_id(value.decode("latin-1"))
                set_request_id(rid)
                return rid
    except Exception:  # noqa: BLE001
        _log.exception("Could not read incoming X-Request-ID; generating one")
    return current_request_id()


def _request_body_chunk(message: Message) -> bytes:
    """Return the ASGI body chunk as bytes.

    Non-bytes payloads must not crash ``len()`` or Starlette's parser.
    A stray ``str`` is encoded so it still counts toward the cap.
    """
    chunk = message.get("body")
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        return bytes(chunk)
    if isinstance(chunk, str):
        return chunk.encode("latin-1", errors="replace")
    return b""


async def _send_json_error(
    send: Send,
    *,
    status: int,
    code: str,
    message: str,
) -> None:
    request_id = current_request_id()
    body = error_payload(code, message, request_id=request_id)
    import json

    payload = json.dumps(body).encode("utf-8")
    headers = MutableHeaders()
    headers["content-type"] = "application/json"
    headers[REQUEST_ID_HEADER] = request_id
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers.raw,
        }
    )
    await send({"type": "http.response.body", "body": payload})


class RequestBodyLimitMiddleware:
    """Reject requests whose body exceeds ``MAX_REQUEST_BODY_BYTES``.

    Fast path: reject when ``Content-Length`` is already over the cap so
    oversized declared bodies never reach multipart parsing. Streaming path:
    wrap the ASGI ``receive`` channel and count body bytes as they arrive so
    chunked transfers, missing ``Content-Length``, or an understated length
    cannot bypass the limit. The wrapper does not buffer the body to measure
    it — it forwards each chunk and stops at the first byte past the cap
    without waiting to drain the rest of the stream.

    ``GET``/``HEAD`` ``/api/health`` is never blocked (including when the
    cap cannot be read) so probes stay up. Other methods on that path are
    still capped. If the cap itself cannot be read the middleware fails
    closed (HTTP 413) so an unlimited body is never accepted. An unparsable
    ``Content-Length`` skips only the fast precheck and still counts
    streamed bytes. A 413 is not sent if the inner app already started a
    response (avoids a second ``http.response.start``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = str(scope.get("method") or "").upper()
        # Only liveness probes skip the cap. POST/PUT/etc. to this path
        # used to send an unlimited body past the limiter.
        if path == HEALTH_PATH and method in HEALTH_EXEMPT_METHODS:
            await self.app(scope, receive, send)
            return

        try:
            max_bytes = get_max_request_body_bytes()
        except Exception:  # noqa: BLE001
            # Fail closed: a broken cap lookup must not accept an unlimited body.
            _log.exception("Body-size middleware failed; rejecting request (fail-closed)")
            _request_id_from_scope(scope)
            await _send_json_error(
                send,
                status=413,
                code=FILE_TOO_LARGE,
                message=FILE_TOO_LARGE_MESSAGE,
            )
            return

        try:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            content_length = headers.get("content-length")
            if content_length:
                length = int(content_length)
                if length > max_bytes:
                    _log.warning(
                        "Request body rejected: Content-Length %s exceeds MAX_REQUEST_BODY_BYTES=%s path=%s",
                        length,
                        max_bytes,
                        path,
                    )
                    _request_id_from_scope(scope)
                    await _send_json_error(
                        send,
                        status=413,
                        code=FILE_TOO_LARGE,
                        message=FILE_TOO_LARGE_MESSAGE,
                    )
                    return
        except Exception:  # noqa: BLE001
            # A missing or unparsable Content-Length is not a broken limiter.
            # Continue and enforce the cap on bytes actually received.
            _log.exception(
                "Content-Length precheck failed; continuing with streamed byte count"
            )

        received = 0
        exceeded = False
        error_sent = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded, error_sent
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] != "http.request":
                return message
            chunk = _request_body_chunk(message)
            received += len(chunk)
            if received <= max_bytes:
                # Forward a coerced bytes body so a non-bytes chunk cannot
                # crash Starlette's parser after we accepted the size.
                if chunk != message.get("body"):
                    return {**message, "body": chunk}
                return message
            exceeded = True
            _log.warning(
                "Request body rejected: received %s bytes exceeds MAX_REQUEST_BODY_BYTES=%s path=%s",
                received,
                max_bytes,
                path,
            )
            # Send 413 immediately. Do not drain leftover chunks — a chunked
            # client can leave more_body=True and stall the worker forever.
            # Do not send a second response.start if the inner app already
            # began one (ASGI protocol violation / connection abort).
            if not error_sent and not response_started:
                error_sent = True
                _request_id_from_scope(scope)
                await _send_json_error(
                    send,
                    status=413,
                    code=FILE_TOO_LARGE,
                    message=FILE_TOO_LARGE_MESSAGE,
                )
            return {"type": "http.disconnect"}

        async def limited_send(message: Message) -> None:
            nonlocal response_started
            if error_sent:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if error_sent:
                _log.info("Inner app exited after oversize body rejection")
                return
            raise
