from __future__ import annotations
from typing import Dict, Optional
from .. import supabase

# Legacy/alt → canonical modern codes
TEAM_ALIAS: Dict[str, str] = {
    "NWE": "NE", "GNB": "GB", "KAN": "KC", "TAM": "TB",
    "SDG": "LAC", "STL": "LAR", "OAK": "LV", "CRD": "ARI",
    "OTI": "TEN", "NOR": "NO", "TBB": "TB", "SFO": "SF",
    "WSH": "WAS", "WFT": "WAS", "JAC": "JAX",
    "ARZ": "ARI", "BLT": "BAL", "RAM": "LAR", "RAI": "LV",
    "LA": "LAR", "SD": "LAC",
    # pass-throughs
    "LV": "LV", "LAC": "LAC", "LAR": "LAR",
}

def canon(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    c = str(code).upper().strip()
    return TEAM_ALIAS.get(c, c)

# Canonical team → (Conference, Division)
DIVISION_BY_TEAM: Dict[str, tuple[str, str]] = {
    # AFC East
    "BUF": ("AFC", "East"), "MIA": ("AFC", "East"),
    "NE": ("AFC", "East"),  "NYJ": ("AFC", "East"),
    # AFC North
    "BAL": ("AFC", "North"), "CIN": ("AFC", "North"),
    "CLE": ("AFC", "North"), "PIT": ("AFC", "North"),
    # AFC South
    "HOU": ("AFC", "South"), "IND": ("AFC", "South"),
    "JAX": ("AFC", "South"), "TEN": ("AFC", "South"),
    # AFC West
    "DEN": ("AFC", "West"), "KC": ("AFC", "West"),
    "LAC": ("AFC", "West"), "LV": ("AFC", "West"),
    # NFC East
    "DAL": ("NFC", "East"), "NYG": ("NFC", "East"),
    "PHI": ("NFC", "East"), "WAS": ("NFC", "East"),
    # NFC North
    "CHI": ("NFC", "North"), "DET": ("NFC", "North"),
    "GB": ("NFC", "North"),  "MIN": ("NFC", "North"),
    # NFC South
    "ATL": ("NFC", "South"), "CAR": ("NFC", "South"),
    "NO": ("NFC", "South"),  "TB": ("NFC", "South"),
    # NFC West
    "ARI": ("NFC", "West"), "LAR": ("NFC", "West"),
    "SF": ("NFC", "West"),  "SEA": ("NFC", "West"),
}

def _format_record(w: int, l: int, t: int) -> str:
    return f"{w}-{l}-{t}" if (t or 0) > 0 else f"{w}-{l}"

def _get_team_record(season: int, team: str) -> Optional[str]:
    """Look up W-L(-T) for (season, team) from team_seasons (if Supabase is configured)."""
    if not supabase or not team or season is None:
        return None
    try:
        resp = (
            supabase.table("team_seasons")
            .select("wins,losses,ties")
            .eq("season", int(season))
            .eq("team", team)
            .maybe_single()
            .execute()
        )
        data = getattr(resp, "data", None)
        if not data:
            return None
        return _format_record(
            int(data.get("wins", 0) or 0),
            int(data.get("losses", 0) or 0),
            int(data.get("ties", 0) or 0),
        )
    except Exception:
        return None

def resolve_hint_values(bundle: dict, line_idx: int) -> dict:
    """
    Compute hint values for the currently revealed season line.
    Returns keys: season, team (canonical), conference, division, record (may be None).
    Also includes player-level 'college' when available (supports bundle['college']
    or bundle['player']['college']).
    """
    lines = bundle.get("stat_lines") or []
    if not lines:
        return {}

    idx = max(0, min(line_idx, len(lines) - 1))
    line = lines[idx]

    season = line.get("season")
    raw_team = line.get("team")
    team = canon(raw_team)

    conf = div = None
    if team and team in DIVISION_BY_TEAM:
        conf, div = DIVISION_BY_TEAM[team]

    record = _get_team_record(int(season), team) if (season and team) else None

    result = {
        "season": season,
        "team": team,
        "conference": conf,
        "division": div,
        "record": record,
    }

    # Player-level: accept both bundle['college'] and bundle['player']['college']
    college = (
        (bundle.get("college") or "")
        or (((bundle.get("player") or {}).get("college")) or "")
    ).strip()
    if college:
        result["college"] = college


    # Player-level: first/last name parsed from full_name
    full = (
        (bundle.get("full_name") or "")
        or ((bundle.get("player") or {}).get("full_name") or "")
    ).strip()

    if full:
        def _strip_suffix(n: str) -> str:
            toks = n.split()
            if toks and toks[-1].rstrip(".").upper() in {"JR", "SR", "II", "III", "IV", "V"}:
                toks = toks[:-1]
            return " ".join(toks)

        clean = _strip_suffix(full)
        parts = clean.split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
        elif parts:
            first, last = parts[0], ""
        else:
            first = last = ""

        if first:
            result["first_name"] = first
        if last:
            result["last_name"] = last


    return result


