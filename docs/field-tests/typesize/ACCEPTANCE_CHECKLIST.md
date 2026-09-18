# Calibrated 27 CFR 16.22 Type-Size Acceptance Test Checklist

- **Purpose:** Provide a lightweight, executable acceptance-test checklist for field testers and QA evaluators to validate calibrated physical millimeter type-size verification.
- **Reference Specification:** [docs/TYPE_SIZE_16_22_DESIGN.md](../../TYPE_SIZE_16_22_DESIGN.md)
- **Status:** Test Specification / Acceptance Protocol (Follow-up Implementation Ready)

---

## 1. Test Matrix Overview

| Test ID | Category | Method Under Test | Physical Test Setup | Target Threshold | Expected Status Outcome |
|---|---|---|---|---|---|
| **TS-01** | Option A (Marker) | ISO/IEC 7810 ID-1 Card | Standard 750 mL bottle; card placed co-planar with 2.2 mm warning text | $\ge 2.0\text{ mm}$ | `pass` (Calibrated: 2.2 mm $\ge$ 2.0 mm) |
| **TS-02** | Option A (Marker) | ISO/IEC 7810 ID-1 Card | Deficient type size (1.4 mm font on 750 mL bottle) with card in frame | $\ge 2.0\text{ mm}$ | `fail` (Type Size Deficient: 1.4 mm < 2.0 mm) |
| **TS-03** | Option A (Marker) | Printed ArUco Target | Small container (187 mL split); target in frame with 1.2 mm text | $\ge 1.0\text{ mm}$ | `pass` (Calibrated: 1.2 mm $\ge$ 1.0 mm) |
| **TS-04** | Option A (Marker) | Physical Ruler Strip | Large container (5 L box wine); ruler in frame with 3.5 mm text | $\ge 3.0\text{ mm}$ | `pass` (Calibrated: 3.5 mm $\ge$ 3.0 mm) |
| **TS-05** | Option A (Failure Mode) | Missing Marker | User selected "Scale Marker Mode", but marker was omitted from photo | $\ge 2.0\text{ mm}$ | `cannot_measure` (Prompt: Place marker in frame) |
| **TS-06** | Option A (Failure Mode) | Excessive Tilt / Skew | Marker placed at severe angle ($>30^\circ$ planar pitch relative to camera) | $\ge 2.0\text{ mm}$ | `cannot_measure` / Retake prompt: "Hold camera parallel" |
| **TS-07** | Option A (Failure Mode) | Out-of-Focus / Blur | Marker in frame but text is blurred (low gradient edge sharpness) | $\ge 2.0\text{ mm}$ | `cannot_measure` / Retake prompt: "Focus camera on text" |
| **TS-08** | Option B (Geometry) | Standard 750 mL Claret | Uncalibrated photo with full 750 mL Bordeaux bottle silhouette | $\ge 2.0\text{ mm}$ | `pass` (Estimated Geometry Scale: $2.4\text{ mm} \pm 0.2\text{ mm}$) |
| **TS-09** | Option B (Geometry) | Marginal Bottle Case | 750 mL bottle silhouette with borderline text ($2.05\text{ mm} \pm 0.25\text{ mm}$) | $\ge 2.0\text{ mm}$ | `warning` (Advisory: Marginal bound, verify with gauge) |
| **TS-10** | Legacy Baseline | Naked Uncalibrated Photo | Close-up of warning label with no marker and cropped container silhouette | $\ge 2.0\text{ mm}$ | `pass` (Wording Pass + Honest 16.22 Physical Advisory) |
| **TS-11** | Statutory Hard-Fail | W02 Missing Warning | Marker in frame, but Government Warning is absent from label | $\ge 2.0\text{ mm}$ | `fail` (W02 Statutory Hard-Fail: Missing Warning) |
| **TS-12** | Statutory Hard-Fail | W03 Title-Case Header | Marker in frame, but header reads `Government Warning:` (Title Case) | $\ge 2.0\text{ mm}$ | `fail` (W03 Statutory Hard-Fail: Non-capital Header) |

---

## 2. Field Acceptance Verification Steps

### Protocol for Option A (Scale Marker)
- [ ] **Step 1:** Ensure test container is placed on a stable, well-lit surface without glare.
- [ ] **Step 2:** Position the physical scale reference (standard ID card, ruler, or printable calibration card) immediately adjacent to and flush with the `GOVERNMENT WARNING:` text panel.
- [ ] **Step 3:** Frame the photo so both the scale reference and the full Government Warning statement are in sharp focus and parallel to the camera sensor.
- [ ] **Step 4:** Submit photo to `/api/review` or `/api/label-check/batch`.
- [ ] **Step 5:** Verify that the response includes `type_size_details` containing:
  - `method: "scale_marker_card_id1"` (or respective marker enum)
  - `pixels_per_mm` (e.g., $12.5 - 18.0\text{ px/mm}$)
  - `measured_capital_height_mm`
  - `required_min_height_mm` ($1.0$, $2.0$, or $3.0\text{ mm}$)
  - `state` (`pass`, `fail`, `warning`, or `cannot_measure`)
- [ ] **Step 6:** Verify that the UI image viewer displays the calibrated digital caliper overlay highlighting measured uppercase characters (`G`, `W`, etc.).

### Protocol for Option B (Container Geometry)
- [ ] **Step 1:** Upload full bottle photo showing complete base and crown/cork apex.
- [ ] **Step 2:** Supply net contents ($750\text{ mL}$) and container class.
- [ ] **Step 3:** Verify system derives silhouette ratio and computes bounded physical height with explicit error margin ($\pm 8\text{--}12\%$).
- [ ] **Step 4:** Verify that borderline cases ($h_{\text{calc}} \approx h_{\min}$) refuse hard pass and fall back to advisory state.

### Privacy & Security Guard Protocol
- [ ] **Step 1:** Upload photo containing a real driver's license or credit card with visible cardholder text/numbers.
- [ ] **Step 2:** Verify that card face textures and PII are redacted/blurred client-side before submission, retaining only high-contrast outer edge contours.
- [ ] **Step 3:** Confirm zero PII is written to backend request timing logs or error traces.

---

## 3. Mandatory Pre-Promotion Acceptance Criteria (Quality / Speed / Accuracy)

Before any implementation PR for calibrated 27 CFR 16.22 measurement is promoted to `-dev` or production:

1. **Imperfect-Photo Matrix Re-Execution:**
   - [ ] Re-run all 10 physical distortion conditions in `docs/field-tests/imperfect/` (`FLAT_CONTROL`, `TILT_15`, `TILT_30`, `CURVE_MILD`, `CURVE_STRONG`, `GLARE`, `SOFT_FOCUS`, `ANGLE_PERSPECTIVE`, `PARTIAL_CROP`, `MULTI_TILT_FB`).
   - [ ] Confirm **10/10 Clean OK outcome (100%)** with **0 DANGER violations** or regressions.
   - [ ] Confirm that image degradation triggers `cannot_measure` or `needs_review` advisory rather than a false pass.
2. **Happy-Path Timing Baseline:**
   - [ ] Measure 10 consecutive executions of uncalibrated label reviews against existing backend baselines.
   - [ ] Verify that uncalibrated review latency exhibits **$\le 5\%$ variance** from current `-dev`/prod (zero added Claude round-trips or token overhead).
3. **Opt-In Measurement Budget:**
   - [ ] Verify that Option A / Option B calibration processing adds **$< 150\text{ ms}$** server wall-clock time.
4. **Safety Guarantee:**
   - [ ] Under no circumstances does a sub-statutory type size (e.g., 1.4 mm on a 750 mL bottle) return a `pass` verdict when a valid scale marker is present.
5. **Honesty Guarantee:**
   - [ ] Under no circumstances does an uncalibrated naked photo claim a millimeter measurement or `pass (calibrated)` status.
6. **Availability Guarantee:**
   - [ ] Zero authentication gates; all test flows operate fail-open for evaluators with `USE_FAST_EXTRACTION=false` by default.
