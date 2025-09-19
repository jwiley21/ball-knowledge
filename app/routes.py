from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from .services.daily import load_players_local, pick_player_of_day, stat_lines_for_player
from .services.scoring import compute_score
from . import supabase, get_today_et
from flask import current_app
from datetime import datetime as _dt, timezone as _tz
from difflib import get_close_matches


bp = Blueprint("main", __name__)

# In-memory cache for local mode
PLAYERS = load_players_local()

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

    # 3) Fetch player meta
    meta = (
        supabase.table("players")
        .select("id,full_name,player_slug,position")
        .eq("id", pid)
        .limit(1)
        .execute()
    )
    mdata = getattr(meta, "data", None) or []
    if not mdata:
        raise RuntimeError(f"Player id {pid} not found in players table.")
    player_meta = mdata[0]

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
        "stat_lines": stat_lines,
    }


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
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username:
            session["username"] = username
            return redirect(url_for("main.play"))

    username = session.get("username")

    # unified daily bundle (DB + ET or JSON)
    bundle = get_today_player_bundle()

    # Track how many lines are revealed in session
    revealed = session.get("revealed", 1)
    revealed = max(1, min(revealed, len(bundle["stat_lines"])))

    return render_template(
        "play.html",
        username=username,
        player_position=bundle["position"],
        stat_lines=bundle["stat_lines"][:revealed],
        revealed=revealed,
    )


@bp.route("/guess", methods=["POST"])
def guess():
    username = session.get("username")
    if not username:
        flash("Enter a username first.")
        return redirect(url_for("main.play"))

    user_guess = request.form.get("guess", "").strip().lower()
    revealed = int(request.form.get("revealed", 1))

    # Same source of truth as /play (DB + ET; JSON fallback)
    bundle = get_today_player_bundle()

    candidates = {
        bundle["full_name"].lower(),
        bundle["player_slug"].replace("-", " ").lower(),
    }
    # fuzzy matching helps minor typos; tune cutoff as you like
    is_correct = (
        user_guess in candidates
        or bool(get_close_matches(user_guess, list(candidates), n=1, cutoff=0.88))
    )

    if not is_correct:
        revealed = min(revealed + 1, 5)  # cap at 5 reveals
        session["revealed"] = revealed
        flash("Nope! Another season line revealed.")
        return redirect(url_for("main.play"))

    # Correct!
    session["revealed"] = 1
    score = compute_score(revealed)
    today_et = str(get_today_et())

    # Save to Supabase if configured and we have a DB player id
    if supabase:
        try:
            # 1) Ensure user exists (Python client doesn’t support .select after .upsert)
            supabase.table("users").upsert({"username": username}, on_conflict="username").execute()

            # 2) Fetch id in a separate query
            user_row = (
                supabase.table("users")
                .select("id")
                .eq("username", username)
                .single()
                .execute()
            )
            user_id = user_row.data["id"]


            supabase.table("results").upsert(
                {
                    "game_date": today_et,            # ET date (key for leaderboard)
                    "user_id": user_id,
                    "revealed": revealed,
                    "score": score,
                    "correct_attempts": revealed,
                },
                on_conflict="game_date,user_id",
            ).execute()

            supabase.table("streaks").upsert(
                {
                    "user_id": user_id,
                    "current_streak": 1,
                    "best_streak": 1,
                    "updated_at": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                on_conflict="user_id",
            ).execute()

        except Exception:
            current_app.logger.exception("Supabase save failed during /guess; continuing without DB.")

    return render_template("result.html", score=score, answer=bundle["full_name"])



@bp.route("/leaderboard")
def leaderboard():
    # Show today's ET leaderboard and fetch rows for the same ET date we save on /guess
    rows = []

    # Compute ET "today" for querying AND a friendly string for the header
    today_obj = get_today_et()                      # datetime.date (America/New_York)
    today_et = str(today_obj)                       # "YYYY-MM-DD" for DB queries
    today_label = today_obj.strftime("%B %d, %Y")   # e.g., "September 19, 2025"

    # If Supabase isn't configured, render an empty state with the date label
    if not supabase:
        return render_template("leaderboard.html", rows=rows, today_label=today_label)

    try:
        # 1) Get today's scores (ET) without a join
        res = (
            supabase.table("results")
            .select("score, user_id")
            .eq("game_date", today_et)
            .order("score", desc=True)
            .limit(50)
            .execute()
        )
        data = res.data or []
        if not data:
            return render_template("leaderboard.html", rows=rows, today_label=today_label)

        # 2) Fetch usernames for all user_ids in one query
        user_ids = list({r.get("user_id") for r in data if r.get("user_id")})
        id_to_name = {}
        if user_ids:
            ures = (
                supabase.table("users")
                .select("id, username")
                .in_("id", user_ids)
                .execute()
            )
            id_to_name = {u["id"]: u["username"] for u in (ures.data or [])}

        # 3) Shape rows for the template
        rows = [
            {"username": id_to_name.get(r["user_id"], "unknown"), "score": r["score"]}
            for r in data
        ]

    except Exception:
        # Never crash the page—log and fall back to empty rows
        current_app.logger.exception("Leaderboard query failed")

    return render_template("leaderboard.html", rows=rows, today_label=today_label)


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
