"""Claude vision integration: turns label image(s) into structured fields.
This is the only module that talks to the Anthropic API. It builds a single
request containing the uploaded image(s) plus a forced tool call
(``record_label_fields``) whose JSON-schema (``EXTRACTION_TOOL``) mirrors
``ExtractedLabelData``. Forcing the tool call guarantees a structured,
parseable response in one round trip (see NFR-1 in docs/PDR.md).

Performance note: ``_CLIENT`` is a module-level singleton so the underlying
``httpx`` connection pool is reused across requests instead of being created
and torn down on every call.
"""

import base64
import io
import os

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError
from PIL import Image, ImageEnhance, ImageFilter, UnidentifiedImageError

from .models import ExtractedLabelData

# Default to the faster/cheaper Haiku model for ~3-5x speed improvement.
# Override with CLAUDE_MODEL env var (e.g. "claude-sonnet-4-6") if higher
# accuracy is needed on complex labels.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-3-5")

# Module-level Anthropic client - reuses the underlying httpx connection pool
# across all requests instead of creating a new client per call.
_CLIENT = Anthropic()

# Claude resizes images so the long edge is at most MAX_IMAGE_DIMENSION px
# before processing them. Keeping images at 1024px (down from 1568) cuts
# upload bandwidth and latency meaningfully, while preserving enough detail
# for text extraction on typical bottle photos. The field-location coordinates
# remain valid because they are fractions of the (already-resized) image dimensions.
MAX_IMAGE_DIMENSION = 1024

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
            "origin_guess": {
                "type": "string",
                "enum": ["domestic", "imported", "unknown"],
                "description": (
                    "Best guess at whether this product is domestic (produced in "
                    "the US) or imported, based on cues such as a 'Product of "
                    "<country>' or 'Imported by/from' statement, the country in "
                    "the name_and_address block, or other label text. Use "
                    "'unknown' only if there is genuinely no indication either way."
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
                            "description": "0-based index of the image this field was read from.",
                        },
                        "x": {
                            "type": "number",
                            "description": "Left edge of the field as a fraction of image width (0.0=left, 1.0=right).",
                        },
                        "y": {
                            "type": "number",
                            "description": "Top edge of the field as a fraction of image height (0.0=top, 1.0=bottom).",
                        },
                        "width": {
                            "type": "number",
                            "description": "Width of the field as a fraction of image width.",
                        },
                        "height": {
                            "type": "number",
                            "description": "Height of the field as a fraction of image height.",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": "How confident you are in the transcription of this field.",
                        },
                    },
                    "required": ["field", "image_index", "x", "y", "width", "height", "confidence"],
                },
            },
            "notes": {
                "type": "string",
                "description": (
                    "Any issues affecting transcription quality (e.g. image angle, "
                    "glare, partial obstruction). Empty string if the image quality "
                    "was acceptable."
                ),
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
            "origin_guess",
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


def _enhance_for_ocr(img: Image.Image) -> Image.Image:
    """Apply light image enhancements to improve text legibility for Claude.

    The goal is to help Claude read labels shot in variable lighting or with
    slight camera blur - common when agents photograph bottles in the field.
    We apply three gentle, non-destructive steps in order:

    1. Auto-levels (contrast stretch) - maps the actual min/max pixel values
       to 0-255 so underexposed or washed-out photos get full tonal range.
       Skips images that are already well-exposed (within 20% of full range).

    2. Sharpening - applies an unsharp mask to crisp up slightly blurry text
       without introducing harsh artefacts on already-sharp photos.

    3. Mild brightness correction - if the image mean luminance is below 80
       (dark) or above 200 (washed out) we nudge it toward the midrange.
       The correction is intentionally small (factor 1.25 / 0.85) to avoid
       over-processing photos that are simply high-contrast by design.

    All operations work on a converted RGB copy so palette/RGBA modes are
    handled correctly and the caller's original image is not mutated.
    """
    # Work in RGB throughout
    if img.mode != "RGB":
        img = img.convert("RGB")

    # --- Step 1: Auto-levels (per-channel histogram stretch) ---
    import struct
    pixels = list(img.getdata())
    # Flatten to per-channel min/max
    r_vals = [p[0] for p in pixels]
    g_vals = [p[1] for p in pixels]
    b_vals = [p[2] for p in pixels]
    r_min, r_max = min(r_vals), max(r_vals)
    g_min, g_max = min(g_vals), max(g_vals)
    b_min, b_max = min(b_vals), max(b_vals)
    # Only stretch if the image is noticeably compressed (range < 80% of 255)
    needs_stretch = (
        (r_max - r_min) < 204 or
        (g_max - g_min) < 204 or
        (b_max - b_min) < 204
    )
    if needs_stretch:
        def stretch(val, lo, hi):
            if hi == lo:
                return val
            return int((val - lo) * 255 / (hi - lo))
        stretched = [
            (stretch(p[0], r_min, r_max),
             stretch(p[1], g_min, g_max),
             stretch(p[2], b_min, b_max))
            for p in pixels
        ]
        img.putdata(stretched)

    # --- Step 2: Unsharp mask sharpening ---
    # radius=1, percent=120, threshold=3 gives a gentle crispening that
    # helps slightly soft phone-camera shots without over-sharpening.
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))

    # --- Step 3: Mild brightness correction ---
    # Compute mean luminance from the green channel (perceptually dominant)
    g_mean = sum(p[1] for p in list(img.getdata())) / (img.width * img.height)
    if g_mean < 80:
        # Dark image - boost brightness slightly
        img = ImageEnhance.Brightness(img).enhance(1.25)
    elif g_mean > 200:
        # Washed-out image - reduce brightness slightly
        img = ImageEnhance.Brightness(img).enhance(0.85)

    return img


def _downscale_if_needed(image_bytes: bytes, media_type: str) -> tuple[bytes, str]:
    """Validate, enhance, and downscale an image for Claude.

    Steps:
    1. Validate the image bytes with Pillow (raises ValueError on corrupt files).
    2. Apply ``_enhance_for_ocr`` to improve legibility on field-shot photos
       with variable lighting or slight blur.
    3. Downscale so the longest edge is at most MAX_IMAGE_DIMENSION px.

    Returns ``(image_bytes, media_type)`` - always JPEG after this step.

    This is CPU-bound (Pillow) and should be called from a thread pool when
    inside an async context (see main.py).

    Raises:
        ValueError: If the bytes cannot be decoded as a valid image.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # Check for file truncation / corruption
        # Re-open after verify() since verify() exhausts the stream
        img = Image.open(io.BytesIO(image_bytes))
    except (UnidentifiedImageError, Exception) as exc:
        raise ValueError(f"Invalid or unreadable image file: {exc}") from exc

    # Convert palette / RGBA modes before processing
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Apply OCR-optimised enhancements (lighting normalisation + sharpening)
    img = _enhance_for_ocr(img)

    # Downscale if the longest edge exceeds the limit
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIMENSION:
        scale = MAX_IMAGE_DIMENSION / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # Always encode as JPEG for consistent compression
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def extract_label_fields(images: list[tuple[bytes, str]]) -> ExtractedLabelData:
    """Send label image(s) to Claude and return the extracted structured fields.

    Uses the module-level ``_CLIENT`` singleton to reuse connection-pool
    resources across requests.

    Args:
        images: list of ``(image_bytes, filename)`` tuples. The filename is
            only used to guess the MIME type.

    Returns:
        The parsed ``ExtractedLabelData`` from the tool call.

    Raises:
        AuthenticationError: API key is missing or invalid.
        RateLimitError: Anthropic rate limit reached.
        APITimeoutError: Request timed out.
        APIConnectionError: Network-level connection failure.
        APIStatusError: Other non-2xx response from the Anthropic API.
        RuntimeError: Claude responded but did not call the expected tool,
            or called it with a payload that does not match the schema.
    """
    image_blocks = []
    for image_bytes, filename in images:
        resized_bytes, media_type = _downscale_if_needed(image_bytes, _media_type_for(filename))
        image_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(resized_bytes).decode("utf-8"),
                },
            }
        )

    if len(images) == 1:
        prompt_text = "Read this alcohol beverage label and record its fields."
    else:
        prompt_text = (
            f"These {len(images)} images show the same alcohol beverage label "
            "(e.g. front and back panels). Read all of them together and record "
            "one combined set of fields."
        )

    # max_tokens set to 800: enough for complete field extraction including
    # field_locations array and notes, while avoiding wasteful padding that
    # increases latency. Haiku is more token-efficient than Sonnet so 800
    # is sufficient for all typical label configurations.
    response = _CLIENT.messages.create(
        model=MODEL,
        max_tokens=800,
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
            try:
                return ExtractedLabelData(**block.input)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Claude returned a tool call with invalid field data: {exc}"
                ) from exc

    raise RuntimeError("Claude did not return structured label data")
