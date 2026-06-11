import base64
import json
import os

from anthropic import Anthropic

from .models import ExtractedLabelData

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

EXTRACTION_TOOL = {
    "name": "record_label_fields",
    "description": "Record the fields read from an alcohol beverage label image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "brand_name": {
                "type": "string",
                "description": "The brand name as printed on the label, exactly as shown (preserve capitalization).",
            },
            "class_type": {
                "type": "string",
                "description": "The class/type designation, e.g. 'Kentucky Straight Bourbon Whiskey', 'Cabernet Sauvignon', 'India Pale Ale'.",
            },
            "alcohol_content": {
                "type": "string",
                "description": "Alcohol content as printed, e.g. '45% Alc./Vol. (90 Proof)' or '12.5% ABV'. Empty string if not present on label.",
            },
            "net_contents": {
                "type": "string",
                "description": "Net contents as printed, e.g. '750 mL' or '12 FL OZ'.",
            },
            "name_and_address": {
                "type": "string",
                "description": "The bottler/producer/importer name and address block, exactly as printed.",
            },
            "country_of_origin": {
                "type": "string",
                "description": "Country of origin statement if present (e.g. 'Product of Scotland'), empty string if not present.",
            },
            "government_warning_header": {
                "type": "string",
                "description": "The exact text of the warning header as printed (e.g. 'GOVERNMENT WARNING:' or 'Government Warning:'), preserving the exact capitalization and punctuation used on the label. Empty string if no warning is present.",
            },
            "government_warning_body": {
                "type": "string",
                "description": "The full text of the government warning statement that follows the header, exactly as printed including punctuation, preserving capitalization. Empty string if not present.",
            },
            "government_warning_present": {
                "type": "boolean",
                "description": "True if any form of a government health warning statement appears on the label.",
            },
            "notes": {
                "type": "string",
                "description": "Any other observations relevant to compliance review (e.g. image is blurry/cut off, text partially obscured by glare, multiple labels visible).",
            },
        },
        "required": [
            "brand_name",
            "class_type",
            "alcohol_content",
            "net_contents",
            "name_and_address",
            "country_of_origin",
            "government_warning_header",
            "government_warning_body",
            "government_warning_present",
            "notes",
        ],
    },
}

SYSTEM_PROMPT = (
    "You are assisting a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance "
    "agent in reading the text printed on an alcohol beverage label image. "
    "Transcribe the text exactly as it appears, including original capitalization, "
    "spacing, and punctuation - this is important because exact wording and "
    "capitalization matter for compliance (especially for the Government Warning "
    "statement). Do not correct, paraphrase, or 'clean up' the text. If a field is "
    "not visible on the label, return an empty string for it. If the image is "
    "low quality, at an angle, or partially obscured, do your best and note the "
    "issue in the 'notes' field. Always respond by calling the record_label_fields tool."
)


def _media_type_for(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/jpeg")


def extract_label_fields(image_bytes: bytes, filename: str) -> ExtractedLabelData:
    client = Anthropic()

    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_label_fields"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _media_type_for(filename),
                            "data": base64.b64encode(image_bytes).decode("utf-8"),
                        },
                    },
                    {
                        "type": "text",
                        "text": "Read this alcohol beverage label and record its fields.",
                    },
                ],
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_label_fields":
            return ExtractedLabelData(**block.input)

    raise RuntimeError("Claude did not return structured label data")
