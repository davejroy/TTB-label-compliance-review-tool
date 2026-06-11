"""Claude vision integration: turns label image(s) into structured fields.

This is the only module that talks to the Anthropic API. It builds a single
request containing the uploaded image(s) plus a forced tool call
(``record_label_fields``) whose JSON-schema (``EXTRACTION_TOOL``) mirrors
``ExtractedLabelData``. Forcing the tool call guarantees a structured,
parseable response in one round trip (see NFR-1 in docs/PDR.md).
"""

import base64
import os

from anthropic import Anthropic

from .models import ExtractedLabelData

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# JSON-schema tool definition passed to Claude. Keep this in sync with
# ExtractedLabelData in models.py - the tool's "input" is parsed directly
# into that model (see extract_label_fields below).
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
            "beverage_type_guess": {
                "type": "string",
                "enum": ["distilled_spirits", "wine", "beer", "unknown"],
                "description": (
                    "Best guess at the beverage category based on the class/type "
                    "designation and overall label appearance: 'distilled_spirits' "
                    "(whiskey, vodka, rum, gin, tequila, liqueurs, etc.), 'wine' "
                    "(wine, cider, mead, etc.), 'beer' (beer, ale, lager, malt "
                    "beverage), or 'unknown' if it cannot be determined."
                ),
            },
            "field_locations": {
                "type": "array",
                "description": (
                    "For each of brand_name, class_type, alcohol_content, "
                    "net_contents, name_and_address, country_of_origin, and "
                    "government_warning that was found on the label, an entry "
                    "describing roughly where it appears and how confident you "
                    "are in the reading. Omit entries for fields that were not "
                    "found."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "enum": [
                                "brand_name",
                                "class_type",
                                "alcohol_content",
                                "net_contents",
                                "name_and_address",
                                "country_of_origin",
                                "government_warning",
                            ],
                        },
                        "image_index": {
                            "type": "integer",
                            "description": "0-based index into the provided images where this field appears.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "How confident you are in this field's transcription, e.g. lower for blurry, angled, or glare-obscured text.",
                        },
                        "x": {
                            "type": "number",
                            "description": "Left edge of the bounding box, as a fraction (0-1) of the image width.",
                        },
                        "y": {
                            "type": "number",
                            "description": "Top edge of the bounding box, as a fraction (0-1) of the image height.",
                        },
                        "width": {
                            "type": "number",
                            "description": "Width of the bounding box, as a fraction (0-1) of the image width.",
                        },
                        "height": {
                            "type": "number",
                            "description": "Height of the bounding box, as a fraction (0-1) of the image height.",
                        },
                    },
                    "required": ["field", "image_index", "confidence", "x", "y", "width", "height"],
                },
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
            "beverage_type_guess",
            "field_locations",
            "notes",
        ],
    },
}

SYSTEM_PROMPT = (
    "You are assisting a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance "
    "agent in reading the text printed on an alcohol beverage label. You may be "
    "given multiple images (e.g. front and back panels of the same bottle, or "
    "multiple photos of the same panel) - treat them as views of a single label "
    "and combine information from all of them into one set of fields. "
    "Transcribe the text exactly as it appears, including original capitalization, "
    "spacing, and punctuation - this is important because exact wording and "
    "capitalization matter for compliance (especially for the Government Warning "
    "statement). Do not correct, paraphrase, or 'clean up' the text. If a field is "
    "not visible in any of the images, return an empty string for it. If an image is "
    "low quality, at an angle, or partially obscured, do your best and note the "
    "issue in the 'notes' field. For each field you do find, also record an entry "
    "in 'field_locations' with an approximate bounding box (as fractions of that "
    "image's width/height, with (0,0) at the top-left corner) showing where on the "
    "image the text appears, the index of the image it appears in, and a confidence "
    "level reflecting how certain you are in the transcription. Always respond by "
    "calling the record_label_fields tool."
)


def _media_type_for(filename: str) -> str:
    """Guess the image MIME type from a filename's extension.

    Defaults to ``image/jpeg`` for unrecognized/missing extensions, since
    that is the most common format for phone photos.
    """
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext, "image/jpeg")


def extract_label_fields(images: list[tuple[bytes, str]]) -> ExtractedLabelData:
    """Extract structured label fields from one or more images of a label.

    Sends all images in a single message to Claude along with the
    ``record_label_fields`` tool definition, and forces that tool to be
    called (``tool_choice``) so the response is always structured. Up to
    ``MAX_IMAGES_PER_LABEL`` (see main.py) images of the same physical label
    (e.g. front/back panels) can be passed and are combined into one
    ``ExtractedLabelData``.

    Args:
        images: list of ``(image_bytes, filename)`` tuples. The filename is
            only used to guess the MIME type.

    Returns:
        The parsed ``ExtractedLabelData`` from the tool call.

    Raises:
        RuntimeError: if Claude's response does not include the expected
            tool call (should not happen given ``tool_choice``, but guarded
            against defensively).
    """
    client = Anthropic()

    image_blocks = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _media_type_for(filename),
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            },
        }
        for image_bytes, filename in images
    ]

    if len(images) == 1:
        prompt_text = "Read this alcohol beverage label and record its fields."
    else:
        prompt_text = (
            f"These {len(images)} images show the same alcohol beverage label "
            "(e.g. front and back panels). Read all of them together and record "
            "one combined set of fields."
        )

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
                    *image_blocks,
                    {
                        "type": "text",
                        "text": prompt_text,
                    },
                ],
            }
        ],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_label_fields":
            return ExtractedLabelData(**block.input)

    raise RuntimeError("Claude did not return structured label data")
