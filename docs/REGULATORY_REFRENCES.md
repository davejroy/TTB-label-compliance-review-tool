# Regulatory References

## Purpose
This document maps relevant federal regulatory frameworks, statutory requirements, and secure software development guidance that apply to the TTB Label Compliance Review Tool. This document is for informational and engineering reference purposes and does not constitute formal legal advice.

---

## Applicability Summary

| Framework / Regulation | Applies? | Why It May Apply | Validation Needed |
|---|---|---|---|
| 27 CFR Part 16 | Yes | Mandatory statutory Government Warning statement on all alcoholic beverages >= 0.5% ABV | Visual verification against statutory text |
| 27 CFR Parts 4, 5, 7 | Yes | Mandatory labeling rules for wine, distilled spirits, and malt beverages | Verify tolerance and mandatory fields |
| T.D. TTB-200 | Yes | Standards of Fill and Modernized Alcohol Labeling Rules | Verified against authorized fill size lists |
| NIST SP 800-218 (SSDF) | Yes | Secure Software Development Framework baseline for federal tooling | Security headers, input validation, no secrets |
| RFC 9110 §15.5.21 | Yes | HTTP 422 Unprocessable Entity semantics for client-fixable input validation errors | Automated unit tests |

---

## Security and Software Development References

- **NIST SP 800-218 (Secure Software Development Framework)**: Secure configuration, input validation, defense-in-depth security response headers (`nosniff`, `DENY`, strict CSP, `no-referrer`, Permissions-Policy).
- **OWASP API Security Top 10**:
  - API1:2023 Broken Object Level Authorization: Mitigated by stateless in-memory processing without data persistence.
  - API3:2023 Broken Object Property Level Authorization / Input Validation: Batch parameter constraints on `image_counts`, `photo_roles`, and `confirmed_beverage_type`.
  - API8:2023 Security Misconfiguration: Centralized security headers middleware.

---

## Sector-Specific References (TTB Regulations)

All citations refer to the Electronic Code of Federal Regulations (eCFR) at https://www.ecfr.gov/current/title-27

### 1. Government Warning Statement
- **27 CFR 16.20**: Mandatory wording verbatim.
- **27 CFR 16.21**: Header format (`GOVERNMENT WARNING:` in capital letters).
- **27 CFR 16.21(c)**: Small containers (<= 100 mL) permitted abbreviated body.
- **27 CFR 16.22**: Type size and legibility standards (advisory in vision model; physical measurement required for definitive legal determination).

### 2. Mandatory Statements & Standards of Fill
- **27 CFR 4.32, 5.63, 7.63**: Mandatory label statements (Brand Name, Class/Type, ABV, Net Contents, Name & Address, Country of Origin for imports).
- **27 CFR 4.36, 5.65, 7.65**: ABV tolerance bands (+/- 0.3 pp for spirits/beer; +/- 1.0 or 1.5 pp for wine).
- **27 CFR 4.72, 5.203, 7.70**: Standards of fill authorized sizes (incorporating Treasury Decision TTB-200 authorized packaging sizes for wine and distilled spirits; malt beverages unrestricted).
- **T.D. TTB-200**: Treasury Decision TTB-200 (89 FR 96570, effective 2025-01-10) modernized standards of fill for wine (§4.72) and distilled spirits (§5.203) packaging sizes (fully automated).

---

## Control Mapping & Evidence Locations

| Requirement / Control | Project Area | Current Support | Gap | Evidence |
|---|---|---|---|---|
| Statutory Warning Header & Body | `backend/app/compliance.py` | Full validation with strict uppercase casing & small-container support | None | `backend/tests/test_compliance.py`, `backend/tests/test_mode_parity_and_case.py` |
| 27 CFR 16.22 Honesty Advisory | `backend/app/compliance.py` | Explicit advisory on verified labels noting physical mm measurement scope | None | `backend/tests/test_compliance.py` |
| T.D. TTB-200 Standards of Fill | `backend/app/compliance.py` | Automated allow-lists for wine (§4.72) and spirits (§5.203) | None | `backend/tests/test_compliance.py` |
| ABV Tolerance Verification | `backend/app/compliance.py` | Distilled spirits, wine, and beer tolerances | None | `backend/tests/test_compliance.py` |
| Mandatory Brand Name Gating | `backend/app/compliance.py` | Strict fail on missing brand; brand-in-address containment guard | None | `backend/tests/test_compliance.py` |
| HTTP Security Headers | `backend/app/main.py` | Centralized middleware | None | `backend/tests/test_safe_hardening.py` |
| Client-Fixable Input Validation | `backend/app/main.py` | HTTP 422 for malformed batch inputs | None | `backend/tests/test_safe_hardening.py` |
| Fail-Open Zero-Login Access | `backend/app/main.py` | Sacred constraint: no 401/403 gates on public review routes | None | `backend/tests/test_safe_hardening.py` |

---

## Compliance Gaps

1. **27 CFR 16.22 Type Size**: Vision models approximate text size from photo pixel dimensions, but definitive physical point-size certification requires physical ruler measurement.
2. **Formula-Dependent Disclosures**: Sulfite levels, artificial colors, and age statements require production formula verification beyond label-only review.

---

## Review Cadence
Review upon any change to Title 27 regulations or quarterly by the project maintainers.

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-09-16 | Updated with safe hardening controls, zero-login policy, and SSDF alignment | Kilroy_Lives |
| 2026-09-16 | Added TTB-200 standards of fill modernization and 27 CFR 16.22 honesty advisory | Kilroy_Lives |
