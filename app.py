import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime
import random
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
@app.route("/welcome")
def welcome():
    # If they are already logged in, bounce them to the dashboard
    if get_current_user():
        return redirect(url_for("index"))
    return render_template("welcome.html")

@app.route("/spectator")
def spectator():
    # Create a dummy session for the spectator
    session["user"] = {
        "id": "spectator",
        "username": "Spectator",
        "role": "spectator"
    }
    flash("Entered as Spectator. You can view stats and log games for players!", "success")
    return redirect(url_for("index"))
@app.route("/")
def index():
    user = get_current_user()

    # 1. CHANGE THIS REDIRECT:
    if not user:
        return redirect(url_for("welcome"))

    # Fetch live leaderboard view
    leaderboard_res = supabase.from_("leaderboard").select("*").execute()
    leaderboard = leaderboard_res.data if leaderboard_res.data else []

    # Fetch recent games with player usernames
    games_res = supabase.from_("games").select(
        "id, score1, score2, created_at, player1:player1_id(username), player2:player2_id(username), winner:winner_id(username)"
    ).order("created_at", desc=True).limit(6).execute()

    recent_games = games_res.data if games_res.data else []

    return render_template("index.html", user=user, leaderboard=leaderboard, games=recent_games)


@app.route("/catalog")
def catalog():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    # Fetch ALL games
    games_res = supabase.from_("games").select(
        "id, score1, score2, created_at, player1:player1_id(username), player2:player2_id(username), winner:winner_id(username)"
    ).order("created_at", desc=True).execute()

    all_games = games_res.data or []

    # Group games by Date
    grouped_games = {}
    for g in all_games:
        # Supabase timestamps look like '2026-10-10T14:30:00'
        # We split at the "T" to grab just the "YYYY-MM-DD" part
        date_part = g["created_at"].split("T")[0]

        # Format to "October 10, 2026"
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        formatted_date = dt.strftime("%B %d, %Y")

        if formatted_date not in grouped_games:
            grouped_games[formatted_date] = []
        grouped_games[formatted_date].append(g)

    return render_template("catalog.html", user=user, grouped_games=grouped_games)
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


@app.route("/pong-night/setup", methods=["GET", "POST"])
def pong_night_setup():
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    if request.method == "POST":
        selected_ids = request.form.getlist("players")

        if len(selected_ids) < 2:
            flash("You must select at least 2 players to start a Pong Night!", "warning")
            return redirect(url_for("pong_night_setup"))

        # Fetch selected player details
        players_res = supabase.from_("profiles").select("id, username").in_("id", selected_ids).execute()
        players = players_res.data

        # Mathematical trick: Shuffle players and have everyone play their neighbor
        random.shuffle(players)
        matches = []
        n = len(players)

        if n == 2:
            # If only 2 players, they just play a best-of-two against each other
            matches.append({"p1": players[0], "p2": players[1]})
            matches.append({"p1": players[1], "p2": players[0]})
        else:
            # Circle logic guarantees exactly 2 games per person
            for i in range(n):
                matches.append({
                    "p1": players[i],
                    "p2": players[(i + 1) % n]
                })

        session["pong_night"] = matches
        return redirect(url_for("pong_night_active"))

    players_res = supabase.from_("profiles").select("*").execute()
    return render_template("pong_night_setup.html", user=user, players=players_res.data)
@app.route("/pong-night/active")
def pong_night_active():
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    matches = session.get("pong_night", [])
    return render_template("pong_night_active.html", user=user, matches=matches)
@app.route("/pong-night/play/<int:match_idx>")
def pong_night_play(match_idx):
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    matches = session.get("pong_night", [])
    if match_idx < 0 or match_idx >= len(matches):
        return redirect(url_for("pong_night_active"))

    match = matches[match_idx]
    return render_template("pong_night_play.html", user=user, match=match, match_idx=match_idx)
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


@app.route("/game/result/<int:game_id>")
def game_result(game_id):
    user = get_current_user()
    if not user: return redirect(url_for("login"))

    game = supabase.from_("games").select("*").eq("id", game_id).single().execute().data
    winner_id = game["winner_id"]
    loser_id = game["player1_id"] if winner_id == game["player2_id"] else game["player2_id"]

    w_stats = supabase.from_("leaderboard").select("*").eq("user_id", winner_id).single().execute().data
    l_stats = supabase.from_("leaderboard").select("*").eq("user_id", loser_id).single().execute().data

    old_elos = session.pop("old_elos", {})
    w_old_elo = old_elos.get(winner_id, w_stats["elo"] - 15)
    l_old_elo = old_elos.get(loser_id, l_stats["elo"] + 15)

    # NEW: Check the URL for the pong night flag
    from_pong_night = request.args.get("from_pong_night")

    return render_template("game_result.html",
                           w_stats=w_stats, l_stats=l_stats,
                           w_old=w_old_elo, l_old=l_old_elo,
                           from_pong_night=from_pong_night)

@app.route("/game/submit", methods=["POST"])
def game_submit():
    user = get_current_user()
    if not user: return redirect(url_for("login"))

    p1_id = request.form.get("p1_id")
    p2_id = request.form.get("p2_id")
    winner_id = request.form.get("winner_id")
    cups_won_by = int(request.form.get("cups_won_by", 0))
    pong_night_idx = request.form.get("pong_night_idx") # NEW: Check if from Pong Night

    if not winner_id:
        flash("You must select a winner!", "danger")
        return redirect(url_for("game_active", p1_id=p1_id, p2_id=p2_id))

    # Calculate Elo (Keep your existing Elo logic here...)
    p1_data = supabase.from_("leaderboard").select("elo").eq("user_id", p1_id).single().execute().data
    p2_data = supabase.from_("leaderboard").select("elo").eq("user_id", p2_id).single().execute().data
    r1, r2 = p1_data["elo"], p2_data["elo"]

    e1 = 1 / (1 + 10 ** ((r2 - r1) / 400))
    e2 = 1 / (1 + 10 ** ((r1 - r2) / 400))
    s1 = 1 if winner_id == p1_id else 0
    s2 = 1 if winner_id == p2_id else 0
    k_adj = 32 * (1 + (cups_won_by / 10))

    new_r1 = round(r1 + k_adj * (s1 - e1))
    new_r2 = round(r2 + k_adj * (s2 - e2))
    score1 = 10 if winner_id == p1_id else max(0, 10 - cups_won_by)
    score2 = 10 if winner_id == p2_id else max(0, 10 - cups_won_by)

    game_data = {
        "player1_id": p1_id, "player2_id": p2_id,
        "score1": score1, "score2": score2,
        "winner_id": winner_id, "tournament_id": None
    }
    res = supabase.from_("games").insert(game_data).execute()
    new_game_id = res.data[0]["id"]

    supabase.from_("profiles").update({"elo": new_r1}).eq("id", p1_id).execute()
    supabase.from_("profiles").update({"elo": new_r2}).eq("id", p2_id).execute()

    session["old_elos"] = {p1_id: r1, p2_id: r2}

    # NEW: If it was a Pong Night game, pop it from the session and pass a flag
    if pong_night_idx is not None:
        idx = int(pong_night_idx)
        matches = session.get("pong_night", [])
        if 0 <= idx < len(matches):
            matches.pop(idx)
            session["pong_night"] = matches
        return redirect(url_for("game_result", game_id=new_game_id, from_pong_night="1"))

    return redirect(url_for("game_result", game_id=new_game_id))

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


@app.route("/profile/<user_id>")
def profile(user_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("login"))

    # Fetch player stats from leaderboard view
    stats = supabase.from_("leaderboard").select("*").eq("user_id", user_id).single().execute().data

    # Fetch all games, grabbing the opponent IDs and the Tournament names
    games_res = supabase.from_("games").select(
        "id, score1, score2, created_at, winner_id, tournament_id, player1:player1_id(id, username), player2:player2_id(id, username), tournaments(name)"
    ).or_(f"player1_id.eq.{user_id},player2_id.eq.{user_id}").order("created_at", desc=True).execute()

    games = games_res.data or []

    # Tracking Variables
    streak_count = 0
    streak_type = None
    tournament_wins = 0
    tournament_losses = 0

    h2h = {}
    trophies = set()

    for g in games:
        # Opponent identification for Head-to-Head
        is_p1 = g["player1"]["id"] == user_id
        opponent = g["player2"] if is_p1 else g["player1"]
        opp_name = opponent["username"]

        if opp_name not in h2h:
            h2h[opp_name] = {"wins": 0, "losses": 0}

        won = (g["winner_id"] == user_id)

        # Tournament & Trophy tracking
        if g.get("tournament_id"):
            if won:
                tournament_wins += 1
                if g.get("tournaments"):
                    trophies.add(g["tournaments"]["name"])
            else:
                tournament_losses += 1

        # Head-to-Head tracking
        if won:
            h2h[opp_name]["wins"] += 1
        else:
            h2h[opp_name]["losses"] += 1

    # Streak Calculation (Only count active uninterrupted streak from most recent game)
    for g in games:
        won = (g["winner_id"] == user_id)
        if streak_type is None:
            streak_type = "W" if won else "L"

        if (won and streak_type == "W") or (not won and streak_type == "L"):
            streak_count += 1
        else:
            break  # Break loop when the streak ends

    return render_template(
        "profile.html",
        user=current_user,
        stats=stats,
        games=games[:10],  # Pass only last 10 for display
        streak=f"{streak_count}{streak_type}" if streak_count > 0 else "None",
        t_wins=tournament_wins,
        t_losses=tournament_losses,
        h2h=h2h,
        trophies=list(trophies)
    )
if __name__ == "__main__":
    app.run(debug=True)