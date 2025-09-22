# app/services/match.py
from __future__ import annotations
from typing import Iterable, List, Optional, Tuple
import re

try:
    # Better quality/speed
    from rapidfuzz import fuzz, process  # type: ignore
    HAVE_RAPIDFUZZ = True
except Exception:
    HAVE_RAPIDFUZZ = False
    from difflib import get_close_matches  # fallback


# --- Normalization helpers ----------------------------------------------------

_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?", re.IGNORECASE)
PUNCT_RE = re.compile(r"[^a-z0-9\s]")

def norm_name(s: str) -> str:
    """
    Normalize human names for matching:
    - lowercase
    - strip punctuation
    - drop Jr/Sr/II/III/IV/V
    - collapse whitespace
    """
    s = s or ""
    s = s.lower()
    s = _SUFFIX_RE.sub("", s)
    s = PUNCT_RE.sub(" ", s)
    s = " ".join(s.split())
    return s

def short_key(s: str) -> str:
    """
    Key like 't brady' for 'tom brady' to catch first-initial + last-name.
    """
    parts = norm_name(s).split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0][0]} {parts[-1]}"


# --- Typo forgiveness for the *correct* player --------------------------------

def is_typo_match(query: str, answer_fullname: str) -> bool:
    """
    True if query is 'close enough' to the *correct* answer (forgiving typos).
    - Accept exact/slug-ish matches via normalization.
    - Accept >= 90 similarity (RapidFuzz) or difflib close match.
    - Accept first-initial + last-name exact (e.g., 't brady').
    """
    qn = norm_name(query)
    an = norm_name(answer_fullname)

    if not qn or not an:
        return False

    # Exact normalized match
    if qn == an:
        return True

    # First-initial + last-name
    if qn == short_key(answer_fullname):
        return True

    if HAVE_RAPIDFUZZ:
        score = fuzz.ratio(qn, an)
        return score >= 90  # tune as desired
    else:
        # difflib fallback (roughly similar; not a percentage)
        alts = [an]
        close = get_close_matches(qn, alts, n=1, cutoff=0.88)
        return bool(close)


# --- Suggestions across the roster -------------------------------------------

def suggest_players(
    query: str,
    population: Iterable[Tuple[str, str]],
    limit: int = 5,
    min_score: int = 80,
) -> List[str]:
    """
    Suggest up to `limit` player full-names similar to `query`.

    population: iterable of (full_name, position) or (full_name, anything).
    We only use full_name for scoring; keep position there if you want to pre-filter.
    """
    qn = norm_name(query)
    if not qn:
        return []

    names: List[str] = [full for (full, _) in population]

    if not names:
        return []

    if HAVE_RAPIDFUZZ:
        # Use token_set_ratio so order & duplicates don’t hurt.
        scored = process.extract(
            qn,
            names,
            scorer=fuzz.token_set_ratio,
            limit=limit * 2,  # extra then filter by min_score
        )
        out: List[str] = []
        for candidate, score, _idx in scored:
            if score >= min_score:
                out.append(candidate)
            if len(out) >= limit:
                break
        return out
    else:
        # difflib fallback
        close = get_close_matches(qn, [norm_name(n) for n in names], n=limit, cutoff=0.85)
        # map back to original capitalized names (simple best-effort)
        mapping = {norm_name(n): n for n in names}
        return [mapping.get(c, c) for c in close]
