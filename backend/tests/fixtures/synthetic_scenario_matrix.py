"""Synthetic label graphics and ground-truth scenarios for functional QA.

This module deliberately keeps the compliance ground truth in structured Python
data rather than asking OCR/vision to read the fixture.  That lets tests answer
two different questions independently:

* Does the deterministic compliance engine return the expected result for a
  known extraction?
* Does the image-quality pipeline handle realistic capture conditions?

The rendered images are fictional, contain no real brand, and are generated
into a caller-supplied directory.  They are not regulatory artwork and are not
evidence.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from app.compliance import CANONICAL_WARNING_BODY, CANONICAL_WARNING_HEADER
from app.claude_client import diagnose_image_quality
from app.models import ApplicationData, ExtractedLabelData


WARNING = {
    "government_warning_present": True,
    "government_warning_header": CANONICAL_WARNING_HEADER,
    "government_warning_body": CANONICAL_WARNING_BODY,
}

BASE_SPIRITS = {
    "brand_name": "NORTH STAR RESERVE",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "name_and_address": "Bottled by North Star Distillery, Bardstown, KY",
    "beverage_type_guess": "distilled_spirits",
    "origin_guess": "domestic",
    **WARNING,
}

BASE_WINE = {
    "brand_name": "BLUE HARBOR CELLARS",
    "class_type": "Red Wine",
    "alcohol_content": "13.5% Alc./Vol.",
    "net_contents": "750 mL",
    "name_and_address": "Imported by Harbor Gateway Imports, New York, NY",
    "country_of_origin": "Product of Chile",
    "beverage_type_guess": "wine",
    "origin_guess": "imported",
    "sulfite_declaration": "CONTAINS SULFITES",
    **WARNING,
}

BASE_BEER = {
    "brand_name": "PRAIRIE SIGNAL",
    "class_type": "Malt Beverage",
    "alcohol_content": "",
    "net_contents": "12 FL. OZ.",
    "name_and_address": "Brewed and canned by Prairie Signal Brewing, Austin, TX",
    "beverage_type_guess": "beer",
    "origin_guess": "domestic",
    **WARNING,
}


def _font(size: int, bold: bool = False):
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for name in names if bold else names[::-1]:
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_label(fields: dict[str, Any], *, theme: str = "spirits") -> Image.Image:
    """Render a readable 900x1200 fictional label with exact supplied text."""
    palette = {
        "spirits": ((239, 232, 211), (27, 46, 63), (173, 118, 44)),
        "wine": ((242, 228, 219), (83, 34, 47), (166, 82, 71)),
        "beer": ((230, 241, 207), (31, 76, 49), (194, 151, 39)),
    }[theme]
    bg, ink, accent = palette
    image = Image.new("RGB", (900, 1200), (63, 67, 75))
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = 90, 70, 810, 1130
    draw.rounded_rectangle((x0, y0, x1, y1), radius=28, fill=bg, outline=accent, width=8)
    draw.line((x0 + 45, y0 + 210, x1 - 45, y0 + 210), fill=accent, width=4)
    draw.line((x0 + 45, y1 - 300, x1 - 45, y1 - 300), fill=accent, width=4)

    cx, max_width = x0 + 55, (x1 - x0) - 110
    y = y0 + 48
    title = _font(52, bold=True)
    body = _font(31)
    small = _font(23)
    warning_head = _font(23, bold=True)
    warning_body = _font(18)

    def put(value: str | None, font, gap: int = 10, fill=ink):
        nonlocal y
        if not value:
            return
        for line in _wrap(draw, str(value), font, max_width):
            draw.text((cx, y), line, font=font, fill=fill)
            y = draw.textbbox((cx, y), line, font=font)[3] + gap

    put(fields.get("brand_name"), title, 14)
    put(fields.get("class_type"), body, 12)
    y += 12
    put(fields.get("alcohol_content"), body, 10)
    put(fields.get("net_contents"), small, 10)
    put(fields.get("name_and_address"), small, 8)
    put(fields.get("country_of_origin"), small, 8)

    y = y1 - 270
    put(fields.get("sulfite_declaration"), small, 8)
    if fields.get("government_warning_present", False):
        put(fields.get("government_warning_header"), warning_head, 4)
        put(fields.get("government_warning_body"), warning_body, 4)

    # A neutral geometric emblem makes different label designs visually
    # distinguishable without borrowing a real trademark or logo.
    draw.ellipse((x1 - 175, y0 + 45, x1 - 90, y0 + 130), outline=accent, width=5)
    draw.line((x1 - 160, y0 + 87, x1 - 105, y0 + 87), fill=accent, width=4)
    return image


def _curved(image: Image.Image, strength: float = 0.32) -> Image.Image:
    """Simulate a label wrapping around a cylindrical bottle."""
    width, height = image.size
    src, out = image.load(), Image.new("RGB", image.size, (63, 67, 75))
    dst = out.load()
    center = width / 2
    for x in range(width):
        normalized = (x - center) / center
        source_x = center + math.sin(normalized * math.pi / 2) * center
        source_x = max(0, min(width - 1, int(source_x)))
        shade = 1.0 - strength * abs(normalized)
        for y in range(height):
            pixel = src[source_x, y]
            dst[x, y] = tuple(max(0, min(255, int(channel * shade))) for channel in pixel)
    return out


def _perspective(image: Image.Image) -> Image.Image:
    width, height = image.size
    # Slightly narrower top and a rightward lean, like a three-quarter bottle shot.
    quad = (75, 50, width - 35, 25, width - 80, height - 45, 35, height - 15)
    return image.transform((width, height), Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC)


def _occlude(image: Image.Image) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    draw.rectangle((570, 230, 900, 930), fill=(125, 91, 68))
    return out


def _glare(image: Image.Image) -> Image.Image:
    overlay = Image.new("L", image.size, 0)
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((100, 100, 800, 820), fill=255)
    overlay = overlay.filter(ImageFilter.GaussianBlur(10))
    bright = ImageEnhance.Brightness(image).enhance(1.6)
    return Image.composite(Image.new("RGB", image.size, "white"), bright, overlay)


def _coverage(image: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", image.size, (48, 53, 61))
    small = image.resize((400, 535), Image.Resampling.LANCZOS)
    canvas.paste(small, ((image.width - small.width) // 2, (image.height - small.height) // 2))
    return canvas


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "spirits_flat_pass",
        "kind": "label_only",
        "theme": "spirits",
        "image_transform": "flat",
        "fields": BASE_SPIRITS,
        "expected": {"brand_name": "pass", "alcohol_content": "pass", "net_contents": "pass", "government_warning": "pass"},
    },
    {
        "id": "wine_imported_pass",
        "kind": "label_only",
        "theme": "wine",
        "image_transform": "flat",
        "fields": BASE_WINE,
        "expected": {"brand_name": "pass", "country_of_origin": "pass", "sulfite_declaration": "pass", "government_warning": "pass"},
    },
    {
        "id": "beer_without_abv_is_federally_reviewable",
        "kind": "label_only",
        "theme": "beer",
        "image_transform": "flat",
        "fields": BASE_BEER,
        "expected": {"alcohol_content": "pass", "net_contents": "pass"},
    },
    {
        "id": "spirits_missing_warning",
        "kind": "label_only",
        "theme": "spirits",
        "image_transform": "flat",
        "fields": {**BASE_SPIRITS, **{"government_warning_present": False, "government_warning_header": "", "government_warning_body": ""}},
        "expected": {"government_warning": "fail"},
    },
    {
        "id": "spirits_wrong_warning_header",
        "kind": "label_only",
        "theme": "spirits",
        "image_transform": "flat",
        "fields": {**BASE_SPIRITS, "government_warning_header": "Government Warning:"},
        "expected": {"government_warning": "fail"},
    },
    {
        "id": "spirits_unauthorized_fill_size",
        "kind": "label_only",
        "theme": "spirits",
        "image_transform": "flat",
        "fields": {**BASE_SPIRITS, "net_contents": "760 mL"},
        "expected": {"net_contents": "fail"},
    },
    {
        "id": "spirits_application_abv_mismatch",
        "kind": "application_review",
        "theme": "spirits",
        "image_transform": "flat",
        "fields": {**BASE_SPIRITS, "alcohol_content": "40% Alc./Vol. (80 Proof)"},
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": BASE_SPIRITS["brand_name"],
            "class_type": BASE_SPIRITS["class_type"],
            "alcohol_content": BASE_SPIRITS["alcohol_content"],
            "net_contents": BASE_SPIRITS["net_contents"],
        },
        "expected": {"alcohol_content": "fail"},
    },
    {
        "id": "quality_three_quarter_angle",
        "kind": "quality",
        "theme": "spirits",
        "image_transform": "perspective",
        "fields": BASE_SPIRITS,
        "expected": {"quality": "readable"},
    },
    {
        "id": "quality_rotated_label",
        "kind": "quality",
        "theme": "wine",
        "image_transform": "rotate",
        "fields": BASE_WINE,
        "expected": {"quality": "readable"},
    },
    {
        "id": "quality_curved_bottle",
        "kind": "quality",
        "theme": "beer",
        "image_transform": "curved",
        "fields": BASE_BEER,
        "expected": {"quality": "readable"},
    },
    {
        "id": "quality_mild_glare",
        "kind": "quality",
        "theme": "spirits",
        "image_transform": "glare",
        "fields": BASE_SPIRITS,
        "expected": {"quality": "manual_review"},
    },
    {
        "id": "quality_severe_blur",
        "kind": "quality",
        "theme": "spirits",
        "image_transform": "blur",
        "fields": BASE_SPIRITS,
        "expected": {"quality": "retake"},
    },
    {
        "id": "quality_partial_occlusion",
        "kind": "quality",
        "theme": "wine",
        "image_transform": "occlusion",
        "fields": BASE_WINE,
        "expected": {"quality": "manual_review"},
    },
    {
        "id": "quality_low_coverage",
        "kind": "quality",
        "theme": "beer",
        "image_transform": "low_coverage",
        "fields": BASE_BEER,
        "expected": {"quality": "retake"},
    },
)


def build_scenario(scenario: dict[str, Any], output_dir: str | Path) -> Path:
    """Render one scenario and return its image path."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    image = render_label(scenario["fields"], theme=scenario["theme"])
    transform = scenario["image_transform"]
    if transform == "perspective":
        image = _perspective(image)
    elif transform == "rotate":
        image = image.rotate(17, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(63, 67, 75))
    elif transform == "curved":
        image = _curved(image)
    elif transform == "glare":
        image = _glare(image)
    elif transform == "blur":
        image = image.filter(ImageFilter.GaussianBlur(5.0))
    elif transform == "occlusion":
        image = _occlude(image)
    elif transform == "low_coverage":
        image = _coverage(image)
    path = output / f"{scenario['id']}.jpg"
    image.save(path, format="JPEG", quality=90)
    return path


def build_all(output_dir: str | Path) -> dict[str, Path]:
    return {scenario["id"]: build_scenario(scenario, output_dir) for scenario in SCENARIOS}


def _with_confidence(fields: dict[str, Any]) -> ExtractedLabelData:
    return ExtractedLabelData(**fields, extraction_confidence=0.98, per_field_confidence={})


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, str]:
    """Run the real compliance engine against deterministic extracted data."""
    extracted = _with_confidence(scenario["fields"])
    if scenario["kind"] == "application_review":
        results = __import__("app.compliance", fromlist=["run_compliance_checks"]).run_compliance_checks(
            ApplicationData(**scenario["application"]), extracted
        )
    else:
        results = __import__("app.compliance", fromlist=["check_label_requirements"]).check_label_requirements(
            extracted,
            confirmed_beverage_type=extracted.beverage_type_guess,
        )
    return {result.field: result.status for result in results}


def quality_result(path: str | Path) -> dict[str, Any]:
    with Image.open(path) as image:
        return diagnose_image_quality(image.convert("RGB"))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render fictional synthetic QA label images.")
    parser.add_argument(
        "--output-dir",
        default="generated-synthetic-labels",
        help="Directory to receive disposable JPEGs (default: generated-synthetic-labels)",
    )
    args = parser.parse_args()
    paths = build_all(args.output_dir)
    print(f"Wrote {len(paths)} synthetic QA images to {args.output_dir}")
