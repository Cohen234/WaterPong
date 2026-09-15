import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback-secret")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY
)


# --- Helpers ---
def get_current_user():
    return session.get("user")


# --- Routes ---

@app.route("/")
def index():
    user = get_current_user()

    # Redirect unauthenticated users to the login page
    if not user:
        return redirect(url_for("login"))

    # Fetch live leaderboard view
    leaderboard_res = supabase.from_("leaderboard").select("*").execute()
    leaderboard = leaderboard_res.data if leaderboard_res.data else []

    # Fetch recent games with player usernames
    games_res = supabase.from_("games").select(
        "id, score1, score2, created_at, player1:player1_id(username), player2:player2_id(username), winner:winner_id(username)"
    ).order("created_at", desc=True).limit(10).execute()

    recent_games = games_res.data if games_res.data else []

    return render_template("index.html", user=user, leaderboard=leaderboard, games=recent_games)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        username = request.form.get("username").strip()

        try:
            # 1. Sign up user in Supabase Auth
            auth_response = supabase.auth.sign_up({"email": email, "password": password})
            user_id = auth_response.user.id

            # 2. Add profile record
            supabase.from_("profiles").insert({"id": user_id, "username": username}).execute()

            flash("Registration successful! Please log in.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            flash(f"Error during registration: {str(e)}", "danger")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        try:
            auth_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
            user_id = auth_response.user.id

            # Fetch username
            profile = supabase.from_("profiles").select("*").eq("id", user_id).single().execute()

            session["user"] = {
                "id": user_id,
                "email": email,
                "username": profile.data["username"]
            }
            return redirect(url_for("index"))
        except Exception as e:
            flash(f"Invalid credentials: {str(e)}", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/game/setup", methods=["GET", "POST"])
def game_setup():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        p1_id = request.form.get("player1_id")
        p2_id = request.form.get("player2_id")

        if not p1_id or not p2_id:
            flash("You must select two players!", "warning")
            return redirect(url_for("game_setup"))

        if p1_id == p2_id:
            flash("A player cannot play against themselves!", "warning")
            return redirect(url_for("game_setup"))

        # Redirect to the active game screen with both player IDs
        return redirect(url_for("game_active", p1_id=p1_id, p2_id=p2_id))

    # Fetch ALL players for the dropdowns (no exclusions)
    players_res = supabase.from_("profiles").select("*").execute()
    players = players_res.data or []

    return render_template("game_setup.html", user=user, players=players)

@app.route("/game/active/<p1_id>/<p2_id>")
def game_active(p1_id, p2_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    # Fetch usernames to display on the clickable buttons
    p1 = supabase.from_("profiles").select("*").eq("id", p1_id).single().execute().data
    p2 = supabase.from_("profiles").select("*").eq("id", p2_id).single().execute().data

    return render_template("game_active.html", user=user, p1=p1, p2=p2)


@app.route("/game/submit", methods=["POST"])
def game_submit():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    p1_id = request.form.get("p1_id")
    p2_id = request.form.get("p2_id")
    winner_id = request.form.get("winner_id")
    cups_won_by = int(request.form.get("cups_won_by", 0))

    if not winner_id:
        flash("You must select a winner!", "danger")
        return redirect(url_for("game_active", p1_id=p1_id, p2_id=p2_id))

    # Assuming a standard 10-cup game to calculate database scores
    if winner_id == p1_id:
        score1 = 10
        score2 = max(0, 10 - cups_won_by)
    else:
        score2 = 10
        score1 = max(0, 10 - cups_won_by)

    game_data = {
        "player1_id": p1_id,
        "player2_id": p2_id,
        "score1": score1,
        "score2": score2,
        "winner_id": winner_id,
        "tournament_id": None  # Removed tournament tracking for now
    }

    supabase.from_("games").insert(game_data).execute()
    flash("Game logged successfully!", "success")
    return redirect(url_for("index"))

@app.route("/tournaments", methods=["GET", "POST"])
def tournaments():
    user = get_current_user()

    if request.method == "POST":
        if not user:
            return redirect(url_for("login"))

        t_name = request.form.get("name")
        if t_name:
            supabase.from_("tournaments").insert({"name": t_name}).execute()
            flash("Tournament created!", "success")
            return redirect(url_for("tournaments"))

    # Fetch tournaments & games attached to them
    tournaments_res = supabase.from_("tournaments").select(
        "*, games(*, player1:player1_id(username), player2:player2_id(username))").order("created_at",
                                                                                         desc=True).execute()
    all_tournaments = tournaments_res.data or []

    return render_template("tournaments.html", user=user, tournaments=all_tournaments)


if __name__ == "__main__":
    app.run(debug=True)