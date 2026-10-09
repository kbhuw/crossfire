# Crossfire

A two-player argument game refereed by an AI judge. Create a room, share the
code, and argue. The judge scores every message live: named fallacies and
off-topic shots cost a heart, genuinely sharp arguments earn one back. First
to 0 hearts loses, or the 30-argument cap ends it and the judge reads the
transcript and calls the winner.

## how it works

- First two people to join a room are the debaters (5 hearts each). Everyone
  else joins as a spectator and can watch but not post.
- The host picks a topic or lets the AI generate one, then starts the game.
- Free-for-all: either debater can send a message at any time, up to 500
  characters. Every message goes to the judge, which returns a verdict:
  `excellent` (+1 heart), `solid`, `weak`, `fallacy` (-1, names the fallacy),
  or `off_topic` (-1).
- End: 0 hearts, the 30-argument cap, or a "call the judge" vote. The judge
  picks a winner, writes a recap, and names an MVP argument. Rematch resets
  the room.

## run it

```sh
pip install -e .          # or: pip install fastapi 'uvicorn[standard]' httpx python-dotenv
cd auth && npm ci           # the Better Auth service (needs node 20+)
cp .env.example .env      # set AI_GATEWAY_API_KEY and BETTER_AUTH_SECRET
uvicorn main:app --port 3123
```

Open http://localhost:3123.

## layout

- `main.py` - FastAPI app: rooms, game engine, judge calls, SSE, static files
- `static/` - the whole client (index.html, app.js, styles.css)

The judge is `openai/gpt-5-mini` through the Vercel AI Gateway
(`JUDGE_MODEL` to override). Rooms live in memory and expire after 6 hours.

## auth

Sign-in is [Better Auth](https://better-auth.com), email + password, sessions
in a local SQLite file (`auth/auth.db`). The Node service (`auth/server.mjs`)
runs beside the app on `127.0.0.1:3922`; FastAPI proxies `/api/auth/*` to it
and checks the session cookie on every API route. `uvicorn` spawns it
automatically at startup. Set `REQUIRE_AUTH=0` to skip it for open local dev.
