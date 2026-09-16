"""Unit tests for Claude model selection and fast extraction feature flag.

Verifies:
1. Default behavior preserves the standard production model (claude-sonnet-4-5).
2. CLAUDE_MODEL environment variable overrides standard production model.
3. USE_FAST_EXTRACTION flag enables faster/cheaper model (defaulting to claude-3-5-haiku-latest).
4. EXTRACTION_FAST_MODEL allows configuring a custom fast model when the flag is enabled.
5. extract_label_fields dynamically uses the model resolved by get_current_model().
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from app.claude_client import (
    DEFAULT_FAST_MODEL,
    DEFAULT_PRODUCTION_MODEL,
    extract_label_fields,
    get_current_model,
)
from app.models import ExtractedLabelData


def test_default_model_is_production_sonnet():
    """Without any environment variables set, model defaults to production Sonnet."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_current_model() == DEFAULT_PRODUCTION_MODEL
        assert get_current_model() == "claude-sonnet-4-5"


def test_claude_model_env_override():
    """CLAUDE_MODEL overrides standard model when USE_FAST_EXTRACTION is false/unset."""
    with patch.dict(os.environ, {"CLAUDE_MODEL": "claude-sonnet-4-6"}, clear=True):
        assert get_current_model() == "claude-sonnet-4-6"


@pytest.mark.parametrize("flag_value", ["true", "True", "TRUE", "1", "yes", "YES"])
def test_use_fast_extraction_enabled(flag_value: str):
    """USE_FAST_EXTRACTION enabled returns default fast model (claude-3-5-haiku-latest)."""
    with patch.dict(os.environ, {"USE_FAST_EXTRACTION": flag_value}, clear=True):
        assert get_current_model() == DEFAULT_FAST_MODEL
        assert get_current_model() == "claude-3-5-haiku-latest"


@pytest.mark.parametrize("flag_value", ["false", "False", "0", "no", "disabled", ""])
def test_use_fast_extraction_disabled(flag_value: str):
    """USE_FAST_EXTRACTION disabled preserves production default."""
    with patch.dict(os.environ, {"USE_FAST_EXTRACTION": flag_value}, clear=True):
        assert get_current_model() == DEFAULT_PRODUCTION_MODEL


def test_use_fast_extraction_with_custom_fast_model():
    """USE_FAST_EXTRACTION enabled with custom EXTRACTION_FAST_MODEL returns custom fast model."""
    with patch.dict(
        os.environ,
        {
            "USE_FAST_EXTRACTION": "true",
            "EXTRACTION_FAST_MODEL": "claude-haiku-custom-model",
        },
        clear=True,
    ):
        assert get_current_model() == "claude-haiku-custom-model"


def test_extract_label_fields_uses_active_model():
    """extract_label_fields must pass the model from get_current_model() to Anthropic client."""
    mock_response = MagicMock()
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "record_label_fields"
    mock_block.input = {
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
    mock_response.content = [mock_block]

    with patch.dict(os.environ, {"USE_FAST_EXTRACTION": "true"}, clear=True):
        with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response) as mock_create:
            # Pass preprocessed image bytes
            result = extract_label_fields([(b"fake_jpeg_bytes", "test.jpg")], preprocessed=True)
            assert isinstance(result, ExtractedLabelData)
            assert result.brand_name == "TEST BRAND"
            mock_create.assert_called_once()
            _, kwargs = mock_create.call_args
            assert kwargs["model"] == DEFAULT_FAST_MODEL
