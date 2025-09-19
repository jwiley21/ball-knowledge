import os
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
    # 1) ensure a daily_game row exists for ET date
    row = (supabase.table("daily_game")
           .select("player_id")
           .eq("game_date", today_str)
           .maybe_single()
           .execute())
    if row.data:
        pid = row.data["player_id"]
    else:
        # choose one random player and persist for today
        player = (supabase.table("players")
                  .select("id")
                  .order("random()")
                  .limit(1)
                  .single()
                  .execute())
        pid = player.data["id"]
        supabase.table("daily_game").upsert({"game_date": today_str, "player_id": pid}).execute()

    # 2) fetch meta
    player_meta = (supabase.table("players")
                   .select("id,full_name,player_slug,position")
                   .eq("id", pid)
                   .single()
                   .execute()).data

     # 3) fetch seasons → adapt to template shape expected by templates
    seasons = (supabase.table("player_seasons")
               .select("season,team,stat1_name,stat1_value,stat2_name,stat2_value,stat3_name,stat3_value")
               .eq("player_id", pid)
               .order("season")
               .execute()).data

    stat_lines = [{
        "season": r["season"], "team": r["team"],
        "stats": {
            r["stat1_name"]: r["stat1_value"],
            r["stat2_name"]: r["stat2_value"],
            r["stat3_name"]: r["stat3_value"],
        }
    } for r in seasons]

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
        except Exception:
            current_app.logger.exception("DB daily fetch failed; falling back to JSON for today.")

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



from flask import current_app  # <-- add near other imports

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
    if supabase and bundle["id"]:
        try:
            user_resp = (supabase.table("users")
                         .upsert({"username": username}, on_conflict="username")
                         .select("id")
                         .execute())
            user_id = user_resp.data["id"] if isinstance(user_resp.data, dict) else user_resp.data[0]["id"]

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
    rows = []
    if not supabase:
        return render_template("leaderboard.html", rows=rows)

    today_et = str(get_today_et())

    try:
        # Fetch scores for ET "today" (no join yet)
        res = (supabase.table("results")
               .select("score, user_id")
               .eq("game_date", today_et)
               .order("score", desc=True)
               .limit(50)
               .execute())
        data = res.data or []
        if not data:
            return render_template("leaderboard.html", rows=rows)

        # Fetch usernames in one go
        user_ids = list({r.get("user_id") for r in data if r.get("user_id")})
        id_to_name = {}
        if user_ids:
            ures = (supabase.table("users")
                    .select("id, username")
                    .in_("id", user_ids)
                    .execute())
            id_to_name = {u["id"]: u["username"] for u in (ures.data or [])}

        rows = [{"username": id_to_name.get(r["user_id"], "unknown"), "score": r["score"]} for r in data]

    except Exception:
        current_app.logger.exception("Leaderboard query failed")

    return render_template("leaderboard.html", rows=rows)


@bp.route("/health")
def health():
    return {"ok": True}

