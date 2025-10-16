# Ball Knowledge

Daily NFL player guessing game (Flask) plus an AI‑assisted CLI that consumes a public API. This repo now includes:

- Web app for the daily guessing game (unchanged functionality)
- CLI tool using argparse with 3+ commands
- Pytest tests with mocked API calls
- GitHub Actions workflow running tests on each push

---

## Status & Badges

Replace `username/repo` below after pushing to GitHub.

![Tests](https://github.com/username/repo/workflows/Tests/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

---

## Web App: Run Locally (Windows)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run.py
```

---

## CLI: Overview

The CLI integrates the Chuck Norris Jokes API (no auth) and provides:

- `random`       – print a random joke
- `categories`   – list joke categories
- `search QUERY` – search for jokes containing QUERY

### Install (same virtualenv as above)

```powershell
pip install -r requirements.txt
```

### Usage

```powershell
python -m src.main --help
python -m src.main random
python -m src.main categories
python -m src.main search code --limit 3
```

---

## Testing

```powershell
pytest -q
```

Tests mock all HTTP calls. No real network access is required.

---

## CI/CD

GitHub Actions workflow is configured at `.github/workflows/tests.yml` and runs tests on push and pull requests.

---

## AI Context

See `AGENTS.md` for project context, commands, and conventions used while working with AI coding assistants.

