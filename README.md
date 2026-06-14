- **Per-field confidence thresholds.** `FIELD_CONFIDENCE_THRESHOLDS` in
  `compliance.py` gives each field its own readability floor. Government Warning
  body text is allowed a lower confidence score (0.45) than brand name (0.60)
  because it is long text on a curved bottle surface. A field whose score falls
  below its threshold fails with a targeted retake request naming the specific
  field and explaining how to improve the photo.
- **Multi-photo extraction and merging.** The `/api/label-check/batch` endpoint
  now accepts an optional `photo_roles` form field (JSON array of strings, one
  per file, e.g. `["front","back"]`). When roles differ, each photo is extracted
  independently and merged via `merge_extracted_label_data`. ABV from the front
  label and the Government Warning from the back label each receive dedicated
  Claude attention rather than competing in a single multi-image prompt.
# TTB Label Compliance Review Tool

A prototype tool that helps TTB compliance agents quickly check whether the
text on an alcohol beverage label matches the corresponding COLA application
data, and whether the required Government Warning statement is present and
worded correctly.

## What it does

1. An agent enters the application data (brand name, class/type, alcohol
   content, net contents, etc.) and uploads a photo of the label.
2. The backend sends the image to Claude (vision) and asks it to transcribe
   the label's text fields exactly as printed.
3. The extracted fields are compared against the application data using
   compliance-aware matching rules, and a Pass / Needs Review / Fail result
   is returned for each field with a plain-language explanation.
4. A **Batch Review** mode lets an agent queue up multiple labels +
   application data and review them all in one submission.
5. A **Label-Only Check** mode lets an agent upload just label image(s) - no
   application data required - and validates the extracted fields directly
   against TTB mandatory label requirements (27 CFR Parts 4, 5, 7, and 16):
   brand name, class/type designation, alcohol content (with the correct
   requirement/exemption logic per beverage type), net contents, the
   bottler/producer/importer name and address, country of origin (for
   imports), and the Government Warning statement. Each requirement gets a
   Pass / Needs Review / Fail with the specific CFR citation and an
   explanation. Useful for spot-checking a label's basic compliance
   independent of any specific COLA application. A missing
   country-of-origin statement is only flagged if the label appears to be
   an imported product (Claude's `origin_guess`, based on cues like
   "Imported by" or "Product of <country>") - a domestic label without one
   is a Pass, not a Needs Review, since the statement isn't required for
   domestic products.
6. Every result includes a **zoomable label image viewer**. Hovering or
   clicking a field highlights the approximate region on the label where
   Claude read that value from, along with a High/Medium/Low confidence
   badge for the transcription.
7. **Batch Review** results can be exported as a **CSV** with a per-field
   breakdown for every label in the batch.

## Tech stack

- **Backend:** Python, FastAPI, Anthropic SDK (Claude vision for label OCR/transcription)
- **Frontend:** React + TypeScript + Vite + Tailwind CSS
- **Tests:** pytest for the compliance matching logic

## Documentation

- [`docs/PDR.md`](docs/PDR.md) - Product Design Record: requirements,
  architecture, design decisions, and trade-offs.
- [`docs/SBOM.md`](docs/SBOM.md) - Software Bill of Materials for the
  backend and frontend dependencies.
- [`docs/HANDOFF.md`](docs/HANDOFF.md) - Handoff summary of current project
  status, recent changes, and suggested next steps.

## Setup & running locally

### Prerequisites

- Python 3.11+
- Node.js 20+
- An [Anthropic API key](https://console.anthropic.com/) with access to a
  Claude model that supports vision (default is `claude-sonnet-4-6`)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...

uvicorn app.main:app --reload --port 8000
```

Run the test suite (no API key required - these test the matching logic only):

```bash
pytest
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api/*` requests to `http://localhost:8000`, so
just open the printed `localhost` URL (default `http://localhost:5173`).

### Production build

```bash
cd frontend
npm run build   # outputs to frontend/dist
```

Serve `frontend/dist` with any static file host, and run the FastAPI app
behind a process manager (e.g. `uvicorn app.main:app` with a reverse proxy).
Set `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`) as environment
variables for the backend process.

## Deploying to Render

This repo includes a `render.yaml` "Blueprint" that defines two services:

- `ttb-label-backend` - a Python web service running the FastAPI app
- `ttb-label-frontend` - a static site serving the built React app, wired to
  call the backend automatically via the `VITE_API_HOST` build variable

To deploy:

1. In the Render dashboard, click **New > Blueprint** and select this repo.
2. Render will detect `render.yaml` and show both services. Click **Apply**.
3. Once `ttb-label-backend` is created, open it, go to **Environment**, and
   add `ANTHROPIC_API_KEY` with your Anthropic API key (this is marked
   `sync: false` in the blueprint so it isn't stored in the repo - you must
   set it manually).
4. Trigger a deploy of `ttb-label-frontend` (it needs `VITE_API_HOST` to be
   resolved from the backend service, which happens automatically once the
   backend exists).
5. Once both are live, open the frontend's `.onrender.com` URL.

Note: on Render's free tier, services "spin down" after inactivity and the
first request after idling can take 30-60 seconds while it wakes up.

## Approach & design decisions

**Why an LLM for extraction instead of traditional OCR?** Plain OCR returns
raw text without structure or judgment - it can't tell you "this is the class
designation" vs. "this is the importer's address," and it can't recognize
that "STONE'S THROW" and "Stone's Throw" are the same brand name. Claude's
vision capability extracts structured fields *and* preserves exact casing
(important for the Government Warning check) in a single pass, which keeps
the architecture simple for a prototype.

**Matching logic (`backend/app/compliance.py`):**
- **Brand name / class-type / net contents / address / country of origin:**
  normalized (case/punctuation-insensitive) exact match -> Pass. If not exact
  but textually similar (>=85% similarity) -> "Needs Review" so an agent can
  eyeball it. Otherwise -> Fail. This directly addresses the
  "STONE'S THROW vs. Stone's Throw" feedback from Dave - cosmetic differences
  shouldn't block a label, but anything more substantial gets a human look.
- **Alcohol content:** the percentage is parsed from both values and compared
  numerically against the TTB tolerance for the beverage type: +/-0.3
  percentage points for distilled spirits (27 CFR 5.65(c)), +/-0.3 points for
  beer (27 CFR 7.65(c)), and for wine +/-1.0 point if the labeled ABV is over
  14% or +/-1.5 points if 14% or below (27 CFR 4.36(b)(1)). Within tolerance
  -> Pass/Needs Review, outside -> Fail. Missing ABV on wine/beer labels is
  flagged as "Needs Review" rather than "Fail" since some wine/beer labels
  are legally exempt from stating ABV.
- **Government Warning:** must be present, the header must read exactly
  `GOVERNMENT WARNING:` (capital letters, per 27 CFR 16.21), and the body
  text must match the statutory wording. Any deviation (title case, reworded
  text, missing statement) is a Fail - this is the one check that is
  intentionally strict, per Jenny's note that this check is exact and agents
  reject labels for things like "Government Warning" in title case.
- **Sulfite declaration (27 CFR 4.32(e)):** wines must bear "Contains Sulfites"
  (or equivalent) if the product contains >=10 ppm SO2. Missing declaration
  is "Needs Review" so the agent can verify with production/lab data.
- **Allergen/additive disclosures (27 CFR 4.32(f), 5.63(c), 7.63(c)):** FD&C
  Yellow No. 5, aspartame, saccharin, and cochineal extract/carmine must be
  declared when present. "Needs Review" since presence requires formula verification.
- **Age statement for straight whiskies (27 CFR 5.74(a)):** mandatory when
  aged less than 4 years; optional at 4+ years. Only applies to straight
  whiskies; others auto-pass. Missing statement on a straight whisky -> "Needs Review".
- **Commodity/importer statement for imported spirits (27 CFR 5.63(a)(2),
  5.66(b)):** imported distilled spirits must identify the importer. Only fires
  for origin_guess == "imported"; domestic -> pass; unknown -> "Needs Review".

**UI/UX:** Designed for a wide range of technical comfort levels per Sarah's
notes - large text, big buttons, drag-and-drop image upload, color-coded
Pass/Needs Review/Fail badges, and side-by-side "Application vs. Label"
values for every field so an agent can see exactly what triggered a flag.
The header uses TTB's official logo and ttb.gov's navy/gold color scheme.

**Camera capture:** On phones and tablets (detected via the `(pointer:
coarse)` media query), each image upload area also offers a "Take Photo"
button that opens the device camera directly, in addition to the regular
file picker. Desktop browsers show only "Choose File", since they don't
support camera capture through this API.

**Visual verification:** Each result panel includes a zoomable view of the
uploaded label image(s). Hovering or clicking a field highlights an
approximate bounding box on the image showing where Claude read that field
from, alongside a confidence badge (High/Medium/Low) for the transcription.
These regions/confidence levels are AI-generated approximations meant as a
starting point for the agent's own visual check, not a guarantee.

**Batch review:** Agents can add multiple label/application pairs and submit
them together, returning a summary table with per-label status and
expandable details, plus an **Export CSV** button that downloads a
per-field breakdown of every label in the batch - addressing the "200-300
applications at once" pain point from peak season.

**Speed:** A single label review is one Claude API call (no multi-step
chains), targeting the "under 5 seconds" requirement from the failed
scanning-vendor pilot. Actual latency depends on the Anthropic API and image
size.

## Assumptions & trade-offs

- **Not integrated with COLA.** Per Marcus, this is explicitly a standalone
  proof-of-concept; the app takes manually-entered application data rather
  than pulling from COLA.
- **Government Warning text is hardcoded** to the standard statutory wording
  (27 CFR 16.21). Real labels for very small containers have alternate
  wording rules that aren't handled here.
- **ABV tolerances** follow 27 CFR 4.36(b)(1) (wine), 5.65(c) (distilled
  spirits), and 7.65(c) (malt beverages). Both the ABV tolerance and the
  minimum extraction-confidence floor for each beverage class are configurable
  in `BEVERAGE_TOLERANCE` in `compliance.py`. Real-world products may have
  additional class/type-specific rules; this tool illustrates the concept and
  is not a substitute for legal review.
- **Net contents matching is exact-after-normalization** (e.g.
  "750mL" == "750 mL"); it does not convert between units (mL vs. fl oz).
- **No persistence/database.** Each review is stateless and nothing is
  stored, per the "don't store anything sensitive for this exercise"
  guidance. A production version would need to address PII/document
  retention requirements.
- **Up to 4 images per label** (e.g. front and back panels) can be uploaded
  together and are sent to Claude in a single request, which combines
  information across all images into one set of extracted fields.
- **Network/API access:** this prototype calls the Anthropic API directly.
  Marcus's note about firewall restrictions on outbound ML API calls would
  need to be addressed for a production deployment (e.g. an approved
  egress allowlist, or a self-hosted vision model).
- **Image quality:** Claude's vision model handles moderately imperfect
  photos (angles, glare, etc.) better than traditional OCR, addressing
  Jenny's request, but extremely poor images will still be flagged via the
  "notes" field rather than silently guessed at.

## Implemented in this version (previously listed as "Possible next steps")

- **Configurable tolerance rules per beverage class.** `BEVERAGE_TOLERANCE` in
  `compliance.py` maps each beverage class (`distilled_spirits`, `wine`, `beer`,
  `unknown`) to its ABV tolerance and a minimum extraction-confidence threshold.
  Operators can adjust both values without touching logic code.
- **Confidence-gated extraction.** The new `extraction_confidence` field (0.0-1.0)
  is populated by Claude and evaluated before any compliance check runs. Images
  below the per-class confidence floor raise `LowConfidenceError` and return a
  fail with a retake-photo prompt instead of silently passing on a best guess.
- **Label-Only Check: beverage-type confirmation.** `check_label_requirements` now
  accepts a `confirmed_beverage_type` parameter. When Claude's guess is `unknown`,
  the result sets `needs_beverage_confirmation=True` and returns no checks so the
  frontend can prompt the agent to confirm or override before requirements are
  evaluated. The agent-confirmed type is also used for fill-size validation.
- **Standards of fill validated (27 CFR 4.72 / 5.203 / 7.70).** Net contents
  now validates the parsed quantity and unit against the closed list of authorised
  fill sizes. Wine and distilled spirits sizes outside the CFR list are a fail;
  beer is exempt from the enumerated list (27 CFR 7.70 requires accuracy but not
  a specific size).
- **Formula-dependent checks carry an explicit caveat.** Sulfite declaration,
  allergen disclosures, age statement, and commodity statement checks all append
  a FORMULA_DEPENDENT notice: a label-level pass does not substitute for
  production/formula record review (e.g. actual SO2 ppm, specific additives,
  aging records, import documentation).

- **Alternate Government Warning text for small containers (<= 100 mL).** Per 27 CFR 16.21(c), containers with capacity <= 100 mL may use an abbreviated body text that omits clause numbers (1) and (2). Both the full-form and short-form body text are accepted as a pass for small containers. `_is_small_container()` in `compliance.py` detects the container size using `_parse_net_contents`; the threshold is configurable via `SMALL_CONTAINER_THRESHOLD_ML` and `SMALL_CONTAINER_THRESHOLD_FLOZ`.
- **Beverage-type confirmation dialog (frontend).** When the backend returns `needs_beverage_confirmation=True`, the Label-Only Check tab now automatically shows a `BeverageTypeDialog` modal with radio buttons for the three beverage types. The agent selects the correct type and clicks **Confirm & Re-check**; the label is re-submitted with `confirmed_beverage_type` set and the result row is updated in place. A **Confirm type** inline link also appears in the results table for any label still waiting for confirmation.

Possible next steps

- Integrate with COLA to pull application data automatically.
- Add state-level ABV and label requirement checks (states vary significantly).
