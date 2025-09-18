import os
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from .services.daily import load_players_local, pick_player_of_day, stat_lines_for_player
from .services.scoring import compute_score
from . import supabase

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

@bp.route("/guess", methods=["POST"])
def guess():
    username = get_username()
    if not username:
        flash("Enter a username first.")
        return redirect(url_for("main.play"))

    user_guess = request.form.get("guess", "").strip().lower()

    today = date.today()
    daily_player = pick_player_of_day(today, PLAYERS)
    is_correct = user_guess in {
        daily_player["player_slug"].lower(),
        daily_player["full_name"].lower(),
    }

    # Update revealed count if wrong
    revealed = int(request.form.get("revealed", 1))
    if not is_correct:
        revealed = min(revealed + 1, 5)  # cap at 5 reveals
        session["revealed"] = revealed
        flash("Nope! Another season line revealed.")
        return redirect(url_for("main.play"))

    # Correct! compute score and record result
    session["revealed"] = 1  # reset for tomorrow
    score = compute_score(revealed)

    # If Supabase configured, upsert users/results/streaks; otherwise just show locally
    if supabase:
        # upsert user by username
        user = (
            supabase.table("users")
            .upsert({"username": username}, on_conflict="username")
            .execute()
        )
        user_id = user.data[0]["id"]

        # upsert result
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

        # naive streak seed (improve later)
        from datetime import datetime
        supabase.table("streaks").upsert(
            {
                "user_id": user_id,
                "current_streak": 1,
                "best_streak": 1,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
            on_conflict="user_id",
        ).execute()

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
