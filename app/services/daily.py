# app/services/daily.py
import json
import os
import random
from datetime import date
from typing import Any

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "players_seed.json")


def load_players_local() -> list[dict[str, Any]]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        players = json.load(f)
    # Normalize college on load so the rest of the app can rely on presence/absence.
    return [_normalize_player(p) for p in players]


def pick_player_of_day(d: date, players: list[dict[str, Any]]) -> dict:
    """Deterministic daily selection from local JSON by seeding RNG with date."""
    rand = random.Random(d.toordinal())
    chosen = rand.choice(players)
    # Ensure normalization even if caller passed in raw list
    return _normalize_player(chosen)


def stat_lines_for_player(player: dict) -> list[dict]:
    # Shuffle a copy so the first reveal changes day-to-day
    seasons = player["seasons"].copy()
    random.Random().shuffle(seasons)
    return seasons


# --- helpers -----------------------------------------------------------------

def _normalize_player(player: dict) -> dict:
    """
    Return a shallow copy of player with 'college' normalized:
    - trims whitespace
    - removes the key if empty/unknown so the hint won't be offered
    """
    out = dict(player)  # shallow copy
    college = (out.get("college") or "").strip()
    if college:
        out["college"] = college
    else:
        out.pop("college", None)
    return out

