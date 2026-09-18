# Regulatory References

This document lists the federal regulations (Code of Federal Regulations, Title 27) governing label compliance checks performed by this tool.

All citations refer to the **Electronic Code of Federal Regulations (eCFR)** at: https://www.ecfr.gov/current/title-27

---

## Government Warning Statement

**Requirement:** All containers of alcoholic beverages containing 0.5%+ ABV must display the Government Warning verbatim.

| Regulation | Description |
|------------|-------------|
| 27 CFR 16.20 | Mandatory Government Warning statement — exact wording required |
| 27 CFR 16.21 | Effective date and applicability |

**Required text (27 CFR 16.20):**
GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

---

## Alcohol Content Statement

| Regulation | Description |
|------------|-------------|
| 27 CFR 4.36 | Wine — alcohol content statement |
| 27 CFR 5.42 | Distilled spirits — alcohol content statement |
| 27 CFR 7.71 | Malt beverages — optional alcohol content |

---

## Net Contents

| Regulation | Description |
|------------|-------------|
| 27 CFR 4.37 | Wine — net contents |
| 27 CFR 4.72 | Wine — standards of fill (incorporating Treasury Decision TTB-200 authorized sizes) |
| 27 CFR 5.43 / 5.70 | Distilled spirits — net contents |
| 27 CFR 5.203 | Distilled spirits — standards of fill (incorporating Treasury Decision TTB-200 authorized sizes) |
| 27 CFR 7.70 / 7.72 | Malt beverages — net contents (unrestricted fill sizes) |
| T.D. TTB-200 | Treasury Decision TTB-200 (89 FR 96570, effective 2025-01-10): Modernized standards of fill for wine & distilled spirits packaging sizes (fully automated) |

---

## Type Size and Legibility (27 CFR 16.22)

| Regulation | Statutory Requirement & Enforcement Tier | System Automation Support |
|---|---|---|
| **27 CFR 16.22(a)(1)** | **Containers $\le 237\text{ mL}$ (8 fl. oz.):** Minimum capital-letter height of **$1.0\text{ mm}$** ($0.04\text{ inches}$), max 40 characters per inch. | **Option A (Scale Marker):** Automated via OpenCV ID-1 card / ArUco detection. Emits `pass` / `fail` / `cannot_measure` with exact mm height $\pm$ uncertainty. |
| **27 CFR 16.22(a)(2)** | **Containers $> 237\text{ mL}$ to $3\text{ L}$ (101.4 fl. oz.):** Minimum capital-letter height of **$2.0\text{ mm}$** ($0.08\text{ inches}$), max 25 characters per inch. | **Option A (Scale Marker):** Automated via OpenCV ID-1 card / ArUco detection. Emits `pass` / `fail` / `cannot_measure` with exact mm height $\pm$ uncertainty. |
| **27 CFR 16.22(a)(3)** | **Containers $> 3\text{ L}$ (101.4 fl. oz.):** Minimum capital-letter height of **$3.0\text{ mm}$** ($0.12\text{ inches}$), max 12 characters per inch. | **Option A (Scale Marker):** Automated via OpenCV ID-1 card / ArUco detection. Emits `pass` / `fail` / `cannot_measure` with exact mm height $\pm$ uncertainty. |
| **27 CFR 16.22(b)** | **Legibility & Background Contrast:** Prominent, conspicuous, readily legible under ordinary conditions on contrasting background. | OCR extraction confidence & binarization validation. |
| **Uncalibrated Photos** | N/A (operational safety boundary) | Uncalibrated photos without scale markers emit an honest statutory advisory with **zero invented mm** and require physical gauge verification. |

See [`docs/TYPE_SIZE_16_22_DESIGN.md`](./TYPE_SIZE_16_22_DESIGN.md) and [`docs/field-tests/typesize/ACCEPTANCE_CHECKLIST.md`](./field-tests/typesize/ACCEPTANCE_CHECKLIST.md) for full technical design and validation protocols.

---

## Country of Origin (Imported Products)

| Regulation | Description |
|------------|-------------|
| 27 CFR 4.35(b) | Wine imports — country of origin required |
| 27 CFR 5.36(d) | Distilled spirits imports |
| 27 CFR 7.59 | Malt beverage imports |
| 27 CFR 27.59 | COLA approval for imports |

---

## Key External Links

- [TTB](https://www.ttb.gov/)
- [COLAs Online](https://www.ttb.gov/labeling/labeling-application.shtml)
- [eCFR Title 27](https://www.ecfr.gov/current/title-27)
- [TTB Beverage Alcohol Manual (PDF)](https://www.ttb.gov/images/pdfs/bam/bam-complete.pdf)
- [Calibrated 27 CFR 16.22 Type-Size Design](./TYPE_SIZE_16_22_DESIGN.md)
- [Type-Size Acceptance Test Checklist](./field-tests/typesize/ACCEPTANCE_CHECKLIST.md)

---

*Last updated: September 2026. Always verify against the current eCFR.*
