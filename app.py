import os
import secrets
import string
from datetime import UTC, datetime
from typing import Dict, Optional

from flask import Flask, redirect, render_template, request, session, url_for, flash
from flask_socketio import SocketIO, emit, join_room
from pypdf import PdfReader
from werkzeug.middleware.proxy_fix import ProxyFix

from question_generator import generate_questions_from_text

def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-this")
app.config["MAX_CONTENT_LENGTH"] = env_int("MAX_CONTENT_LENGTH_MB", 25) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = env_bool("SESSION_COOKIE_SECURE", False)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

cors_env = os.environ.get("CORS_ALLOWED_ORIGINS", "*").strip()
cors_allowed_origins = "*" if cors_env == "*" else [o.strip() for o in cors_env.split(",") if o.strip()]

socketio = SocketIO(
    app,
    cors_allowed_origins=cors_allowed_origins,
    manage_session=False,
    async_mode="eventlet",
)

MAX_PLAYERS = 6
POINTS_CORRECT = 10
POINTS_WRONG = -5
ALLOWED_ROUNDS = {10, 15}
ALLOWED_DIFFICULTY = {"easy", "medium", "hard"}

rooms: Dict[str, Dict] = {}


def generate_room_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(chars) for _ in range(length))
        if code not in rooms:
            return code


def extract_text_from_pdfs(files) -> str:
    parts = []
    for file in files:
        if not file or not file.filename.lower().endswith(".pdf"):
            continue
        try:
            reader = PdfReader(file)
            for page in reader.pages:
                parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def get_room(code: str) -> Optional[Dict]:
    return rooms.get(code.upper())


def get_player(room: Dict, player_id: str) -> Optional[Dict]:
    return room["players"].get(player_id)


def room_public_state(room: Dict) -> Dict:
    players = list(room["players"].values())
    sorted_players = sorted(players, key=lambda p: p["score"], reverse=True)
    return {
        "code": room["code"],
        "leader_id": room["leader_id"],
        "status": room["status"],
        "difficulty": room["difficulty"],
        "rounds": room["rounds"],
        "current_round": room["current_round"],
        "players": [
            {
                "id": p["id"],
                "name": p["name"],
                "score": p["score"],
                "connected": p["connected"],
            }
            for p in sorted_players
        ],
    }


def current_question_payload(room: Dict) -> Optional[Dict]:
    if room["status"] != "in_progress":
        return None
    idx = room["current_round"] - 1
    if idx < 0 or idx >= len(room["questions"]):
        return None
    q = room["questions"][idx]
    return {
        "round": room["current_round"],
        "total_rounds": room["rounds"],
        "id": q["id"],
        "question": q["question"],
        "options": q["options"],
    }


def emit_room_state(code: str):
    room = rooms[code]
    socketio.emit("room_state", room_public_state(room), to=code)


def active_player_ids(room: Dict):
    return [p["id"] for p in room["players"].values() if p["connected"]]


def finalize_round(room: Dict):
    idx = room["current_round"] - 1
    question = room["questions"][idx]
    correct_index = question["correct_index"]

    details = []
    for player in room["players"].values():
        answer = room["answers_current"].get(player["id"])
        if answer is None:
            result = "no_answer"
            delta = 0
        elif answer == correct_index:
            result = "correct"
            delta = POINTS_CORRECT
        else:
            result = "wrong"
            delta = POINTS_WRONG

        player["score"] += delta
        details.append(
            {
                "player_id": player["id"],
                "name": player["name"],
                "answer_index": answer,
                "answer_text": question["options"][answer] if answer is not None else None,
                "result": result,
                "delta": delta,
                "score": player["score"],
            }
        )

    details.sort(key=lambda x: x["score"], reverse=True)
    round_report = {
        "round": room["current_round"],
        "correct_index": correct_index,
        "correct_text": question["options"][correct_index],
        "details": details,
    }
    room["round_reports"].append(round_report)
    room["answers_current"] = {}
    room["awaiting_next"] = True

    socketio.emit("round_result", round_report, to=room["code"])
    emit_room_state(room["code"])


def maybe_finalize_if_all_answered(room: Dict):
    if room["awaiting_next"] or room["status"] != "in_progress":
        return
    required = set(active_player_ids(room))
    answered = set(room["answers_current"].keys())
    if required and required.issubset(answered):
        finalize_round(room)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/create-room", methods=["POST"])
def create_room():
    name = (request.form.get("name") or "").strip()
    difficulty = (request.form.get("difficulty") or "easy").lower().strip()
    rounds = int(request.form.get("rounds") or 10)
    files = request.files.getlist("pdfs")

    if not name:
        flash("Name is required.")
        return redirect(url_for("index"))
    if difficulty not in ALLOWED_DIFFICULTY:
        flash("Difficulty must be easy, medium, or hard.")
        return redirect(url_for("index"))
    if rounds not in ALLOWED_ROUNDS:
        flash("Rounds must be 10 or 15.")
        return redirect(url_for("index"))
    if not files:
        flash("Upload at least one PDF.")
        return redirect(url_for("index"))

    text = extract_text_from_pdfs(files)
    questions = generate_questions_from_text(text, rounds, difficulty)

    code = generate_room_code()
    player_id = secrets.token_hex(8)
    room = {
        "code": code,
        "leader_id": player_id,
        "status": "waiting",
        "difficulty": difficulty,
        "rounds": rounds,
        "created_at": datetime.now(UTC).isoformat(),
        "questions": questions,
        "current_round": 0,
        "players": {
            player_id: {
                "id": player_id,
                "name": name,
                "score": 0,
                "connected": False,
                "sid": None,
            }
        },
        "answers_current": {},
        "round_reports": [],
        "awaiting_next": False,
    }
    rooms[code] = room

    session["room_code"] = code
    session["player_id"] = player_id
    return redirect(url_for("room_page", code=code))


@app.route("/join-room", methods=["POST"])
def join_room_http():
    name = (request.form.get("name") or "").strip()
    code = (request.form.get("room_code") or "").upper().strip()

    room = get_room(code)
    if not name:
        flash("Name is required.")
        return redirect(url_for("index"))
    if not room:
        flash("Room not found.")
        return redirect(url_for("index"))
    if len(room["players"]) >= MAX_PLAYERS:
        flash("Room is full.")
        return redirect(url_for("index"))
    if room["status"] != "waiting":
        flash("Game already started for this room.")
        return redirect(url_for("index"))

    player_id = secrets.token_hex(8)
    room["players"][player_id] = {
        "id": player_id,
        "name": name,
        "score": 0,
        "connected": False,
        "sid": None,
    }

    session["room_code"] = code
    session["player_id"] = player_id
    return redirect(url_for("room_page", code=code))


@app.route("/room/<code>")
def room_page(code):
    code = code.upper()
    room = get_room(code)
    player_id = session.get("player_id")
    if not room or not player_id or player_id not in room["players"]:
        flash("Join the room first.")
        return redirect(url_for("index"))
    return render_template("room.html", room_code=code)


@socketio.on("connect")
def on_connect():
    code = (session.get("room_code") or "").upper()
    player_id = session.get("player_id")
    room = get_room(code)
    if not code or not player_id or not room or player_id not in room["players"]:
        return False

    player = room["players"][player_id]
    player["connected"] = True
    player["sid"] = request.sid

    join_room(code)
    emit("joined", {"player_id": player_id, "room_code": code, "player_name": player["name"]})
    emit_room_state(code)

    q = current_question_payload(room)
    if q and not room["awaiting_next"]:
        emit("question", q)
    if room["awaiting_next"] and room["round_reports"]:
        emit("round_result", room["round_reports"][-1])


@socketio.on("disconnect")
def on_disconnect():
    code = (session.get("room_code") or "").upper()
    player_id = session.get("player_id")
    room = get_room(code)
    if room and player_id in room["players"]:
        room["players"][player_id]["connected"] = False
        room["players"][player_id]["sid"] = None
        emit_room_state(code)


@socketio.on("start_game")
def on_start_game():
    code = (session.get("room_code") or "").upper()
    player_id = session.get("player_id")
    room = get_room(code)
    if not room or player_id != room["leader_id"]:
        return
    if room["status"] != "waiting":
        return
    if len(room["players"]) < 2:
        emit("error_msg", {"message": "At least 2 players are needed to start."})
        return

    room["status"] = "in_progress"
    room["current_round"] = 1
    room["awaiting_next"] = False
    room["answers_current"] = {}

    emit_room_state(code)
    emit("question", current_question_payload(room), to=code)


@socketio.on("submit_answer")
def on_submit_answer(payload):
    code = (session.get("room_code") or "").upper()
    player_id = session.get("player_id")
    room = get_room(code)
    if not room or room["status"] != "in_progress" or room["awaiting_next"]:
        return
    if player_id not in room["players"]:
        return

    choice = payload.get("choice") if isinstance(payload, dict) else None
    if not isinstance(choice, int):
        return

    q = room["questions"][room["current_round"] - 1]
    if choice < 0 or choice >= len(q["options"]):
        return

    if player_id in room["answers_current"]:
        return

    room["answers_current"][player_id] = choice
    emit("answer_received", {"player_id": player_id})
    maybe_finalize_if_all_answered(room)


@socketio.on("next_round")
def on_next_round():
    code = (session.get("room_code") or "").upper()
    player_id = session.get("player_id")
    room = get_room(code)
    if not room or player_id != room["leader_id"] or room["status"] != "in_progress":
        return
    if not room["awaiting_next"]:
        return

    if room["current_round"] >= room["rounds"]:
        room["status"] = "finished"
        standings = sorted(room["players"].values(), key=lambda p: p["score"], reverse=True)
        winner = standings[0]["name"] if standings else None
        socketio.emit(
            "game_over",
            {
                "winner": winner,
                "standings": [
                    {"name": p["name"], "score": p["score"], "connected": p["connected"]}
                    for p in standings
                ],
            },
            to=code,
        )
        emit_room_state(code)
        return

    room["current_round"] += 1
    room["awaiting_next"] = False
    room["answers_current"] = {}
    emit_room_state(code)
    emit("question", current_question_payload(room), to=code)


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = env_int("PORT", 5000)
    socketio.run(app, host=host, port=port)
