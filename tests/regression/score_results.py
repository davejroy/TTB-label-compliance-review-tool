#!/usr/bin/env python3
"""Grade a regression results JSON against tests/regression/images/manifest.json.

Targets after the accuracy fixes:
  good 6/6 pass
  degraded verdicts >= 26/27
  strict planted defects >= 20/26
  no retake on readable photos
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "images" / "manifest.json"


def _results_path() -> Path:
    raw = os.environ.get("TTB_REGRESSION_RESULTS")
    if raw:
        return Path(raw)
    default = HERE / "results" / "review.json"
    if default.exists():
        return default
    print("No results file. Run run_regression.py first or set TTB_REGRESSION_RESULTS.", file=sys.stderr)
    sys.exit(2)


def _outcome(body) -> str:
    if not isinstance(body, dict):
        return "error"
    error = body.get("error") or body.get("message") or ""
    status = body.get("overall_status") or ""
    if status:
        return status
    lowered = str(error).lower()
    if "retake" in lowered or "too low" in lowered:
        return "retake"
    return "error"


def _field_status(body: dict) -> dict[str, str]:
    return {
        item.get("field"): item.get("status")
        for item in body.get("fields") or []
        if isinstance(item, dict) and item.get("field")
    }


def _defect_field(name: str) -> str:
    if name.startswith("government_warning"):
        return "government_warning"
    return name


def _load_results(payload) -> dict:
    """Return the id→record mapping, rejecting an empty wrapper."""
    if not isinstance(payload, dict):
        raise ValueError("results payload must be an object")
    if "results" in payload:
        results = payload.get("results")
        if not isinstance(results, dict) or not results:
            raise ValueError("empty results mapping")
        return results
    if not payload:
        raise ValueError("empty results mapping")
    return payload


def _http_status(rec) -> int | None:
    if isinstance(rec, dict) and isinstance(rec.get("status"), int):
        return rec["status"]
    return None


def _typesize_state(body: dict) -> str | None:
    if not isinstance(body, dict):
        return None
    for item in body.get("fields") or []:
        if not isinstance(item, dict) or item.get("field") != "government_warning":
            continue
        details = item.get("type_size_details") or {}
        if isinstance(details, dict) and details.get("state"):
            return details["state"]
        fmt = item.get("warning_format") or {}
        for finding in fmt.get("findings") or []:
            if isinstance(finding, dict) and finding.get("code") == "type_size":
                return finding.get("status")
    return None


def main() -> int:
    manifest = {row["id"]: row for row in json.loads(MANIFEST.read_text())}
    payload = json.loads(_results_path().read_text())
    try:
        results = _load_results(payload)
    except ValueError as exc:
        print(f"Refusing to score: {exc}", file=sys.stderr)
        return 2
    good_ok = 0
    good_n = 0
    deg_ok = 0
    deg_n = 0
    strict = 0
    defect_n = 0
    ts_ok = 0
    ts_n = 0
    edge_ok = 0
    edge_n = 0
    readable_retakes = []
    rows = []
    for fid, rec in results.items():
        meta = manifest.get(fid) or {}
        body = rec.get("body") if isinstance(rec, dict) else {}
        outcome = _outcome(body) if isinstance(body, dict) else "error"
        group = meta.get("group") or rec.get("group")
        expected = meta.get("expected")
        http_status = _http_status(rec)
        rows.append((fid, group, expected, outcome))
        if group == "good":
            good_n += 1
            if outcome == "pass":
                good_ok += 1
        elif group == "degraded":
            deg_n += 1
            # Extreme blur / 80 px are allowed retakes; everything else should pass.
            extreme = fid.endswith("gblurX_B1") or "tiny" in fid or fid.endswith("gblurX")
            if outcome == "pass" or (extreme and outcome == "retake"):
                deg_ok += 1
            if outcome == "retake" and not extreme:
                readable_retakes.append(fid)
        elif group == "defect":
            fields = _field_status(body) if isinstance(body, dict) else {}
            for defect in meta.get("defects") or []:
                defect_n += 1
                if fields.get(_defect_field(defect)) == "fail":
                    strict += 1
        elif group == "typesize":
            ts_n += 1
            want = meta.get("ts_expected") or expected
            measured = _typesize_state(body) if isinstance(body, dict) else None
            fields = _field_status(body) if isinstance(body, dict) else {}
            if http_status is not None and http_status >= 500:
                continue
            if want == "fail":
                if measured == "fail" or fields.get("government_warning") == "fail" or outcome == "fail":
                    ts_ok += 1
            elif want == "pass":
                if outcome != "error" and measured != "fail" and fields.get("government_warning") != "fail":
                    ts_ok += 1
        elif group == "edge":
            edge_n += 1
            if http_status is not None and http_status >= 500:
                continue
            if expected == "reject":
                if outcome in ("error", "retake", "fail"):
                    edge_ok += 1
            elif expected and outcome == expected:
                edge_ok += 1

    print(f"good {good_ok}/{good_n}")
    print(f"degraded {deg_ok}/{deg_n}")
    print(f"strict defects {strict}/{defect_n}")
    print(f"typesize {ts_ok}/{ts_n}")
    print(f"edge {edge_ok}/{edge_n}")
    print("outcomes", Counter(item[3] for item in rows))
    if readable_retakes:
        print("readable retakes", readable_retakes)

    targets = [
        (good_n == 0 or good_ok == good_n, f"good labels {good_ok}/{good_n} (want all pass)"),
        (deg_n == 0 or deg_ok >= 26 or deg_ok == deg_n, f"degraded {deg_ok}/{deg_n} (want >=26/27)"),
        (defect_n == 0 or strict >= 20, f"strict defects {strict}/{defect_n} (want >=20/26)"),
        (not readable_retakes, "no retake on readable degraded photos"),
        (ts_n == 0 or ts_ok == ts_n, f"typesize {ts_ok}/{ts_n} (want all ts_expected)"),
        (edge_n == 0 or edge_ok == edge_n, f"edge {edge_ok}/{edge_n} (want all reject)"),
    ]
    failed = [msg for ok, msg in targets if not ok]
    for ok, msg in targets:
        print(("PASS" if ok else "FAIL"), msg)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
