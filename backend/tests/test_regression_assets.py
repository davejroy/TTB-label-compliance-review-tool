"""The committed 59-image regression set stays host-free and complete."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGRESSION = ROOT / "tests" / "regression"
MANIFEST = REGRESSION / "images" / "manifest.json"

# Public-safe substring checks. Internal host/infra names are not spelled here.
_GENERIC_FORBIDDEN = (
    "onrender.com",
    "localhost",
    "sk-ant-",
)

# SHA-256 (utf-8, lowercase) of additional tokens that must not appear in
# public fixtures. CI may also set REGRESSION_FORBIDDEN_WORDS to a comma-
# separated plaintext list (kept out of this public repository).
_FORBIDDEN_DIGESTS = frozenset(
    {
        "8be0b5445fca6eb16d14c6f4eaff1cf1d1a3362ef660bfb2b66e74bf309b7e61",
        "d20dbd6a870c25193970ab56921ac6c12c60f19b53ca5cf7d8d30e89adf83cda",
        "2a973d2b28e300e93308a9f3e3dbd12db285ca501f8dc6a9013c496f64685232",
        "2ce1d14038a732cc4f5b3843ce4a30fd38976f5acdea3655d64ac0db71f38097",
    }
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extra_forbidden_from_env() -> tuple[str, ...]:
    raw = os.environ.get("REGRESSION_FORBIDDEN_WORDS", "")
    return tuple(token.strip().lower() for token in raw.split(",") if token.strip())


def _scan_paths() -> tuple[Path, ...]:
    paths = [
        REGRESSION / "run_regression.py",
        REGRESSION / "score_results.py",
        MANIFEST,
    ]
    readme = REGRESSION / "README.md"
    if readme.is_file():
        paths.append(readme)
    return tuple(paths)


def test_regression_manifest_has_59_rows_and_files():
    rows = json.loads(MANIFEST.read_text())
    assert len(rows) == 59
    missing = [row["id"] for row in rows if not (REGRESSION / "images" / row["file"]).is_file()]
    assert missing == []


def test_regression_scripts_have_no_hardcoded_hosts():
    extra = _extra_forbidden_from_env()
    for path in _scan_paths():
        text = path.read_text().lower()
        for token in _GENERIC_FORBIDDEN + extra:
            assert token not in text, f"{path.name} contains a forbidden host token"
        for match in _WORD_RE.findall(text):
            assert _sha256_hex(match) not in _FORBIDDEN_DIGESTS, (
                f"{path.name} contains a hashed forbidden token"
            )
        if path.name == "run_regression.py":
            assert "ttb_regression_base_url" in text
