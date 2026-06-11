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
┌─────────────────┐        multipart/form-data         ┌──────────────────────┐
│   React + Vite   │ ──────────────────────────────────▶│  FastAPI (uvicorn)    │
│   frontend       │ /api/review, /api/review/batch,     │  backend/app/main.py │
│                  │ /api/label-check/batch               │                       │
│                  │◀────────────────────────────────────│                       │
└─────────────────┘        JSON results                 └──────────┬────────────┘
                                                                     │
                                                                     │ image bytes +
                                                                     │ tool-call schema
                                                                     ▼
                                                          ┌──────────────────────┐
                                                          │  Anthropic API        │
                                                          │  Claude (vision)      │
                                                          │  claude_client.py     │
                                                          └──────────┬────────────┘
                                                                     │
                                                                     │ structured fields
                                                                     │ (ExtractedLabelData)
                                                                     ▼
                                                          ┌──────────────────────┐
                                                          │  compliance.py        │
                                                          │  matching & TTB       │
                                                          │  requirement checks   │
                                                          └──────────────────────┘
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
  Label-Only modes.
- **`components/SingleReview.tsx` / `BatchReview.tsx`** - application-data
  + label review flows.
- **`components/LabelOnlyCheck.tsx`** - label-only requirements flow.
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

## 8. Assumptions & trade-offs

- **Not integrated with COLA.** This is explicitly a standalone
  proof-of-concept; the app takes manually-entered application data rather
  than pulling from COLA.
- **Government Warning text is hardcoded** to the standard statutory
  wording (27 CFR 16.21). Real labels for very small containers have
  alternate wording rules that aren't handled here.
- **ABV tolerances** follow 27 CFR 4.36(b)(1) (wine), 5.65(c) (distilled
  spirits), and 7.65(c) (malt beverages), but real-world products can have
  additional class/type-specific rules and are intended to illustrate the
  concept rather than be a substitute for legal review.
- **Net contents matching is exact-after-normalization** (e.g. "750mL" ==
  "750 mL"); it does not convert between units (mL vs. fl oz).
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

## 9. Possible next steps

- Configurable tolerance rules per beverage class.
- **Label-Only Check enhancements:** let the agent confirm/override the
  detected beverage type (`beverage_type_guess`) before requirements are
  evaluated.
- **Standards of fill:** validate net contents against the authorized
  standards-of-fill sizes per 27 CFR 4.72 / 5.203 / 7.70.
- **Additional mandatory statements:** sulfite declaration ("Contains
  Sulfites") for wines with >=10ppm sulfur dioxide (27 CFR 4.32(e)),
  aspartame/saccharin declarations, FD&C Yellow No. 5, and allergen
  labeling.
- **Age statement checks** for straight whiskies aged less than 4 years (27
  CFR 5.74), and **commodity statement** requirements for certain imported
  spirits.
