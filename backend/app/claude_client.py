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
import logging

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError


# Module-level logger - handlers/level configured by the application host
# (uvicorn, gunicorn, etc.).  __name__ scopes records to this module.
_log = logging.getLogger(__name__)


class ImageQualityError(ValueError):
    """Raised when an uploaded image cannot be read due to poor quality.

    Inherits ValueError so legacy callers that catch ValueError still work.
    The ``user_message`` attribute holds a short, plain-English string that
    is safe to return directly to the end-user (no internal detail leaked).

    Best-practice note (RFC 7807 / PEP 3151): using a dedicated exception
    class lets callers distinguish a user-actionable photo error from an
    unrelated system error without parsing message strings.
    """

    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


# Minimum pixel area (width * height) required after downscaling.  Images
# smaller than this threshold rarely contain legible label text.
_MIN_PIXEL_AREA = 100 * 100  # 10 000 px - roughly a 100x100 thumbnail
from .models import ExtractedLabelData

# Default to the faster/cheaper Haiku model for ~3-5x speed improvement.
# Override with CLAUDE_MODEL env var (e.g. "claude-sonnet-4-6") if higher
# accuracy is needed on complex labels.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-3-5")

# Module-level Anthropic client - reuses the underlying httpx connection pool
# across all requests instead of creating a new client per call.
_CLIENT = Anthropic()

# Maximum pixel dimension for images sent to Claude. 800px gives Claude
# enough detail to read label text while meaningfully cutting upload size
# and Claude processing time vs. the previous 1024px limit.
MAX_IMAGE_DIMENSION = 800

# JPEG encode quality for processed images. 82 produces ~25% smaller files
# than quality=90 with no meaningful loss in text legibility for a vision
# model reading characters.
JPEG_QUALITY = 82

# Timeout in seconds for the Claude API call. Prevents the event loop from
# hanging indefinitely if the API is slow or unresponsive.
CLAUDE_TIMEOUT = 30.0

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
                        "x": {"type": "number", "description": "Left edge as fraction of image width."},
                        "y": {"type": "number", "description": "Top edge as fraction of image height."},
                        "width": {"type": "number", "description": "Width as fraction of image width."},
                        "height": {"type": "number", "description": "Height as fraction of image height."},
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
            "sulfite_declaration": {
                "type": "string",
                "description": (
                    "The sulfite/sulfur dioxide disclosure statement as printed on the "
                    "label, e.g. 'Contains Sulfites', 'Contains Sulfiting Agents'. "
                    "Leave empty string if no such statement appears."
                ),
            },
            "allergen_statements": {
                "type": "string",
                "description": (
                    "Any allergen or additive disclosure statements on the label, "
                    "e.g. 'FD&C Yellow No. 5', 'PHENYLKETONURICS: CONTAINS PHENYLALANINE' "
                    "(aspartame), saccharin warnings, 'Contains: Cochineal Extract / Carmine'. "
                    "Concatenate multiple statements with a semicolon. "
                    "Leave empty string if none are present."
                ),
            },
            "age_statement": {
                "type": "string",
                "description": (
                    "The age statement as printed on the label for whiskies and other "
                    "aged spirits, e.g. 'Aged 2 Years', 'Aged 18 Months', '12 Year Old'. "
                    "Leave empty string if no age statement appears."
                ),
            },
            "commodity_statement": {
                "type": "string",
                "description": (
                    "The commodity or importer statement on imported spirits labels, "
                    "e.g. 'Imported Scotch Whisky', 'Imported by XYZ Imports, New York, NY'. "
                    "Leave empty string if no such statement appears or the product is domestic."
                ),
            },
            "is_alcohol_beverage_label": {
                "type": "boolean",
                "description": (
                    "True if the image appears to be a label from an alcohol beverage "
                    "(beer, wine, malt beverage, distilled spirits, or similar). "
                    "False if the image is clearly NOT an alcohol beverage label "
                    "(e.g. a food product, soft drink, cleaning product, or anything "
                    "other than an alcoholic drink). When in doubt, return True."
                ),
            "extraction_confidence": {
                "type": "number",
                "description": (
                    "Overall readability / extraction confidence: a float from 0.0 "
                    "(image completely unreadable - blank, blurred, or wrong subject) "
                    "to 1.0 (crystal-clear label, all text perfectly legible). "
                    "Score based on: focus/blur, lighting/glare, label coverage, "
                    "and how confidently each required field could be read. "
                    "0.0-0.49 = unreliable; 0.50-0.69 = marginal; 0.70-0.89 = good; 0.90-1.0 = excellent."
                ),
            },
            },
        },
            "per_field_confidence": {
                "type": "object",
                "description": (
                    "Per-field readability scores: a JSON object mapping each field "
                    "you populated to a float 0.0-1.0 reflecting how clearly you read "
                    "that specific field. Lower score = harder to read. Government Warning "
                    "body text on a curved bottle legitimately scores 0.50-0.65 even on "
                    "a well-lit photo. Example: {brand_name: 0.95, government_warning_body: "
                    "0.55, alcohol_content: 0.88}. Drives per-field confidence gating so "
                    "unreadable fields fail with a targeted retake request."
                ),
                "additionalProperties": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },

        "required": [
            "brand_name", "class_type", "alcohol_content", "net_contents",
            "name_and_address", "country_of_origin", "government_warning_header",
            "government_warning_body", "government_warning_present",
            "beverage_type_guess", "origin_guess", "field_locations", "notes",
            "sulfite_declaration", "allergen_statements",
            "age_statement", "commodity_statement",
            "is_alcohol_beverage_label",
            "extraction_confidence",
        ],
    },
}

SYSTEM_PROMPT = (
    "You are a label image classifier and text extractor for a TTB (Alcohol and "
    "Tobacco Tax and Trade Bureau) compliance tool. "
    "FIRST: Determine whether the image shows an alcohol beverage label. Alcohol "
    "beverage labels are for products such as beer, ale, lager, malt beverages, "
    "wine, cider, mead, distilled spirits (whiskey, vodka, rum, gin, tequila, "
    "brandy, liqueurs, etc.). If the image shows ANYTHING ELSE - such as vitamins, "
    "supplements, food, condiments, soft drinks, cleaning products, medications, "
    "or any non-alcoholic product - you MUST set is_alcohol_beverage_label to false. "
    "Do NOT assume the image is an alcohol label just because you were asked to "
    "analyze it. Look at the actual product shown in the image. "
    "SECOND: If and only if it IS an alcohol beverage label, transcribe the label "
    "text exactly as it appears, including original capitalization, spacing, and "
    "punctuation. Do not correct, paraphrase, or 'clean up' the text. You may be "
    "given multiple images (e.g. front and back panels of the same bottle) - treat "
    "them as views of a single label and combine information from all of them. "
    "If a field is not visible, return an empty string. If image quality is poor, "
    "do your best and note the issue in 'notes'. For each field found, record an "
    "entry in 'field_locations' with an approximate bounding box (fractions 0-1 of "
    "the image's width/height, (0,0) at top-left), the image index, and confidence. "
    "Important: label text often wraps with end-of-line hyphens (e.g. 'Con-' on "
    "one line, 'sumption of' on the next). When transcribing, join hyphenated line "
    "breaks back into the full word (e.g. 'Consumption of'). "
    "Always respond by calling the record_label_fields tool. "
    "THIRD: Set extraction_confidence to a float 0.0-1.0 reflecting overall "
    "image readability (0.0 = completely unreadable; 1.0 = perfectly legible). "
    "Consider: focus/blur, lighting/glare, how much of the label is visible, "
    "and how confidently each required field could be extracted. "
    "A score below 0.70 means the image quality is too low for reliable compliance "
    "checking and the agent will be asked to retake the photo."
)
    "FOURTH: For per_field_confidence set a JSON object mapping each extracted field to "
    "a score 0.0-1.0 for how clearly that specific field was readable. High (0.85+) = "
    "sharp text; low (<0.55) = blurred or partially visible. Government Warning body "
    "text on a curved bottle legitimately scores 0.50-0.65 on a good photo."


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
    """Apply memory-efficient enhancements to improve text legibility for Claude.

    Uses Pillow's built-in C-level operations throughout - no Python pixel
    loops - so memory overhead is minimal even on constrained instances.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Auto-contrast: stretch histogram to full 0-255 range, clipping 1%
    # of outlier pixels so a single glare spot does not dominate.
    img = ImageOps.autocontrast(img, cutoff=1)
    # Gentle unsharp mask to crisphen slightly blurry phone-camera shots.
    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    # Brightness nudge: measure mean luminance via C-level histogram stats
    # (no Python pixel iteration), then apply a small correction if needed.
    stat = ImageStat.Stat(img)
    mean_luminance = sum(stat.mean) / 3
    if mean_luminance < 80:
        img = ImageEnhance.Brightness(img).enhance(1.25)
    elif mean_luminance > 200:
        img = ImageEnhance.Brightness(img).enhance(0.85)
    return img


def prepare_image(image_bytes: bytes, filename: str) -> tuple[bytes, str]:
    """Validate, enhance, downscale, and JPEG-encode one uploaded image.

    This is the single processing step that replaces the old
    ``_downscale_if_needed`` call. Callers should store the returned
    ``(processed_bytes, "image/jpeg")`` and pass them directly to
    ``extract_label_fields`` via the ``preprocessed`` argument to avoid
    re-processing the same image twice.

    Steps (in order):
    1. Validate with Pillow (raises ValueError on corrupt/unreadable files).
    2. Convert palette/RGBA modes to RGB.
    3. Downscale so the longest edge is at most MAX_IMAGE_DIMENSION px.
       Downscaling first keeps enhancement work as cheap as possible.
    4. Apply ``_enhance_for_ocr`` (auto-contrast, sharpening, brightness).
    5. JPEG-encode at JPEG_QUALITY.

    This is CPU-bound (Pillow) and should be called from a thread pool when
    inside an async context (see main.py).

    Raises:
        ValueError: If the bytes cannot be decoded as a valid image.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
        img = Image.open(io.BytesIO(image_bytes))
    except (UnidentifiedImageError, Exception) as exc:
                # Log the technical detail internally but give the user a friendly,
                # actionable message (never expose raw exc str to end-users in prod).
                _log.warning("Unreadable image '%s': %s", filename, exc)
                raise ImageQualityError(
                                "Photo quality is too low to read. The file could not be decoded "
                                "as a valid image. Please submit a new, clear photo saved as "
                                "JPEG or PNG."
                ) from exc

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Downscale FIRST so enhancement runs on the smaller image.
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIMENSION:
        scale = MAX_IMAGE_DIMENSION / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    # Reject images that are too small to contain readable label text.
    # This catches heavily cropped screenshots, icons, and thumbnails
    # that would waste an API call and produce unreliable OCR output.
    # Best practice: validate inputs early and fail fast with a clear,
    # user-actionable error rather than returning a low-confidence result.
    w, h = img.size
    if w * h < _MIN_PIXEL_AREA:
            _log.warning(
                            "Image '%s' too small (%dx%d px) - rejecting.", filename, w, h
            )
            raise ImageQualityError(
                            f"Photo quality is too low to read. The image is only {w}x{h} "
                            "pixels, which is too small for reliable text recognition. "
                            "Please submit a new photo taken closer to the label."
            )
    
    img = _enhance_for_ocr(img)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue(), "image/jpeg"


# Keep the old name as an alias so existing callers in main.py are not broken
# until they are updated to use prepare_image directly.
_downscale_if_needed = prepare_image


def extract_label_fields(
    images: list[tuple[bytes, str]],
    preprocessed: bool = False,
) -> ExtractedLabelData:
    """Send label image(s) to Claude and return the extracted structured fields.

    Args:
        images: list of ``(image_bytes, filename)`` tuples.
        preprocessed: if True, ``image_bytes`` are already processed JPEG
            bytes (output of ``prepare_image``) and the media type is always
            ``image/jpeg``. Avoids re-running the expensive Pillow pipeline
            on bytes that were already validated, enhanced, and downscaled.
            If False (default), ``prepare_image`` is called on each image.

    Returns:
        The parsed ``ExtractedLabelData`` from the tool call.

    Raises:
        AuthenticationError, RateLimitError, APITimeoutError,
        APIConnectionError, APIStatusError, RuntimeError.
    """
    image_blocks = []
    for image_bytes, filename in images:
        if preprocessed:
            final_bytes, media_type = image_bytes, "image/jpeg"
        else:
            final_bytes, media_type = prepare_image(image_bytes, filename)
        image_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(final_bytes).decode("utf-8"),
            },
        })

    prompt_text = (
        "Analyze this product label image and record its fields."
        if len(images) == 1
        else (
            f"Analyze these {len(images)} product label images (they may show "
            "different panels of the same product). Record one combined set of fields."
        )
    )

    response = _CLIENT.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_PROMPT,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "tool", "name": "record_label_fields"},
        timeout=CLAUDE_TIMEOUT,
        messages=[{"role": "user", "content": [*image_blocks, {"type": "text", "text": prompt_text}]}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "record_label_fields":
            try:
                data = ExtractedLabelData(**block.input)
            except ValidationError as exc:
                raise RuntimeError(
                    f"Claude returned a tool call with invalid field data: {exc}"
                ) from exc
            # Product type guard: reject non-alcohol labels before compliance checks.
            if not data.is_alcohol_beverage_label:
                raise ValueError(
                    "The submitted image does not appear to be an alcohol beverage "
                    "label. This tool only reviews labels for beer, wine, malt "
                    "beverages, and distilled spirits. Please resubmit with a photo "
                    "of an alcohol beverage label."
                )
            return data

    raise RuntimeError("Claude did not return structured label data")
