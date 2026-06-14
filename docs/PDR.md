# Product Design Record (PDR) - TTB Label Compliance Review Tool

## 1. Overview

The TTB Label Compliance Review Tool is a standalone prototype that helps
TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance agents quickly
check whether the text printed on an alcohol beverage label matches the
corresponding COLA (Certificate of Label Approval) application data, and
whether the label carries a correctly worded Government Warning statement.
A second mode lets an agent validate a label image on its own against the
baseline mandatory-statement requirements in 27 CFR Parts 4, 5, 7, and 16,
with no application data required.

This document captures the requirements, architecture, and the design
decisions/trade-offs that shaped the prototype, consolidating feedback
gathered from the TTB stakeholders referenced throughout the codebase and
commit history (Dave, Jenny, Sarah, Marcus).

## 2. Goals

- Let an agent compare a photographed label against COLA application data
  and get a per-field Pass / Needs Review / Fail result with a
  plain-language explanation, in well under the "5 seconds" target that a
  prior scanning-vendor pilot failed to meet.
- Support reviewing a single label or a batch of labels (the "200-300
  applications during peak season" use case).
- Support a label-only mode for spot-checking a label's basic legal
  requirements without needing a COLA application on file.
- Be usable by agents with a wide range of technical comfort levels.
- Avoid storing any uploaded images or extracted data.

## 3. Non-goals

- Integration with the live COLA system (out of scope for this
  proof-of-concept; application data is entered manually).
- Legally authoritative determinations - results are advisory and intended
  to support, not replace, human review.
- Full coverage of every TTB mandatory statement (e.g. sulfite/allergen
  declarations, age statements) - see "Possible next steps" in the README.

## 4. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Accept 1-4 images per label and combine them into a single set of extracted fields. |
| FR-2 | Extract brand name, class/type, alcohol content, net contents, bottler/producer/importer name & address, country of origin, and Government Warning text from the label image(s). |
| FR-3 | Compare extracted fields against application data (brand name, class/type, alcohol content, net contents) and return Pass / Needs Review / Fail per field with an explanation. |
| FR-4 | Validate the Government Warning statement's header casing and body wording against the statutory text (27 CFR 16.21). |
| FR-5 | Support a Batch Review of multiple label/application pairs in one submission, with a summary table and CSV export. |
| FR-6 | Support a Label-Only Check that validates extracted fields against TTB mandatory label requirements (27 CFR Parts 4, 5, 7, 16) without application data, including the correct ABV-statement requirement/exemption per beverage type. |
| FR-7 | Provide a zoomable label image viewer that highlights the approximate region used for each extracted field, with a transcription confidence badge (High/Medium/Low). |
| FR-8 | Allow a manual text override when OCR/vision extraction quality is insufficient. |

## 5. Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-1 | A single label review should complete in one Claude API call (no multi-step chains) to keep latency low. |
| NFR-2 | No persistence - uploaded images and extracted/compliance data are not stored server-side. |
| NFR-3 | UI must be usable by non-technical agents: large text, big buttons, drag-and-drop upload, color-coded status badges. |
| NFR-4 | Backend test suite must run without requiring an Anthropic API key (matching/requirements logic is pure and independently testable). |

## 6. Architecture

```
Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ        multipart/form-data         Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
Ã¢ÂÂ   React + Vite   Ã¢ÂÂ Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ¶Ã¢ÂÂ  FastAPI (uvicorn)    Ã¢ÂÂ
Ã¢ÂÂ   frontend       Ã¢ÂÂ /api/review, /api/review/batch,     Ã¢ÂÂ  backend/app/main.py Ã¢ÂÂ
Ã¢ÂÂ                  Ã¢ÂÂ /api/label-check/batch               Ã¢ÂÂ                       Ã¢ÂÂ
Ã¢ÂÂ                  Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ                       Ã¢ÂÂ
Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ        JSON results                 Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ¬Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
                                                                     Ã¢ÂÂ
                                                                     Ã¢ÂÂ image bytes +
                                                                     Ã¢ÂÂ tool-call schema
                                                                     Ã¢ÂÂ¼
                                                          Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
                                                          Ã¢ÂÂ  Anthropic API        Ã¢ÂÂ
                                                          Ã¢ÂÂ  Claude (vision)      Ã¢ÂÂ
                                                          Ã¢ÂÂ  claude_client.py     Ã¢ÂÂ
                                                          Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ¬Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
                                                                     Ã¢ÂÂ
                                                                     Ã¢ÂÂ structured fields
                                                                     Ã¢ÂÂ (ExtractedLabelData)
                                                                     Ã¢ÂÂ¼
                                                          Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
                                                          Ã¢ÂÂ  compliance.py        Ã¢ÂÂ
                                                          Ã¢ÂÂ  matching & TTB       Ã¢ÂÂ
                                                          Ã¢ÂÂ  requirement checks   Ã¢ÂÂ
                                                          Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
```

### Backend (`backend/app/`)

- **`main.py`** - FastAPI routes. Handles file size/count validation,
  orchestrates extraction + compliance checks, and shapes the HTTP
  responses (`ReviewResult`, `LabelCheckResult`).
- **`claude_client.py`** - Builds the Claude vision request (image
  attachments + a `record_label_fields` tool definition) and parses the
  tool-call response into `ExtractedLabelData`.
- **`models.py`** - Pydantic models shared across the API: `ApplicationData`,
  `ExtractedLabelData`, `FieldLocation`, `FieldResult`, `ReviewResult`,
  `LabelCheckResult`.
- **`compliance.py`** - Pure matching/validation logic:
  - `run_compliance_checks` - application-data-vs-label comparison.
  - `check_label_requirements` - label-only TTB mandatory-statement checks.
  - Shared helpers for normalization, similarity scoring, ABV tolerance,
    and Government Warning validation.

### Frontend (`frontend/src/`)

- **`App.tsx`** - top-level layout/routing between Single, Batch, and
  Label-Only modes. Renders the TTB-branded header (official TTB seal/
  wordmark logo, navy/gold color scheme matching ttb.gov).
- **`components/SingleReview.tsx` / `BatchReview.tsx`** - application-data
  + label review flows.
- **`components/LabelOnlyCheck.tsx`** - label-only requirements flow.
- **`components/ImageDropzone.tsx`** - drag-and-drop / file-picker / camera
  capture for label images (see 7.7).
- **`components/ResultsPanel.tsx` / `LabelCheckResultsPanel.tsx`** -
  per-label result display (status badges, application-vs-label values).
- **`components/LabelImageViewer.tsx`** - zoomable image viewer with
  bounding-box overlays driven by `field_locations`.
- **`components/ConfidenceBadge.tsx` / `StatusBadge.tsx`** - small
  presentational badges.
- **`csv.ts`** - CSV serialization/download for batch export.
- **`api.ts`** - typed wrappers around the backend endpoints.

## 7. Key design decisions

### 7.1 LLM-based extraction instead of traditional OCR

Plain OCR returns raw text without structure or judgment - it can't tell
you "this is the class designation" vs. "this is the importer's address,"
and it can't recognize that "STONE'S THROW" and "Stone's Throw" are the same
brand name. Claude's vision capability extracts structured fields *and*
preserves exact casing (important for the Government Warning check) in a
single pass, keeping the architecture simple for a prototype.

### 7.2 Matching logic (`backend/app/compliance.py`)

- **Brand name / class-type / net contents / address / country of origin:**
  normalized (case/punctuation-insensitive) exact match -> Pass. If not
  exact but textually similar (>=85% similarity) -> "Needs Review" so an
  agent can eyeball it. Otherwise -> Fail. This addresses the "STONE'S
  THROW vs. Stone's Throw" feedback - cosmetic differences shouldn't block
  a label, but anything more substantial gets a human look.
- **Alcohol content:** the percentage is parsed from both values and
  compared numerically against the TTB tolerance for the beverage type:
  +/-0.3 percentage points for distilled spirits (27 CFR 5.65(c)), +/-0.3
  points for beer (27 CFR 7.65(c)), and for wine +/-1.0 point if the labeled
  ABV is over 14% or +/-1.5 points if 14% or below (27 CFR 4.36(b)(1)).
  Within tolerance -> Pass/Needs Review, outside -> Fail. Missing ABV on
  wine/beer labels is "Needs Review" rather than "Fail" since some wine/beer
  labels are legally exempt from stating ABV.
- **Government Warning:** must be present, the header must read exactly
  `GOVERNMENT WARNING:` (capital letters, per 27 CFR 16.21), and the body
  text must match the statutory wording (case-insensitively). Any other
  deviation (reworded text, missing statement) is a Fail - this is the one
  check that is intentionally strict, since agents reject labels for things
  like "Government Warning" in title case.
- **Country of origin (Label-Only Check):** only required for imported
  products. A missing statement is judged against `origin_guess` (Claude's
  domestic/imported/unknown call based on cues like "Imported by" or
  "Product of <country>"): domestic -> Pass, imported -> Fail, unknown ->
  Needs Review. This avoids flagging every domestic label as "Needs Review"
  for a statement it isn't required to have.
- **Net contents:** normalized so equivalent unit notations (e.g. `12 oz`
  vs `12 FL. OZ.`) match; does not convert between unit systems (mL vs fl
  oz).

### 7.3 UI/UX

Designed for a wide range of technical comfort levels: large text, big
buttons, drag-and-drop image upload, color-coded Pass/Needs Review/Fail
badges, and side-by-side "Application vs. Label" values for every field so
an agent can see exactly what triggered a flag.

### 7.4 Visual verification

Each result panel includes a zoomable view of the uploaded label image(s).
Hovering or clicking a field highlights an approximate bounding box on the
image showing where Claude read that field from, alongside a confidence
badge (High/Medium/Low). These regions/confidence levels are AI-generated
approximations meant as a starting point for the agent's own visual check,
not a guarantee.

### 7.5 Batch review & CSV export

Agents can queue multiple label/application pairs and submit them together,
returning a summary table with per-label status and expandable details,
plus an Export CSV button that downloads a per-field breakdown of every
label in the batch - addressing the "200-300 applications at once" pain
point from peak season.

### 7.6 Speed

A single label review is one Claude API call (no multi-step chains),
targeting the "under 5 seconds" requirement from the failed
scanning-vendor pilot. Actual latency depends on the Anthropic API and
image size.

### 7.7 Branding & camera capture

The UI uses TTB's official logo (`frontend/public/ttb-logo.png`, also
cropped to `favicon.png`) and matches ttb.gov's navy/gold color scheme
(`#083c6f` header background, `#15396a` primary actions, `#ffbe2e` accent
border) so the tool feels like part of the TTB site family.

`ImageDropzone` offers drag-and-drop, a "Choose File" picker (always
available), and - on touch-primary devices only - a "Take Photo" button.
"Take Photo" uses a hidden `<input type="file" accept="image/*"
capture="environment">`, which opens the device's rear camera directly on
mobile browsers; the browser handles the camera permission prompt, so no
app-level permission code is needed. Device type is detected via the
`(pointer: coarse)` media query and `navigator.maxTouchPoints`, since
desktop browsers don't support camera capture through this API and would
otherwise just open a redundant file picker.

## 8. Assumptions & trade-offs

- **Not integrated with COLA.** This is explicitly a standalone
  proof-of-concept; the app takes manually-entered application data rather
  than pulling from COLA.
- **Government Warning text is hardcoded** to the standard statutory
  wording (27 CFR 16.21). Real labels for very small containers have
  alternate wording rules that aren't handled here.
- **ABV tolerances** follow 27 CFR 4.36(b)(1) (wine), 5.65(c) (distilled
  spirits), and 7.65(c) (malt beverages). Both the tolerance and the minimum
  extraction-confidence floor are configurable per beverage class in
  `BEVERAGE_TOLERANCE` (`compliance.py`). Real-world products may have
  additional class/type-specific rules; this tool is not a substitute for
  legal review.
- **Net contents:** in the Application vs. Label review, matching is
  exact-after-normalization (e.g. "750mL" == "750 mL"). In the Label-Only
  Check, the parsed quantity is validated against the authorised standards
  of fill (27 CFR 4.72 / 5.203); beer is exempt from the enumerated list.
- **No persistence/database.** Each review is stateless and nothing is
  stored - a production version would need to address PII/document
  retention requirements.
- **Up to 4 images per label** can be uploaded together and are sent to
  Claude in a single request, combining information across all images into
  one set of extracted fields.
- **Network/API access:** this prototype calls the Anthropic API directly.
  A production deployment behind a restricted government network would need
  an approved egress allowlist or a self-hosted vision model.
- **Image quality:** Claude's vision model handles moderately imperfect
  photos (angles, glare, etc.) better than traditional OCR, but extremely
  poor images are flagged via the "notes" field rather than silently
  guessed at.
- **Field location/confidence data is AI-generated** and approximate - it
  is a starting point for visual verification, not a guarantee of accuracy.

## 9. Implemented enhancements (previously "Possible next steps")

The items below were listed as future work in earlier versions and have been
implemented in the current codebase.

- **Configurable tolerance rules per beverage class.** `BEVERAGE_TOLERANCE` in
  `compliance.py` maps each beverage class to its ABV tolerance and a minimum
  extraction-confidence threshold. Both values are operator-adjustable without
  changing any logic.
- **Confidence-gated extraction.** `ExtractedLabelData.extraction_confidence`
  (0.0-1.0) is populated by Claude based on image readability. `assert_extraction_confidence()`
  in `compliance.py` is called at the top of both entry points; images below the
  per-class threshold raise `LowConfidenceError` (HTTP 422 in `main.py`) and
  request a retake instead of silently passing on a low-quality best guess.
- **Label-Only Check: beverage-type confirmation.** `check_label_requirements`
  now accepts `confirmed_beverage_type` (agent override). When the type cannot
  be resolved, `LabelCheckResult.needs_beverage_confirmation=True` is returned and
  no checks are run; the frontend prompts the agent to confirm before evaluation.
  `LabelCheckResult.beverage_type_confirmed` records whether the type was confirmed.
- **Standards of fill validated (27 CFR 4.72 / 5.203 / 7.70).** `_check_label_net_contents`
  now parses the quantity and unit and validates against the authorised size lists.
  Wine and distilled spirits sizes outside the CFR list are a fail; beer is exempt
  (no restricted size list under 27 CFR 7.70).
- **Formula-dependent statements carry an explicit caveat.** Sulfite declaration,
  allergen disclosures, age statement, and commodity statement checks append a
  `_FORMULA_DEPENDENT` notice: a label-level pass does NOT substitute for
  production/formula record review (actual SO2 ppm, specific additives, aging
  records, import documentation).


### Phase 2 enhancements (this version)

- **Per-field confidence thresholds** (`FIELD_CONFIDENCE_THRESHOLDS`): each field
  has its own readability floor so Government Warning body text (long text on a
  curved bottle) is allowed a lower score than brand name. A field below its
  threshold fails with a named retake request.
- **Multi-photo extraction and merging** (`merge_extracted_label_data`,
  `photo_roles` batch param): front and back label photos can be extracted
  independently and merged so each panel gets dedicated Claude attention.
- `LabelCheckResult.photo_sources` records which photo roles contributed.
- `ExtractedLabelData.per_field_confidence` dict added to model and schema.

## 10. Remaining next steps

- Integrate with COLA to pull application data automatically.
- Add state-level ABV and label requirement checks.
- Support alternate Government Warning text for small containers (<100 mL).
- Add a frontend confirmation dialog for the beverage-type override flow.
