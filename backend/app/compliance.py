import re
from difflib import SequenceMatcher

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

# Approximate ABV tolerances allowed by TTB regulations (simplified for this
# prototype - real rules vary further by class/type, e.g. 27 CFR 4.36, 5.37, 7.71).
ABV_TOLERANCE = {
    "distilled_spirits": 0.15,
    "wine": 1.0,
    "beer": 0.3,
}


def _normalize(text: str | None) -> str:
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
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _extract_percent(text: str | None) -> float | None:
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


def overall_status(results: list[FieldResult]) -> str:
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warning" for r in results):
        return "warning"
    return "pass"
