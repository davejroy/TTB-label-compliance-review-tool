# TTB Label Compliance Review Tool

Standalone prototype for reviewing alcohol label artwork against application data. The app accepts one or many label images, runs offline OCR, and highlights likely matches, mismatches, and manual-review items for the most common TTB checks.

## What this prototype does

- Upload a single label or a batch of label images
- Enter application metadata for each queued label
- Run OCR locally with `tesseract.js`
- Compare extracted text against:
  - brand name
  - class/type
  - alcohol content
  - net contents
  - bottler / producer
  - country of origin
  - government warning text
- Flag manual-review cases where OCR can only partially verify compliance (for example, warning text bold styling)
- Allow a text override for low-quality images so agents can still complete a review quickly

## Run locally

### Prerequisites

- Node.js 20+

### Install

```bash
npm install
```

### Start the app

```bash
npm start
```

Then open `http://localhost:3000`.

## Test

```bash
npm test
```

## Notes and assumptions

- OCR runs locally and does not depend on external AI APIs, which keeps the prototype usable in restricted government network environments.
- The government warning check verifies exact wording and uppercase text from OCR output, but still marks formatting as manual review because OCR cannot reliably prove bold styling.
- Brand-name matching is intentionally punctuation-insensitive so obvious equivalents such as `STONE'S THROW` and `Stone's Throw` are treated as matches.
- Low-quality or angled images are not auto-corrected in this prototype; instead, reviewers can provide a text override when OCR quality is not sufficient.
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

## Tech stack

- **Backend:** Python, FastAPI, Anthropic SDK (Claude vision for label OCR/transcription)
- **Frontend:** React + TypeScript + Vite + Tailwind CSS
- **Tests:** pytest for the compliance matching logic

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
  numerically against an approximate TTB tolerance (0.15% for spirits, 1.0%
  for wine, 0.3% for beer - simplified from 27 CFR 4.36/5.37/7.71). Within
  tolerance -> Pass/Needs Review, outside -> Fail. Missing ABV on wine/beer
  labels is flagged as "Needs Review" rather than "Fail" since some
  wine/beer labels are legally exempt from stating ABV.
- **Government Warning:** must be present, the header must read exactly
  `GOVERNMENT WARNING:` (capital letters, per 27 CFR 16.21), and the body
  text must match the statutory wording. Any deviation (title case, reworded
  text, missing statement) is a Fail - this is the one check that is
  intentionally strict, per Jenny's note that this check is exact and agents
  reject labels for things like "Government Warning" in title case.

**UI/UX:** Designed for a wide range of technical comfort levels per Sarah's
notes - large text, big buttons, drag-and-drop image upload, color-coded
Pass/Needs Review/Fail badges, and side-by-side "Application vs. Label"
values for every field so an agent can see exactly what triggered a flag.

**Batch review:** Agents can add multiple label/application pairs and submit
them together, returning a summary table with per-label status and
expandable details - addressing the "200-300 applications at once" pain
point from peak season.

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
- **ABV tolerances are simplified** approximations of the real TTB
  regulations, which vary further by product class and are intended to
  illustrate the concept rather than be legally authoritative.
- **Net contents matching is exact-after-normalization** (e.g.
  "750mL" == "750 mL"); it does not convert between units (mL vs. fl oz).
- **No persistence/database.** Each review is stateless and nothing is
  stored, per the "don't store anything sensitive for this exercise"
  guidance. A production version would need to address PII/document
  retention requirements.
- **Single image per label.** Multi-page applications or multiple label
  panels (front/back) aren't supported in this prototype.
- **Network/API access:** this prototype calls the Anthropic API directly.
  Marcus's note about firewall restrictions on outbound ML API calls would
  need to be addressed for a production deployment (e.g. an approved
  egress allowlist, or a self-hosted vision model).
- **Image quality:** Claude's vision model handles moderately imperfect
  photos (angles, glare, etc.) better than traditional OCR, addressing
  Jenny's request, but extremely poor images will still be flagged via the
  "notes" field rather than silently guessed at.

## Possible next steps

- Confidence scores / highlighted regions on the label image showing where
  each field was read from.
- Side-by-side image viewer with zoom for agents to manually verify flagged
  fields.
- CSV export of batch results.
- Configurable tolerance rules per beverage class.
