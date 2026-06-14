# Handoff Document - TTB Label Compliance Review Tool

This document summarizes the current state of the project for whoever picks
up work next: what the tool does, what's been built, what's in progress, and
where to find more detail.

## Project status

This is a working prototype, deployed and functional. Both review modes
(COLA Application Match and Label-Only Check) are implemented end-to-end,
including batch review, image highlighting, confidence badges, and CSV
export.

## What's implemented

- **Single Label review** - upload 1-4 label images plus COLA application
  data (brand name, class/type, ABV, net contents); Claude vision extracts
  the label fields and the backend compares them against the application
  data, returning Pass / Needs Review / Fail with explanations.
- **Batch Review** - queue multiple label/application pairs, review them in
  one submission, see a summary table, and export results as CSV.
- **Label-Only Check** - validate a label against TTB mandatory requirements
  (27 CFR Parts 4, 5, 7, 16) without any application data: brand name,
  class/type, ABV statement (with correct requirement/exemption logic per
  beverage type), net contents, bottler/producer/importer name & address,
  country of origin (imports only), and the Government Warning statement.
- **Image viewer** - zoomable label image viewer with per-field region
  highlighting and High/Medium/Low confidence badges based on Claude's
  transcription confidence.
- **Manual override** - agents can manually edit/override extracted text
  when OCR quality is poor.
- **Branding** - frontend uses TTB.gov-style branding, including the
  official TTB logo in the header and favicon.
- **Camera capture** - "Take Photo" option for capturing label images
  directly from a camera, shown only on touch-capable devices (not on
  touchscreen desktops).

## Recent changes (most recent first)

- Added step-by-step labels to the Single Label review form.
- Stopped flagging missing country-of-origin on domestic labels (only
  flagged when `origin_guess` indicates an imported product).
- Fixed "Take Photo" incorrectly showing on touchscreen desktops.
- Applied TTB.gov-style branding (logo, favicon, color scheme) to the
  frontend.
- Corrected ABV tolerances and CFR citations against current 27 CFR text.
- Added field confidence/highlighting, zoomable image viewer, and CSV
  export for Batch Review.
- Added Label-Only Check mode.

## Architecture quick reference

- **Backend:** Python / FastAPI (`backend/`), single Claude vision API call
  per review for label transcription, then rule-based compliance matching
  (no persistence of images or extracted data).
- **Frontend:** React + TypeScript + Vite + Tailwind CSS (`frontend/`).
- **Tests:** pytest covers the compliance matching logic
  (`cd backend && pytest`), no API key required.
- **Deploy:** see `render.yaml` for the Render.com deployment config.

## Recently implemented (previously "Known gaps")

The items below were listed as known gaps and have now been implemented:

- **Configurable tolerance rules per beverage class.** `BEVERAGE_TOLERANCE` in
  `compliance.py` maps each beverage class to its ABV tolerance and minimum
  extraction-confidence threshold.
- **Confidence-gated extraction.** `extraction_confidence` (0.0-1.0) is populated
  by Claude. Images below the per-class threshold raise `LowConfidenceError`
  (HTTP 422) and request a retake instead of silently passing.
- **Beverage-type confirmation.** `check_label_requirements` now accepts
  `confirmed_beverage_type` override; unknown types return
  `needs_beverage_confirmation=True` so the agent can confirm before evaluation.
- **Standards of fill validated.** Net contents now validated against 27 CFR
  4.72 (wine) / 5.203 (spirits) / 7.70 (beer) authorised size lists.
- **Formula-dependent caveats.** Sulfite, allergen, age-statement, and
  commodity-statement checks now carry `_FORMULA_DEPENDENT` notices.


### Phase 2 improvements (this session)

- **Per-field confidence thresholds** (`FIELD_CONFIDENCE_THRESHOLDS`): each field
  gets its own readability floor. Gov Warning body text allowed 0.45 vs brand name
  0.60 (long text on a curved bottle). Failed fields produce a named retake request.
- **Multi-photo extraction and merging** (`merge_extracted_label_data`;
  `photo_roles` batch param): front + back label photos extracted independently
  then merged by highest-confidence field value. Fixes spurious Gov Warning failures.
- `LabelCheckResult.photo_sources` records which photo roles contributed.
- `ExtractedLabelData.per_field_confidence` dict in model, schema, and prompt.

### Phase 3 enhancements (this session)

- **Small-container alternate Government Warning text** (`SMALL_CONTAINER_THRESHOLD_ML = 100.0` in `compliance.py`): containers <= 100 mL accept either the full-form or the abbreviated body that omits clause numbers (1)/(2). Pass message notes which form was used. Per 27 CFR 16.21(c).
- **Beverage-type confirmation dialog** (`BeverageTypeDialog` in `LabelOnlyCheck.tsx`): when `needs_beverage_confirmation=True` the UI shows a modal with radio buttons for the three beverage types. Agent confirms; label is re-checked with `confirmed_beverage_type`; result updated in place without a full form re-submit.
- **Frontend types.ts updated**: `LabelCheckResult` now includes `needs_beverage_confirmation?`, `beverage_type_confirmed?`, `photo_sources?`.
- **`checkLabelsBatch` in `api.ts` updated**: accepts and passes `confirmedBeverageType` and `photoRoles` to the backend.

## Remaining next steps

- Integrate with COLA to pull application data automatically.
- Add state-level ABV and label requirement checks.
## Where to look first

- `README.md` - setup, running locally, feature overview.
- `docs/PDR.md` - full requirements, architecture, and design
  decisions/trade-offs.
- `docs/SBOM.md` - dependency inventory for backend and frontend.
