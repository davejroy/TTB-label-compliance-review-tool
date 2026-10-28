"""Unit tests for Claude model selection and fast extraction feature flag.

Verifies:
1. Default behavior preserves the standard production model (claude-sonnet-4-6).
2. CLAUDE_MODEL environment variable overrides standard production model.
3. USE_FAST_EXTRACTION flag enables faster/cheaper model (defaulting to claude-haiku-4-5).
4. EXTRACTION_FAST_MODEL allows configuring a custom fast model when the flag is enabled.
5. extract_label_fields dynamically uses the model resolved by get_current_model().
"""

import os
from unittest.mock import MagicMock, patch

import pytest
import app.claude_client as claude_client
from app.claude_client import (
    DEFAULT_FAST_MODEL,
    DEFAULT_PRODUCTION_MODEL,
    EXTRACTION_TOOL,
    SYSTEM_PROMPT,
    extract_label_fields,
    get_current_model,
)
from app.models import ExtractedLabelData


def test_default_model_is_production_sonnet():
    """Without any environment variables set, model defaults to production Sonnet."""
    with patch.dict(os.environ, {}, clear=True):
        assert get_current_model() == DEFAULT_PRODUCTION_MODEL
        assert get_current_model() == "claude-sonnet-4-6"


def test_claude_model_env_override():
    """CLAUDE_MODEL overrides standard model when USE_FAST_EXTRACTION is false/unset."""
    with patch.dict(os.environ, {"CLAUDE_MODEL": "claude-opus-4-6"}, clear=True):
        assert get_current_model() == "claude-opus-4-6"


@pytest.mark.parametrize("flag_value", ["true", "True", "TRUE", "1", "yes", "YES"])
def test_use_fast_extraction_enabled(flag_value: str):
    """USE_FAST_EXTRACTION enabled returns default fast model (claude-haiku-4-5)."""
    with patch.dict(os.environ, {"USE_FAST_EXTRACTION": flag_value}, clear=True):
        assert get_current_model() == DEFAULT_FAST_MODEL
        assert get_current_model() == "claude-haiku-4-5"


@pytest.mark.parametrize("flag_value", ["false", "False", "0", "no", "disabled", ""])
def test_use_fast_extraction_disabled(flag_value: str):
    """USE_FAST_EXTRACTION disabled preserves production default."""
    with patch.dict(os.environ, {"USE_FAST_EXTRACTION": flag_value}, clear=True):
        assert get_current_model() == DEFAULT_PRODUCTION_MODEL


def test_stale_module_model_alias_removed():
    """Import-time MODEL = get_current_model() alias is unused and must stay gone."""
    assert not hasattr(claude_client, "MODEL")


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


def test_extract_request_uses_output_cap_and_cache_control():
    """Latency trim: compact max_tokens and cached static prefix on every call."""
    from app.claude_client import EXTRACTION_MAX_TOKENS

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

    with patch("app.claude_client._CLIENT.messages.create", return_value=mock_response) as mock_create:
        extract_label_fields([(b"fake_jpeg_bytes", "test.jpg")], preprocessed=True)
        _, kwargs = mock_create.call_args
        assert kwargs["max_tokens"] == EXTRACTION_MAX_TOKENS
        assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}


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


def _brand_prompt_text() -> str:
    return EXTRACTION_TOOL["input_schema"]["properties"]["brand_name"]["description"]


def test_brand_prompt_keeps_prominent_display_name_even_if_also_in_bottler():
    """Prompt must treat the display heading as brand even when it repeats in the bottler line."""
    brand_desc = _brand_prompt_text()
    combined = f"{SYSTEM_PROMPT}\n{brand_desc}"
    assert "most prominent display name" in combined.lower()
    assert "OLD TOM DISTILLERY" in combined
    assert "Bottled by" in combined
    assert "only occurrence" in combined.lower()
    # The previous rule blanked Old Tom because the display name also appeared
    # in the producer line. That wording must stay gone.
    assert "do not treat 'Bottled by Old Tom Distillery' as brand_name" not in combined
    assert "Never invent or infer a brand name from the" not in combined


def test_brand_prompt_does_not_force_zero_confidence_for_missing_brand():
    """A missing brand is a field result, not a forced 0.0 confidence score."""
    brand_desc = _brand_prompt_text()
    brand_rule_start = SYSTEM_PROMPT.find("CRITICAL BRAND NAME RULE:")
    brand_rule_end = SYSTEM_PROMPT.find("If image quality is poor,")
    brand_rule = SYSTEM_PROMPT[brand_rule_start:brand_rule_end]
    assert "0.0" not in brand_desc
    assert "0.0" not in brand_rule
