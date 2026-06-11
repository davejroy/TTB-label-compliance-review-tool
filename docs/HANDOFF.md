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

## Known gaps / suggested next steps

These are documented in more detail in `README.md` ("Possible next steps")
and `docs/PDR.md` (section 9):

- Configurable tolerance rules per beverage class.
- Let the agent confirm/override the detected beverage type
  (`beverage_type_guess`) before Label-Only requirements are evaluated.
- Validate net contents against authorized standards-of-fill sizes (27 CFR
  4.72 / 5.203 / 7.70) - currently only confirms a quantity is stated.
- Additional mandatory statements not yet checked: sulfite declaration (27
  CFR 4.32(e)), aspartame/saccharin, FD&C Yellow No. 5, allergen labeling -
  these need ingredient-level data not visible on most labels.
- Age statement checks for straight whiskies aged < 4 years (27 CFR 5.74)
  and commodity statement requirements for certain imported spirits.

## Where to look first

- `README.md` - setup, running locally, feature overview.
- `docs/PDR.md` - full requirements, architecture, and design
  decisions/trade-offs.
- `docs/SBOM.md` - dependency inventory for backend and frontend.
