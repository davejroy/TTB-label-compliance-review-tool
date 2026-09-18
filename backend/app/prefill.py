"""COLA Application pre-fill parser and preset loader for Match mode.

Supports parsing CSV and JSON template imports into ApplicationData objects
with clear HTTP 422 Unprocessable Entity validation errors on bad input
(never 401 Unauthorized or 500 Internal Server Error).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

from pydantic import ValidationError

from .models import ApplicationData, BeverageType

_log = logging.getLogger(__name__)

# Canonical header mappings (case-insensitive, normalized punctuation/underscores)
_HEADER_ALIASES: dict[str, str] = {
    # beverage_type
    "beveragetype": "beverage_type",
    "beverage_type": "beverage_type",
    "beverage": "beverage_type",
    "type": "beverage_type",
    "class_type_beverage": "beverage_type",
    "product_type": "beverage_type",
    # brand_name
    "brandname": "brand_name",
    "brand_name": "brand_name",
    "brand": "brand_name",
    # class_type
    "classtype": "class_type",
    "class_type": "class_type",
    "class": "class_type",
    "designation": "class_type",
    "class/type": "class_type",
    "class_or_type": "class_type",
    # alcohol_content
    "alcoholcontent": "alcohol_content",
    "alcohol_content": "alcohol_content",
    "alcohol": "alcohol_content",
    "abv": "alcohol_content",
    "alc_by_vol": "alcohol_content",
    "alc_vol": "alcohol_content",
    "proof": "alcohol_content",
    # net_contents
    "netcontents": "net_contents",
    "net_contents": "net_contents",
    "net": "net_contents",
    "volume": "net_contents",
    "contents": "net_contents",
    "size": "net_contents",
    # name_and_address
    "nameandaddress": "name_and_address",
    "name_and_address": "name_and_address",
    "address": "name_and_address",
    "producer": "name_and_address",
    "bottler": "name_and_address",
    "distiller": "name_and_address",
    "importer": "name_and_address",
    "producer_name_and_address": "name_and_address",
    # country_of_origin
    "countryoforigin": "country_of_origin",
    "country_of_origin": "country_of_origin",
    "origin": "country_of_origin",
    "country": "country_of_origin",
}

_VALID_BEVERAGE_TYPES: set[BeverageType] = {"distilled_spirits", "wine", "beer"}

_BEVERAGE_TYPE_NORMALIZATIONS: dict[str, BeverageType] = {
    "spirits": "distilled_spirits",
    "spirit": "distilled_spirits",
    "distilled": "distilled_spirits",
    "distilled_spirits": "distilled_spirits",
    "distilled spirits": "distilled_spirits",
    "liquor": "distilled_spirits",
    "wine": "wine",
    "cider": "wine",
    "mead": "wine",
    "beer": "beer",
    "malt": "beer",
    "malt beverage": "beer",
    "malt_beverage": "beer",
    "ale": "beer",
    "lager": "beer",
}


def normalize_header(header: str) -> str:
    """Normalize a header string to match canonical ApplicationData field names."""
    cleaned = header.strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned in _HEADER_ALIASES:
        return _HEADER_ALIASES[cleaned]
    # Strip non-alphanumeric chars for alias lookup
    alpha = "".join(c for c in cleaned if c.isalnum() or c == "_")
    return _HEADER_ALIASES.get(alpha, cleaned)


def normalize_beverage_type(val: Any) -> BeverageType:
    """Normalize raw beverage type input string to valid BeverageType enum literal."""
    if not isinstance(val, str) or not val.strip():
        raise ValueError(
            "Beverage type is required and must be one of: 'distilled_spirits', 'wine', 'beer'."
        )
    raw = val.strip().lower().replace("-", "_")
    if raw in _VALID_BEVERAGE_TYPES:
        return raw  # type: ignore[return-value]
    if raw in _BEVERAGE_TYPE_NORMALIZATIONS:
        return _BEVERAGE_TYPE_NORMALIZATIONS[raw]
    for key, normalized in _BEVERAGE_TYPE_NORMALIZATIONS.items():
        if key in raw:
            return normalized
    raise ValueError(
        f"Invalid beverage type '{val}'. Expected 'distilled_spirits', 'wine', or 'beer'."
    )


def _dict_to_application_data(d: dict[str, Any], row_idx: int | None = None) -> ApplicationData:
    """Validate and convert a raw dictionary into an ApplicationData instance.

    Raises ValueError with user-friendly actionable detail on failure.
    """
    prefix = f"Row {row_idx}: " if row_idx is not None else ""
    # Normalize dictionary keys
    normalized_dict: dict[str, Any] = {}
    for k, v in d.items():
        norm_key = normalize_header(str(k))
        if norm_key:
            normalized_dict[norm_key] = v

    # Required field presence check
    required_fields = ["brand_name", "class_type", "alcohol_content", "net_contents"]
    missing: list[str] = []

    # Beverage type resolution
    raw_bev = normalized_dict.get("beverage_type")
    if not raw_bev:
        missing.append("beverage_type")
    else:
        try:
            normalized_dict["beverage_type"] = normalize_beverage_type(raw_bev)
        except ValueError as exc:
            raise ValueError(f"{prefix}{exc}") from exc

    for req in required_fields:
        val = normalized_dict.get(req)
        if val is None or not str(val).strip():
            missing.append(req)

    if missing:
        missing_str = ", ".join(missing)
        raise ValueError(
            f"{prefix}Missing required application field(s): {missing_str}. "
            "Please ensure beverage_type, brand_name, class_type, alcohol_content, and net_contents are populated."
        )

    # Convert values to strings where appropriate
    for key in ("brand_name", "class_type", "alcohol_content", "net_contents", "name_and_address", "country_of_origin"):
        if key in normalized_dict and normalized_dict[key] is not None:
            normalized_dict[key] = str(normalized_dict[key]).strip() or None
        else:
            normalized_dict[key] = None

    try:
        return ApplicationData(
            beverage_type=normalized_dict["beverage_type"],
            brand_name=normalized_dict["brand_name"],
            class_type=normalized_dict["class_type"],
            alcohol_content=normalized_dict["alcohol_content"],
            net_contents=normalized_dict["net_contents"],
            name_and_address=normalized_dict.get("name_and_address"),
            country_of_origin=normalized_dict.get("country_of_origin"),
        )
    except ValidationError as exc:
        raise ValueError(f"{prefix}Invalid application data structure: {exc}") from exc


def parse_application_json(content: str | bytes) -> list[ApplicationData]:
    """Parse JSON text/bytes into a list of ApplicationData objects.

    Supports a single ApplicationData JSON object or a JSON array of objects.
    Raises ValueError on decode or validation failure.
    """
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"JSON file must be valid UTF-8 text: {exc}") from exc

    text = content.strip()
    if not text:
        raise ValueError("JSON file is empty.")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON syntax (line {exc.lineno}, col {exc.colno}): {exc.msg}") from exc

    if isinstance(data, dict):
        return [_dict_to_application_data(data)]
    if isinstance(data, list):
        if not data:
            raise ValueError("JSON array is empty. Please provide at least one application entry.")
        results: list[ApplicationData] = []
        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Array item {idx} is not a valid JSON object.")
            results.append(_dict_to_application_data(item, row_idx=idx))
        return results

    raise ValueError("JSON root must be an object or an array of objects.")


def parse_application_csv(content: str | bytes) -> list[ApplicationData]:
    """Parse CSV text/bytes into a list of ApplicationData objects.

    Uses DictReader to handle RFC 4180 quoting, CRLF/LF line endings, and custom headers.
    Raises ValueError on syntax or validation failure.
    """
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"CSV file must be valid UTF-8 text: {exc}") from exc

    text = content.strip()
    if not text:
        raise ValueError("CSV file is empty.")

    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unable to parse CSV structure: {exc}") from exc

    if not reader.fieldnames:
        raise ValueError("CSV file is missing a header row.")

    rows = list(reader)
    if not rows:
        raise ValueError("CSV file contains headers but has no data rows.")

    results: list[ApplicationData] = []
    for idx, row in enumerate(rows, start=1):
        # Ignore completely empty rows
        if not any(v and v.strip() for v in row.values() if v is not None):
            continue
        results.append(_dict_to_application_data(row, row_idx=idx))

    if not results:
        raise ValueError("CSV file contains no non-empty application data rows.")

    return results


def parse_application_template(content: str | bytes, filename: str = "") -> list[ApplicationData]:
    """Universal template parser: detects CSV or JSON from filename/content and parses applications.

    Raises ValueError with actionable message on parse or validation failure.
    """
    fname = filename.lower().strip()
    raw_str = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
    raw_str = raw_str.strip()

    if fname.endswith(".json") or (not fname.endswith(".csv") and (raw_str.startswith("{") or raw_str.startswith("["))):
        return parse_application_json(content)
    return parse_application_csv(content)


# Built-in demo presets for evaluators
BUILTIN_COLA_PRESETS: list[dict[str, Any]] = [
    {
        "id": "preset_bourbon",
        "name": "Bourbon Whiskey (Kentucky Straight)",
        "description": "Old Tom Distillery 45% ABV (90 Proof) 750 mL Spirits application",
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Old Tom Distillery",
            "class_type": "Kentucky Straight Bourbon Whiskey",
            "alcohol_content": "45% Alc./Vol. (90 Proof)",
            "net_contents": "750 mL",
            "name_and_address": "Old Tom Distillery, Bardstown, KY",
            "country_of_origin": None,
        },
    },
    {
        "id": "preset_cabernet",
        "name": "Cabernet Sauvignon (Napa Valley)",
        "description": "Silver Oak Cellars 14.2% ABV 750 mL Domestic Wine application",
        "application": {
            "beverage_type": "wine",
            "brand_name": "Silver Oak Cellars",
            "class_type": "Cabernet Sauvignon",
            "alcohol_content": "14.2% Alc./Vol.",
            "net_contents": "750 mL",
            "name_and_address": "Silver Oak Cellars, Oakville, CA",
            "country_of_origin": None,
        },
    },
    {
        "id": "preset_ipa",
        "name": "India Pale Ale (Craft Malt Beverage)",
        "description": "Hop Valley Brewing Co. 6.8% ABV 12 FL OZ Beer application",
        "application": {
            "beverage_type": "beer",
            "brand_name": "Hop Valley Brewing Co.",
            "class_type": "India Pale Ale",
            "alcohol_content": "6.8% Alc./Vol.",
            "net_contents": "12 FL OZ",
            "name_and_address": "Hop Valley Brewing Co., Eugene, OR",
            "country_of_origin": None,
        },
    },
    {
        "id": "preset_scotch",
        "name": "Single Malt Scotch Whisky (Import)",
        "description": "Highland Glen 43% ABV 700 mL Imported Spirits application",
        "application": {
            "beverage_type": "distilled_spirits",
            "brand_name": "Highland Glen",
            "class_type": "Single Malt Scotch Whisky",
            "alcohol_content": "43% Alc./Vol.",
            "net_contents": "700 mL",
            "name_and_address": "Imported by Glen Import Co., New York, NY",
            "country_of_origin": "Product of Scotland",
        },
    },
]
