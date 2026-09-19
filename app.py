import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime
import random
import functools
import math
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
def generate_bracket_rounds(seeded_players):
    """
    Pairs #1 vs Last, #2 vs 2nd-to-last with power-of-2 standard single elimination.
    If player count is not a power of 2 (e.g. 3, 5, 6), top seeds receive a BYE.
    """
    n = len(seeded_players)
    if n < 2:
        return []

    # Next power of 2
    bracket_size = 1 << (n - 1).bit_length()
    byes = bracket_size - n

    # Standard bracket seed pairing for round 1
    # For size 4: (1,4), (2,3)
    # For size 8: (1,8), (4,5), (2,7), (3,6)
    def get_seed_order(size):
        if size == 2:
            return [1, 2]
        prev = get_seed_order(size // 2)
        res = []
        for s in prev:
            res.extend([s, size + 1 - s])
        return res

    seed_order = get_seed_order(bracket_size)
    seed_map = {p["seed"]: p for p in seeded_players}

    round_1_matches = []
    match_id = 1

    for i in range(0, len(seed_order), 2):
        s1 = seed_order[i]
        s2 = seed_order[i + 1]

        p1 = seed_map.get(s1)
        p2 = seed_map.get(s2)

        match = {
            "match_id": match_id,
            "round": 1,
            "p1": p1,
            "p2": p2,
            "winner_id": None,
            "score1": None,
            "score2": None,
            "is_bye": False
        }

        # Auto-advance BYE if a player doesn't have an opponent
        if p1 and not p2:
            match["winner_id"] = p1["id"]
            match["is_bye"] = True
        elif p2 and not p1:
            match["winner_id"] = p2["id"]
            match["is_bye"] = True

        round_1_matches.append(match)
        match_id += 1

    rounds = [round_1_matches]

    # Generate placeholder matches for subsequent rounds
    curr_matches = round_1_matches
    round_num = 2
    while len(curr_matches) > 1:
        next_round_matches = []
        for i in range(0, len(curr_matches), 2):
            next_round_matches.append({
                "match_id": match_id,
                "round": round_num,
                "p1": None,
                "p2": None,
                "winner_id": None,
                "score1": None,
                "score2": None,
                "is_bye": False
            })
            match_id += 1
        rounds.append(next_round_matches)
        curr_matches = next_round_matches
        round_num += 1

    # Propagate any initial round 1 BYEs into round 2
    for idx, r1_match in enumerate(rounds[0]):
        if r1_match["is_bye"] and r1_match["winner_id"]:
            next_m_idx = idx // 2
            is_slot_p1 = (idx % 2 == 0)
            winner = r1_match["p1"] if r1_match["p1"] and r1_match["p1"]["id"] == r1_match["winner_id"] else r1_match["p2"]
            if len(rounds) > 1:
                if is_slot_p1:
                    rounds[1][next_m_idx]["p1"] = winner
                else:
                    rounds[1][next_m_idx]["p2"] = winner

    return rounds
@app.route("/")
def index():
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    # Safely query without raising an exception if row 1 is absent
    try:
        state_res = supabase.from_("pong_night_state").select("is_active").eq("id", 1).maybe_single().execute()
        active_pong_night = state_res.data.get("is_active", False) if state_res.data else False
    except Exception:
        active_pong_night = False

    leaderboard_res = supabase.from_("leaderboard").select("*").execute()
    leaderboard = leaderboard_res.data if leaderboard_res.data else []

    games_res = supabase.from_("games").select(
        "id, score1, score2, created_at, player1:player1_id(username), player2:player2_id(username), winner:winner_id(username)"
    ).order("created_at", desc=True).limit(6).execute()
    recent_games = games_res.data if games_res.data else []

    return render_template("index.html", user=user, leaderboard=leaderboard, games=recent_games, active_pong_night=active_pong_night)


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
    if not user: return redirect(url_for("welcome"))

    if request.method == "POST":
        selected_ids = request.form.getlist("players")
        if len(selected_ids) < 2:
            flash("You must select at least 2 players to start a Pong Night!", "warning")
            return redirect(url_for("pong_night_setup"))

        players_res = supabase.from_("profiles").select("id, username").in_("id", selected_ids).execute()
        players = players_res.data
        random.shuffle(players)
        matches = []
        n = len(players)

        if n == 2:
            matches.append({"p1": players[0], "p2": players[1]})
            matches.append({"p1": players[1], "p2": players[0]})
        else:
            for i in range(n):
                matches.append({"p1": players[i], "p2": players[(i + 1) % n]})

        # NEW: Save matches to the global database instead of local session
        supabase.from_("pong_night_state").upsert({
            "id": 1,
            "is_active": True,
            "queue": matches,
            "completed": []
        }).execute()

        return redirect(url_for("pong_night_active"))

    players_res = supabase.from_("profiles").select("*").execute()
    return render_template("pong_night_setup.html", user=user, players=players_res.data)
@app.route("/dismiss-tournament-popup")
def dismiss_tournament_popup():
    session.pop("last_pool_play", None)
    return redirect(url_for("index"))

@app.route("/pong-night/end")
def pong_night_end():
    user = get_current_user()
    if not user: return redirect(url_for("welcome"))

    # Grab the completed games before wiping the database
    state = supabase.from_("pong_night_state").select("*").eq("id", 1).single().execute().data
    completed_games = state.get("completed", [])

    # Temporarily store the games in the session to trigger the popup on the dashboard
    if completed_games:
        session["last_pool_play"] = completed_games

    # Wipe the global state
    supabase.from_("pong_night_state").update({"is_active": False, "queue": [], "completed": []}).eq("id", 1).execute()
    flash("Pong Night has been concluded!", "success")
    return redirect(url_for("index"))


@app.route("/pong-night/active")
def pong_night_active():
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    state_res = supabase.from_("pong_night_state").select("*").eq("id", 1).maybe_single().execute()
    state = state_res.data if state_res else None

    matches = state["queue"] if state and state.get("queue") else []
    completed = state["completed"] if state and state.get("completed") else []

    return render_template("pong_night_active.html", user=user, matches=matches, completed=completed)


@app.route("/pong-night/play/<int:match_idx>")
def pong_night_play(match_idx):
    user = get_current_user()
    if not user:
        return redirect(url_for("welcome"))

    state_res = supabase.from_("pong_night_state").select("queue").eq("id", 1).maybe_single().execute()
    state = state_res.data if state_res else None
    matches = state["queue"] if state and state.get("queue") else []

    if match_idx < 0 or match_idx >= len(matches):
        return redirect(url_for("pong_night_active"))

    return render_template("pong_night_play.html", user=user, match=matches[match_idx], match_idx=match_idx)
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
    if not user:
        return redirect(url_for("login"))

    game = supabase.from_("games").select("*").eq("id", game_id).single().execute().data
    winner_id = game["winner_id"]
    loser_id = game["player1_id"] if winner_id == game["player2_id"] else game["player2_id"]

    w_stats = supabase.from_("leaderboard").select("*").eq("user_id", winner_id).single().execute().data
    l_stats = supabase.from_("leaderboard").select("*").eq("user_id", loser_id).single().execute().data

    old_elos = session.pop("old_elos", {})
    w_old_elo = old_elos.get(winner_id, w_stats["elo"] - 15)
    l_old_elo = old_elos.get(loser_id, l_stats["elo"] + 15)

    from_pong_night = request.args.get("from_pong_night")
    from_tournament_id = request.args.get("from_tournament_id")

    return render_template("game_result.html",
                           w_stats=w_stats, l_stats=l_stats,
                           w_old=w_old_elo, l_old=l_old_elo,
                           from_pong_night=from_pong_night,
                           from_tournament_id=from_tournament_id)

@app.route("/game/submit", methods=["POST"])
def game_submit():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    p1_id = request.form.get("p1_id")
    p2_id = request.form.get("p2_id")
    winner_id = request.form.get("winner_id")
    cups_won_by = int(request.form.get("cups_won_by", 0))

    pong_night_idx = request.form.get("pong_night_idx")
    tournament_id = request.form.get("tournament_id")
    round_idx = request.form.get("round_idx")
    match_idx = request.form.get("match_idx")

    if not winner_id:
        flash("You must select a winner!", "danger")
        return redirect(url_for("index"))

    # Pull decayed rating for calculation
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

    # 1. Log game attached to tournament_id if present
    game_data = {
        "player1_id": p1_id,
        "player2_id": p2_id,
        "score1": score1,
        "score2": score2,
        "winner_id": winner_id,
        "tournament_id": int(tournament_id) if tournament_id else None
    }
    res = supabase.from_("games").insert(game_data).execute()
    new_game_id = res.data[0]["id"]

    # 2. Update player ratings
    supabase.from_("profiles").update({"elo": new_r1}).eq("id", p1_id).execute()
    supabase.from_("profiles").update({"elo": new_r2}).eq("id", p2_id).execute()
    session["old_elos"] = {p1_id: r1, p2_id: r2}

    # 3. Bracket Progression Handling
    if tournament_id and round_idx is not None and match_idx is not None:
        t_id = int(tournament_id)
        r_i = int(round_idx)
        m_i = int(match_idx)

        t_res = supabase.from_("tournaments").select("bracket").eq("id", t_id).single().execute()
        bracket = t_res.data["bracket"]

        match = bracket[r_i][m_i]
        match["winner_id"] = winner_id
        match["score1"] = score1
        match["score2"] = score2

        winner_obj = match["p1"] if match["p1"]["id"] == winner_id else match["p2"]

        # Advance to the next round if not the finals
        if r_i + 1 < len(bracket):
            next_m_idx = m_i // 2
            is_slot_p1 = (m_i % 2 == 0)
            if is_slot_p1:
                bracket[r_i + 1][next_m_idx]["p1"] = winner_obj
            else:
                bracket[r_i + 1][next_m_idx]["p2"] = winner_obj

            supabase.from_("tournaments").update({"bracket": bracket}).eq("id", t_id).execute()
        else:
            # Tournament Final Match Complete: Declare Champion
            supabase.from_("tournaments").update({
                "bracket": bracket,
                "winner_id": winner_id,
                "status": "COMPLETED"
            }).eq("id", t_id).execute()

        return redirect(url_for("game_result", game_id=new_game_id, from_tournament_id=t_id))

    # Pong Night queue progression
    if pong_night_idx is not None:
        idx = int(pong_night_idx)
        state = supabase.from_("pong_night_state").select("*").eq("id", 1).single().execute().data
        queue = state.get("queue", [])
        completed = state.get("completed", [])

        if 0 <= idx < len(queue):
            finished_match = queue.pop(idx)
            finished_match["score1"] = score1
            finished_match["score2"] = score2
            finished_match["winner_id"] = winner_id
            completed.append(finished_match)

            supabase.from_("pong_night_state").update({
                "queue": queue,
                "completed": completed
            }).eq("id", 1).execute()

        return redirect(url_for("game_result", game_id=new_game_id, from_pong_night="1"))

    return redirect(url_for("game_result", game_id=new_game_id))

@app.route("/tournaments", methods=["GET"])
def tournaments():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    # Fetch tournaments & games attached to them
    tournaments_res = supabase.from_("tournaments").select(
        "*, games(*, player1:player1_id(username), player2:player2_id(username))").order("created_at", desc=True).execute()
    all_tournaments = tournaments_res.data or []

    return render_template("tournaments.html", user=user, tournaments=all_tournaments)
@app.route("/tournament/<int:tournament_id>/bracket")
def tournament_bracket(tournament_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    t_res = supabase.from_("tournaments").select("*, winner:winner_id(username)").eq("id", tournament_id).single().execute()
    tournament = t_res.data

    return render_template("tournament_bracket.html", user=user, tournament=tournament)
@app.route("/tournament/setup", methods=["GET", "POST"])
def tournament_setup():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    all_players = supabase.from_("profiles").select("*").execute().data

    if request.method == "POST":
        t_name = request.form.get("name")
        mode = request.form.get("tourney_mode")
        pool_games = session.get("last_pool_play", [])

        pool_player_ids = set()
        for g in pool_games:
            pool_player_ids.add(g["p1"]["id"])
            pool_player_ids.add(g["p2"]["id"])
        pool_player_ids = list(pool_player_ids)

        selected_ids = []
        use_pool_stats = False

        if mode in ["all_pool", "custom_size"]:
            selected_ids = pool_player_ids
            use_pool_stats = True
        elif mode == "custom_players":
            selected_ids = request.form.getlist("selected_players")
            use_pool_stats = False

        if not selected_ids:
            flash("No players selected for the tournament!", "danger")
            return redirect(url_for("tournament_setup"))

        stats_res = supabase.from_("leaderboard").select("*").in_("user_id", selected_ids).execute()
        stats = {row["user_id"]: row for row in stats_res.data}

        pool_stats = {uid: {"wins": 0, "games": 0, "h2h": {}} for uid in selected_ids}
        if use_pool_stats and pool_games:
            for g in pool_games:
                p1, p2, w = g["p1"]["id"], g["p2"]["id"], g["winner_id"]
                if p1 in pool_stats: pool_stats[p1]["games"] += 1
                if p2 in pool_stats: pool_stats[p2]["games"] += 1
                if w in pool_stats:
                    pool_stats[w]["wins"] += 1
                    loser = p2 if w == p1 else p1
                    pool_stats[w]["h2h"][loser] = pool_stats[w]["h2h"].get(loser, 0) + 1

        players = []
        for uid in selected_ids:
            p_stat = stats.get(uid, {})
            players.append({
                "id": uid,
                "username": p_stat.get("username", "Unknown"),
                "elo": p_stat.get("elo", 1200),
                "pool_win_pct": (pool_stats[uid]["wins"] / max(1, pool_stats[uid]["games"])) if use_pool_stats else 0,
                "overall_wl": p_stat.get("win_rate", 0),
                "h2h": pool_stats[uid]["h2h"] if use_pool_stats else {}
            })

        players.sort(key=lambda x: x["elo"], reverse=True)
        for i, p in enumerate(players):
            p["elo_rank"] = i + 1

        if use_pool_stats:
            players.sort(key=lambda x: x["pool_win_pct"], reverse=True)
            for i, p in enumerate(players):
                p["pool_rank"] = i + 1
            for p in players:
                p["score"] = (p["elo_rank"] + p["pool_rank"]) / 2.0
        else:
            for p in players:
                p["score"] = p["elo_rank"]

        def compare(a, b):
            if a["score"] != b["score"]:
                return -1 if a["score"] < b["score"] else 1
            if use_pool_stats:
                a_beat_b = a["h2h"].get(b["id"], 0)
                b_beat_a = b["h2h"].get(a["id"], 0)
                if a_beat_b != b_beat_a:
                    return -1 if a_beat_b > b_beat_a else 1
            if a["overall_wl"] != b["overall_wl"]:
                return -1 if a["overall_wl"] > b["overall_wl"] else 1
            return -1 if a["id"] < b["id"] else 1

        players.sort(key=functools.cmp_to_key(compare))

        if mode == "custom_size":
            target_size = int(request.form.get("target_size", len(players)))
            players = players[:target_size]

        for i, p in enumerate(players):
            p["seed"] = i + 1

        # Generate the interactive single-elimination bracket
        bracket = generate_bracket_rounds(players)

        insert_res = supabase.from_("tournaments").insert({
            "name": t_name,
            "seeds": players,
            "bracket": bracket,
            "status": "ACTIVE"
        }).execute()

        new_t_id = insert_res.data[0]["id"]
        session.pop("last_pool_play", None)
        flash(f"Tournament Generated with {len(players)} seeds!", "success")
        return redirect(url_for("tournament_bracket", tournament_id=new_t_id))

    return render_template("tournament_setup.html", user=user, all_players=all_players, has_pool=bool(session.get("last_pool_play")))
@app.route("/tournament/<int:tournament_id>/play/<int:round_idx>/<int:match_idx>")
def tournament_play(tournament_id, round_idx, match_idx):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    t_res = supabase.from_("tournaments").select("*").eq("id", tournament_id).single().execute()
    tournament = t_res.data
    bracket = tournament.get("bracket", [])

    match = bracket[round_idx][match_idx]
    if not match["p1"] or not match["p2"] or match["winner_id"]:
        flash("This match cannot be played currently.", "warning")
        return redirect(url_for("tournament_bracket", tournament_id=tournament_id))

    return render_template("tournament_play.html", user=user, tournament=tournament, match=match, round_idx=round_idx, match_idx=match_idx)

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