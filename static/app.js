/* Crossfire client */
const $app = document.getElementById("app");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const HEART_SVG = (filled) =>
  `<svg class="heart ${filled ? "filled" : ""}" viewBox="0 0 24 24"><path d="M12 21s-7.5-4.9-9.8-9.2C.5 8.6 2.4 5 5.8 5c2 0 3.6 1.1 4.4 2.7h3.6C14.6 6.1 16.2 5 18.2 5c3.4 0 5.3 3.6 3.6 6.8C19.5 16.1 12 21 12 21z"/></svg>`;

const DOTS = `<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>`;

/* ---------------- home ---------------- */

function renderHome() {
  $app.innerHTML = `
  <div class="center-screen">
    <div class="home-title">
      <div class="over">AI REFEREE PRESENTS</div>
      <h1>CROSS<span>FIRE</span></h1>
      <p class="muted small" style="margin-top:16px;line-height:1.6">
        Two debaters. Five hearts each. The AI ref judges every argument —<br/>
        fallacies cost a heart, killer points earn one back.<br/>
        Last thinker standing wins.
      </p>
    </div>
    <div class="card home-card">
      <input type="text" id="name" placeholder="your fighter name" maxlength="24" />
      <button class="btn btn-gold w-full" id="create">CREATE A ROOM</button>
      <div class="divider">or join a fight</div>
      <div class="join-row">
        <input type="text" id="code" placeholder="ROOM CODE" maxlength="6" style="letter-spacing:.3em;font-weight:700" />
        <button class="btn btn-blue" id="join">JOIN</button>
      </div>
      <p class="err" id="err"></p>
    </div>
    <p class="muted tiny" style="margin-top:24px">share the room link — friends can debate or just watch</p>
  </div>`;

  document.getElementById("create").onclick = async (e) => {
    const name = document.getElementById("name").value.trim();
    const err = document.getElementById("err");
    if (!name) return (err.textContent = "enter your name first");
    e.target.disabled = true;
    e.target.textContent = "building the ring…";
    const res = await fetch("/api/rooms", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!res.ok) {
      e.target.disabled = false;
      e.target.textContent = "CREATE A ROOM";
      return (err.textContent = data.error || "failed to create room");
    }
    localStorage.setItem(`crossfire:${data.code}:pid`, data.playerId);
    localStorage.setItem(`crossfire:${data.code}:name`, name);
    location.href = `/room/${data.code}`;
  };
  document.getElementById("join").onclick = () => {
    const c = document.getElementById("code").value.trim().toUpperCase();
    if (c) location.href = `/room/${c}`;
  };
  document.getElementById("code").addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase();
  });
}

/* ---------------- room ---------------- */

let room = null;
let pid = null;
let myRole = null;
let prevHearts = {};
let es = null;

function heartsHtml(p) {
  const lost = prevHearts[p.id] !== undefined && p.hearts < prevHearts[p.id];
  prevHearts[p.id] = p.hearts;
  const hearts = Array.from({ length: 5 }, (_, i) =>
    HEART_SVG(i < p.hearts).replace('class="heart ', `class="heart ${lost && i === p.hearts ? "heart-pop " : ""}`)
  ).join("");
  return `<div class="hearts ${lost ? "shake" : ""}">${hearts}</div>`;
}

function verdictChip(f) {
  if (f.pending) return `<span class="tiny muted">ref thinking${DOTS}</span>`;
  if (f.verdict === "excellent") return `<span class="verdict-chip plus">+1 HEART · EXCELLENT</span>`;
  if (f.verdict === "off_topic") return `<span class="verdict-chip minus">−1 HEART · OFF TOPIC</span>`;
  if (f.verdict === "fallacy") return `<span class="verdict-chip minus">−1 HEART · ${esc((f.fallacy || "fallacy").toUpperCase())}</span>`;
  if (f.verdict === "weak") return `<span class="verdict-chip weak">weak</span>`;
  return "";
}

function feedHtml(f, debaterIds) {
  if (f.kind === "sys") return `<p class="sys-line">${esc(f.text)}</p>`;
  if (f.kind === "ref") {
    return `<div class="ref-callout"><span class="ref-tag">REF</span><p>${esc(f.text)}</p></div>`;
  }
  const idx = debaterIds.indexOf(f.playerId);
  const side = idx === 0 ? 0 : 1;
  const showNote = f.note && !["excellent", "fallacy", "off_topic"].includes(f.verdict);
  return `
    <div class="msg ${side === 0 ? "left" : "right"}">
      <div class="msg-meta">
        <span class="name-tag t${side}">${esc(f.name)}</span>
        ${verdictChip(f)}
      </div>
      <p>${esc(f.text)}</p>
      ${showNote ? `<p class="judge-note">${esc(f.note)}</p>` : ""}
    </div>`;
}

function statsFor(p) {
  const msgs = room.feed.filter((f) => f.kind === "msg" && f.playerId === p.id && f.verdict);
  return {
    fallacies: msgs.filter((f) => f.verdict === "fallacy" || f.verdict === "off_topic").length,
    excellent: msgs.filter((f) => f.verdict === "excellent").length,
    args: msgs.length,
  };
}

function renderRoom() {
  if (!room) return;
  const debaters = room.players.filter((p) => p.role === "debater");
  const [a, b] = debaters;
  const me = room.players.find((p) => p.id === pid);
  const isHost = me && me.id === room.hostId;
  const debaterIds = debaters.map((d) => d.id);

  let body = "";
  if (room.status === "lobby") {
    const ready = debaters.length === 2 && !!room.topic;
    body = `
    <div class="lobby">
      <div>
        <h2>IN THE LOBBY</h2>
        <p class="lobby-sub">${debaters.length < 2 ? "waiting for a second debater — share the room link" : "both fighters are here — lock a topic and start"}</p>
      </div>
      ${me && me.role === "debater" ? `
      <div class="topic-row">
        <input type="text" id="topicDraft" placeholder="type the thing you two always argue about…" maxlength="140" />
        <button class="btn btn-blue" id="setTopic">SET</button>
      </div>
      <button class="btn-dashed" id="aiTopic">or let the ref pick a fight for you</button>` : ""}
      ${room.topic ? `<p class="topic-line" style="border:none;padding:0;margin:0">topic: <span class="gold" style="font-weight:600">“${esc(room.topic)}”</span></p>` : ""}
      ${isHost
        ? `<button class="btn btn-red w-full" id="startBtn" ${ready ? "" : "disabled"}>${ready ? "START THE FIGHT" : "NEED 2 DEBATERS + A TOPIC"}</button>`
        : `<p class="lobby-sub">${me && me.role === "spectator" ? "you are watching — waiting for the host to start" : "waiting for the host to start the fight"}</p>`}
    </div>`;
  } else {
    const feedRows = room.feed.map((f) => feedHtml(f, debaterIds)).join("");
    let tail = "";
    if (room.status === "live" && me && me.role === "debater") {
      tail = `
      <div class="composer">
        <div class="composer-row">
          <input type="text" id="draft" placeholder="make your argument…" maxlength="500" />
          <button class="btn btn-gold" id="send">ARGUE</button>
        </div>
        <div class="composer-note">
          <span>every argument gets judged — fallacies cost a heart</span>
          <button class="link-btn" id="callJudge">call the judge</button>
        </div>
      </div>`;
    } else if (room.status === "live") {
      tail = `<p class="spectator-note">you are in the crowd — enjoy the show</p>`;
    } else {
      tail = gameOverHtml(me, debaters);
    }
    body = `<div class="feed" id="feed">${feedRows}</div>${tail}`;
  }

  $app.innerHTML = `
  <div class="room-wrap">
    <div class="room-head">
      <div><span class="brand">CROSSFIRE</span><span class="room-code">room ${room.code}</span></div>
      <button class="btn btn-ghost small" id="share" style="padding:6px 12px">share room</button>
    </div>
    <div class="scoreboard">
      <div class="score-grid">
        <div>
          <p class="fighter-name ${a ? "blue" : "muted"}">${a ? esc(a.name) : "waiting for debater…"}</p>
          ${a ? heartsHtml(a) : ""}
        </div>
        <div>
          <div class="vs">VS</div>
          ${room.status === "live" ? `<div class="arg-count">${room.judged}/${room.cap} args</div>` : ""}
        </div>
        <div style="text-align:right">
          <p class="fighter-name ${b ? "red" : "muted"}">${b ? esc(b.name) : "waiting for debater…"}</p>
          ${b ? `<div class="hearts right ${""}">${Array.from({ length: 5 }, (_, i) => HEART_SVG(i < b.hearts)).join("")}</div>` : ""}
        </div>
      </div>
      ${room.topic ? `<p class="topic-line">arguing: <span class="gold" style="font-weight:600">“${esc(room.topic)}”</span></p>` : ""}
      ${room.spectatorCount ? `<p class="crowd">${room.spectatorCount} watching from the crowd</p>` : ""}
    </div>
    ${body}
    <p class="err" id="err"></p>
  </div>`;

  const feed = document.getElementById("feed");
  if (feed) feed.scrollTop = feed.scrollHeight;

  document.getElementById("share").onclick = () => {
    navigator.clipboard.writeText(location.href);
    document.getElementById("share").textContent = "link copied";
    setTimeout(() => (document.getElementById("share").textContent = "share room"), 1500);
  };

  const err = (m) => (document.getElementById("err").textContent = m);
  const api = async (path, body = {}) => {
    const res = await fetch(`/api/rooms/${room.code}/${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ playerId: pid, ...body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "failed");
    return data;
  };

  const setBtn = document.getElementById("setTopic");
  if (setBtn)
    setBtn.onclick = () => {
      const t = document.getElementById("topicDraft").value.trim();
      if (t) api("topic", { topic: t }).catch((e) => err(e.message));
    };
  const aiBtn = document.getElementById("aiTopic");
  if (aiBtn) aiBtn.onclick = () => api("topic", { mode: "ai" }).catch((e) => err(e.message));
  const startBtn = document.getElementById("startBtn");
  if (startBtn) startBtn.onclick = () => api("start").catch((e) => err(e.message));
  const send = document.getElementById("send");
  const draft = document.getElementById("draft");
  if (send && draft) {
    const doSend = () => {
      const t = draft.value.trim();
      if (!t) return;
      draft.value = "";
      api("message", { text: t }).catch((e) => {
        err(e.message);
        draft.value = t;
      });
    };
    send.onclick = doSend;
    draft.onkeydown = (e) => e.key === "Enter" && doSend();
    draft.focus();
  }
  const callBtn = document.getElementById("callJudge");
  if (callBtn) callBtn.onclick = () => api("end").catch((e) => err(e.message));
  const rematch = document.getElementById("rematch");
  if (rematch) rematch.onclick = () => api("rematch").catch(() => {});
  const newRoom = document.getElementById("newRoom");
  if (newRoom) newRoom.onclick = () => (location.href = "/");
}

function gameOverHtml(me, debaters) {
  const f = room.final;
  const statCards = debaters
    .map((p, i) => {
      const s = statsFor(p);
      const won = f && f.winnerId === p.id;
      return `<div class="stat-card ${won ? "won" : ""}">
        <p class="small" style="font-weight:700" class="${i === 0 ? "blue" : "red"}">${esc(p.name)}</p>
        <p class="tiny muted" style="margin-top:4px">${p.hearts} hearts left · ${s.excellent} excellent · ${s.fallacies} fallacies</p>
      </div>`;
    })
    .join("");
  return `
  <div class="gameover">
    ${f ? `
    <div>
      <p class="verdict-tag">THE VERDICT</p>
      <h2 class="winner-name">${esc(f.winnerName).toUpperCase()} WINS</h2>
      ${f.reason ? `<p class="muted small" style="margin-top:8px">${esc(f.reason)}</p>` : ""}
    </div>
    ${f.summary ? `<p class="muted small" style="line-height:1.6;max-width:460px;margin:0 auto">${esc(f.summary)}</p>` : ""}
    ${f.mvp ? `<div class="mvp-box"><p class="mvp-tag">MVP ARGUMENT</p><p>${esc(f.mvp)}</p></div>` : ""}` : `
    <p class="muted small">ref is deliberating${DOTS}</p>`}
    <div class="stat-grid">${statCards}</div>
    <div style="display:flex;gap:8px;justify-content:center">
      ${me && me.role === "debater" ? `<button class="btn btn-gold" id="rematch">REMATCH</button>` : ""}
      <button class="btn btn-ghost" id="newRoom">new room</button>
    </div>
  </div>`;
}

function renderJoin(code) {
  const roster = room && room.players.length
    ? `<div class="roster">${room.players.map((p) => `<p class="tiny muted">${esc(p.name)} · ${p.role}</p>`).join("")}</div>`
    : "";
  $app.innerHTML = `
  <div class="center-screen">
    <div class="card" style="width:100%;max-width:380px">
      <p class="gold tiny" style="font-weight:700;letter-spacing:.3em">ROOM ${code}</p>
      <h2 class="display" style="font-size:24px;font-weight:900;margin:4px 0">WHO GOES THERE</h2>
      <p class="muted tiny" style="margin-bottom:16px">first two to join fight — everyone else gets a seat in the crowd</p>
      <input type="text" id="jname" placeholder="your name" maxlength="24" />
      <button class="btn btn-gold w-full" id="joinBtn" style="margin-top:12px">STEP IN</button>
      <p class="err" id="err" style="margin-top:8px"></p>
      ${roster}
    </div>
  </div>`;
  const doJoin = async () => {
    const name = document.getElementById("jname").value.trim();
    const err = document.getElementById("err");
    if (!name) return (err.textContent = "enter your name");
    const res = await fetch(`/api/rooms/${code}/join`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!res.ok) return (err.textContent = data.error || "could not join");
    localStorage.setItem(`crossfire:${code}:pid`, data.playerId);
    localStorage.setItem(`crossfire:${code}:name`, name);
    pid = data.playerId;
    myRole = data.role;
    renderRoom();
  };
  document.getElementById("joinBtn").onclick = doJoin;
  document.getElementById("jname").onkeydown = (e) => e.key === "Enter" && doJoin();
  document.getElementById("jname").focus();
}

function renderNotFound() {
  $app.innerHTML = `
  <div class="center-screen">
    <p class="display" style="font-size:28px;font-weight:900">ROOM NOT FOUND</p>
    <p class="muted small" style="margin:8px 0 20px">this fight either ended or never existed</p>
    <a href="/" class="gold small">back to lobby</a>
  </div>`;
}

async function initRoom(code) {
  pid = localStorage.getItem(`crossfire:${code}:pid`);

  // initial snapshot decides join vs room view
  try {
    const res = await fetch(`/api/rooms/${code}`);
    if (!res.ok) return renderNotFound();
    room = (await res.json()).room;
  } catch {
    return renderNotFound();
  }

  if (!pid) {
    renderJoin(code);
  } else {
    renderRoom();
  }

  if (es) es.close();
  es = new EventSource(`/api/rooms/${code}/events`);
  es.onmessage = (e) => {
    try {
      room = JSON.parse(e.data);
      if (pid) renderRoom();
      else renderJoin(code);
    } catch {}
  };
  es.onerror = () => {
    // EventSource auto-reconnects; if the room vanished, show not found on next fetch
    fetch(`/api/rooms/${code}`).then((r) => {
      if (!r.ok) {
        es.close();
        renderNotFound();
      }
    });
  };
}

/* ---------------- boot ---------------- */

const path = location.pathname;
const m = path.match(/^\/room\/([A-Z0-9]{4,8})\/?$/i);
if (m) initRoom(m[1].toUpperCase());
else renderHome();
