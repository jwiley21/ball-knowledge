import os
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
from datetime import timedelta  # add this



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

def _update_streaks_after_win(user_id: int) -> None:
    """Increment user's current streak if they also solved yesterday; update best streak."""
    if not supabase or not user_id:
        return

    # Was there a result yesterday?
    yday = str(get_today_et() - timedelta(days=1))
    try:
        yres = (
            supabase.table("results")
            .select("id")
            .eq("user_id", user_id)
            .eq("game_date", yday)
            .maybe_single()
            .execute()
        )
        had_yesterday = bool(getattr(yres, "data", None))
    except Exception:
        current_app.logger.exception("streaks: check yesterday failed")
        had_yesterday = False

    # Existing streak row
    try:
        srow = (
            supabase.table("streaks")
            .select("current_streak,best_streak")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        sdata = getattr(srow, "data", None) or {}
        prev_current = int(sdata.get("current_streak") or 0)
        prev_best = int(sdata.get("best_streak") or 0)
    except Exception:
        prev_current = 0
        prev_best = 0

    new_current = (prev_current + 1) if had_yesterday else 1
    new_best = max(prev_best, new_current)

    try:
        supabase.table("streaks").upsert(
            {
                "user_id": user_id,
                "current_streak": new_current,
                "best_streak": new_best,
                "updated_at": _dt.now(_tz.utc).isoformat(),
            },
            on_conflict="user_id",
        ).execute()
    except Exception:
        current_app.logger.exception("streaks: upsert failed")


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
            resp = supabase.table("v_players_eligible").select("full_name, position").execute()
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
        presp = supabase.table("v_players_eligible").select("id").limit(5000).execute()
        pids = [r["id"] for r in (getattr(presp, "data", None) or [])]
        if not pids:
            raise RuntimeError("No players available in DB to choose daily game.")
        import random
        pid = random.choice(pids)
        supabase.table("daily_game").upsert({"game_date": today_str, "player_id": pid}).execute()

    # 3) Fetch player meta (INCLUDE college)
    meta = (
        supabase.table("v_players_eligible")
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


@bp.route("/", methods=["GET", "POST"])
def landing():
    # Username form now lives here
    if request.method == "POST":
        proposed = (request.form.get("username") or "").strip()
        if proposed:
            if session.get("username"):
                flash("Username is locked for this browser.")
                return redirect(url_for("main.landing"))

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
                    return redirect(url_for("main.landing"))
                try:
                    supabase.table("users").insert({"username": proposed}).execute()
                except Exception:
                    flash("That username is already taken. Try another.")
                    return redirect(url_for("main.landing"))

            session.permanent = True
            session["username"] = proposed
            session["username_locked"] = True
            return redirect(url_for("main.landing"))

    username = session.get("username")
    if username:
        session.permanent = True

    return render_template("landing.html",
                           username=username,
                           username_locked=bool(session.get("username_locked")))



def get_username() -> str | None:
    return session.get("username")


@bp.route("/play", methods=["GET"])
def play():
    # Require username before playing
    if not session.get("username"):
        flash("Create a display name first.")
        return redirect(url_for("main.landing"))

    # Reset daily state on new ET day (do NOT clear username; we keep it locked)
    today_et = str(get_today_et())
    if session.get("last_game_date") != today_et:
        session["last_game_date"] = today_et
        session["revealed"] = 1
        session["hints_used"] = []
        session.pop("suggestions", None)
        session.pop("cheated_today", None)

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
    used = set(hints_used)

    HIDE = {"record", "conference"}  # <--- toggle anything here
    available_hints = [h for h in HINT_COSTS.keys() if h not in hints_used and h not in HIDE]


    # If Team is bought, Conference & Division are free via Team → hide their buttons
    if "team" in used:
        available_hints = [h for h in available_hints if h not in ("conference", "division")]
    # If Division is bought, Conference is redundant → hide its button
    elif "division" in used:
        available_hints = [h for h in available_hints if h != "conference"]


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
        mode="daily",
    )


def _db_player_bundle_for_id(pid) -> dict:
    """Build a bundle for a specific player id (used by Practice)."""
    meta = (
        supabase.table("v_players_eligible")
        .select("id,full_name,player_slug,position,college")
        .eq("id", pid)
        .limit(1)
        .execute()
    )
    mdata = getattr(meta, "data", None) or []
    if not mdata:
        raise RuntimeError(f"Player id {pid} not found in players.")
    player_meta = mdata[0]
    college = (player_meta.get("college") or "").strip() or None

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
        "college": college,
        "stat_lines": stat_lines,
    }


def _get_random_player_id() -> int | None:
    """Pick a random player id from DB or None if unavailable."""
    if not supabase:
        return None
    presp = supabase.table("v_players_eligible").select("id").limit(5000).execute()
    pids = [r["id"] for r in (getattr(presp, "data", None) or [])]
    if not pids:
        return None
    import random
    return random.choice(pids)

def _bundle_for_pid_or_json(pid=None, json_slug=None):
    """Return a bundle for a DB player (by id) or JSON fallback (by slug)."""
    if pid:
        return _db_player_bundle_for_id(pid)
    # JSON fallback by slug
    p = next((x for x in (PLAYERS or []) if x.get("player_slug") == json_slug), None)
    if not p:
        # pick a random JSON player if slug missing
        import random
        p = random.choice(PLAYERS or [])
    return {
        "id": None,
        "full_name": p.get("full_name"),
        "player_slug": p.get("player_slug"),
        "position": p.get("position"),
        "college": (p.get("college") or None),
        "stat_lines": stat_lines_for_player(p),
    }

def _timed_pick_new_player():
    """Pick a new random player and stash identifier in session."""
    pid = _get_random_player_id()
    if pid is None:
        # JSON fallback
        import random
        p = random.choice(PLAYERS or [])
        session["timed_pid"] = None
        session["timed_json_slug"] = p.get("player_slug")
    else:
        session["timed_pid"] = pid
        session.pop("timed_json_slug", None)


@bp.route("/timed", methods=["GET"])
def timed():
    if not session.get("username"):
        flash("Create a display name first.")
        return redirect(url_for("main.landing"))

    start_new = (request.args.get("new") == "1")
    # New run: reset state + start clock
    if start_new or not session.get("timed_active"):
        session["timed_active"] = True
        session["timed_total"] = 0
        session["timed_revealed"] = 1
        session["timed_hints_used"] = []
        session.pop("timed_suggestions", None)
        session["timed_started_at"] = _dt.now(_tz.utc).isoformat()
        _timed_pick_new_player()

    # Compute remaining seconds (2 minutes total)
    seconds_total = 120
    seconds_left = seconds_total
    try:
        started_iso = session.get("timed_started_at")
        if started_iso:
            started = _dt.fromisoformat(started_iso)
            if started.tzinfo is None:
                started = started.replace(tzinfo=_tz.utc)
            now = _dt.now(_tz.utc)
            elapsed = int((now - started).total_seconds())
            seconds_left = max(0, seconds_total - elapsed)
    except Exception:
        seconds_left = seconds_total

    # If time expired, finalize server-side (save Top 10 only)
    if seconds_left <= 0 and session.get("timed_active"):
        total = int(session.get("timed_total", 0) or 0)
        saved = False
        if supabase and session.get("username"):
            uid = _get_or_create_user_id_ci(session["username"])
            if uid:
                saved = _timed_maybe_save_top10(total, uid)
        # Clear run state
        for k in ("timed_active", "timed_total", "timed_revealed", "timed_hints_used",
                  "timed_suggestions", "timed_pid", "timed_json_slug", "timed_started_at"):
            session.pop(k, None)
        return render_template("timed_result.html", total=total, saved=saved)

    # Build current bundle
    bundle = _bundle_for_pid_or_json(
        pid=session.get("timed_pid"),
        json_slug=session.get("timed_json_slug"),
    )
    lines = bundle.get("stat_lines") or []

    revealed = int(session.get("timed_revealed", 1) or 1)
    revealed = max(1, min(revealed, len(lines) or 1))

    hints_used = [str(h).lower() for h in session.get("timed_hints_used", [])]
    HIDE = {"record", "conference"}  # <--- toggle anything here
    available_hints = [h for h in HINT_COSTS.keys() if h not in hints_used and h not in HIDE]


    suggestions = session.get("timed_suggestions", [])
    live_score = compute_total_score(revealed, hints_used)

    return render_template(
        "timed.html",
        username=session.get("username"),
        total_score=session.get("timed_total", 0),

        player_position=bundle.get("position", ""),
        stat_lines=lines[:revealed],
        revealed=revealed,

        hints_for_lines=[hints_resolve(bundle, i) for i in range(revealed)],
        hints_used=hints_used,
        available_hints=available_hints,
        hint_costs=HINT_COSTS,
        hint_action=url_for("main.timed_hint"),

        suggestions=suggestions,

        seconds=seconds_left,             # <- shows remaining seconds
        start_score=START_SCORE,
        penalty_per_reveal=PENALTY_PER_REVEAL,
        live_score=live_score,
    )


@bp.post("/timed/hint")
def timed_hint():
    if not session.get("timed_active"):
        return redirect(url_for("main.timed", new=1))

    # Keep revealed in sync
    revealed = int(request.form.get("revealed", 1) or 1)
    session["timed_revealed"] = revealed

    kind = (request.form.get("hint_type") or "").strip().lower()
    if not kind or kind not in HINT_COSTS:
        flash("Unknown hint.")
        return redirect(url_for("main.timed"))

    used = {str(h).lower() for h in session.get("timed_hints_used", [])}
    if kind not in used:
        used.add(kind)
        session["timed_hints_used"] = list(used)

    return redirect(url_for("main.timed"))


@bp.post("/timed/guess")
def timed_guess():
    if not session.get("username"):
        return redirect(url_for("main.landing"))
    if not session.get("timed_active"):
        return redirect(url_for("main.timed", new=1))

    user_guess_raw = (request.form.get("guess") or "").strip()
    revealed = int(request.form.get("revealed", 1) or 1)
    from_suggestion = (request.form.get("from_suggestion") == "1")

    # Build current bundle
    bundle = _bundle_for_pid_or_json(
        pid=session.get("timed_pid"),
        json_slug=session.get("timed_json_slug"),
    )

    # Correctness
    candidates = {
        bundle["full_name"].lower(),
        bundle["player_slug"].replace("-", " ").lower(),
    }
    correct_via_typo = is_typo_match(user_guess_raw, bundle["full_name"])
    is_correct = (user_guess_raw.lower() in candidates) or correct_via_typo

    if is_correct:
        # Points LEFT after reveals + hint buys
        hints_used = [str(h).lower() for h in session.get("timed_hints_used", [])]
        per_player = compute_total_score(revealed, hints_used)

        session["timed_total"] = int(session.get("timed_total", 0)) + int(per_player)

        # Next player: reset per-answer state
        session["timed_revealed"] = 1
        session["timed_hints_used"] = []
        session.pop("timed_suggestions", None)
        _timed_pick_new_player()
        flash(f"Correct! +{per_player} points.")
        return redirect(url_for("main.timed"))

    # Wrong → suggestions or reveal
    population = _get_suggest_population()
    same_pos = [(n, pos) for (n, pos) in population if pos == bundle.get("position")]
    pool = same_pos if same_pos else population
    suggestions = suggest_players(user_guess_raw, pool, limit=4, min_score=72)

    if suggestions and not from_suggestion:
        session["timed_suggestions"] = suggestions
        flash("Not quite — did you mean one of these? (This try didn’t count.)")
        return redirect(url_for("main.timed"))

    if suggestions:
        session["timed_suggestions"] = suggestions

    revealed = min(int(session.get("timed_revealed", 1) or 1) + 1, 5)
    session["timed_revealed"] = revealed
    flash("Nope! Another season line revealed.")
    return redirect(url_for("main.timed"))

def _timed_maybe_save_top10(score: int, user_id: int) -> bool:
    """
    Save a timed score only if it qualifies for the global Top 10.
    - Reads current Top 10 (desc).
    - If fewer than 10 rows exist, insert.
    - Otherwise insert only when `score` is strictly greater than the current 10th place.
    Notes:
      * No .select() chaining after .insert() (compat with older supabase-py).
      * Verifies by re-reading Top 10; logs what happened.
    Returns True iff we attempted the insert and a re-read shows it in Top 10.
    """
    if not supabase:
        return False
    try:
        # 1) Read current Top 10
        top_resp = (
            supabase.table("timed_results")
            .select("score")
            .order("score", desc=True)
            .limit(10)
            .execute()
        )
        top_scores = [int(r["score"]) for r in (getattr(top_resp, "data", None) or []) if r.get("score") is not None]

        qualifies = (len(top_scores) < 10) or (score > min(top_scores) if top_scores else True)
        if not qualifies:
            current_app.logger.info(f"[timed/top10] not qualified: score={score}, top10={top_scores}")
            return False

        # 2) Insert (no .select() chaining)
        supabase.table("timed_results").insert(
            {"user_id": int(user_id), "score": int(score)}
        ).execute()

        # 3) Verify by re-reading Top 10
        verify = (
            supabase.table("timed_results")
            .select("user_id,score")
            .order("score", desc=True)
            .limit(10)
            .execute()
        )
        rows = getattr(verify, "data", None) or []
        saved = any(
            int(r.get("user_id", -1)) == int(user_id) and int(r.get("score", -10**9)) == int(score)
            for r in rows
        )
        current_app.logger.info(f"[timed/top10] saved={saved} score={score} user_id={user_id} top10_after={[r.get('score') for r in rows]}")
        return saved
    except Exception as e:
        current_app.logger.exception(f"[timed/top10] save failed: {e}")
        return False






@bp.post("/timed/skip")
def timed_skip():
    if not session.get("username"):
        return redirect(url_for("main.landing"))
    if not session.get("timed_active"):
        return redirect(url_for("main.timed", new=1))

    # Fixed penalty for this round (overall total can go negative)
    session["timed_total"] = int(session.get("timed_total", 0)) - 50

    # Next round: reset per-answer state and pick a new player
    session["timed_revealed"] = 1
    session["timed_hints_used"] = []
    session.pop("timed_suggestions", None)
    _timed_pick_new_player()

    flash("Skipped. -50 points applied.")
    return redirect(url_for("main.timed"))


@bp.post("/timed/finish")
def timed_finish():
    # Read the total accumulated score from this run
    total = int(session.get("timed_total", 0) or 0)

    saved = False
    if supabase and session.get("username"):
        try:
            uid = _get_or_create_user_id_ci(session["username"])
            if uid:
                saved = _timed_maybe_save_top10(total, uid)  # (score, user_id)
        except Exception:
            current_app.logger.exception("_timed_maybe_save_top10 failed")

    # Clear run state; keep username
    for k in ("timed_active", "timed_revealed", "timed_hints_used",
              "timed_suggestions", "timed_pid", "timed_json_slug", "timed_started_at"):
        session.pop(k, None)

    # Render results with a single, consistent variable name: total
    return render_template("timed_result.html", total=total, saved=saved)





@bp.route("/leaderboard/timed")
def timed_leaderboard():
    rows = []
    if not supabase:
        return render_template("timed_leaderboard.html", rows=rows)

    try:
        res = (
            supabase.table("timed_results")
            .select("user_id,score,inserted_at")
            .order("score", desc=True)
            .limit(10)
            .execute()
        )
        data = getattr(res, "data", None) or []
        user_ids = sorted({r["user_id"] for r in data if r.get("user_id") is not None})
        id_to_name = {}
        if user_ids:
            ures = supabase.table("users").select("id,username").in_("id", user_ids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}
        rows = [
            {"username": id_to_name.get(r["user_id"], "unknown"),
             "score": r["score"],
             "when": r.get("inserted_at")}
            for r in data
        ]
    except Exception:
        current_app.logger.exception("Timed leaderboard query failed")

    return render_template("timed_leaderboard.html", rows=rows)


@bp.route("/leaderboards")
def leaderboards():
    active = (request.args.get("tab") or "daily").lower()
    today_et = str(get_today_et())

    daily_rows = []
    timed_rows = []
    alltime_rows = []

    if supabase:
        try:
            # Daily (today)
            res = (supabase.table("results")
                   .select("score,user_id,cheated")
                   .eq("game_date", today_et)
                   .order("score", desc=True)
                   .limit(50)
                   .execute())
            data = getattr(res, "data", None) or []
            uids = sorted({r["user_id"] for r in data if r.get("user_id") is not None})
            id_to_name = {}
            if uids:
                ures = supabase.table("users").select("id,username").in_("id", uids).execute()
                id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}
            daily_rows = [{
                "username": id_to_name.get(r["user_id"], "unknown"),
                "score": r["score"],
                "cheated": bool(r.get("cheated"))  # <-- include cheated flag
            } for r in data]




        except Exception:
            current_app.logger.exception("leaderboards daily failed")

        try:
            # Timed Top 10
            tres = (supabase.table("timed_results")
                    .select("user_id,score,inserted_at")
                    .order("score", desc=True)
                    .limit(10)
                    .execute())
            tdata = getattr(tres, "data", None) or []
            tuids = sorted({r["user_id"] for r in tdata if r.get("user_id") is not None})
            t_id_to_name = {}
            if tuids:
                tures = supabase.table("users").select("id,username").in_("id", tuids).execute()
                t_id_to_name = {u["id"]: u["username"] for u in (getattr(tures, "data", None) or [])}
            timed_rows = [{"username": t_id_to_name.get(r["user_id"], "unknown"),
                           "score": r["score"],
                           "when": r.get("inserted_at")} for r in tdata]
        except Exception:
            current_app.logger.exception("leaderboards timed failed")

        try:
            # Daily all-time (sum)
            res2 = supabase.table("results").select("user_id,score").execute()
            d2 = getattr(res2, "data", None) or []
            from collections import defaultdict
            agg = defaultdict(int)
            for r in d2:
                uid = r.get("user_id")
                s = r.get("score") or 0
                if uid:
                    agg[uid] += s

            auids = list(agg.keys())

            # id -> username
            a_id_to_name = {}
            if auids:
                aures = supabase.table("users").select("id,username").in_("id", auids).execute()
                a_id_to_name = {u["id"]: u["username"] for u in (getattr(aures, "data", None) or [])}

            # id -> current_streak (mirror the logic used in /leaderboard/all-time)
            id_to_streak = {}
            if auids:
                try:
                    sres = (
                        supabase.table("streaks")
                        .select("user_id,current_streak")
                        .in_("user_id", auids)
                        .execute()
                    )
                    id_to_streak = {s["user_id"]: int(s.get("current_streak") or 0)
                                    for s in (getattr(sres, "data", None) or [])}
                except Exception:
                    current_app.logger.exception("leaderboards all-time streaks fetch failed")

            alltime_rows = sorted(
                [{
                    "username": a_id_to_name.get(uid, "unknown"),
                    "total_score": total,
                    "streak": id_to_streak.get(uid, 0),
                } for uid, total in agg.items()],
                key=lambda x: x["total_score"], reverse=True
            )
        except Exception:
            current_app.logger.exception("leaderboards all-time failed")

    return render_template("leaderboards.html",
                           active=active,
                           today_label=today_et,
                           daily_rows=daily_rows,
                           timed_rows=timed_rows,
                           alltime_rows=alltime_rows)




@bp.route("/practice", methods=["GET"])
def practice():
    # Require username first
    if not session.get("username"):
        flash("Create a display name first.")
        return redirect(url_for("main.landing"))

    # Start a new practice run when asked (?new=1) or none exists
    force_new = (request.args.get("new") == "1")
    pid = session.get("practice_pid")
    if force_new or not pid:
        pid = _get_random_player_id()
        if pid is None:
            # JSON fallback
            import random
            p = random.choice(PLAYERS or [])
            session["practice_pid"] = None
            session["practice_json_slug"] = p.get("player_slug")
            bundle = {
                "id": None,
                "full_name": p.get("full_name"),
                "player_slug": p.get("player_slug"),
                "position": p.get("position"),
                "college": (p.get("college") or None),
                "stat_lines": stat_lines_for_player(p),
            }
        else:
            session["practice_pid"] = pid
            session.pop("practice_json_slug", None)
            bundle = _db_player_bundle_for_id(pid)

        # reset per-run state
        session["practice_revealed"] = 1
        session["practice_hints_used"] = []
        session.pop("practice_suggestions", None)
    else:
        # Re-hydrate existing bundle
        json_slug = session.get("practice_json_slug")
        if json_slug:
            p = next((x for x in (PLAYERS or []) if x.get("player_slug") == json_slug), None)
            if not p:
                return redirect(url_for("main.practice", new=1))
            bundle = {
                "id": None,
                "full_name": p.get("full_name"),
                "player_slug": p.get("player_slug"),
                "position": p.get("position"),
                "college": (p.get("college") or None),
                "stat_lines": stat_lines_for_player(p),
            }
        else:
            try:
                bundle = _db_player_bundle_for_id(pid)
            except Exception:
                current_app.logger.exception("practice: failed to rebuild bundle; starting new")
                return redirect(url_for("main.practice", new=1))

    # Clamp revealed
    lines = bundle.get("stat_lines") or []
    revealed = int(session.get("practice_revealed", 1) or 1)
    revealed = max(1, min(revealed, len(lines) or 1))

    # Hints
    hints_used = [str(h).lower() for h in session.get("practice_hints_used", [])]
    used = set(hints_used)
    HIDE = {"record", "conference"}  # <--- toggle anything here
    available_hints = [h for h in HINT_COSTS.keys() if h not in hints_used and h not in HIDE]
    if "team" in used:
        available_hints = [h for h in available_hints if h not in ("conference", "division")]
    elif "division" in used:
        available_hints = [h for h in available_hints if h != "conference"]

    # Suggestions
    suggestions = session.get("practice_suggestions", [])

    # Score (practice only; not saved)
    live_score = compute_total_score(revealed, hints_used)

    return render_template(
        "play.html",  # reuse template
        mode="practice",
        username=session.get("username"),
        username_locked=True,
        already_played_today=False,  # never blocks in practice
        player_position=bundle.get("position", ""),
        stat_lines=lines[:revealed],
        revealed=revealed,
        hints_for_lines=[hints_resolve(bundle, i) for i in range(revealed)],
        hints_used=hints_used,
        available_hints=available_hints,
        hint_costs=HINT_COSTS,
        suggestions=suggestions,
        live_score=live_score,
        start_score=START_SCORE,
        penalty_per_reveal=PENALTY_PER_REVEAL,
        # point the forms to practice endpoints
        guess_action=url_for("main.practice_guess"),
        hint_action=url_for("main.practice_hint"),
    )

@bp.post("/practice/guess")
def practice_guess():
    if not session.get("username"):
        flash("Create a display name first.")
        return redirect(url_for("main.landing"))

    user_guess_raw = (request.form.get("guess") or "").strip()
    revealed = int(request.form.get("revealed", 1) or 1)
    from_suggestion = (request.form.get("from_suggestion") == "1")

    # Rebuild current bundle
    json_slug = session.get("practice_json_slug")
    pid = session.get("practice_pid")
    if json_slug:
        p = next((x for x in (PLAYERS or []) if x.get("player_slug") == json_slug), None)
        if not p:
            return redirect(url_for("main.practice", new=1))
        bundle = {
            "id": None,
            "full_name": p.get("full_name"),
            "player_slug": p.get("player_slug"),
            "position": p.get("position"),
            "college": (p.get("college") or None),
            "stat_lines": stat_lines_for_player(p),
        }
    else:
        if not pid:
            return redirect(url_for("main.practice", new=1))
        bundle = _db_player_bundle_for_id(pid)

    # Check correctness (same logic as daily)
    candidates = {
        bundle["full_name"].lower(),
        bundle["player_slug"].replace("-", " ").lower(),
    }
    correct_via_typo = is_typo_match(user_guess_raw, bundle["full_name"])
    is_correct = (user_guess_raw.lower() in candidates) or correct_via_typo

    # Correct -> show practice result (no DB writes)
    if is_correct:
        # compute score for fun
        hints_used = session.get("practice_hints_used", [])
        score = compute_total_score(revealed, hints_used)

        # clear current run
        for k in ("practice_revealed", "practice_hints_used", "practice_suggestions", "practice_pid", "practice_json_slug"):
            session.pop(k, None)

        return render_template(
            "practice_result.html",
            success=True,
            answer=bundle["full_name"],
            score=score,
        )

    # Wrong -> suggestions flow (no attempt count if showing suggestions)
    population = _get_suggest_population()
    same_pos = [(n, pos) for (n, pos) in population if pos == bundle.get("position")]
    pool = same_pos if same_pos else population
    suggestions = suggest_players(user_guess_raw, pool, limit=4, min_score=80)

    if suggestions and not from_suggestion:
        session["practice_suggestions"] = suggestions
        flash("Not quite — did you mean one of these? (This try didn’t count.)")
        return redirect(url_for("main.practice"))

    if suggestions:
        session["practice_suggestions"] = suggestions

    revealed = min(int(session.get("practice_revealed", 1) or 1) + 1, 5)
    session["practice_revealed"] = revealed
    flash("Nope! Another season line revealed.")
    return redirect(url_for("main.practice"))


@bp.post("/practice/hint")
def practice_hint():
    if not session.get("username"):
        flash("Create a display name first.")
        return redirect(url_for("main.landing"))

    revealed = int(request.form.get("revealed", 1) or 1)
    session["practice_revealed"] = revealed

    kind = (request.form.get("hint_type") or "").strip().lower()
    if not kind or kind not in HINT_COSTS:
        flash("Unknown hint.")
        return redirect(url_for("main.practice"))

    used = {str(h).lower() for h in session.get("practice_hints_used", [])}

    # Guard: if Team already bought, ignore Conference/Division charges
    if "team" in used and kind in {"conference", "division"}:
        return redirect(url_for("main.practice"))

    if kind not in used:
        used.add(kind)
        session["practice_hints_used"] = list(used)

    return redirect(url_for("main.practice"))


@bp.post("/practice/giveup")
def practice_giveup():
    if not session.get("username"):
        return redirect(url_for("main.landing"))

    # Determine current answer to show
    json_slug = session.get("practice_json_slug")
    pid = session.get("practice_pid")
    answer = "Unknown"
    try:
        if json_slug:
            p = next((x for x in (PLAYERS or []) if x.get("player_slug") == json_slug), None)
            if p:
                answer = p.get("full_name", "Unknown")
        elif pid:
            meta = (
                supabase.table("v_players_eligible")
                .select("full_name")
                .eq("id", pid)
                .limit(1)
                .execute()
            )
            row = (getattr(meta, "data", None) or [{}])[0]
            answer = row.get("full_name", "Unknown")
    except Exception:
        pass

    # Clear current run
    for k in ("practice_revealed", "practice_hints_used", "practice_suggestions", "practice_pid", "practice_json_slug"):
        session.pop(k, None)

    return render_template("practice_result.html", success=False, answer=answer, score=0)


@bp.post("/guess")
def guess():
    username = session.get("username")
    if not username:
        flash("Enter a username first.")
        return redirect(url_for("main.landing"))

    # If today's game already completed for this username: block further guesses
    if has_played_today(username):
        flash("You've already completed today's game. Come back tomorrow!")
        return redirect(url_for("main.play"))

    user_guess_raw = (request.form.get("guess") or "").strip()
    user_guess = user_guess_raw.lower()
    revealed = int(request.form.get("revealed", 1) or 1)
    from_suggestion = (request.form.get("from_suggestion") == "1")

    bundle = get_today_player_bundle()
    lines = bundle.get("stat_lines") or []

    # Clamp reveal count to available lines (and a hard cap of 5)
    max_reveal = min(5, len(lines) if lines else 1)
    revealed = max(1, min(revealed, max_reveal))

    # Exact/slug candidates
    candidates = {
        (bundle.get("full_name") or "").lower(),
        (bundle.get("player_slug") or "").replace("-", " ").lower(),
    }

    # Typo forgiveness
    correct_via_typo = is_typo_match(user_guess_raw, bundle.get("full_name") or "")
    is_correct = (user_guess in candidates) or correct_via_typo

    # ----- Correct -> count & finish ------------------------------------------
    if is_correct:
        hints_used = session.get("hints_used", [])
        score = compute_total_score(revealed, hints_used)
        today_str = str(get_today_et())

        # Persist to DB (results + streak update + cheat detection)
        if supabase and bundle.get("id"):
            try:
                user_id = _get_or_create_user_id_ci(username)
                if user_id is not None:
                    supabase.table("results").upsert(
                        {
                            "game_date": today_str,
                            "user_id": int(user_id),
                            "revealed": int(revealed),
                            "score": int(score),
                            "correct_attempts": int(revealed),
                            "cheated": bool(session.get("cheated_today", False)),
                        },
                        on_conflict="game_date,user_id",
                    ).execute()
                    # Update streaks (safe-guarded)
                    try:
                        _update_streaks_after_win(user_id)
                    except Exception:
                        current_app.logger.exception("streaks update failed")
            except Exception:
                current_app.logger.exception("Supabase save failed during /guess; continuing without DB.")

        # Mark as solved in session (helps local mode)
        session["solved_today"] = True
        # Reset per-game UI bits
        session["revealed"] = 1
        session["hints_used"] = []
        session.pop("suggestions", None)

        return render_template(
            "result.html",
            score=score,
            answer=bundle["full_name"],
            cheated=bool(session.get("cheated_today"))  # <-- pass flag to template
        )


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
    revealed = min(revealed + 1, max_reveal)
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
        # Today's results, highest score first
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

        user_ids = sorted({r["user_id"] for r in data if r.get("user_id") is not None})

        # id -> username
        id_to_name = {}
        if user_ids:
            ures = supabase.table("users").select("id, username").in_("id", user_ids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}

        # id -> current_streak
        id_to_streak = {}
        if user_ids:
            try:
                sres = (
                    supabase.table("streaks")
                    .select("user_id,current_streak")
                    .in_("user_id", user_ids)
                    .execute()
                )
                id_to_streak = {s["user_id"]: int(s.get("current_streak") or 0)
                                for s in (getattr(sres, "data", None) or [])}
            except Exception:
                current_app.logger.exception("Leaderboard streaks fetch failed")
                id_to_streak = {}

        rows = [
            {
                "username": id_to_name.get(r["user_id"], "unknown"),
                "score": r["score"],
                "streak": id_to_streak.get(r["user_id"], 0),
            }
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
        # Sum scores per user
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

        # id -> username
        id_to_name = {}
        if user_ids:
            ures = supabase.table("users").select("id, username").in_("id", user_ids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}

        # id -> current_streak
        id_to_streak = {}
        if user_ids:
            try:
                sres = (
                    supabase.table("streaks")
                    .select("user_id,current_streak")
                    .in_("user_id", user_ids)
                    .execute()
                )
                id_to_streak = {s["user_id"]: int(s.get("current_streak") or 0)
                                for s in (getattr(sres, "data", None) or [])}
            except Exception:
                current_app.logger.exception("All-time streaks fetch failed")

        rows = sorted(
            [
                {
                    "username": id_to_name.get(uid, "unknown"),
                    "total_score": total,
                    "streak": id_to_streak.get(uid, 0),
                }
                for uid, total in agg.items()
            ],
            key=lambda x: x["total_score"],
            reverse=True,
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

@bp.get("/debug-timed-save")
def debug_timed_save():
    if not supabase:
        return {"ok": False, "err": "supabase not configured"}
    try:
        uname = "timed-probe-user"
        uid = _get_or_create_user_id_ci(uname)
        if not uid:
            return {"ok": False, "err": "failed to ensure user"}

        saved = _timed_maybe_save_top10(7, uid)

        res = (
            supabase.table("timed_results")
            .select("user_id,score,inserted_at")
            .order("score", desc=True)
            .limit(10)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        uids = sorted({r["user_id"] for r in rows if r.get("user_id") is not None})
        id_to_name = {}
        if uids:
            ures = supabase.table("users").select("id,username").in_("id", uids).execute()
            id_to_name = {u["id"]: u["username"] for u in (getattr(ures, "data", None) or [])}
        top10 = [{"username": id_to_name.get(r["user_id"], "unknown"),
                  "score": r["score"], "when": r.get("inserted_at")} for r in rows]
        return {"ok": True, "saved": saved, "top10": top10}
    except Exception as e:
        current_app.logger.exception("debug-timed-save failed")
        return {"ok": False, "err": str(e)}


# Very primative and lose cheat detection, if user leaves tab during daily game, will be flagged
@bp.post("/cheat-mark")
def cheat_mark():
    # Only mark for DAILY games; ignore practice/timed
    m = request.form.get("mode") or request.args.get("mode") or ""
    if m.lower() == "daily":
        session["cheated_today"] = True
    return jsonify(ok=True)
