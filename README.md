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