START_SCORE = 100
PENALTY_PER_REVEAL = 20  # lose 20 points per extra stat line revealed

def compute_score(revealed: int) -> int:
    """revealed is how many season lines the user saw (1..5)."""
    score = START_SCORE - PENALTY_PER_REVEAL * (max(1, revealed) - 1)
    return max(0, score)
