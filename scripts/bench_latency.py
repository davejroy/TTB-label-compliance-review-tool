#!/usr/bin/env python3
"""Measure per-stage review latency against a running backend.

Usage:
    python scripts/bench_latency.py --url http://127.0.0.1:8000
    python scripts/bench_latency.py --url http://127.0.0.1:8000 \\
        --front path/to/front.jpg --back path/to/back.jpg --repeat 2

The script POSTs ``/api/review`` (or ``/api/label-check/batch`` with
``--label-check``) and prints ``timings_ms`` plus cache token counts from
the JSON body. It does not send API keys and never prints image bytes.

``--pipeline-only`` profiles the local Pillow / type-size path without an
HTTP server, so CPU-stage before/after numbers can be gathered offline.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _synthetic_jpeg(width: int, height: int, quality: int = 85) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (214, 204, 188))
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, width - 50, height - 50], outline=(18, 18, 18), width=5)
    draw.rectangle([90, 160, width - 90, 760], fill=(248, 246, 238))
    for index, line in enumerate(
        ("SAMPLE BRAND", "Cabernet Sauvignon", "13.5% Alc./Vol.", "750 mL", "GOVERNMENT WARNING:")
    ):
        draw.text((120, 200 + index * 64), line, fill=(12, 12, 12))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _read_image(path: str | None, fallback_w: int, fallback_h: int) -> tuple[str, bytes]:
    if path:
        data = Path(path).read_bytes()
        return Path(path).name, data
    return f"synthetic_{fallback_w}x{fallback_h}.jpg", _synthetic_jpeg(fallback_w, fallback_h)


def _print_timings(label: str, body: dict) -> None:
    timings = body.get("timings_ms") or {}
    print(f"\n{label}")
    print(f"  overall_status     {body.get('overall_status')}")
    print(f"  processing_time_ms {body.get('processing_time_ms')}")
    for key in (
        "decode",
        "pre_gate",
        "resize",
        "model",
        "compliance",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "model_ttft_ms",
        "model_generation_ms",
    ):
        if key in timings:
            print(f"  {key:28s} {timings[key]}")
    cache_read = timings.get("cache_read_input_tokens", 0)
    cache_create = timings.get("cache_creation_input_tokens", 0)
    if cache_read:
        print("  cache                HIT (cache_read_input_tokens > 0)")
    elif cache_create:
        print("  cache                WRITE (cache_creation_input_tokens > 0)")
    else:
        print("  cache                no write/read reported")


def _post_review(
    url: str,
    files: list[tuple[str, bytes]],
    *,
    label_check: bool,
    token: str,
    timeout: float,
) -> dict:
    import urllib.request

    boundary = "----ttbBenchBoundary7f3a"
    parts: list[bytes] = []

    def _field(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    def _file(field: str, filename: str, data: bytes) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                f"Content-Type: image/jpeg\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(data)
        parts.append(b"\r\n")

    if label_check:
        endpoint = url.rstrip("/") + "/api/label-check/batch"
        for name, data in files:
            _file("files", name, data)
        _field("image_counts", f"[{len(files)}]")
        _field("confirmed_beverage_type", "wine")
        if len(files) == 2:
            _field("photo_roles", json.dumps(["front", "back"]))
    else:
        endpoint = url.rstrip("/") + "/api/review"
        for name, data in files:
            _file("files", name, data)
        _field(
            "application",
            json.dumps(
                {
                    "beverage_type": "wine",
                    "brand_name": "SAMPLE BRAND",
                    "class_type": "Cabernet Sauvignon",
                    "alcohol_content": "13.5% Alc./Vol.",
                    "net_contents": "750 mL",
                }
            ),
        )

    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    request = urllib.request.Request(endpoint, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, list):
        return payload[0]
    return payload


def _pipeline_only(files: list[tuple[str, bytes]]) -> None:
    from app.claude_client import (
        open_image_for_intake,
        original_bytes_for_typesize,
        prepare_image,
        prepare_images_concurrent,
    )
    from app.cv_typesize import evaluate_type_size_from_image_bytes
    from app.quality import assess_image_quality

    print("pipeline-only (no HTTP / no model call)")
    decode_ms = pre_gate_ms = resize_ms = 0
    opened_images = []
    qualities = []
    originals = []
    for name, data in files:
        t0 = time.perf_counter()
        img = open_image_for_intake(data, name)
        decode_ms += int((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        quality = assess_image_quality(img)
        pre_gate_ms += int((time.perf_counter() - t0) * 1000)
        opened_images.append(img)
        qualities.append(quality)
        originals.append(original_bytes_for_typesize(img, data))

    t0 = time.perf_counter()
    prepare_images_concurrent(
        files,
        images=opened_images,
        qualities=qualities,
    )
    resize_ms = int((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    for orig, (name, _data) in zip(originals, files):
        evaluate_type_size_from_image_bytes(orig, name, net_contents="750 mL")
    compliance_ms = int((time.perf_counter() - t0) * 1000)

    print(f"  decode     {decode_ms}")
    print(f"  pre_gate   {pre_gate_ms}")
    print(f"  resize     {resize_ms}")
    print(f"  model      (skipped)")
    print(f"  compliance {compliance_ms}  (type-size only; rules are <1 ms)")
    for quality, (name, _) in zip(qualities, files):
        print(
            f"  {name}: blur={quality.blur_score} exposure={quality.exposure} "
            f"enhance={quality.blur_score < 400 or quality.exposure != 'ok'}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Backend origin, e.g. http://127.0.0.1:8000")
    parser.add_argument("--front", help="Front-label image path")
    parser.add_argument("--back", help="Back-label image path")
    parser.add_argument("--repeat", type=int, default=2, help="How many times to POST (default 2)")
    parser.add_argument("--label-check", action="store_true", help="Use /api/label-check/batch")
    parser.add_argument("--token", default="", help="Optional Bearer token")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--pipeline-only",
        action="store_true",
        help="Profile local decode/pre-gate/resize/type-size only",
    )
    args = parser.parse_args()

    files = [_read_image(args.front, 1400, 1800)]
    if args.back or args.front:
        if args.back:
            files.append(_read_image(args.back, 1400, 1800))
    else:
        files.append(_read_image(None, 1400, 1600))

    print(f"images: {', '.join(f'{n} ({len(b)} bytes)' for n, b in files)}")

    if args.pipeline_only:
        _pipeline_only(files)
        return 0

    if not args.url:
        parser.error("--url is required unless --pipeline-only is set")

    for attempt in range(1, args.repeat + 1):
        started = time.perf_counter()
        body = _post_review(
            args.url,
            files,
            label_check=args.label_check,
            token=args.token,
            timeout=args.timeout,
        )
        wall_ms = int((time.perf_counter() - started) * 1000)
        _print_timings(f"request {attempt}  wall_ms={wall_ms}", body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
