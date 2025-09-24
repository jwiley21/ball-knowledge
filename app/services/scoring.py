# app/services/scoring.py

from __future__ import annotations
from typing import Iterable

# Base scoring knobs
START_SCORE = 100
PENALTY_PER_REVEAL = 10  # lose 10 points per extra stat line revealed (beyond the first)

# Hint costs (all keys MUST be lowercase and match what the UI uses)
HINT_COSTS = {
    "team": 15,
    "division": 10,
    "conference": 8,
    "record": 8,
    "college": 20,
    "first_name": 50,
    "last_name": 60,
}

def compute_score(revealed: int) -> int:
    """
    Legacy/simple scoring:
    - revealed is how many season lines the user saw (1..5).
    - Only penalizes extra lines revealed.
    """
    r = max(1, int(revealed or 1))
    score = START_SCORE - PENALTY_PER_REVEAL * (r - 1)
    return max(0, score)

def hint_penalty(hints_used: Iterable[str] | None) -> int:
    """
    Sum the unique hint costs. Case-insensitive.
    """
    if not hints_used:
        return 0
    unique = {str(h).strip().lower() for h in hints_used if str(h).strip()}
    return sum(HINT_COSTS.get(h, 0) for h in unique)

def compute_total_score(revealed: int, hints_used: Iterable[str] | None) -> int:
    """
    Advanced scoring:
    - Base score minus (reveal penalties) minus (hint penalties).
    - Floors at zero.
    """
    base = compute_score(revealed)
    hp = hint_penalty(hints_used)
    total = base - hp
    return max(0, int(total))
