# PDF Quiz Arena (Multiplayer)

A Python website where users create/join rooms, upload one or more PDFs, and compete in live MCQ rounds.

## Features
- Room-based multiplayer quiz (2 to 6 players)
- No login; each player enters a display name
- Room host sets:
  - Difficulty: easy / medium / hard
  - Rounds: 10 or 15
- PDF text extraction from uploaded files
- Auto-generated 4-option MCQs from PDF content
- LLM-based question generation via OpenAI (with local fallback if API is unavailable)
- Live scoring:
  - Correct answer: +10
  - Wrong answer: -5
  - No answer: 0
- Round report after each question (correct/wrong/no answer + updated scores)
- Final standings and winner announcement

## Local run
1. Create and activate virtual environment
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Start server:
   ```bash
   python app.py
   ```
4. Open `http://localhost:5000`

## Deploy (public internet access)
This project is deployment-ready now.

### Option A: Render (quickest)
1. Push code to GitHub.
2. Create a new Web Service on Render from your repo.
3. Render auto-detects `render.yaml`, or set manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --worker-class eventlet --workers 1 --bind 0.0.0.0:$PORT app:app`
4. Set env vars:
   - `SECRET_KEY` = long random string
   - `SESSION_COOKIE_SECURE` = `true`
   - `CORS_ALLOWED_ORIGINS` = your domain (or `*` for testing)
5. Deploy and share your Render URL.

### Option B: Any Docker host
1. Build image:
   ```bash
   docker build -t pdf-quiz-arena .
   ```
2. Run container:
   ```bash
   docker run -p 5000:5000 -e SECRET_KEY="your-secret" pdf-quiz-arena
   ```
3. Put behind HTTPS reverse proxy (Nginx/Caddy/Cloudflare Tunnel) and share public URL.

## Production environment variables
Use `.env.example` as template:
- `SECRET_KEY`
- `PORT`
- `HOST`
- `CORS_ALLOWED_ORIGINS`
- `SESSION_COOKIE_SECURE`
- `SESSION_COOKIE_SAMESITE`
- `MAX_CONTENT_LENGTH_MB`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

## Enable OpenAI question generation
1. Set `OPENAI_API_KEY` in your host environment (Render Environment tab).
2. Optionally set `OPENAI_MODEL` (default: `gpt-4o-mini`).
3. Redeploy.

If OpenAI is unavailable for any reason, the app automatically falls back to the built-in heuristic generator so gameplay continues.

## Important scaling note
Room and game state is currently stored in-memory (`rooms` dict). This means:
- Works well on a single server instance.
- If server restarts, active rooms are lost.
- For multi-instance scaling, migrate room state to Redis/DB and use Socket.IO message queue.

## Notes
- Question generation is heuristic from extracted PDF text.
- If a PDF is scanned image-only, text extraction may be weak without OCR.
