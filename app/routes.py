from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from .services.daily import load_players_local, pick_player_of_day, stat_lines_for_player
from .services.scoring import compute_score,compute_total_score, HINT_COSTS
from . import supabase, get_today_et
from flask import current_app
from datetime import datetime as _dt, timezone as _tz
from difflib import get_close_matches
from .services.hints import resolve_hint_values
# Use an alias so we never shadow it accidentally
from .services.hints import resolve_hint_values as hints_resolve
from .services.match import is_typo_match, suggest_players


from .services.scoring import (
    START_SCORE,
    PENALTY_PER_REVEAL,
    HINT_COSTS,
    compute_score,
    compute_total_score,
)

# Check if the current username has already recorded a result today
def has_played_today(username: str) -> bool:
    if not username:
        return False
    today_et = str(get_today_et())
    # DB path
    if supabase:
        try:
            u = (
                supabase.table("users")
                .select("id,username")
                .ilike("username", username)
                .maybe_single()
                .execute()
            )
            row = getattr(u, "data", None)
            uid = row["id"] if (row and row.get("username", "").lower() == username.lower()) else None


            if not uid:
                return False
            r = (supabase.table("results")
                 .select("user_id")
                 .eq("user_id", uid)
                 .eq("game_date", today_et)
                 .maybe_single()
                 .execute())
            return bool(getattr(r, "data", None))
        except Exception:
            current_app.logger.exception("has_played_today failed; falling back to session flag")
            return bool(session.get("solved_today"))
    # Local/session fallback
    return bool(session.get("solved_today"))



bp = Blueprint("main", __name__)

# In-memory cache for local mode
PLAYERS = load_players_local()

# Cached list of (full_name, position) for suggestions
_SUGGEST_CACHE: list[tuple[str, str]] | None = None

def _get_suggest_population() -> list[tuple[str, str]]:
    global _SUGGEST_CACHE
    if _SUGGEST_CACHE is not None:
        return _SUGGEST_CACHE

    out: list[tuple[str, str]] = []
    if supabase:
        try:
            resp = supabase.table("players").select("full_name, position").execute()
            rows = getattr(resp, "data", []) or []
            out = [(r["full_name"], r.get("position") or "") for r in rows if r.get("full_name")]
        except Exception:
            current_app.logger.exception("Failed to build suggestion population from DB; falling back to JSON")
    if not out:
        # local JSON fallback
        for p in PLAYERS or []:
            out.append((p.get("full_name", ""), p.get("position", "")))
    _SUGGEST_CACHE = out
    return out


def _db_player_bundle(today_str: str) -> dict:
    """Return today's player & stat lines from Supabase. Creates daily row if missing."""
    # 1) Try to get today's daily_game row
    resp = (
        supabase.table("daily_game")
        .select("player_id")
        .eq("game_date", today_str)
        .limit(1)
        .execute()
    )
    data = getattr(resp, "data", None) or []
    pid = data[0]["player_id"] if data else None

    # 2) If missing, choose a random player id in Python and persist the daily_game row
    if not pid:
        presp = supabase.table("players").select("id").limit(5000).execute()
        pids = [r["id"] for r in (getattr(presp, "data", None) or [])]
        if not pids:
            raise RuntimeError("No players available in DB to choose daily game.")
        import random
        pid = random.choice(pids)
        supabase.table("daily_game").upsert({"game_date": today_str, "player_id": pid}).execute()

    # 3) Fetch player meta (INCLUDE college)
    meta = (
        supabase.table("players")
        .select("id,full_name,player_slug,position,college")
        .eq("id", pid)
        .limit(1)
        .execute()
    )
    mdata = getattr(meta, "data", None) or []
    if not mdata:
        raise RuntimeError(f"Player id {pid} not found in players table.")
    player_meta = mdata[0]
    college = (player_meta.get("college") or "").strip() or None

    # 4) Fetch seasons and adapt to template shape
    sresp = (
        supabase.table("player_seasons")
        .select("season,team,stat1_name,stat1_value,stat2_name,stat2_value,stat3_name,stat3_value")
        .eq("player_id", pid)
        .order("season")
        .execute()
    )
    sdata = getattr(sresp, "data", None) or []

    stat_lines = [{
        "season": r["season"], "team": r["team"],
        "stats": {
            r["stat1_name"]: r["stat1_value"],
            r["stat2_name"]: r["stat2_value"],
            r["stat3_name"]: r["stat3_value"],
        }
    } for r in sdata]

    return {
        "id": pid,
        "full_name": player_meta["full_name"],
        "player_slug": player_meta["player_slug"],
        "position": player_meta["position"],
        "college": college,            # <-- now included
        "stat_lines": stat_lines,
    }

def _get_or_create_user_id_ci(username: str) -> int | None:
    """Case-insensitive get-or-create for users.username."""
    if not supabase or not username:
        return None

    # Case-insensitive exact match: ilike, then verify in Python
    sel = (
        supabase.table("users")
        .select("id,username")
        .ilike("username", username)
        .maybe_single()
        .execute()
    )
    row = getattr(sel, "data", None)
    if row and (row.get("username", "").lower() == username.lower()):
        return row["id"]

    # Not found -> try to insert (DB index prevents duplicates)
    try:
        supabase.table("users").insert({"username": username}).execute()
    except Exception:
        # Likely a race/duplicate; fall through to reselect
        pass

    sel2 = (
        supabase.table("users")
        .select("id,username")
        .ilike("username", username)
        .maybe_single()
        .execute()
    )
    row2 = getattr(sel2, "data", None)
    return row2["id"] if row2 and (row2.get("username", "").lower() == username.lower()) else None



def get_today_player_bundle() -> dict:
    """Single source of truth for /play and /guess.
       Prefer Supabase + ET; fall back to local JSON if DB fails/not configured."""
    today_et = str(get_today_et())
    if supabase:
        try:
            return _db_player_bundle(today_et)
        except Exception as e:
             current_app.logger.warning("DB daily fetch failed; falling back to JSON for today: %s", e)

    # JSON fallback (dev only), but still use ET date for determinism
    p = pick_player_of_day(get_today_et(), PLAYERS)
    return {
        "id": None,
        "full_name": p["full_name"],
        "player_slug": p["player_slug"],
        "position": p["position"],
        "stat_lines": stat_lines_for_player(p),
    }






def get_username() -> str | None:
    return session.get("username")


@bp.route("/", methods=["GET", "POST"])
@bp.route("/play", methods=["GET", "POST"])
def play():
    # Reset daily state on new ET day (do NOT clear username; we keep it locked)
    today_et = str(get_today_et())
    if session.get("last_game_date") != today_et:
        session["last_game_date"] = today_et
        session["revealed"] = 1
        session["hints_used"] = []
        session.pop("suggestions", None)
        # Do not reset username or username_locked here

    # Username submit: only allow if username is not already set
    if request.method == "POST":
        proposed = (request.form.get("username") or "").strip()
        if proposed:
            if session.get("username"):
                flash("Username is locked for this browser.")
                return redirect(url_for("main.play"))

            # Case-insensitive availability + reservation
            if supabase:
                chk = (
                    supabase.table("users")
                    .select("id,username")
                    .ilike("username", proposed)
                    .maybe_single()
                    .execute()
                )
                row = getattr(chk, "data", None)
                if row and (row.get("username", "").lower() == proposed.lower()):
                    flash("That username is already taken. Try another.")
                    return redirect(url_for("main.play"))

                # Reserve now (DB index prevents race dupes)
                try:
                    supabase.table("users").insert({"username": proposed}).execute()
                except Exception:
                    # If a race happened, treat as taken
                    flash("That username is already taken. Try another.")
                    return redirect(url_for("main.play"))

            # Lock into session
            session.permanent = True
            session["username"] = proposed
            session["username_locked"] = True
            return redirect(url_for("main.play"))


    username = session.get("username")
    username_locked = bool(session.get("username_locked"))
    if username:
        session.permanent = True


    # Determine if this user has already finished today
    already_played_today = has_played_today(username or "")

    # Get bundle and lines
    bundle = get_today_player_bundle()
    lines = bundle.get("stat_lines") or []

    # Reveal count clamp
    revealed = int(session.get("revealed", 1) or 1)
    if lines:
        revealed = max(1, min(revealed, len(lines)))
    else:
        revealed = 1

    # Normalize hints_used
    hints_used = [str(h).lower() for h in session.get("hints_used", [])]
    available_hints = [h for h in HINT_COSTS.keys() if h not in hints_used]

    # Build per-line hints for revealed lines
    hints_for_lines = []
    for i in range(revealed):
        try:
            hv = hints_resolve(bundle, i)
        except Exception:
            current_app.logger.exception("resolve_hint_values failed at line %s", i)
            hv = {}
        hints_for_lines.append(hv)

    # Suggestions from the last wrong-but-close guess
    suggestions = session.pop("suggestions", [])

    # Compute live score
    live_score = compute_total_score(revealed, hints_used)

    return render_template(
        "play.html",
        username=username,
        username_locked=username_locked,
        already_played_today=already_played_today,

        player_position=bundle.get("position", ""),
        stat_lines=lines[:revealed],
        revealed=revealed,

        hints_for_lines=hints_for_lines,
        hints_used=hints_used,
        available_hints=available_hints,
        hint_costs=HINT_COSTS,

        suggestions=suggestions,

        live_score=live_score,
        start_score=START_SCORE,
        penalty_per_reveal=PENALTY_PER_REVEAL,
    )




   







@bp.post("/guess")
def guess():
    username = session.get("username")
    if not username:
        flash("Enter a username first.")
        return redirect(url_for("main.play"))

    # If today's game already completed for this username: block further guesses
    if has_played_today(username):
        flash("You've already completed today's game. Come back tomorrow!")
        return redirect(url_for("main.play"))

    user_guess_raw = (request.form.get("guess") or "").strip()
    user_guess = user_guess_raw.lower()
    revealed = int(request.form.get("revealed", 1) or 1)
    from_suggestion = (request.form.get("from_suggestion") == "1")

    bundle = get_today_player_bundle()

    # Exact/slug candidates
    candidates = {
        bundle["full_name"].lower(),
        bundle["player_slug"].replace("-", " ").lower(),
    }

    # Typo forgiveness
    correct_via_typo = is_typo_match(user_guess_raw, bundle["full_name"])
    is_correct = (user_guess in candidates) or correct_via_typo

    # ----- Correct -> count & finish ------------------------------------------
    if is_correct:
        hints_used = session.get("hints_used", [])
        score = compute_total_score(revealed, hints_used)

        # Persist to DB
        if supabase and bundle.get("id"):
            try:
                user_id = _get_or_create_user_id_ci(username)

                if user_id is not None:
                    supabase.table("results").upsert(
                        {
                            "game_date": str(get_today_et()),
                            "user_id": int(user_id),
                            "revealed": int(revealed),
                            "score": int(score),
                            "correct_attempts": int(revealed),
                        },
                        on_conflict="game_date,user_id",
                    ).execute()
            except Exception:
                current_app.logger.exception("Supabase save failed during /guess; continuing without DB.")

        # Mark as solved in session (helps local mode)
        session["solved_today"] = True
        # Reset per-game UI bits
        session["revealed"] = 1
        session["hints_used"] = []
        session.pop("suggestions", None)

        return render_template("result.html", score=score, answer=bundle["full_name"])

    # ----- Wrong ---------------------------------------------------------------
    # Build suggestions (prefer same position)
    population = _get_suggest_population()
    same_pos = [(n, pos) for (n, pos) in population if pos == bundle.get("position")]
    pool = same_pos if same_pos else population
    suggestions = suggest_players(user_guess_raw, pool, limit=4, min_score=80)

    # If suggestions exist and this is NOT from a suggestion button:
    # show suggestions and DO NOT count this try (no reveal increment).
    if suggestions and not from_suggestion:
        session["suggestions"] = suggestions
        flash("Not quite — did you mean one of these? (This try didn’t count.)")
        return redirect(url_for("main.play"))

    # Otherwise: this wrong try counts (either clicked suggestion but wrong, or no suggestions)
    if suggestions:
        session["suggestions"] = suggestions  # still show them
    revealed = min(revealed + 1, 5)
    session["revealed"] = revealed
    flash("Nope! Another season line revealed.")
    return redirect(url_for("main.play"))


@bp.post("/hint")
def hint():
    # Keep revealed in sync when you click a hint button
    revealed = int(request.form.get("revealed", 1) or 1)
    session["revealed"] = revealed

    # Normalize the posted hint type to lowercase
    kind = (request.form.get("hint_type") or "").strip().lower()
    if not kind or kind not in HINT_COSTS:
        flash("Unknown hint.")
        return redirect(url_for("main.play"))

    # Record single purchase per hint kind (global per game)
    current = session.get("hints_used", [])
    hints_used = {str(h).lower() for h in current}
    if kind not in hints_used:
        hints_used.add(kind)
        session["hints_used"] = list(hints_used)

    return redirect(url_for("main.play"))






@bp.route("/leaderboard")
def leaderboard():
    rows = []
    today_et = str(get_today_et())
    today_label = today_et

    if not supabase:
        return render_template("leaderboard.html", rows=rows, today_label=today_label)

    try:
        # Fetch today's results, highest score first
        res = (
            supabase.table("results")
            .select("score, user_id")
            .eq("game_date", today_et)
            .order("score", desc=True)
            .limit(50)
            .execute()
        )
        data = getattr(res, "data", None) or []
        if not data:
            return render_template("leaderboard.html", rows=rows, today_label=today_label)

        user_ids = sorted({r["user_id"] for r in data if "user_id" in r and r["user_id"] is not None})
        id_to_name = {}
        if user_ids:
            ures = supabase.table("users").select("id, username").in_("id", user_ids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}

        rows = [
            {"username": id_to_name.get(r["user_id"], "unknown"), "score": r["score"]}
            for r in data
        ]
    except Exception:
        current_app.logger.exception("Leaderboard query failed")

    return render_template("leaderboard.html", rows=rows, today_label=today_label)



@bp.route("/leaderboard/all-time")
def all_time():
    rows = []
    if not supabase:
        return render_template("all_time.html", rows=rows)

    try:
        res = supabase.table("results").select("user_id,score").execute()
        data = getattr(res, "data", None) or []
        if not data:
            return render_template("all_time.html", rows=rows)

        from collections import defaultdict
        agg = defaultdict(int)
        for r in data:
            uid = r.get("user_id")
            s = r.get("score") or 0
            if uid:
                agg[uid] += s

        user_ids = list(agg.keys())
        id_to_name = {}
        if user_ids:
            ures = supabase.table("users").select("id, username").in_("id", user_ids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}

        rows = sorted(
            [{"username": id_to_name.get(uid, "unknown"), "total_score": total} for uid, total in agg.items()],
            key=lambda x: x["total_score"], reverse=True
        )
    except Exception:
        current_app.logger.exception("All-time leaderboard query failed")

    return render_template("all_time.html", rows=rows)


@bp.route("/health")
def health():
    return {"ok": True}

@bp.route("/debug")
def debug():
    info = {
        "supabase_configured": bool(supabase),
        "today_et": str(get_today_et()),
        "save_probe_ok": None,
        "save_probe_error": None,
    }

    if not supabase:
        info["save_probe_ok"] = False
        info["save_probe_error"] = "supabase not configured"
        return info

    # Try to upsert a test user + a result for today ET
    try:
       
        supabase.table("users").upsert({"username": "local-probe-user"}, on_conflict="username").execute()
        u = (
            supabase.table("users")
            .select("id")
            .eq("username", "local-probe-user")
            .single()
            .execute()
        )
        uid = u.data["id"]


        gdate = info["today_et"]
        (supabase.table("results")
         .upsert({"game_date": gdate, "user_id": uid, "revealed": 1, "score": 100, "correct_attempts": 1},
                 on_conflict="game_date,user_id")
         .execute())
        info["save_probe_ok"] = True
    except Exception as e:
        info["save_probe_ok"] = False
        info["save_probe_error"] = str(e)

    return info

@bp.get("/debug-hints")
def debug_hints():
    bundle = get_today_player_bundle()
    lines = bundle.get("stat_lines") or []
    out = []
    from .services.hints import canon, resolve_hint_values
    for i, line in enumerate(lines):
        raw_team = line.get("team")
        hv = resolve_hint_values(bundle, i)
        out.append({
            "i": i,
            "season": line.get("season"),
            "raw_team": raw_team,
            "canon_team": canon(raw_team),
            "conference": hv.get("conference"),
            "division": hv.get("division"),
            "record": hv.get("record"),
        })
    return {"player": bundle.get("full_name"), "lines": out}

