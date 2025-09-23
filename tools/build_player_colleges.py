# tools/build_player_colleges.py
from __future__ import annotations

import os
import re
import sys
import argparse
from typing import Optional, Tuple, List, Dict, Any

import pandas as pd


# =============================================================================
# Normalization helpers
# =============================================================================

def norm_name(s: str) -> str:
    """Lowercase, strip suffixes (Jr./Sr./II/III/IV/V), remove punctuation, collapse spaces."""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\.?$", "", s.strip(), flags=re.I)
    s = re.sub(r"[^\w\s]", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def last_name(s: str) -> str:
    s = norm_name(s)
    parts = s.split()
    return parts[-1] if parts else ""


def first_initial(s: str) -> str:
    s = norm_name(s)
    return s[0] if s else ""


def norm_pos(p: Optional[str]) -> Optional[str]:
    """Map variants into broad buckets to reduce false mismatches."""
    if not isinstance(p, str):
        return None
    p = p.upper().strip()
    if p in {"QB"}: return "QB"
    if p in {"RB", "FB"}: return "RB"
    if p in {"WR"}: return "WR"
    if p in {"TE"}: return "TE"
    if p in {"CB", "DB", "S", "FS", "SS"}: return "DB"
    if p in {"LB", "ILB", "OLB", "MLB"}: return "LB"
    if p in {"DE", "DT", "DL", "NT", "EDGE"}: return "DL"
    if p in {"OT", "OG", "OC", "C", "G", "T", "OL"}: return "OL"
    return p


# per-first-name nicknames (secondary key)
NICK_MAP = {
    "benjamin": "ben",
    "cameron": "cam",
    "casey": "case",
    "cedarian": "ceedee",
    "augustus": "gus",
    "algiers": "aj",
    "charles": "charlie",
    "christopher": "chris",
    "anthony": "tony",
    "andrew": "andy",
    "antonio": "tony",
    "michael": "mike",
    "nicholas": "nick",
    "robert": "rob",
    "william": "will",
    "richard": "rich",
    "steven": "steve",
    "stephen": "steve",
    "wesley": "wes",
    "zaccheus": "zack",
    "zachary": "zach",
    "samuel": "sam",
    "tyrone": "ty",
    "raymell": "ray",
    "thomas": "tom",
    "vincent": "vince",
    "timothy": "tim",
    "willie": "will",
}


def nickname_norm_key(full_name: str) -> str:
    s = norm_name(full_name)
    parts = s.split()
    if not parts:
        return s
    parts[0] = NICK_MAP.get(parts[0], parts[0])
    return " ".join(parts)


# full-name alias map (tertiary key): legal/common roster identities
FULL_ALIAS_MAP = {
    # Big/common
    "quintorres jones": "julio jones",
    "rayne prescott": "dak prescott",
    "reginald bush": "reggie bush",
    "reginald wayne": "reggie wayne",
    "vincent young": "vince young",
    "vincent testaverde": "vinny testaverde",
    "theodore bridgewater": "teddy bridgewater",
    "theodore ginn": "ted ginn",
    "thomas brady": "tom brady",
    "richard gannon": "rich gannon",
    "touraj houshmandzadeh": "tj houshmandzadeh",
    "tuanigamanuolepola tagovailoa": "tua tagovailoa",
    "tyshun samuel": "deebo samuel",
    "steven smith": "steve smith",     # will separate via first season
    "stevonne smith": "steve smith",

    # Your stubborn examples / useful oddities
    "nicholas mullens": "nick mullens",
    "paul lock": "drew lock",           # data oddity; Drew = Andrew Stephen Lock
    "robb y bortles": "blake bortles",
    "robb y anderson": "robby anderson",
    "robert chosen": "robby anderson",  # aka chosen anderson
    "william fuller": "will fuller",
    "william lawrence": "trevor lawrence",  # William Trevor Lawrence
    "timothy couch": "tim couch",
    "timothy hightower": "tim hightower",
    "timothy yeldon": "tj yeldon",
    "tamurice higgins": "tee higgins",
    "rod godwin": "chris godwin",
    "richard proehl": "ricky proehl",
}

def full_alias_key(full_name: str) -> str:
    base = norm_name(full_name)
    return FULL_ALIAS_MAP.get(base, base)


def clean_college(raw: Optional[str]) -> Optional[str]:
    """
    Normalize messy college strings:
    - Split on ';' or '/' (multiple stops), prefer a 4-year over JC/CC if present.
    - Trim and collapse spaces.
    """
    if not isinstance(raw, str):
        return None
    tokens = re.split(r"[;/]", raw)
    tokens = [t.strip() for t in tokens if t and t.strip()]
    if not tokens:
        return None

    def is_jc(x: str) -> bool:
        xl = x.lower()
        return " jc" in xl or " junior college" in xl or " community college" in xl or xl.endswith(" jc")

    four_years = [t for t in tokens if not is_jc(t)]
    chosen = four_years[0] if four_years else tokens[0]
    chosen = re.sub(r"\s+", " ", chosen).strip()
    return chosen or None


# =============================================================================
# Discovery
# =============================================================================

def discover_players_csv(repo_root: str) -> Optional[str]:
    for p in (
        os.path.join(repo_root, "data", "players.csv"),
        os.path.join(repo_root, "supabase_seed", "players.csv"),
    ):
        if os.path.exists(p):
            return p
    return None


def default_seasons_csv(players_csv: str) -> str:
    return os.path.join(os.path.dirname(players_csv), "player_seasons.csv")


# =============================================================================
# Selection/scoring
# =============================================================================

def candidate_weight(source: str, pos_match: bool, year_gap: Optional[float]) -> float:
    """
    Aggregate weight for a single candidate record.
    Higher = more confidence.
    """
    # Source trust
    w_source = {"roster": 3.0, "draft": 2.5, "players": 1.5}.get(source, 1.0)

    # Position match bonus
    w_pos = 2.0 if pos_match else 0.0

    # Year proximity bonus (0 gap -> +3; 1 -> +2; 2 -> +1; >=3 -> +0)
    w_year = 0.0
    if year_gap is not None and pd.notna(year_gap):
        try:
            g = float(year_gap)
            if g <= 0: w_year = 3.0
            elif g <= 1: w_year = 2.0
            elif g <= 2: w_year = 1.0
            else: w_year = 0.0
        except Exception:
            w_year = 0.0

    # Base weight
    return 1.0 + w_source + w_pos + w_year


def pick_college_via_scores(g: pd.DataFrame, year_gap_max: int) -> Tuple[Optional[str], int, Dict[str, float]]:
    """
    Score all candidate rows; return best college, reason_code, and score breakdown.
      reason_code:
        0 = strong (has pos match and year_gap <= 1)
        1 = solid (pos match OR small year gap <= 2)
        2 = weak-but-only-option
        3 = any non-null with low evidence
        4 = none
    """
    if g.empty or "college" not in g.columns:
        return None, 4, {}

    df = g.copy()
    df["college"] = df["college"].map(clean_college)
    df = df.dropna(subset=["college"])
    if df.empty:
        return None, 4, {}

    # flags
    df["pos_match"] = False
    if "meta_position_norm" in df.columns and "position_norm" in df.columns:
        df["pos_match"] = (df["meta_position_norm"] == df["position_norm"])

    if "year_gap" not in df.columns:
        df["year_gap"] = pd.NA

    # compute per-row weights
    def detect_source(tag: str) -> str:
        if pd.isna(tag):
            return "players"
        tag = str(tag)
        if tag.startswith("roster"): return "roster"
        if tag.startswith("draft"):  return "draft"
        if tag.startswith("players"):return "players"
        return "players"

    df["__source"] = df["cand_source"].map(detect_source)
    df["__w"] = df.apply(lambda r: candidate_weight(r["__source"], bool(r["pos_match"]),
                                                    (float(r["year_gap"]) if pd.notna(r["year_gap"]) else None)), axis=1)

    # aggregate by college
    scores = df.groupby("college")["__w"].sum().to_dict()

    # choose best college (highest total score)
    best_college = max(scores.items(), key=lambda kv: kv[1])[0]

    # derive reason from rows supporting the winner
    sup = df[df["college"] == best_college]
    any_pos = bool(sup["pos_match"].any())
    min_gap = float(sup["year_gap"].min()) if sup["year_gap"].notna().any() else 99.0

    if any_pos and min_gap <= 1:
        reason = 0
    elif any_pos or min_gap <= 2:
        reason = 1
    elif len(scores) == 1:
        reason = 2
    else:
        reason = 3

    return best_college, reason, scores


def build_lastname_pool(sources: List[pd.DataFrame]) -> pd.DataFrame:
    """Make a big pool keyed by last name for fallback matching."""
    frames = []
    for df in sources:
        if df is None or df.empty:
            continue
        if "__name" not in df.columns:
            continue
        tmp = df.copy()
        tmp["__last"] = tmp["__name"].map(last_name)
        cols = ["__last", "college"] + [c for c in ("meta_position_norm", "meta_year") if c in tmp.columns]
        frames.append(tmp[cols].assign(cand_source="lastname_fallback"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["__last", "college", "meta_position_norm", "meta_year", "cand_source"])


# =============================================================================
# Load first seasons
# =============================================================================

def load_first_seasons(players: pd.DataFrame, seasons_csv: str) -> Optional[pd.Series]:
    """Compute first season per player_slug using player_seasons.csv."""
    if not os.path.exists(seasons_csv):
        print(f"[WARN] No player_seasons.csv at {seasons_csv}; proceeding without season tie-breaker")
        return None

    try:
        sez = pd.read_csv(seasons_csv)
    except Exception as e:
        print(f"[WARN] Could not load seasons from {seasons_csv}: {e}")
        return None

    if "season" not in sez.columns:
        print("[WARN] player_seasons.csv missing 'season' column; skipping season tie-breaker")
        return None

    sez = sez.dropna(subset=["season"]).copy()
    sez["season"] = pd.to_numeric(sez["season"], errors="coerce")

    if "player_slug" in sez.columns:
        fs = sez.dropna(subset=["player_slug"]).groupby("player_slug")["season"].min()
        print(f"[*] Computed first season for {fs.size} slugs (via seasons.player_slug)")
        return fs

    if "player_id" in sez.columns and "id" in players.columns:
        link = sez[["player_id", "season"]].dropna(subset=["player_id"])
        id_to_slug = players.dropna(subset=["id", "player_slug"])[["id", "player_slug"]].copy()
        link["player_id"] = link["player_id"].astype(str)
        id_to_slug["id"] = id_to_slug["id"].astype(str)
        link = link.merge(id_to_slug, left_on="player_id", right_on="id", how="left").dropna(subset=["player_slug"])
        fs = link.groupby("player_slug")["season"].min()
        print(f"[*] Computed first season for {fs.size} slugs (via seasons.player_id → players.id)")
        return fs

    print("[WARN] Could not associate seasons to player_slug; skipping season tie-breaker")
    return None


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Build player_slug→college mapping using nfl_data_py players + rosters + draft picks with robust disambiguation."
    )
    parser.add_argument("--players", help="Path to players.csv (default: auto-discover)")
    parser.add_argument("--seasons", help="Path to player_seasons.csv (default: alongside players.csv)")
    parser.add_argument("--out-dir", help="Output directory (default: same as players.csv)")
    parser.add_argument("--year-gap", type=int, default=3, help="Max |first_season - (draft/roster year)| considered 'close'")
    parser.add_argument("--lowercase-slugs", action="store_true", help="Force output player_slug to lowercase")
    parser.add_argument("--roster-start", type=int, default=1995, help="First roster season to pull (default 1995)")
    parser.add_argument("--roster-end", type=int, default=0, help="Last roster season to pull (0 = auto current year)")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    players_csv = args.players or discover_players_csv(repo_root)
    if not players_csv or not os.path.exists(players_csv):
        print(f"[ERR] Could not find players.csv. Tried:")
        print(f"  - {os.path.join(repo_root, 'data', 'players.csv')}")
        print(f"  - {os.path.join(repo_root, 'supabase_seed', 'players.csv')}")
        print("Pass --players <path> to specify explicitly.")
        sys.exit(1)

    out_dir = args.out_dir or os.path.dirname(players_csv)
    os.makedirs(out_dir, exist_ok=True)

    try:
        import nfl_data_py as nfl
    except Exception:
        print("[ERR] nfl_data_py not installed. Run: pip install nfl_data_py")
        raise

    # Load players
    players = pd.read_csv(players_csv)
    need = {"full_name", "player_slug"}
    if not need.issubset(players.columns):
        print(f"[ERR] {players_csv} must include columns: {', '.join(sorted(need))}")
        sys.exit(1)

    if "position" in players.columns:
        players["position_norm"] = players["position"].map(norm_pos)
    else:
        players["position_norm"] = None

    if args.lowercase_slugs:
        players["player_slug"] = players["player_slug"].astype(str).str.lower()

    # First season per slug (tie-breaker)
    seasons_csv = args.seasons or default_seasons_csv(players_csv)
    first_season_by_slug = load_first_seasons(players, seasons_csv)

    # ---- Source A: import_players() ----
    print("[*] Fetching nfl_data_py players metadata…")
    meta = nfl.import_players()
    name_col = "display_name" if "display_name" in meta.columns else ("name" if "name" in meta.columns else None)
    college_col = "college_name" if "college_name" in meta.columns else ("college" if "college" in meta.columns else None)
    pos_col = "position" if "position" in meta.columns else None
    draft_col = "draft_year" if "draft_year" in meta.columns else None
    if not name_col or not college_col:
        sample_cols = list(meta.columns)[:12]
        print(f"[ERR] Could not find expected columns in players metadata. Saw: {sample_cols}")
        sys.exit(1)
    keep_cols = [name_col, college_col] + ([pos_col] if pos_col else []) + ([draft_col] if draft_col else [])
    meta = meta[keep_cols].drop_duplicates().copy()
    meta.rename(columns={name_col: "__name", college_col: "college"}, inplace=True)
    if pos_col:
        meta.rename(columns={pos_col: "meta_position"}, inplace=True)
        meta["meta_position_norm"] = meta["meta_position"].map(norm_pos)
    if draft_col:
        meta.rename(columns={draft_col: "meta_year"}, inplace=True)
        meta["meta_year"] = pd.to_numeric(meta["meta_year"], errors="coerce")

    # ---- Source B: import_rosters() ----
    current_year = pd.Timestamp.today().year
    roster_end = (current_year if args.roster_end in (0, None) else args.roster_end)
    years = list(range(args.roster_start, roster_end + 1))
    print(f"[*] Fetching rosters for seasons {years[0]}–{years[-1]} (this may take a moment)…")
    rosters_list = []
    for y in years:
        try:
            r = nfl.import_rosters([y])
            if "player_name" in r.columns and "college" in r.columns:
                keep = ["player_name", "position", "college"]
                if "season" in r.columns:
                    keep.append("season")
                r = r[keep].copy()
                r.rename(columns={"player_name": "__name", "position": "meta_position", "season": "meta_year"}, inplace=True)
                rosters_list.append(r)
        except Exception:
            continue
    roster = (pd.concat(rosters_list, ignore_index=True)
              if rosters_list else pd.DataFrame(columns=["__name", "meta_position", "college", "meta_year"]))
    roster["meta_position_norm"] = roster["meta_position"].map(norm_pos) if "meta_position" in roster.columns else None
    if "meta_year" in roster.columns:
        roster["meta_year"] = pd.to_numeric(roster["meta_year"], errors="coerce")

    # ---- Source C: import_draft_picks() ----
    try:
        draft = nfl.import_draft_picks()
        d_name = "player_name" if "player_name" in draft.columns else ("name" if "name" in draft.columns else None)
        d_college = "college_name" if "college_name" in draft.columns else ("college" if "college" in draft.columns else None)
        d_year = "draft_year" if "draft_year" in draft.columns else ("year" if "year" in draft.columns else None)
        keep = []
        if d_name: keep.append(d_name)
        if d_college: keep.append(d_college)
        if d_year: keep.append(d_year)
        draft = draft[keep].drop_duplicates().copy()
        if d_name: draft.rename(columns={d_name: "__name"}, inplace=True)
        if d_college: draft.rename(columns={d_college: "college"}, inplace=True)
        if d_year:
            draft.rename(columns={d_year: "meta_year"}, inplace=True)
            draft["meta_year"] = pd.to_numeric(draft["meta_year"], errors="coerce")
        draft["meta_position_norm"] = None
    except Exception:
        draft = pd.DataFrame(columns=["__name", "college", "meta_year", "meta_position_norm"])

    if "__name" not in draft.columns:
        draft["__name"] = pd.Series(dtype=object)

    # ---- Build keys for all sources: exact, nickname, full-alias ----
    players["__key_exact"]  = players["full_name"].map(norm_name)
    players["__key_nick"]   = players["full_name"].map(nickname_norm_key)
    players["__key_alias"]  = players["full_name"].map(full_alias_key)
    players["__last"]       = players["full_name"].map(last_name)
    players["__first_init"] = players["full_name"].map(first_initial)

    for df in (meta, roster, draft):
        if "__name" not in df.columns:
            continue
        df["__key_exact"]  = df["__name"].map(norm_name)
        df["__key_nick"]   = df["__name"].map(nickname_norm_key)
        df["__key_alias"]  = df["__name"].map(full_alias_key)
        df["__last"]       = df["__name"].map(last_name)
        df["__first_init"] = df["__name"].map(first_initial)

    def prep(df: pd.DataFrame, key_col: str, tag: str) -> pd.DataFrame:
        cols = [key_col, "college", "__last", "__first_init"] + [c for c in ("meta_position_norm", "meta_year") if c in df.columns]
        out = df[cols].copy()
        out.rename(columns={key_col: "__key"}, inplace=True)
        out["cand_source"] = tag
        return out

    sources = [
        prep(meta,   "__key_exact", "players_exact"),
        prep(meta,   "__key_nick",  "players_nick"),
        prep(meta,   "__key_alias", "players_alias"),
        prep(roster, "__key_exact", "roster_exact"),
        prep(roster, "__key_nick",  "roster_nick"),
        prep(roster, "__key_alias", "roster_alias"),
        prep(draft,  "__key_exact", "draft_exact"),
        prep(draft,  "__key_nick",  "draft_nick"),
        prep(draft,  "__key_alias", "draft_alias"),
    ]

    candidates = pd.concat(sources, ignore_index=True)

    # Join each key; union via concat (no DataFrame.append)
    cand_exact = players.merge(candidates, left_on="__key_exact", right_on="__key", how="left")
    cand_nick  = players.merge(candidates, left_on="__key_nick",  right_on="__key", how="left")
    cand_alias = players.merge(candidates, left_on="__key_alias", right_on="__key", how="left")
    cand = pd.concat([cand_exact, cand_nick, cand_alias], ignore_index=True)

    # Disambiguate player-side last name (avoid collision with candidate-side "__last")
    if "__last_x" in cand.columns:
        cand["__p_last"] = cand["__last_x"]
    elif "__last" in cand.columns:
        cand["__p_last"] = cand["__last"]
    else:
        cand["__p_last"] = pd.NA

    # Precompute year_gap for scoring
    if first_season_by_slug is not None and "meta_year" in cand.columns:
        cand["year_gap"] = cand.apply(
            lambda r: (abs(float(r["meta_year"]) - float(first_season_by_slug.get(r["player_slug"], float("nan"))))
                       if pd.notna(r["meta_year"]) and r["player_slug"] in first_season_by_slug.index else pd.NA),
            axis=1
        )
    else:
        cand["year_gap"] = pd.NA

    # ---- Build last-name fallback pool ----
    ln_pool = build_lastname_pool([meta, roster, draft])

    # ---- Select per slug ----
    grouped_results: List[Dict[str, Any]] = []
    audit_rows: List[Dict[str, Any]] = []

    for slug, g in cand.groupby("player_slug", as_index=False):
        best, reason, score_map = pick_college_via_scores(g, args.year_gap)

        # If nothing chosen, try last-name fallback with constraints:
        #   - same last name (from player side: "__p_last")
        #   - same position bucket (if known)
        #   - year proximity within args.year_gap
        if best is None:
            p_last = g["__p_last"].iloc[0] if "__p_last" in g.columns else None
            p_pos  = g["position_norm"].iloc[0] if "position_norm" in g.columns else None
            fs = int(first_season_by_slug.get(slug)) if (first_season_by_slug is not None and slug in first_season_by_slug.index) else None

            if p_last:
                f = ln_pool[ln_pool["__last"] == p_last].copy()
                if p_pos:
                    f = f[(f["meta_position_norm"].isna()) | (f["meta_position_norm"] == p_pos)]
                if fs is not None and "meta_year" in f.columns:
                    f["year_gap"] = (f["meta_year"] - fs).abs()
                    f = f[f["year_gap"].isna() | (f["year_gap"] <= args.year_gap)]
                else:
                    f["year_gap"] = pd.NA

                if not f.empty:
                    # Shape to minimal candidate schema expected by the scorer
                    f["position_norm"] = p_pos
                    best, reason, score_map = pick_college_via_scores(f, args.year_gap)

        grouped_results.append({"player_slug": slug, "college": best})

        # Distinct colleges across original candidates only (not the fallback pool)
        distinct_cols = g["college"].dropna().map(clean_college).dropna().str.lower().nunique()

        # Audit only if: no pick, weak (>=3), or conflicting colleges
        if (best is None) or (reason >= 3) or (distinct_cols > 1):
            first_season = None
            if first_season_by_slug is not None and slug in first_season_by_slug.index:
                try:
                    first_season = int(first_season_by_slug.get(slug))  # type: ignore[arg-type]
                except Exception:
                    first_season = None

            audit_rows.append({
                "player_slug": slug,
                "full_name": g["full_name"].iloc[0],
                "position": g["position"].iloc[0] if "position" in g.columns else None,
                "position_norm": g["position_norm"].iloc[0],
                "first_season": first_season,
                "picked_college": best,
                "reason_code": reason,
                "distinct_candidate_colleges": int(distinct_cols),
                "scores": score_map,
            })

    result_df = pd.DataFrame(grouped_results)
    total = len(result_df)
    hit = int(result_df["college"].notna().sum())
    pct = (hit / total * 100.0) if total else 0.0
    print(f"[INFO] Matched college for {hit}/{total} players ({pct:.1f}%)")

    # ---- Outputs ----
    out_map_csv   = os.path.join(out_dir, "player_colleges.csv")
    out_join_csv  = os.path.join(out_dir, "players_with_college.csv")
    out_audit_csv = os.path.join(out_dir, "ambiguous_player_colleges.csv")

    map_df = result_df[["player_slug", "college"]].dropna()
    if args.lowercase_slugs:
        map_df["player_slug"] = map_df["player_slug"].astype(str).str.lower()
    map_df.to_csv(out_map_csv, index=False)
    print(f"[OK] Wrote {out_map_csv} ({len(map_df)} rows)")

    merged_out = players.merge(result_df, on="player_slug", how="left")
    keep_cols = ["full_name", "player_slug", "college"]
    if "position" in players.columns:
        keep_cols.insert(2, "position")
    merged_out[keep_cols].to_csv(out_join_csv, index=False)
    print(f"[OK] Wrote {out_join_csv}")

    if audit_rows:
        pd.DataFrame(audit_rows).to_csv(out_audit_csv, index=False)
        print(f"[NOTE] Wrote audit file with potential ambiguities: {out_audit_csv}")
        print("      reason_code: 0=strong, 1=solid, 2=weak-only-option, 3=low-evidence, 4=none")
        print("      Only rows with conflicting colleges, weak picks, or no pick are included.")
    else:
        print("[NOTE] No ambiguous name groups detected.")

    print("[DONE] player_colleges mapping build complete.")


if __name__ == "__main__":
    main()
