"""Deterministic label-image fixture generator (pure Pillow, no numpy).

Generates a permanent, version-controlled corpus of synthetic alcohol-label
photos used by the backend test suite.  Two families are produced:

  compliant/      - labels that carry every mandatory TTB element correctly
  non_compliant/  - labels with a single, documented defect each
  quality/        - the SAME base compliant label re-rendered under different
                    photographic conditions (sharp / blurry / dark / bright /
                    glare / low-coverage / curved) to exercise the image
                    readability + enhancement pipeline.

Run:  python tests/fixtures/generate_fixtures.py
It is idempotent: identical bytes are produced on every run, so images only
need to be committed once and never regenerated.  A manifest.json records the
measured Laplacian-variance (blur) and mean-luminance for every file so the
readability thresholds are derived EMPIRICALLY rather than guessed.
"""
from __future__ import annotations

import json
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "labels")

W, H = 800, 1000  # portrait bottle-label photo

# Canonical government warning (matches app.compliance.CANONICAL_WARNING_BODY).
WARNING_HEADER = "GOVERNMENT WARNING:"
WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)


def _font(size: int):
    """Best-effort truetype; falls back to Pillow's bitmap default."""
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_label(fields, coverage=0.86, bg=(245, 242, 232), fg=(20, 20, 20)):
    """Render a flat, front-on label photo.

    ``coverage`` is the fraction of the frame height the label panel occupies;
    lower values simulate a bottle photographed from too far away (the label
    is small in frame -> less readable text).
    """
    img = Image.new("RGB", (W, H), (60, 62, 68))  # dark "table" background
    draw = ImageDraw.Draw(img)

    panel_h = int(H * coverage)
    panel_w = int(W * min(1.0, coverage + 0.05))
    x0 = (W - panel_w) // 2
    y0 = (H - panel_h) // 2
    draw.rectangle([x0, y0, x0 + panel_w, y0 + panel_h], fill=bg)
    draw.rectangle([x0, y0, x0 + panel_w, y0 + panel_h], outline=(120, 110, 90), width=3)

    pad = 28
    cx = x0 + pad
    cw = panel_w - 2 * pad
    y = y0 + pad

    brand_font = _font(46)
    body_font = _font(26)
    small_font = _font(20)
    warn_head_font = _font(22)
    warn_font = _font(19)

    def line(text, font, gap=10, color=fg):
        nonlocal y
        for ln in _wrap(draw, text, font, cw):
            draw.text((cx, y), ln, font=font, fill=color)
            bbox = draw.textbbox((cx, y), ln, font=font)
            y = bbox[3] + gap

    if fields.get("brand_name"):
        line(fields["brand_name"], brand_font, gap=16)
    if fields.get("class_type"):
        line(fields["class_type"], body_font, gap=14)
    if fields.get("alcohol_content"):
        line(fields["alcohol_content"], body_font, gap=14)
    if fields.get("net_contents"):
        line(fields["net_contents"], small_font, gap=14)
    if fields.get("name_and_address"):
        line(fields["name_and_address"], small_font, gap=14)

    if fields.get("warning", True):
        y += 18
        head = fields.get("warning_header", WARNING_HEADER)
        body = fields.get("warning_body", WARNING_BODY)
        # header + first sentence share a line in real labels; keep header bold-ish
        draw.text((cx, y), head, font=warn_head_font, fill=fg)
        hb = draw.textbbox((cx, y), head, font=warn_head_font)
        y = hb[3] + 4
        line(body, warn_font, gap=6)

    return img


def measure(img):
    """Return (laplacian_variance, mean_luminance) using the SAME method the
    app uses (pure Pillow FIND_EDGES variance)."""
    grey = img.convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    lap = ImageStat.Stat(edges).var[0]
    lum = sum(ImageStat.Stat(img).mean) / 3
    return round(lap, 1), round(lum, 1)


# ----- photographic-condition transforms -------------------------------------

def as_blurry(img, radius=3.0):
    return img.filter(ImageFilter.GaussianBlur(radius))


def as_dark(img, factor=0.28):
    return ImageEnhance.Brightness(img).enhance(factor)


def as_bright(img, factor=1.9):
    return ImageEnhance.Brightness(img).enhance(factor)


def as_glare(img):
    """Blow out a bright elliptical hotspot (simulated flash/reflection)."""
    out = img.copy()
    overlay = Image.new("L", (W, H), 0)
    od = ImageDraw.Draw(overlay)
    od.ellipse([W * 0.30, H * 0.10, W * 0.85, H * 0.55], fill=255)
    overlay = overlay.filter(ImageFilter.GaussianBlur(60))
    white = Image.new("RGB", (W, H), (255, 255, 255))
    return Image.composite(white, out, overlay)


def as_curved(img, strength=90):
    """Approximate bottle curvature: horizontally compress edges via a
    per-column sine warp so text distorts toward the sides."""
    import math
    src = img.load()
    out = Image.new("RGB", (W, H), (60, 62, 68))
    dst = out.load()
    cx = W / 2
    for x in range(W):
        # map destination column x back to a source column that is stretched
        # near the centre and compressed at the edges
        norm = (x - cx) / cx  # -1..1
        sx = cx + math.sin(norm * math.pi / 2) * cx
        sx = min(W - 1, max(0, int(sx)))
        for y in range(H):
            dst[x, y] = src[sx, y]
    # slight shading toward edges
    return out


# ----- the canonical compliant field-set ------------------------------------

COMPLIANT_FIELDS = {
    "brand_name": "OLD TOM DISTILLERY",
    "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)",
    "net_contents": "750 mL",
    "name_and_address": "Bottled by Old Tom Distillery, Bardstown, KY",
    "warning": True,
}


def _save(img, relpath, manifest, note):
    full = os.path.join(ROOT, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    img.save(full, format="JPEG", quality=90)
    lap, lum = measure(img)
    manifest[relpath] = {
        "laplacian_variance": lap,
        "mean_luminance": lum,
        "expectation": note,
    }
    print(f"{relpath:<44} blur={lap:>8}  lum={lum:>6}  {note}")


def build():
    manifest = {}
    print("=" * 96)
    print("COMPLIANT")
    print("-" * 96)
    base = draw_label(COMPLIANT_FIELDS)
    _save(base, "compliant/all_elements_present.jpg", manifest,
          "PASS - every mandatory element present & correct")

    small = draw_label({**COMPLIANT_FIELDS,
                        "net_contents": "50 mL",
                        "warning_body": WARNING_BODY},
                       )
    _save(small, "compliant/small_container_50ml.jpg", manifest,
          "PASS - 50mL small container (full 27 CFR 16.21 warning; 16.22 1 mm type size)")

    print("\n" + "=" * 96)
    print("NON-COMPLIANT (single documented defect each)")
    print("-" * 96)
    _save(draw_label({**COMPLIANT_FIELDS, "warning": False}, ),
          "non_compliant/missing_government_warning.jpg", manifest,
          "FAIL - government warning entirely absent")
    _save(draw_label({**COMPLIANT_FIELDS, "alcohol_content": None}, ),
          "non_compliant/missing_alcohol_content.jpg", manifest,
          "FAIL - alcohol content / ABV missing")
    _save(draw_label({**COMPLIANT_FIELDS, "net_contents": None}, ),
          "non_compliant/missing_net_contents.jpg", manifest,
          "FAIL - net contents missing")
    _save(draw_label({**COMPLIANT_FIELDS, "brand_name": None}, ),
          "non_compliant/missing_brand_name.jpg", manifest,
          "FAIL - brand name missing")
    _save(draw_label({**COMPLIANT_FIELDS,
                      "warning_header": "WARNING:"}, ),
          "non_compliant/wrong_warning_header.jpg", manifest,
          "FAIL - warning header not the exact 'GOVERNMENT WARNING:' text")
    _save(draw_label({**COMPLIANT_FIELDS,
                      "warning_body": "Drinking may be bad for you."}, ),
          "non_compliant/altered_warning_text.jpg", manifest,
          "FAIL - warning body text materially altered")

    print("\n" + "=" * 96)
    print("QUALITY VARIANTS (same compliant label, different capture conditions)")
    print("-" * 96)
    _save(base, "quality/sharp_welllit.jpg", manifest,
          "READABLE - reference sharp, well-lit capture")
    _save(as_blurry(base, 2.0), "quality/blur_mild.jpg", manifest,
          "ENHANCE - mildly out of focus, should recover after sharpening")
    _save(as_blurry(base, 5.0), "quality/blur_severe.jpg", manifest,
          "RETAKE(focus) - severely out of focus, below readability floor")
    _save(as_dark(base), "quality/dark.jpg", manifest,
          "ENHANCE/RETAKE(lighting) - underexposed")
    _save(as_bright(base), "quality/bright.jpg", manifest,
          "ENHANCE/RETAKE(lighting) - overexposed / washed out")
    _save(as_glare(base), "quality/glare.jpg", manifest,
          "RETAKE(glare) - specular flash hotspot obscures text")
    _save(draw_label(COMPLIANT_FIELDS, coverage=0.35),
          "quality/low_coverage.jpg", manifest,
          "RETAKE(coverage) - label too small in frame / too far away")
    _save(as_curved(base), "quality/curved.jpg", manifest,
          "ENHANCE - simulated bottle curvature, still human-readable")

    with open(os.path.join(ROOT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print("\nWrote", os.path.join(ROOT, "manifest.json"),
          "with", len(manifest), "entries")
    return manifest


if __name__ == "__main__":
    build()
