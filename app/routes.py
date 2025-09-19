import os
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from .services.daily import load_players_local, pick_player_of_day, stat_lines_for_player
from .services.scoring import compute_score
from . import supabase
from flask import current_app
from datetime import datetime as _dt, timezone as _tz


bp = Blueprint("main", __name__)

# In-memory cache for local mode
PLAYERS = load_players_local()

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

    username = get_username()

    # Determine daily player (local deterministic). If Supabase present, read from `daily_game` later.
    today = date.today()
    daily_player = pick_player_of_day(today, PLAYERS)
    stat_lines = stat_lines_for_player(daily_player)

    # Track how many lines are revealed in session
    revealed = session.get("revealed", 1)
    revealed = max(1, min(revealed, len(stat_lines)))

    return render_template(
        "play.html",
        username=username,
        player_position=daily_player["position"],
        stat_lines=stat_lines[:revealed],
        revealed=revealed,
    )

from flask import current_app  # <-- add near other imports

@bp.route("/guess", methods=["POST"])
def guess():
    username = get_username()
    if not username:
        flash("Enter a username first.")
        return redirect(url_for("main.play"))

    user_guess = request.form.get("guess", "").strip().lower()

    # Daily player (local JSON mode). If you implemented DB daily pick, you can swap this
    # to use pick_daily_from_db(str(date.today())) + fetch player name from DB.
    today = date.today()
    daily_player = pick_player_of_day(today, PLAYERS)

    is_correct = user_guess in {
        daily_player["player_slug"].lower(),
        daily_player["full_name"].lower(),
    }

    revealed = int(request.form.get("revealed", 1))
    if not is_correct:
        revealed = min(revealed + 1, 5)
        session["revealed"] = revealed
        flash("Nope! Another season line revealed.")
        return redirect(url_for("main.play"))

    # Correct path
    session["revealed"] = 1
    score = compute_score(revealed)

    # Save to Supabase if configured—but never crash the page if it fails
    if supabase:
        try:
            user_resp = (
                supabase.table("users")
                .upsert({"username": username}, on_conflict="username")
                .select("id")
                .execute()
            )
            user_id = user_resp.data["id"] if isinstance(user_resp.data, dict) else user_resp.data[0]["id"]

            supabase.table("results").upsert(
                {
                    "game_date": str(today),
                    "user_id": user_id,
                    "revealed": revealed,
                    "score": score,
                    "correct_attempts": revealed,
                },
                on_conflict="game_date,user_id",
            ).execute()

            from datetime import datetime as _dt
            supabase.table("streaks").upsert(
                {
                    "user_id": user_id,
                    "current_streak": 1,
                    "best_streak": 1,
                    "updated_at": _dt.now(_tz.utc).isoformat().replace("+00:00", "Z"),

                },
                on_conflict="user_id",
            ).execute()

        except Exception:
            current_app.logger.exception("Supabase save failed during /guess; continuing without DB.")

    return render_template("result.html", score=score, answer=daily_player["full_name"])


@bp.route("/leaderboard")
def leaderboard():
    rows = []
    if supabase:
        today = str(date.today())
        res = (
            supabase.table("results")
            .select("score, users!inner(username)")
            .eq("game_date", today)
            .order("score", desc=True)
            .limit(50)
            .execute()
        )
        for r in res.data:
            rows.append({"username": r["users"]["username"], "score": r["score"]})

    return render_template("leaderboard.html", rows=rows)
