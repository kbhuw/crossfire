import asyncio
import json
import os
import secrets
import string
import time
import uuid

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

GATEWAY = "https://ai-gateway.vercel.sh/v1/chat/completions"
MODEL = os.environ.get("JUDGE_MODEL", "openai/gpt-5-mini")
HEARTS_MAX = 5
ARG_CAP = 30
FEED_LIMIT = 300
ROOM_TTL = 6 * 3600

app = FastAPI()

rooms: dict[str, dict] = {}
listeners: dict[str, list[asyncio.Queue]] = {}

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_code() -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(6))
        if code not in rooms:
            return code


def feed_item(kind: str, text: str, **kw) -> dict:
    return {"id": uuid.uuid4().hex, "ts": int(time.time() * 1000), "kind": kind, "text": text, **kw}


def push_feed(room: dict, item: dict):
    room["feed"].append(item)
    if len(room["feed"]) > FEED_LIMIT:
        del room["feed"][: len(room["feed"]) - FEED_LIMIT]


def sys_msg(room: dict, text: str):
    push_feed(room, feed_item("sys", text))


def ref_msg(room: dict, text: str, **kw):
    push_feed(room, feed_item("ref", text, **kw))


def debaters(room: dict) -> list[dict]:
    return [p for p in room["players"] if p["role"] == "debater"]


def public(room: dict) -> dict:
    return {
        "code": room["code"],
        "status": room["status"],
        "topic": room["topic"],
        "hostId": room["hostId"],
        "players": room["players"],
        "feed": room["feed"],
        "judged": room["judged"],
        "cap": room["cap"],
        "final": room.get("final"),
        "endedBy": room.get("endedBy"),
        "spectatorCount": len([p for p in room["players"] if p["role"] == "spectator"]),
        "version": room["version"],
    }


def broadcast(room: dict):
    room["version"] += 1
    room["lastTouched"] = time.time()
    payload = public(room)
    for q in listeners.get(room["code"], []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def get_room(code: str) -> dict | None:
    room = rooms.get(code.upper())
    if room:
        room["lastTouched"] = time.time()
    return room


def get_player(room: dict, pid: str | None) -> dict | None:
    if not pid:
        return None
    return next((p for p in room["players"] if p["id"] == pid), None)


# ---------- judge ----------


async def call_judge(system: str, user: str) -> str:
    key = os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise RuntimeError("AI_GATEWAY_API_KEY not set")
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            GATEWAY,
            headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
            },
        )
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


def parse_json(raw: str) -> dict:
    try:
        start, end = raw.index("{"), raw.rindex("}")
        return json.loads(raw[start : end + 1])
    except Exception:
        return {}


def transcript(room: dict, limit: int) -> str:
    msgs = [f for f in room["feed"] if f["kind"] == "msg"]
    return "\n".join(f"{m['name']}: {m['text']}" for m in msgs[-limit:])


VALID = {"excellent", "solid", "weak", "fallacy", "off_topic"}


async def judge_message(room: dict, player: dict, text: str) -> dict:
    opponents = ", ".join(p["name"] for p in debaters(room) if p["id"] != player["id"])
    system = f'''You are the referee of a live debate game called Crossfire.
TOPIC: "{room["topic"]}"
Two debaters argue back and forth. You judge ONE new message.

Verdicts:
- "excellent": a genuinely strong move - clean rebuttal, real evidence, steelmanning, a fair concession used well, or a decisive logical point. Rare. Worth +1 heart.
- "solid": a normal reasonable argument. 0 hearts.
- "weak": sloppy - unsupported claims, circular reasoning, vague filler. Not quite a named fallacy. 0 hearts.
- "fallacy": a clear, nameable logical fallacy - strawman, ad hominem, appeal to emotion, false dilemma, slippery slope, cherry-picking, whataboutism, no true scotsman, anecdotal evidence, appeal to authority, hasty generalization, red herring, moving the goalposts, gish gallop, tu quoque, bandwagon, appeal to nature, correlation is not causation. -1 heart.
- "off_topic": does not engage the topic at all. -1 heart.

Rules:
- Judge REASONING, not opinions. A position you disagree with is not a fallacy.
- Personal attacks on {opponents or "the opponent"} count as ad hominem.
- Only mark "fallacy" when the fallacy is real and identifiable - name it.
- "note": ONE punchy referee-voice sentence, max 140 chars. Say WHY it is a fallacy or WHAT made it excellent. Direct, a little witty, never cruel. No emojis.

Reply ONLY as JSON: {{"verdict":"...","fallacy":"... or null","note":"..."}}'''
    recent = transcript(room, 10)
    user = f'{"Recent exchange:\n" + recent + "\n\n" if recent else ""}New message by {player["name"]}:\n"{text}"'
    parsed = parse_json(await call_judge(system, user))
    verdict = parsed.get("verdict", "solid")
    if verdict not in VALID:
        verdict = "solid"
    delta = 1 if verdict == "excellent" else (-1 if verdict in ("fallacy", "off_topic") else 0)
    fallacy = None
    if verdict == "fallacy":
        fallacy = parsed.get("fallacy") or "fallacy"
    elif verdict == "off_topic":
        fallacy = "off topic"
    return {"verdict": verdict, "fallacy": fallacy, "note": (parsed.get("note") or "")[:200], "delta": delta}


async def pick_winner(room: dict) -> dict | None:
    d = debaters(room)
    if len(d) < 2:
        return None
    a, b = d[0], d[1]

    def stats(p):
        msgs = [f for f in room["feed"] if f["kind"] == "msg" and f.get("playerId") == p["id"] and f.get("verdict")]
        fallacies = [f for f in msgs if f["verdict"] in ("fallacy", "off_topic")]
        return {
            "name": p["name"],
            "hearts": p["hearts"],
            "arguments": len(msgs),
            "fallacies": len(fallacies),
            "fallacyNames": [f.get("fallacy") for f in fallacies if f.get("fallacy")],
            "excellent": len([f for f in msgs if f["verdict"] == "excellent"]),
        }

    sa, sb = stats(a), stats(b)
    system = f'''You are the referee of a debate game called Crossfire. The debate on "{room["topic"]}" just ended. Read the transcript and stats, then name the winner.

Who argued better ON THE TOPIC - weighing quality of reasoning, fallacies committed, and engagement - not who you agree with. Hearts and fallacy counts matter, but a debater who argued sharply despite losing hearts can still win.
- "reason": one sentence why they won.
- "summary": 2-3 sentence recap of the debate, referee voice, punchy, no emojis.
- "mvp": the single best argument made in the debate - short quote plus who said it.
Reply ONLY as JSON: {{"winner":"<exact name>","reason":"...","summary":"...","mvp":"..."}}'''
    user = f"Stats:\n{a['name']}: {json.dumps(sa)}\n{b['name']}: {json.dumps(sb)}\n\nTranscript:\n{transcript(room, 200)}"
    parsed = parse_json(await call_judge(system, user))
    winner = next((p for p in d if p["name"] == (parsed.get("winner") or "").strip()), None)
    if not winner:
        winner = a if a["hearts"] >= b["hearts"] else b
    return {
        "winnerId": winner["id"],
        "winnerName": winner["name"],
        "reason": parsed.get("reason") or "",
        "summary": parsed.get("summary") or "",
        "mvp": parsed.get("mvp") or "",
    }


async def generate_topic() -> str:
    system = 'Generate one debate topic for a game between friends. Pick something genuinely arguable - tech, food, life choices, sports, movies, money, ethics light. Provocative but friendly. A single short statement people can take sides on, like "Cereal is soup" or "Remote work made us worse at our jobs". Reply ONLY as JSON: {"topic":"..."}'
    parsed = parse_json(await call_judge(system, "give me a topic"))
    return (parsed.get("topic") or "Pineapple belongs on pizza")[:140]


async def finish(room: dict, ended_by: str):
    if room["status"] == "over":
        return
    room["status"] = "over"
    room["endedBy"] = ended_by
    ref_msg(room, "that's it - the ref is writing the verdict")
    broadcast(room)
    try:
        room["final"] = await pick_winner(room)
    except Exception as e:
        print("final verdict failed", e)
        d = debaters(room)
        if len(d) == 2:
            winner = d[0] if d[0]["hearts"] >= d[1]["hearts"] else d[1]
            room["final"] = {
                "winnerId": winner["id"],
                "winnerName": winner["name"],
                "reason": "won on points",
                "summary": "",
                "mvp": "",
            }
    broadcast(room)


async def check_end(room: dict):
    if room["status"] != "live":
        return
    dead = next((p for p in debaters(room) if p["hearts"] <= 0), None)
    if dead:
        await finish(room, "hearts")
    elif room["judged"] >= room["cap"]:
        await finish(room, "cap")


async def adjudicate(room: dict, item: dict, player: dict, text: str):
    try:
        v = await judge_message(room, player, text)
        item["pending"] = False
        item["verdict"] = v["verdict"]
        item["fallacy"] = v["fallacy"]
        item["note"] = v["note"]
        item["delta"] = v["delta"]
        room["judged"] += 1
        p = get_player(room, player["id"])
        if p:
            p["hearts"] = max(0, min(HEARTS_MAX, p["hearts"] + v["delta"]))
        if v["delta"] != 0 or v["verdict"] == "excellent":
            ref_msg(
                room,
                v["note"] or v["verdict"],
                verdict=v["verdict"],
                fallacy=v["fallacy"],
                playerId=player["id"],
                name=player["name"],
                delta=v["delta"],
            )
        broadcast(room)
        await check_end(room)
    except Exception as e:
        item["pending"] = False
        item["verdict"] = "solid"
        item["note"] = "the ref blinked - no call"
        broadcast(room)
        print("judge failed", e)


# ---------- routes ----------


@app.post("/api/rooms")
async def create_room(req: Request):
    body = await req.json()
    name = (body.get("name") or "").strip()[:24]
    if not name:
        return JSONResponse({"error": "name required"}, 400)
    code = new_code()
    host = {"id": uuid.uuid4().hex, "name": name, "role": "debater", "hearts": HEARTS_MAX, "joinedAt": int(time.time() * 1000)}
    room = {
        "code": code,
        "createdAt": int(time.time() * 1000),
        "lastTouched": time.time(),
        "status": "lobby",
        "topic": None,
        "hostId": host["id"],
        "players": [host],
        "feed": [],
        "judged": 0,
        "cap": ARG_CAP,
        "final": None,
        "version": 0,
    }
    rooms[code] = room
    sys_msg(room, f"{name} created the room")
    return {"code": code, "playerId": host["id"], "room": public(room)}


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    if room["status"] == "over":
        return JSONResponse({"error": "game over - start a new room"}, 400)
    body = await req.json()
    name = (body.get("name") or "").strip()[:24] or "anon"
    role = "debater" if len(debaters(room)) < 2 else "spectator"
    player = {"id": uuid.uuid4().hex, "name": name, "role": role, "hearts": HEARTS_MAX, "joinedAt": int(time.time() * 1000)}
    room["players"].append(player)
    sys_msg(room, f"{name} joined as {role}")
    broadcast(room)
    return {"playerId": player["id"], "role": role}


@app.get("/api/rooms/{code}")
async def room_state(code: str):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    return {"room": public(room)}


@app.get("/api/rooms/{code}/events")
async def room_events(code: str):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    listeners.setdefault(room["code"], []).append(q)

    async def stream():
        try:
            yield f"data: {json.dumps(public(room))}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            listeners.get(room["code"], []).remove(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@app.post("/api/rooms/{code}/topic")
async def set_topic(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    if room["status"] != "lobby":
        return JSONResponse({"error": "topic is locked"}, 400)
    body = await req.json()
    player = get_player(room, body.get("playerId"))
    if not player or player["role"] != "debater":
        return JSONResponse({"error": "only debaters set the topic"}, 403)
    if body.get("mode") == "ai":
        try:
            room["topic"] = await generate_topic()
        except Exception:
            room["topic"] = "Pineapple belongs on pizza"
    else:
        topic = (body.get("topic") or "").strip()[:140]
        if not topic:
            return JSONResponse({"error": "empty topic"}, 400)
        room["topic"] = topic
    sys_msg(room, f'topic locked: "{room["topic"]}"')
    broadcast(room)
    return {"ok": True, "topic": room["topic"]}


@app.post("/api/rooms/{code}/start")
async def start_debate(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    body = await req.json()
    player = get_player(room, body.get("playerId"))
    if not player or player["id"] != room["hostId"]:
        return JSONResponse({"error": "only the host can start"}, 403)
    if room["status"] != "lobby":
        return JSONResponse({"error": "already started"}, 400)
    if len(debaters(room)) < 2:
        return JSONResponse({"error": "need 2 debaters"}, 400)
    if not room["topic"]:
        return JSONResponse({"error": "set a topic first"}, 400)
    room["status"] = "live"
    a, b = debaters(room)
    ref_msg(room, f'FIGHT. {a["name"]} vs {b["name"]}. Topic: "{room["topic"]}". Free-for-all - argue whenever. {room["cap"]} arguments max, fallacies cost a heart, killers earn one back.')
    broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/message")
async def post_message(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    body = await req.json()
    player = get_player(room, body.get("playerId"))
    if not player:
        return JSONResponse({"error": "bad player"}, 403)
    if player["role"] != "debater":
        return JSONResponse({"error": "spectators watch, they do not argue"}, 403)
    if room["status"] != "live":
        return JSONResponse({"error": "debate is not live"}, 400)
    text = (body.get("text") or "").strip()[:500]
    if not text:
        return JSONResponse({"error": "empty"}, 400)
    item = feed_item("msg", text, playerId=player["id"], name=player["name"], pending=True)
    push_feed(room, item)
    broadcast(room)
    asyncio.create_task(adjudicate(room, item, player, text))
    return {"ok": True}


@app.post("/api/rooms/{code}/end")
async def end_debate(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    if room["status"] != "live":
        return JSONResponse({"error": "debate is not live"}, 400)
    body = await req.json()
    player = get_player(room, body.get("playerId"))
    if not player or player["role"] != "debater":
        return JSONResponse({"error": "only debaters can call the judge"}, 403)
    each_has_args = all(len([f for f in room["feed"] if f["kind"] == "msg" and f.get("playerId") == d["id"]]) >= 2 for d in debaters(room))
    if not each_has_args or room["judged"] < 4:
        return JSONResponse({"error": "too early - both debaters need a few arguments on the board"}, 400)
    await finish(room, "call")
    return {"ok": True}


@app.post("/api/rooms/{code}/rematch")
async def rematch(code: str, req: Request):
    room = get_room(code)
    if not room:
        return JSONResponse({"error": "room not found"}, 404)
    body = await req.json()
    player = get_player(room, body.get("playerId"))
    if not player or player["role"] != "debater":
        return JSONResponse({"error": "only debaters can rematch"}, 403)
    if room["status"] != "over":
        return JSONResponse({"error": "game is not over"}, 400)
    room["status"] = "lobby"
    room["topic"] = None
    room["feed"] = []
    room["judged"] = 0
    room["final"] = None
    room.pop("endedBy", None)
    for p in room["players"]:
        p["hearts"] = HEARTS_MAX
    sys_msg(room, "rematch - hearts restored, awaiting topic")
    broadcast(room)
    return {"ok": True}


# ---------- static ----------

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/room/{code}")
async def room_page(code: str):
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")


async def sweeper():
    while True:
        await asyncio.sleep(600)
        now = time.time()
        for code in [c for c, r in rooms.items() if now - r["lastTouched"] > ROOM_TTL]:
            rooms.pop(code, None)
            listeners.pop(code, None)


@app.on_event("startup")
async def startup():
    asyncio.create_task(sweeper())
