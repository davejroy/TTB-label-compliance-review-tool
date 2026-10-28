# Synthetic QA scenario matrix

This directory contains two complementary fixture layers:

1. `labels/` contains the committed regression corpus used by the existing
   image-quality tests.
2. `synthetic_scenario_matrix.py` renders a broader matrix into a temporary
   directory during tests. It keeps exact expected extraction data in Python so
   compliance assertions do not depend on OCR or an external model.

The matrix includes:

- compliant spirits, imported wine, and beer without a federal ABV statement;
- missing and incorrectly formatted Government Warning text;
- an unauthorized spirits fill size;
- an application-vs-label ABV mismatch;
- three-quarter perspective, rotation, cylindrical curvature, glare, severe
  blur, partial occlusion, and low label coverage.

Run only this matrix:

```bash
cd backend
pytest tests/test_synthetic_scenario_matrix.py -q
```

The generated JPEGs are disposable QA artifacts. They are fictional and must
not be used as regulatory artwork, evidence, or a substitute for a human
review of the real label.
