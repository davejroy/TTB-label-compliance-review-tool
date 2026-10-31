#!/usr/bin/env python3
"""Replay the 59-image regression set against a deployed review API.

Requires TTB_REGRESSION_BASE_URL (origin only, no path). Optional
TTB_REGRESSION_TOKEN is sent as a Bearer token and is never logged.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
MANIFEST_PATH = IMAGES / "manifest.json"


def _base_url() -> str:
    raw = (os.environ.get("TTB_REGRESSION_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        print("Set TTB_REGRESSION_BASE_URL to the review API origin (no path).", file=sys.stderr)
        sys.exit(2)
    if "://" not in raw:
        print("TTB_REGRESSION_BASE_URL must include a scheme (https://…).", file=sys.stderr)
        sys.exit(2)
    return raw


def _headers() -> dict[str, str]:
    token = (os.environ.get("TTB_REGRESSION_TOKEN") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _out_dir() -> Path:
    raw = os.environ.get("TTB_REGRESSION_OUT")
    path = Path(raw) if raw else HERE / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _selected(manifest: list[dict]) -> list[dict]:
    ids = {item.strip() for item in os.environ.get("TTB_REGRESSION_IDS", "").split(",") if item.strip()}
    groups = {
        item.strip()
        for item in os.environ.get("TTB_REGRESSION_GROUPS", "good,degraded,defect,typesize,edge").split(",")
        if item.strip()
    }
    chosen = []
    for row in manifest:
        if ids and row["id"] not in ids:
            continue
        if row.get("group") not in groups:
            continue
        chosen.append(row)
    return chosen


class Pacer:
    def __init__(self, gap: float) -> None:
        self.gap = gap
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self._last + self.gap - time.time()
            if delay > 0:
                time.sleep(delay)
            self._last = time.time()


def _open_image(row: dict) -> tuple[str, bytes, str]:
    path = IMAGES / row["file"]
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return path.name, data, mime


def review_one(session: requests.Session, base: str, row: dict, pacer: Pacer) -> dict:
    name, data, mime = _open_image(row)
    files = [("files", (name, data, mime))]
    form = {"application": json.dumps(row["application"])}
    last = {"status": 0, "body": None, "seconds": 0.0}
    for attempt in range(3):
        pacer.wait()
        started = time.time()
        try:
            response = session.post(
                f"{base}/api/review",
                files=files,
                data=form,
                headers=_headers(),
                timeout=240,
            )
            try:
                body = response.json()
            except ValueError:
                body = response.text[:2000]
            last = {
                "status": response.status_code,
                "body": body,
                "seconds": round(time.time() - started, 2),
                "attempt": attempt,
            }
            if response.status_code == 429:
                time.sleep(35)
                continue
            break
        except requests.RequestException as exc:
            last = {
                "status": 0,
                "body": f"request failed: {exc.__class__.__name__}",
                "seconds": round(time.time() - started, 2),
                "attempt": attempt,
            }
    return {
        "id": row["id"],
        "group": row.get("group"),
        "expected": row.get("expected"),
        "defects": row.get("defects") or [],
        **last,
    }


def main() -> int:
    base = _base_url()
    manifest = json.loads(MANIFEST_PATH.read_text())
    rows = _selected(manifest)
    if not rows:
        print("No manifest rows selected.", file=sys.stderr)
        return 2
    workers = max(1, int(os.environ.get("TTB_REGRESSION_WORKERS", "2")))
    gap = float(os.environ.get("TTB_REGRESSION_GAP", "4.6"))
    pacer = Pacer(gap)
    out = {}
    print(f"Reviewing {len(rows)} labels against the configured base URL ({workers} workers).", flush=True)
    with requests.Session() as session, ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(review_one, session, base, row, pacer) for row in rows]
        for future in as_completed(futures):
            rec = future.result()
            out[rec["id"]] = rec
            print(f"{rec['id']:32s} {rec['status']:3d} {rec['seconds']:6.1f}s", flush=True)
    dest = _out_dir() / "review.json"
    dest.write_text(json.dumps({"results": out}, indent=1))
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
