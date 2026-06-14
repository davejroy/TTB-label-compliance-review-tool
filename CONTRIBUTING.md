# Contributing to TTB Label Compliance Review Tool

Thank you for your interest in contributing! This guide explains how to set up the development environment and submit changes.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the frontend)
- An Anthropic API key

## Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/davejroy/TTB-label-compliance-review-tool.git
cd TTB-label-compliance-review-tool

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install backend dependencies
pip install -r requirements.txt

# 4. Copy and configure environment variables
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

# 5. Run the backend
uvicorn backend.app.main:app --reload

# 6. (Optional) Install and run the frontend
cd frontend && npm install && npm run dev
```

## Running Tests

```bash
pytest tests/ -v
```

## Code Style

- Python code follows **PEP 8** with 4-space indentation.
- All new functions and classes must have **docstrings** following the Google style.
- Use **type annotations** for all function signatures.
- Module-level loggers must use `logging.getLogger(__name__)`.
- Never expose raw exception messages to end-user API responses.

## Making Changes

1. Create a feature branch: `git checkout -b feature/your-feature-name`
2. Make your changes with appropriate tests.
3. Ensure all tests pass: `pytest tests/`
4. Commit with a descriptive message following [Conventional Commits](https://www.conventionalcommits.org/).
5. Open a Pull Request against `main`.

## Compliance Rule Changes

If you are updating TTB compliance rules (in `backend/app/compliance.py`):

1. Cite the specific **27 CFR section** in your code comment and PR description.
2. Add a corresponding entry to `CHANGELOG.md` under the appropriate version.
3. Update `docs/REGULATORY_REFERENCES.md` if you are adding a new regulatory citation.

## Security

Please do **not** open public Issues for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
