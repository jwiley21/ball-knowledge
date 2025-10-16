# Ball Knowledge + CLI (AI Context)

## Overview
This repository contains a Flask web app for a daily NFL player guessing game (Ball Knowledge) and an additional AI-assisted CLI project that consumes a public API (Chuck Norris Jokes). The CLI is structured and tested to meet the midterm requirements (argparse, tests with mocking, CI, and documentation).

## Web App (Summary)
- Framework: Flask
- Data: Supabase (optional), local JSON fallback
- Key services: `app/services/` (scoring, hints, matching)
- Routes: `app/routes.py` (daily, practice, timed, leaderboards)
- Templates: `app/templates/`
- Notable change: Cheat detection updated to reduce false positives (see `/cheat-mark`).

## CLI Project
- Package: `src/`
  - `src/api.py`: API calls to Chuck Norris Jokes API with error handling
  - `src/main.py`: argparse CLI with three commands: `random`, `categories`, `search`
  - `src/__main__.py`: enables `python -m src`
- Tests: `tests/`
  - `tests/test_api.py`: mocks `requests.get`
  - `tests/test_main.py`: mocks API layer and verifies CLI output
- CI: GitHub Actions workflow at `.github/workflows/tests.yml`

## Commands
- `python -m src.main random` → prints a random joke
- `python -m src.main categories` → lists categories
- `python -m src.main search <query> [--limit N]` → prints N jokes

## Coding Standards
- Python 3.10+
- PEP 8 style
- Docstrings on all public functions
- API calls isolated for testability (mock `requests.get`)

## Testing Guidance
- Use `pytest`
- Mock external calls: `@patch('src.api.requests.get')`
- CLI tests mock `src.api.*` functions and assert stdout

## CI
- Workflow runs `pip install -r requirements.txt` and `pytest`
- Badge can be added to README (replace with real `owner/repo` when pushing)

## AI Assistance Notes
- This file is intended to prime AI tools (Copilot, ChatGPT, Claude, Gemini) with project context.
- When asking for changes, reference file paths and expected behaviors.
- Keep this file updated as architecture evolves.

