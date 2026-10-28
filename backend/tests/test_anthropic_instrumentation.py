"""Unit tests for structured Anthropic call instrumentation.

Every Messages API call must emit one INFO line with model, token counts,
optional cache tokens, API latency, and a request id when present. Logs must
never include image bytes, prompts, or secrets. A mocked Anthropic client is
used so no live API key is required.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.claude_client import (
    DEFAULT_PRODUCTION_MODEL,
    extract_label_fields,
    _log_anthropic_call,
)
from app.models import ExtractedLabelData


def _tool_input() -> dict:
    return {
        "brand_name": "TEST BRAND",
        "class_type": "Whiskey",
        "alcohol_content": "40% ABV",
        "net_contents": "750 mL",
        "name_and_address": "Test Distillery",
        "country_of_origin": "",
        "government_warning_header": "GOVERNMENT WARNING:",
        "government_warning_body": "Test Warning",
        "government_warning_present": True,
        "beverage_type_guess": "distilled_spirits",
        "origin_guess": "domestic",
        "field_locations": [],
        "notes": "",
        "sulfite_declaration": "",
        "allergen_statements": "",
        "age_statement": "",
        "commodity_statement": "",
        "is_alcohol_beverage_label": True,
        "per_field_confidence": {},
        "extraction_confidence": 0.95,
    }


def _mock_message(
    *,
    input_tokens: int = 1200,
    output_tokens: int = 80,
    cache_read: int | None = 40,
    cache_creation: int | None = 15,
    request_id: str | None = "req_test_123",
    message_id: str = "msg_test_456",
    model: str = DEFAULT_PRODUCTION_MODEL,
) -> SimpleNamespace:
    block = SimpleNamespace(type="tool_use", name="record_label_fields", input=_tool_input())
    usage_kwargs: dict[str, int] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cache_read is not None:
        usage_kwargs["cache_read_input_tokens"] = cache_read
    if cache_creation is not None:
        usage_kwargs["cache_creation_input_tokens"] = cache_creation
    response = SimpleNamespace(
        content=[block],
        usage=SimpleNamespace(**usage_kwargs),
        model=model,
        id=message_id,
    )
    if request_id is not None:
        response._request_id = request_id
    return response


def _parse_anthropic_logs(caplog: pytest.LogCaptureFixture) -> list[dict]:
    records = [r for r in caplog.records if "AnthropicCall:" in r.message]
    parsed = []
    for rec in records:
        json_str = rec.message.replace("AnthropicCall: ", "", 1)
        parsed.append(json.loads(json_str))
    return parsed


def test_extract_label_fields_logs_structured_call_metrics(caplog: pytest.LogCaptureFixture) -> None:
    """A successful mocked Anthropic call logs model, tokens, cache, latency, and request id."""
    image_bytes = b"SECRET_IMAGE_BYTES_MUST_NOT_APPEAR"
    mock_response = _mock_message()

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response) as mock_create:
            result = extract_label_fields([(image_bytes, "label.jpg")], preprocessed=True)

    assert isinstance(result, ExtractedLabelData)
    mock_create.assert_called_once()

    events = _parse_anthropic_logs(caplog)
    assert len(events) == 1
    data = events[0]
    assert data["event"] == "anthropic_call"
    assert data["model"] == DEFAULT_PRODUCTION_MODEL
    assert data["input_tokens"] == 1200
    assert data["output_tokens"] == 80
    assert data["cache_read_input_tokens"] == 40
    assert data["cache_creation_input_tokens"] == 15
    assert data["request_id"] == "req_test_123"
    assert isinstance(data["latency_ms"], int)
    assert data["latency_ms"] >= 0

    combined = " ".join(r.message for r in caplog.records)
    assert "SECRET_IMAGE_BYTES_MUST_NOT_APPEAR" not in combined
    assert "ANTHROPIC_API_KEY" not in combined
    assert "system" not in data
    assert "messages" not in data
    assert "prompt" not in data
    assert "token" not in data
    assert "api_key" not in data


def test_extract_label_fields_omits_absent_cache_and_request_id(caplog: pytest.LogCaptureFixture) -> None:
    """Cache token keys log as 0 when the response does not provide them; request_id stays omitted."""
    mock_response = _mock_message(cache_read=None, cache_creation=None, request_id=None, message_id="")
    # No HTTP request id and no message id.
    if hasattr(mock_response, "_request_id"):
        delattr(mock_response, "_request_id")

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response):
            extract_label_fields([(b"fake_jpeg", "label.jpg")], preprocessed=True)

    data = _parse_anthropic_logs(caplog)[0]
    assert data["cache_read_input_tokens"] == 0
    assert data["cache_creation_input_tokens"] == 0
    assert "request_id" not in data
    assert data["input_tokens"] == 1200
    assert data["output_tokens"] == 80


def test_extract_label_fields_falls_back_to_message_id(caplog: pytest.LogCaptureFixture) -> None:
    """When no HTTP request id exists, the message id is logged as request_id."""
    mock_response = _mock_message(request_id=None, message_id="msg_only_789")
    if hasattr(mock_response, "_request_id"):
        delattr(mock_response, "_request_id")

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response):
            extract_label_fields([(b"fake_jpeg", "label.jpg")], preprocessed=True)

    data = _parse_anthropic_logs(caplog)[0]
    assert data["request_id"] == "msg_only_789"


def test_extract_label_fields_logs_on_api_error(caplog: pytest.LogCaptureFixture) -> None:
    """A failed Anthropic call still logs model and latency (fail-open observability)."""
    api_error = RuntimeError("upstream unavailable")

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        with patch("app.claude_client._CLIENT.messages.create", side_effect=api_error):
            with pytest.raises(RuntimeError, match="upstream unavailable"):
                extract_label_fields([(b"fake_jpeg", "label.jpg")], preprocessed=True)

    events = _parse_anthropic_logs(caplog)
    assert len(events) == 1
    data = events[0]
    assert data["model"] == DEFAULT_PRODUCTION_MODEL
    assert isinstance(data["latency_ms"], int)
    assert data["latency_ms"] >= 0
    assert data["input_tokens"] is None
    assert data["output_tokens"] is None


def test_extract_label_fields_logs_request_id_from_api_error(caplog: pytest.LogCaptureFixture) -> None:
    """Request id on an Anthropic SDK error is captured when the response is missing."""

    class _ApiError(RuntimeError):
        def __init__(self) -> None:
            super().__init__("overloaded")
            self.request_id = "req_from_error"

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        with patch("app.claude_client._CLIENT.messages.create", side_effect=_ApiError()):
            with pytest.raises(RuntimeError, match="overloaded"):
                extract_label_fields([(b"fake_jpeg", "label.jpg")], preprocessed=True)

    data = _parse_anthropic_logs(caplog)[0]
    assert data["request_id"] == "req_from_error"


def test_instrumentation_logging_failure_is_fail_open() -> None:
    """A metrics logging error must not prevent a successful extraction."""
    mock_response = _mock_message()
    with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response):
        with patch("app.claude_client.json.dumps", side_effect=ValueError("serializer boom")):
            result = extract_label_fields([(b"fake_jpeg", "label.jpg")], preprocessed=True)
    assert result.brand_name == "TEST BRAND"


def test_log_anthropic_call_rejects_non_serializable_usage_mocks(caplog: pytest.LogCaptureFixture) -> None:
    """MagicMock usage fields are ignored so the log stays JSON-serializable."""
    from unittest.mock import MagicMock

    response = MagicMock()
    response.model = DEFAULT_PRODUCTION_MODEL
    response.id = MagicMock()
    response._request_id = MagicMock()
    response.usage = MagicMock()

    with caplog.at_level(logging.INFO, logger="app.claude_client"):
        _log_anthropic_call(model=DEFAULT_PRODUCTION_MODEL, latency_ms=12, response=response)

    data = _parse_anthropic_logs(caplog)[0]
    assert data["model"] == DEFAULT_PRODUCTION_MODEL
    assert data["input_tokens"] is None
    assert data["output_tokens"] is None
    assert "request_id" not in data
    assert data["latency_ms"] == 12
