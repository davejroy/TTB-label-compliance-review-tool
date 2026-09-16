# Regulatory References

This document lists the federal regulations (Code of Federal Regulations, Title 27) governing label compliance checks performed by this tool.

All citations refer to the **Electronic Code of Federal Regulations (eCFR)** at: https://www.ecfr.gov/current/title-27

---

## Government Warning Statement

**Requirement:** All containers of alcoholic beverages containing 0.5%+ ABV must display the Government Warning verbatim.

| Regulation | Description |
|------------|-------------|
| 27 CFR 16.20 | Mandatory Government Warning statement — exact wording required |
| 27 CFR 16.21 | Mandatory Government Warning statement — general requirements (casing, format) |
| 27 CFR 16.22 | General requirements for warning statement legibility, prominence, and type size |
| T.D. TTB-200 | Modernization of Qualification, Labeling, and Advertising of Alcohol Beverages (Standards of Fill and Mandatory Statements) |

**Required text (27 CFR 16.20):**
GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic beverages impairs your ability to drive a car or operate machinery, and may cause health problems.

**Small containers (<= 100 mL, 27 CFR 16.21(c)):**
Containers with capacity 100 mL or less may display an abbreviated Government Warning omitting clause numbers (1) and (2).

---

## Compliance Gaps & Regulatory Scope Notes

| Reference / Rule | Status | Scope & Gap Analysis |
|------------------|--------|----------------------|
| **27 CFR 16.22** | Advisory / Gap | Legibility, type size (minimum font height in mm/inches), characters-per-inch, and background contrast rules cannot be conclusively certified via vision model OCR alone due to photographic resolution and bottle curvature. Advisory warnings are provided; manual measurement is required for definitive determination. |
| **T.D. TTB-200** | Implemented / Partial Gap | Standards of fill updates from Treasury Decision TTB-200 are incorporated into authorized size checks. Formula-dependent statements (sulfites, allergens, age statements) flag advisory notices noting that laboratory/formula records supersede label-only review. |
| **27 CFR 4.32, 5.63, 7.63** | Implemented (W10 Brand Check) | Brand name is a mandatory field on all alcohol beverage labels. If no brand name is printed or legible on the label, the tool enforces a strict fail status and does not permit guessing or hallucinating brand names from class/type designations or producer text. Low-readability brand names prompt a retake request. |

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
| 27 CFR 5.43 | Distilled spirits — net contents |
| 27 CFR 7.72 | Malt beverages — net contents |

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

---

*Last updated: September 2026. Always verify against the current eCFR.*
