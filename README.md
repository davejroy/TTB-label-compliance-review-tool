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

## Possible next steps

- Confidence scores / highlighted regions on the label image showing where
  each field was read from.
- Side-by-side image viewer with zoom for agents to manually verify flagged
  fields.
- CSV export of batch results.
- Configurable tolerance rules per beverage class.
