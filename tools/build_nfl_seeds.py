# tools/build_nfl_seeds.py
# Build seed CSVs from nflverse via nfl_data_py, with progress bars,
# schema-robust aliasing, and full "First Last" player names.
#
# Outputs:
#   supabase_seed/players.csv
#   supabase_seed/player_seasons.csv
#   supabase_seed/team_seasons.csv
#
# Usage (PowerShell):
#   cd C:\ball-knowledge
#   .\.venv\Scripts\Activate.ps1
#   python -m pip install nfl_data_py pandas pyarrow python-slugify tqdm
#   $env:BK_YEARS="2022-2023"; python -u tools\build_nfl_seeds.py   # quick test
#   Remove-Item Env:BK_YEARS; python -u tools\build_nfl_seeds.py    # full run

import os, uuid, time
from collections import Counter, defaultdict

import pandas as pd
from slugify import slugify
from tqdm import tqdm
import nfl_data_py as nfl

# ---------------- Settings ----------------
# Default years; override with BK_YEARS="2018-2020" or "2018,2019"
YEARS = list(range(2000, 2025))
POS_WHITELIST = {"QB", "RB", "WR"}

# "Notable" gates to limit players (tune to your target counts)
QB_MIN_PASS_YARDS = 5000    # career passing yards
RB_MIN_RUSH_ATT   = 400     # career rush attempts
WR_MIN_REC        = 200     # career receptions

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "supabase_seed")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------- Log / timing ----------------
def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

class Section:
    def __init__(self, label): self.label = label
    def __enter__(self):
        self.t0 = time.perf_counter()
        log(f"→ {self.label}…")
    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self.t0
        log(f"✓ {self.label} done in {dt:0.1f}s")

# ---------------- Env year override ----------------
_env_years = os.getenv("BK_YEARS")
if _env_years:
    parts = _env_years.split("-")
    try:
        if len(parts) == 2:
            a, b = map(int, parts)
            YEARS = list(range(a, b + 1))
        else:
            YEARS = [int(x) for x in _env_years.split(",")]
        log(f"Using BK_YEARS override: {YEARS[0]}–{YEARS[-1]} ({len(YEARS)} years)")
    except Exception:
        log("Could not parse BK_YEARS; using default.")

# ---------------- Team normalization for hints ----------------
TEAM_CANON = {
    # AFC
    "BUF":"BUF","MIA":"MIA","NE":"NE","NYJ":"NYJ",
    "BAL":"BAL","CIN":"CIN","CLE":"CLE","PIT":"PIT",
    "HOU":"HOU","IND":"IND","JAX":"JAX","TEN":"TEN",
    "DEN":"DEN","KC":"KC","LAC":"LAC","LV":"LV",
    # NFC
    "DAL":"DAL","NYG":"NYG","PHI":"PHI","WAS":"WAS","WSH":"WAS","WFT":"WAS",
    "CHI":"CHI","DET":"DET","GB":"GB","MIN":"MIN",
    "ATL":"ATL","CAR":"CAR","NO":"NO","NOR":"NO","TB":"TB","TBB":"TB",
    "ARI":"ARI","LAR":"LAR","STL":"LAR","SF":"SF","SFO":"SF","SEA":"SEA",
    # Moves/aliases
    "OAK":"LV",
    "SD":"LAC","SDG":"LAC",
    "JAC":"JAX",
    "LA":"LA",
}
def normalize_team(t):
    if t is None or t == "":
        return None
    t = str(t).upper()
    return TEAM_CANON.get(t, t)

# ---------------- Column alias maps ----------------
# Weekly data aliases → canonical
ALIASES = {
    "player_id":        ["player_id","gsis_id","pfr_player_id"],
    "player_name":      ["player_name","name","display_name"],
    "position":         ["position","pos"],
    "season":           ["season","year"],
    "recent_team":      ["recent_team","team","team_abbr"],

    "passing_yards":    ["passing_yards","pass_yards","yards_pass"],
    "passing_tds":      ["passing_tds","pass_tds"],
    "interceptions":    ["interceptions","ints","int","interception"],

    "rushing_attempts": ["rushing_attempts","rush_attempts","carries","rushing_att","att_rush","rush_att"],
    "rushing_yards":    ["rushing_yards","rush_yards"],
    "rushing_tds":      ["rushing_tds","rush_tds"],

    "receiving_yards":  ["receiving_yards","rec_yards","recv_yards"],
    "receiving_tds":    ["receiving_tds","rec_tds","recv_tds"],
    "receptions":       ["receptions","rec"],
}
NUMERIC_METRICS = [
    "passing_yards","passing_tds","interceptions",
    "rushing_yards","rushing_tds","rushing_attempts",
    "receiving_yards","receiving_tds","receptions",
]

def ensure_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure df has canonical columns by copying from first available alias."""
    cols = set(df.columns)
    for canon, options in ALIASES.items():
        if canon in cols:
            continue
        for opt in options:
            if opt in cols:
                df[canon] = df[opt]
                break
    return df

def most_frequent_team(series: pd.Series) -> str | None:
    s = series.dropna().astype(str).str.upper()
    if s.empty:
        return None
    counts = Counter(s)
    return normalize_team(counts.most_common(1)[0][0])

# ---------------- Players meta (full names) ----------------
# Meta aliases for nfl.import_players() → canonical
META_ALIASES = {
    "player_id":     ["player_id","gsis_id"],
    "display_name":  ["display_name","full_name","name"],
    "first_name":    ["first_name","first"],
    "last_name":     ["last_name","last"],
}
def ensure_meta_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols = set(df.columns)
    for canon, options in META_ALIASES.items():
        if canon in cols:
            continue
        for opt in options:
            if opt in cols:
                df[canon] = df[opt]
                break
    return df

def build_name_map() -> dict:
    """Return {player_id: 'First Last'} using nfl.import_players(), prioritizing First+Last."""
    meta = nfl.import_players()
    meta = ensure_meta_columns(meta)

    if "player_id" not in meta.columns:
        raise RuntimeError("import_players() missing player_id/gsis_id — update META_ALIASES or nfl_data_py.")

    # Prefer First + Last; fallback to display_name
    first = meta.get("first_name");  last = meta.get("last_name")
    if first is None: first = pd.Series([""] * len(meta))
    if last  is None: last  = pd.Series([""] * len(meta))
    full = (first.fillna("").astype(str).str.strip() + " " +
            last.fillna("").astype(str).str.strip()).str.replace(r"\s+", " ", regex=True).str.strip()

    disp = meta.get("display_name")
    if disp is None: disp = pd.Series([""] * len(meta))
    disp = disp.fillna("").astype(str).str.strip()

    # Use full where available; otherwise display_name
    best = full.where(full.ne(""), disp).str.replace(r"\s+", " ", regex=True).str.strip()

    pid = meta["player_id"].astype(str)
    return dict(zip(pid, best))

# ---------------- Pipeline ----------------
def main():
    with Section("Download weekly player data"):
        weekly = nfl.import_weekly_data(YEARS, downcast=True)

    with Section("Normalize columns & filter positions"):
        weekly = ensure_canonical_columns(weekly)
        desired = [
            "player_id","player_name","position","season","recent_team",
            "passing_yards","passing_tds","interceptions",
            "rushing_yards","rushing_tds","rushing_attempts",
            "receiving_yards","receiving_tds","receptions",
        ]
        have = [c for c in desired if c in weekly.columns]
        weekly = weekly[have].copy()
        weekly = weekly[weekly["position"].isin(POS_WHITELIST)]
        log(f"weekly rows after filter: {len(weekly):,}")
        weekly = ensure_canonical_columns(weekly)

    with Section("Attach proper full names (First Last)"):
        name_map = build_name_map()
        if "player_id" in weekly.columns:
            weekly["player_id"] = weekly["player_id"].astype(str)
            mapped = weekly["player_id"].map(name_map)

            # Always prefer the mapped full name; only fall back to existing if map is missing
            existing = weekly.get("player_name")
            if existing is None:
                weekly["player_name"] = mapped
            else:
                existing = existing.fillna("").astype(str).str.strip()
                weekly["player_name"] = mapped.where(mapped.notna() & (mapped.str.strip() != ""), existing)
        else:
            log("WARN: weekly has no player_id column to attach full names; leaving as-is.")

    with Section("Aggregate to season totals + primary team"):
        gcols = [c for c in ["player_id","player_name","position","season"] if c in weekly.columns]
        agg_map = {c: "sum" for c in NUMERIC_METRICS if c in weekly.columns}
        if not agg_map:
            raise RuntimeError("No metric columns found to aggregate. Check nfl_data_py version/schema.")
        season_totals = weekly.groupby(gcols, dropna=False).agg(agg_map).reset_index()

        if "recent_team" in weekly.columns:
            team_lookup = (
                weekly.groupby(gcols, dropna=False)["recent_team"]
                .agg(most_frequent_team)
                .reset_index(name="team")
            )
            season_totals = season_totals.merge(team_lookup, on=gcols, how="left")
        else:
            season_totals["team"] = None

        if "season" in season_totals.columns:
            season_totals = season_totals[season_totals["season"] >= 2000]

        # Fill ONLY numeric metrics
        for col in NUMERIC_METRICS:
            if col in season_totals.columns:
                season_totals[col] = season_totals[col].fillna(0)

        log(f"season rows (2000+): {len(season_totals):,}")

    with Section("Compute career totals for notable filters"):
        careers_aggs = {}
        if "passing_yards" in season_totals.columns:    careers_aggs["pass_yards"] = ("passing_yards","sum")
        if "rushing_attempts" in season_totals.columns: careers_aggs["rush_att"]   = ("rushing_attempts","sum")
        if "receptions" in season_totals.columns:       careers_aggs["rec"]        = ("receptions","sum")
        if not careers_aggs:
            careers_aggs = {"dummy": ("team","count")}
        careers = season_totals.groupby(
            ["player_id","player_name","position"], dropna=False
        ).agg(**careers_aggs).reset_index()

    with Section("Apply notable gates"):
        notable_ids = set()
        def clean_name(x):
            if x is None: return ""
            return str(x).strip()

        for row in tqdm(list(careers.itertuples(index=False)), desc="Filtering players", unit="player"):
            pid, name, pos = row.player_id, clean_name(row.player_name), row.position
            pass_yards = getattr(row, "pass_yards", 0) or 0
            rush_att   = getattr(row, "rush_att", 0) or 0
            rec        = getattr(row, "rec", 0) or 0
            if not name:
                continue
            if pos == "QB" and pass_yards >= QB_MIN_PASS_YARDS:
                notable_ids.add(pid)
            elif pos == "RB" and rush_att >= RB_MIN_RUSH_ATT:
                notable_ids.add(pid)
            elif pos == "WR" and rec >= WR_MIN_REC:
                notable_ids.add(pid)

        filtered = season_totals[season_totals["player_id"].isin(notable_ids)].copy()
        if "season" in filtered.columns:
            filtered = filtered[filtered["season"] >= 2000]
        for col in NUMERIC_METRICS:
            if col in filtered.columns:
                filtered[col] = filtered[col].fillna(0)
        filtered = filtered[filtered["player_name"].notna()]
        filtered = filtered[filtered["player_name"].astype(str).str.strip() != ""]
        filtered = filtered[filtered["position"].isin(POS_WHITELIST)]
        log(f"kept players: {filtered['player_id'].nunique():,} | kept player-seasons: {len(filtered):,}")

    with Section("Build players.csv (UUIDs + unique slugs)"):
        uniq_players = filtered[["player_id","player_name","position"]].drop_duplicates()
        pid_to_uuid = {}
        rows_players = []
        seen_slugs = {}

        for row in tqdm(list(uniq_players.itertuples(index=False)), desc="Players", unit="p"):
            pid = row.player_id
            name = str(row.player_name).strip()
            pos  = row.position

            uid = str(uuid.uuid4())
            pid_to_uuid[pid] = uid

            base = slugify(name) if name else f"unknown-{uid.split('-')[0]}"
            slug = base
            if slug in seen_slugs:
                slug = f"{base}-{pos.lower()}-{uid.split('-')[0]}"
            seen_slugs[slug] = True

            rows_players.append({
                "id": uid,
                "full_name": name or f"Unknown {str(pid)[-6:]}",
                "player_slug": slug,
                "position": pos,
            })
        players_df = pd.DataFrame(rows_players).sort_values("full_name")

    with Section("Build player_seasons.csv (3 stats per position)"):
        def ival(row, col):
            try:
                v = getattr(row, col)
                return int(v) if pd.notna(v) else 0
            except Exception:
                return 0

        rows_seasons = []
        for row in tqdm(list(filtered.itertuples(index=False)), desc="Seasons", unit="row"):
            pos = row.position
            rec = {
                "player_id": pid_to_uuid[row.player_id],
                "season": int(getattr(row, "season", 0) or 0),
                "team": normalize_team(getattr(row, "team", None)),
                "stat1_name": None, "stat1_value": 0,
                "stat2_name": None, "stat2_value": 0,
                "stat3_name": None, "stat3_value": 0,
            }
            if pos == "QB":
                rec.update({
                    "stat1_name": "Pass Yds", "stat1_value": ival(row, "passing_yards"),
                    "stat2_name": "Pass TD",  "stat2_value": ival(row, "passing_tds"),
                    "stat3_name": "INT",      "stat3_value": ival(row, "interceptions"),
                })
            elif pos == "RB":
                rec.update({
                    "stat1_name": "Rush Att", "stat1_value": ival(row, "rushing_attempts"),
                    "stat2_name": "Rush Yds", "stat2_value": ival(row, "rushing_yards"),
                    "stat3_name": "Rush TD",  "stat3_value": ival(row, "rushing_tds"),
                })
            elif pos == "WR":
                rec.update({
                    "stat1_name": "Rec",     "stat1_value": ival(row, "receptions"),
                    "stat2_name": "Rec Yds", "stat2_value": ival(row, "receiving_yards"),
                    "stat3_name": "Rec TD",  "stat3_value": ival(row, "receiving_tds"),
                })
            rows_seasons.append(rec)
        player_seasons_df = pd.DataFrame(rows_seasons)

    with Section("Build team_seasons.csv (wins/losses/ties)"):
        sched = nfl.import_schedules(YEARS)
        if "game_type" in sched.columns:
            sched = sched[sched["game_type"] == "REG"]

        wins = defaultdict(int); losses = defaultdict(int); ties = defaultdict(int)
        home_team_col = "home_team" if "home_team" in sched.columns else "home"
        away_team_col = "away_team" if "away_team" in sched.columns else "away"
        home_score_col = "home_score" if "home_score" in sched.columns else "home_score"
        away_score_col = "away_score" if "away_score" in sched.columns else "away_score"

        for g in tqdm(list(sched.itertuples(index=False)), desc="Games", unit="game"):
            season = int(getattr(g, "season"))
            ht = normalize_team(getattr(g, home_team_col, None))
            at = normalize_team(getattr(g, away_team_col, None))
            hs = int(getattr(g, home_score_col, 0) or 0)
            as_ = int(getattr(g, away_score_col, 0) or 0)
            if not ht or not at:
                continue
            if hs > as_:
                wins[(season, ht)] += 1; losses[(season, at)] += 1
            elif as_ > hs:
                wins[(season, at)] += 1; losses[(season, ht)] += 1
            else:
                ties[(season, ht)] += 1; ties[(season, at)] += 1

        keys = set(list(wins.keys()) + list(losses.keys()) + list(ties.keys()))
        rows_ts = []
        for (season, team) in tqdm(list(keys), desc="Team-Seasons", unit="team"):
            rows_ts.append({
                "season": season,
                "team": team,
                "wins": wins.get((season, team), 0),
                "losses": losses.get((season, team), 0),
                "ties": ties.get((season, team), 0),
            })
        team_seasons_df = pd.DataFrame(rows_ts)

    with Section("Write CSVs"):
        players_csv = os.path.join(OUT_DIR, "players.csv")
        player_seasons_csv = os.path.join(OUT_DIR, "player_seasons.csv")
        team_seasons_csv = os.path.join(OUT_DIR, "team_seasons.csv")

        players_df.to_csv(players_csv, index=False)
        player_seasons_df.to_csv(player_seasons_csv, index=False)
        team_seasons_df.to_csv(team_seasons_csv, index=False)

        log(f"Wrote:\n  {players_csv}\n  {player_seasons_csv}\n  {team_seasons_csv}")
        log(f"Players: {len(players_df):,} | Player-Seasons: {len(player_seasons_df):,} | Team-Seasons: {len(team_seasons_df):,}")

if __name__ == "__main__":
    main()
