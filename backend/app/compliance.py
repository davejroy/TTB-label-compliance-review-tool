"""Compliance matching and TTB label requirement checks.

This module is the core "business logic" of the prototype and is kept free
of any I/O (no network calls, no file access) so it can be unit tested
without an Anthropic API key.

Two entry points are exported:

- ``run_compliance_checks`` - compares a label's extracted fields against a
  COLA application's data (Application vs. Label review).
- ``check_label_requirements`` - validates a label's extracted fields
  against the baseline TTB mandatory label requirements (27 CFR Parts 4, 5,
  7, 16), independent of any application data (Label-Only Check).

Every check returns a ``FieldResult`` with a ``status`` of ``"pass"``,
``"warning"`` (needs human review), or ``"fail"``, plus a plain-language
``message`` explaining the result.
"""

import re
import logging
from difflib import SequenceMatcher


# Module-level logger for compliance checks.
# Enables audit-trail logging of every label's compliance outcome.
_log = logging.getLogger(__name__)
from .models import ApplicationData, ExtractedLabelData, FieldResult

# Canonical Government Warning text per 27 CFR 16.21. Must appear verbatim,
# with "GOVERNMENT WARNING:" in capital letters and bold (we can only check
# the text/casing from an image, not boldness).
CANONICAL_WARNING_HEADER = "GOVERNMENT WARNING:"
CANONICAL_WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)

# ABV tolerances allowed by TTB regulations:
# - Distilled spirits: +/-0.3 percentage points (27 CFR 5.65(c)).
# - Wine: +/-1.0 percentage point if the labeled ABV is over 14%, or +/-1.5
#   percentage points if 14% or below (27 CFR 4.36(b)(1)). See
#   ``WINE_ABV_TOLERANCE_THRESHOLD`` and ``_wine_abv_tolerance``.
# - Malt beverages (beer): +/-0.3 percentage points for products at or above
#   0.5% ABV (27 CFR 7.65(c)).
ABV_TOLERANCE = {
    "distilled_spirits": 0.3,
    "beer": 0.3,
}

# Wine ABV percentage at/below which the wider +/-1.5 point tolerance applies
# (27 CFR 4.36(b)(1)).
WINE_ABV_TOLERANCE_THRESHOLD = 14.0
WINE_ABV_TOLERANCE_LOW = 1.5
WINE_ABV_TOLERANCE_HIGH = 1.0


def _wine_abv_tolerance(app_pct: float, label_pct: float) -> float:
    """Return the wine ABV tolerance per 27 CFR 4.36(b)(1).

    Wines containing more than 14% ABV get a +/-1.0 point tolerance; wines at
    or below 14% get the wider +/-1.5 point tolerance. Since either value
    could be the "true" ABV, the higher of the two is used to decide which
    tolerance band applies.
    """
    if max(app_pct, label_pct) > WINE_ABV_TOLERANCE_THRESHOLD:
        return WINE_ABV_TOLERANCE_HIGH
    return WINE_ABV_TOLERANCE_LOW


def _normalize(text: str | None) -> str:
    """Lowercase, strip punctuation, and collapse whitespace.

    Used for case/punctuation-insensitive comparisons (e.g. brand name,
    class/type) so that "STONE'S THROW" and "Stone's Throw" are treated as
    the same value.
    """
    if not text:
        return ""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9% ]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_net_contents(text: str | None) -> str:
    """Normalize net contents, treating equivalent unit abbreviations the same
    (e.g. '12 FL. OZ.' and '12 oz' both normalize to '12 oz')."""
    norm = _normalize(text)
    norm = re.sub(r"\bfl(uid)?\b", "", norm)
    norm = re.sub(r"\bounces?\b", "oz", norm)
    norm = re.sub(r"\bmilliliters?\b", "ml", norm)
    norm = re.sub(r"\bliters?\b", "l", norm)
    norm = re.sub(r"\s+", " ", norm)
    return norm.strip()


def _normalize_whitespace(text: str | None) -> str:
    """Collapse runs of whitespace to a single space, preserving case.

    Used for the Government Warning text, where casing matters (the header
    must be all-caps) but incidental line-wrapping/spacing differences from
    OCR/transcription should not cause a mismatch.
    """
    if not text:
        return ""
    # Rejoin words split across lines with a hyphen (e.g. "CON-\nSUMPTION" -> "CONSUMPTION")
    text = re.sub(r"-\n\s*", "", text.strip())
    return re.sub(r"\s+", " ", text.strip())


def _similarity(a: str, b: str) -> float:
    """Return a 0-1 similarity ratio between two strings (via difflib)."""
    return SequenceMatcher(None, a, b).ratio()


def _extract_percent(text: str | None) -> float | None:
    """Pull the first ``NN.N%`` style percentage out of free text, if any."""
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return float(match.group(1))
    return None


def _check_text_field(
    field: str,
    label_name: str,
    application_value: str,
    label_value: str | None,
    normalizer=_normalize,
) -> FieldResult:
    """Compare an application value to the corresponding label value.

    - Missing on the label -> ``fail``.
    - Equal after normalization -> ``pass``.
    - Not equal but >=85% similar after normalization -> ``warning`` (likely
      a cosmetic difference; flagged for a human to confirm).
    - Otherwise -> ``fail``.

    ``normalizer`` controls how values are normalized before comparison
    (e.g. ``_normalize`` for case/punctuation-insensitive text,
    ``_normalize_net_contents`` for unit-aware net-contents comparison).
    """
    norm_app = normalizer(application_value)
    norm_label = normalizer(label_value)

    if not label_value:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=application_value,
            label_value=label_value,
            message=f"{label_name} not found on label.",
        )

    if norm_app == norm_label:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=application_value,
            label_value=label_value,
            message=f"{label_name} matches application.",
        )

    similarity = _similarity(norm_app, norm_label)
    if similarity >= 0.85:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="warning",
            application_value=application_value,
            label_value=label_value,
            message=(
                f"{label_name} is a likely match but differs in formatting/casing "
                f"from the application. Please confirm visually."
            ),
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="fail",
        application_value=application_value,
        label_value=label_value,
        message=f"{label_name} on label does not match application.",
    )


def _check_alcohol_content(
    application: ApplicationData, extracted: ExtractedLabelData
) -> FieldResult:
    """Compare the application's stated ABV to the label's ABV.

    Percentages are parsed out of both values and compared numerically
    against the applicable TTB tolerance for the application's beverage type
    (wine uses the ABV-dependent tolerance from ``_wine_abv_tolerance`` per
    27 CFR 4.36(b)(1); distilled spirits and beer use ``ABV_TOLERANCE``,
    falling back to text comparison if either value has no parseable
    percentage). A missing ABV on a wine/beer label is a ``warning`` rather
    than a ``fail``, since some wine/beer labels are legally exempt from
    stating ABV (27 CFR 4.36 / 7.65).
    """
    label_value = extracted.alcohol_content
    field, label_name = "alcohol_content", "Alcohol Content"

    if not label_value:
        # Some wines (<=14% ABV) and beers may legally omit ABV from the label.
        if application.beverage_type in ("wine", "beer"):
            return FieldResult(
                field=field,
                label_name=label_name,
                status="warning",
                application_value=application.alcohol_content,
                label_value=label_value,
                message=(
                    "Alcohol content not found on label. This may be acceptable "
                    f"for {application.beverage_type.replace('_', ' ')} "
                    "(verify whether an exemption applies)."
                ),
            )
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=application.alcohol_content,
            label_value=label_value,
            message="Alcohol content not found on label.",
        )

    app_pct = _extract_percent(application.alcohol_content)
    label_pct = _extract_percent(label_value)

    if app_pct is None or label_pct is None:
        return _check_text_field(field, label_name, application.alcohol_content, label_value)

    if application.beverage_type == "wine":
        tolerance = _wine_abv_tolerance(app_pct, label_pct)
    else:
        tolerance = ABV_TOLERANCE.get(application.beverage_type, 0.3)
    diff = abs(app_pct - label_pct)

    if diff == 0:
        status, message = "pass", "Alcohol content matches application."
    elif diff <= tolerance:
        status, message = (
            "warning",
            f"Alcohol content differs by {diff:.2f}%, within the typical "
            f"{tolerance}% TTB tolerance but should be confirmed.",
        )
    else:
        status, message = (
            "fail",
            f"Alcohol content differs by {diff:.2f}%, exceeding the typical "
            f"{tolerance}% TTB tolerance.",
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status=status,
        application_value=application.alcohol_content,
        label_value=label_value,
        message=message,
    )


def _check_government_warning(extracted: ExtractedLabelData) -> FieldResult:
    """Validate the Government Warning statement (27 CFR 16.21).

    This check is intentionally strict and shared by both
    ``run_compliance_checks`` and ``check_label_requirements``:

    - The statement must be present at all -> otherwise ``fail``.
    - The header must read exactly ``GOVERNMENT WARNING:`` in capital
      letters (title case is a ``fail``, not a warning).
    - The body text must match the canonical statutory wording
      case-insensitively. A near match (>=95% similarity) is reported as a
      "minor wording differences" issue but is still an overall ``fail``,
      since any deviation from the required text is non-compliant.
    """
    field, label_name = "government_warning", "Government Warning"

    if not extracted.government_warning_present:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
            label_value=None,
            message="Government Warning statement not found on label. This is required on all alcohol beverage labels.",
        )

    header = (extracted.government_warning_header or "").strip()
    body = _normalize_whitespace(extracted.government_warning_body)
    canonical_body = _normalize_whitespace(CANONICAL_WARNING_BODY)

    issues = []
    if header != CANONICAL_WARNING_HEADER:
        issues.append(
            f"header must read exactly '{CANONICAL_WARNING_HEADER}' in capital letters "
            f"(found '{header}')"
        )

    if body.lower() != canonical_body.lower():
        similarity = _similarity(body.lower(), canonical_body.lower())
        if similarity >= 0.95:
            issues.append("warning text has minor wording differences from the required text")
        else:
            issues.append("warning text does not match the required statement")

    if not issues:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
            label_value=header + " " + body,
            message="Government Warning statement matches the required text exactly.",
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="fail",
        application_value=CANONICAL_WARNING_HEADER + " " + CANONICAL_WARNING_BODY,
        label_value=(header + " " + body).strip(),
        message="Government Warning issue(s): " + "; ".join(issues) + ".",
    )


def run_compliance_checks(
    application: ApplicationData, extracted: ExtractedLabelData
) -> list[FieldResult]:
    """Compare a label's extracted fields against the COLA application data.

    Always checks brand name, class/type, alcohol content, net contents,
    and the Government Warning. Name/address and country of origin are only
    checked if the application provided values for them (they are optional
    on ``ApplicationData``).
    """
    results = [
        _check_text_field(
            "brand_name", "Brand Name", application.brand_name, extracted.brand_name
        ),
        _check_text_field(
            "class_type", "Class/Type", application.class_type, extracted.class_type
        ),
        _check_alcohol_content(application, extracted),
        _check_text_field(
            "net_contents",
            "Net Contents",
            application.net_contents,
            extracted.net_contents,
            normalizer=_normalize_net_contents,
        ),
        _check_government_warning(extracted),
    ]

    if application.name_and_address:
        results.append(
            _check_text_field(
                "name_and_address",
                "Name and Address",
                application.name_and_address,
                extracted.name_and_address,
            )
        )

    if application.country_of_origin:
        results.append(
            _check_text_field(
                "country_of_origin",
                "Country of Origin",
                application.country_of_origin,
                extracted.country_of_origin,
            )
        )

    return results


def _presence_check(
    field: str,
    label_name: str,
    label_value: str | None,
    requirement: str,
    found_message: str | None = None,
) -> FieldResult:
    """Generic "is this required field present on the label?" check.

    Used by ``check_label_requirements`` for fields that the TTB regulations
    require to simply be present (brand name, class/type, name & address),
    as opposed to fields that need value-level validation.
    """
    if label_value and label_value.strip():
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=requirement,
            label_value=label_value,
            message=found_message or f"{label_name} is present on the label.",
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="fail",
        application_value=requirement,
        label_value=label_value,
        message=f"{label_name} was not found on the label. {requirement}",
    )


def _check_label_alcohol_content(
    extracted: ExtractedLabelData, beverage_type: str | None
) -> FieldResult:
    """Check the ABV statement against the per-beverage-type requirement.

    Unlike ``_check_alcohol_content`` (which compares to an application
    value), this only checks whether *some* ABV statement is present and
    whether one is required at all for ``beverage_type``:

    - Distilled spirits: required (27 CFR 5.63(a)(3) / 5.65) -> ``fail`` if missing.
    - Wine: required over 14% ABV (27 CFR 4.36); below that it may be
      omitted -> ``warning`` if missing (can't tell ABV without the value).
    - Beer: not federally required unless alcohol is derived from added
      flavors/ingredients (27 CFR 7.63(a)(3) / 7.65) -> ``pass`` if missing.
    - Unknown beverage type: ``warning`` if missing, since we can't
      determine the requirement.
    """
    field, label_name = "alcohol_content", "Alcohol Content"
    requirement = (
        "Alcohol content (ABV) statement is required on distilled spirits labels "
        "(27 CFR 5.63(a)(3) / 5.65) and on wine labels over 14% ABV (27 CFR 4.36). "
        "It is not federally required on malt beverage labels unless alcohol is "
        "derived from added flavors or other nonbeverage ingredients (27 CFR "
        "7.63(a)(3) / 7.65), though some states require it."
    )
    label_value = extracted.alcohol_content

    if label_value:
        if _extract_percent(label_value) is None:
            return FieldResult(
                field=field,
                label_name=label_name,
                status="warning",
                application_value=requirement,
                label_value=label_value,
                message=(
                    "Alcohol content text was found but does not appear to include "
                    "a percentage. Verify the format (e.g. 'XX% Alc./Vol.')."
                ),
            )
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=requirement,
            label_value=label_value,
            message=f"Alcohol content ({label_value}) is stated on the label.",
        )

    if beverage_type == "distilled_spirits":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=requirement,
            label_value=label_value,
            message="Alcohol content statement is required on distilled spirits labels (27 CFR 5.63(a)(3) / 5.65) and was not found.",
        )

    if beverage_type == "wine":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="warning",
            application_value=requirement,
            label_value=label_value,
            message=(
                "Alcohol content not found. Required for wines over 14% ABV "
                "(27 CFR 4.36); wines at or below 14% ABV may omit it if labeled "
                "'table wine' or 'light wine'. Verify which applies."
            ),
        )

    if beverage_type == "beer":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=requirement,
            label_value=label_value,
            message=(
                "Alcohol content not stated. Federal law does not require an ABV "
                "statement on malt beverage labels unless alcohol is derived from "
                "added flavors or other nonbeverage ingredients (27 CFR "
                "7.63(a)(3) / 7.65), though some state laws require it - verify "
                "state requirements."
            ),
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="warning",
        application_value=requirement,
        label_value=label_value,
        message=(
            "Alcohol content not found and the beverage type could not be "
            "determined. Verify whether an ABV statement is required for this "
            "product."
        ),
    )


def _check_label_net_contents(extracted: ExtractedLabelData) -> FieldResult:
    """Check that the label states a net contents quantity (27 CFR 4.37 / 5.70 / 7.70).

    Only confirms a quantity-looking value is present; does not validate it
    against the authorized standards-of-fill sizes (27 CFR 4.72 / 5.203 /
    7.70) (see "Possible next steps" in docs/PDR.md).
    """
    field, label_name = "net_contents", "Net Contents"
    requirement = (
        "Net contents must be stated in conformance with standards of fill "
        "(27 CFR 4.37 / 5.70 / 7.70)."
    )
    label_value = extracted.net_contents

    if not label_value or not label_value.strip():
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=requirement,
            label_value=label_value,
            message=f"Net contents not found on label. {requirement}",
        )

    if not re.search(r"\d", label_value):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="warning",
            application_value=requirement,
            label_value=label_value,
            message="Net contents text was found but does not appear to include a quantity. Verify the format.",
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="pass",
        application_value=requirement,
        label_value=label_value,
        message=f"Net contents ({label_value}) is stated on the label.",
    )


def _check_label_country_of_origin(extracted: ExtractedLabelData) -> FieldResult:
    """Check for a country-of-origin statement, required only for imports.

    A country-of-origin statement is only mandatory for imported products,
    so a missing statement is judged using ``extracted.origin_guess``
    (Claude's best guess, from cues like a "Product of <country>" or
    "Imported by" statement) rather than always being flagged:

    - Statement present -> ``pass``.
    - Missing and the label appears domestic -> ``pass`` (not required).
    - Missing and the label appears imported -> ``fail`` (required, missing).
    - Missing and origin can't be determined -> ``warning`` so the agent can
      verify whether the product is imported.
    """
    field, label_name = "country_of_origin", "Country of Origin"
    requirement = (
        "A country-of-origin statement (e.g. 'Product of Scotland') is required "
        "for imported products (27 CFR 27.59, 4.35(b), 5.69, 7.69)."
    )
    label_value = extracted.country_of_origin

    if label_value and label_value.strip():
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=requirement,
            label_value=label_value,
            message=f"Country of origin statement found: '{label_value}'.",
        )

    if extracted.origin_guess == "domestic":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=requirement,
            label_value=label_value,
            message=(
                "No country-of-origin statement found, but this label appears to "
                "be a domestic (US) product, for which one is not required."
            ),
        )

    if extracted.origin_guess == "imported":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="fail",
            application_value=requirement,
            label_value=label_value,
            message=(
                "This label appears to be an imported product, but no "
                "country-of-origin statement was found. " + requirement
            ),
        )

    return FieldResult(
        field=field,
        label_name=label_name,
        status="warning",
        application_value=requirement,
        label_value=label_value,
        message=(
            "No country-of-origin statement found, and it could not be "
            "determined whether this product is domestic or imported. This is "
            "only required if the product is imported - verify whether this "
            "applies to this product."
        ),
    )



def _check_sulfite_declaration(extracted) -> FieldResult:
    """Check for a sulfite declaration on wine labels (27 CFR 4.32(e)).

    Wines containing 10 ppm or more of sulfur dioxide must bear the statement
    'Contains Sulfites' (or equivalent). This check applies only to wine;
    for other beverage types it returns a pass. If the label does not disclose
    sulfite level, a warning is issued prompting the agent to verify with lab data.
    """
    field, label_name = "sulfite_declaration", "Sulfite Declaration"
    requirement = (
        "Wines containing 10 ppm or more of sulfur dioxide must bear the "
        "statement 'Contains Sulfites' or equivalent (27 CFR 4.32(e))."
    )
    bev = (extracted.beverage_type_guess or "").lower()
    if "wine" not in bev and bev not in ("", "unknown"):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value="N/A",
            message="Sulfite declaration is only required for wine products.",
        )
    val = extracted.sulfite_declaration
    if val and _normalize(val):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value=val,
            message="Sulfite declaration found on label.",
        )
    return FieldResult(
        field=field,
        label_name=label_name,
        status="warning",
        application_value=None,
        label_value=None,
        message=(
            "No sulfite declaration found. If this wine contains >=10 ppm "
            "sulfur dioxide, 'Contains Sulfites' (or equivalent) is required "
            "(27 CFR 4.32(e)). Verify SO\u2082 level with production data."
        ),
    )


def _check_allergen_statements(extracted) -> FieldResult:
    """Check for mandatory allergen / additive disclosure statements.

    TTB regulations require disclosure of certain additives when present:
    FD&C Yellow No. 5 (tartrazine), aspartame, saccharin, and cochineal
    extract/carmine (27 CFR 4.32(f), 5.63(c), 7.63(c); TTB Ruling 2012-1).
    Because ingredient presence cannot be determined from a label photo alone,
    this check is always a warning prompting the agent to verify formula records.
    """
    field, label_name = "allergen_statements", "Allergen / Additive Declarations"
    requirement = (
        "FD&C Yellow No. 5, aspartame, saccharin, and cochineal extract / "
        "carmine must be declared on labels when present (27 CFR 4.32(f), "
        "5.63(c), 7.63(c); TTB Ruling 2012-1)."
    )
    val = extracted.allergen_statements
    if val and _normalize(val):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value=val,
            message="Allergen/additive declaration(s) found on label.",
        )
    return FieldResult(
        field=field,
        label_name=label_name,
        status="warning",
        application_value=None,
        label_value=None,
        message=(
            "No allergen or additive declarations detected. Verify via "
            "formula/ingredient records: FD&C Yellow No. 5, aspartame, "
            "saccharin, and cochineal extract/carmine require mandatory label "
            "disclosure when present (27 CFR 4.32(f), 5.63(c), 7.63(c))."
        ),
    )


def _check_age_statement(extracted) -> FieldResult:
    """Check age statement requirements for straight whiskies (27 CFR 5.74).

    Straight whiskies aged less than 4 years must state the age on the label.
    Whiskies aged 4+ years need not show an age statement unless they choose
    to do so. This check applies only to straight whiskies.
    """
    field, label_name = "age_statement", "Age Statement (Straight Whisky)"
    requirement = (
        "Straight whiskies aged less than 4 years must state the actual age "
        "on the label (27 CFR 5.74(a)). Optional for those aged 4+ years."
    )
    class_type = _normalize(extracted.class_type or "")
    is_straight_whisky = (
        ("whisky" in class_type or "whiskey" in class_type or "bourbon" in class_type)
        and "straight" in class_type
    )
    if not is_straight_whisky:
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value="N/A",
            message="Age statement check applies only to straight whiskies.",
        )
    val = extracted.age_statement
    if val and _normalize(val):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value=val,
            message="Age statement found on label.",
        )
    return FieldResult(
        field=field,
        label_name=label_name,
        status="warning",
        application_value=None,
        label_value=None,
        message=(
            "No age statement found on this straight whisky label. If the "
            "product was aged less than 4 years, the age must be stated on "
            "the label (27 CFR 5.74(a)). Verify aging records."
        ),
    )


def _check_commodity_statement(extracted) -> FieldResult:
    """Check for commodity / importer statement on imported spirits (27 CFR 5.63).

    Imported distilled spirits must identify the importer and state the
    class/type (27 CFR 5.63(a)(2), 5.66(b)). Only applied to imported products;
    domestic products return pass. Unknown origin triggers a warning.
    """
    field, label_name = "commodity_statement", "Commodity / Importer Statement"
    requirement = (
        "Imported distilled spirits must state the class/type and bear the "
        "importer's name and address (27 CFR 5.63(a)(2), 5.66(b))."
    )
    bev = (extracted.beverage_type_guess or "").lower()
    if bev not in ("distilled_spirits", "", "unknown"):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value="N/A",
            message="Commodity statement check applies only to imported distilled spirits.",
        )
    origin = (extracted.origin_guess or "unknown").lower()
    if origin == "domestic":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value="N/A",
            message="Product appears domestic; commodity importer statement not required.",
        )
    if origin == "unknown":
        return FieldResult(
            field=field,
            label_name=label_name,
            status="warning",
            application_value=None,
            label_value=None,
            message=(
                "Could not determine whether product is imported. If imported, "
                "the label must identify the importer and commodity per "
                "27 CFR 5.63(a)(2) and 5.66(b). Verify import status."
            ),
        )
    val = extracted.commodity_statement
    if val and _normalize(val):
        return FieldResult(
            field=field,
            label_name=label_name,
            status="pass",
            application_value=None,
            label_value=val,
            message="Commodity/importer statement found on label.",
        )
    return FieldResult(
        field=field,
        label_name=label_name,
        status="fail",
        application_value=None,
        label_value=None,
        message=(
            "Imported distilled spirits label is missing a commodity/importer "
            "statement. The label must identify the importer and state the "
            "class/type (27 CFR 5.63(a)(2), 5.66(b))."
        ),
    )


def check_label_requirements(
    extracted: ExtractedLabelData, beverage_type: str | None = None
) -> list[FieldResult]:
    """Validate a label against TTB mandatory label requirements, independent of
    any COLA application data."""
    return [
        _presence_check(
            "brand_name",
            "Brand Name",
            extracted.brand_name,
            "A brand name is required on every label (27 CFR 4.32 / 5.63(a)(1), 5.64 / 7.63(a)(1), 7.64).",
        ),
        _presence_check(
            "class_type",
            "Class/Type Designation",
            extracted.class_type,
            "A class, type, or other required designation must be stated on the label (27 CFR 4.34 / 5.63(a)(2), 5.141 / 7.63(a)(2), 7.141).",
        ),
        _check_label_alcohol_content(extracted, beverage_type),
        _check_label_net_contents(extracted),
        _presence_check(
            "name_and_address",
            "Name and Address",
            extracted.name_and_address,
            "The name and address of the bottler, producer, packer, or importer is required (27 CFR 4.35 / 5.66-5.68 / 7.66-7.68).",
        ),
        _check_government_warning(extracted),
        _check_label_country_of_origin(extracted),
        _check_sulfite_declaration(extracted),
        _check_allergen_statements(extracted),
        _check_age_statement(extracted),
        _check_commodity_statement(extracted),
    ]


def overall_status(results: list[FieldResult]) -> str:
    """Roll up a list of FieldResults into a single overall status.

    ``fail`` if any field failed, else ``warning`` if any field needs
    review, else ``pass``.
    """
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warning" for r in results):
        return "warning"
    return "pass"
