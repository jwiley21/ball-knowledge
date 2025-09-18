import json
import os
import random
from datetime import date
from typing import Any

SEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "players_seed.json")

def load_players_local() -> list[dict[str, Any]]:
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def pick_player_of_day(d: date, players: list[dict[str, Any]]) -> dict:
    """Deterministic daily selection from local JSON by seeding RNG with date."""
    rand = random.Random(d.toordinal())
    return rand.choice(players)

def stat_lines_for_player(player: dict) -> list[dict]:
    # Shuffle a copy so the first reveal changes day-to-day
    seasons = player["seasons"].copy()
    random.Random().shuffle(seasons)
    return seasons
