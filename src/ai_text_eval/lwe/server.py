"""Local writing application (LWE M4).

A loopback-only HTTP app over `http.server`. No dependencies, no network
beyond `127.0.0.1`, no telemetry, no accounts. The contributor runs it, writes,
and exports; nothing leaves their machine until they choose to send the
session directory.

**Why a browser and not a terminal.** The evidence depends on distinguishing a
paste from typing at capture time rather than inferring it later, and the
browser is the only place that reports `insertFromPaste` as a fact. A terminal
editor would leave the tool guessing from timing — which is exactly the
inference `docs/LWE_DESIGN.md` §1.1 prohibits.

**Security posture.** Tamper-evidence, not secrecy (§4.2 of the design). The
per-session token stops another process on the same machine from appending to
a live session; it is not a defence against the contributor, who is not the
party the record is protected from.

**The writing surface offers nothing.** No autocomplete, no suggestions, no
grammar rewriting. CAS §3.2 prohibits text from tools "that offer generative
rewriting", so a writing environment for this corpus must not be one.
"""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ai_text_eval.lwe.session import (
    Attestations,
    SessionError,
    SessionIntent,
    SessionState,
    WritingSession,
    create_session,
    load_session,
)

DEFAULT_ROOT = Path("sessions")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionStore:
    """Sessions on disk, with their in-memory tokens.

    Tokens live only for the process lifetime by design: they guard a live
    local endpoint, and persisting them would turn a transient guard into a
    credential sitting in the evidence directory.
    """

    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._live: dict[str, WritingSession] = {}
        self._lock = threading.Lock()

    def create(self, intent: SessionIntent, attestations: Attestations) -> WritingSession:
        with self._lock:
            session_id = f"{utc_now().replace(':', '').replace('-', '')}-{secrets.token_hex(3)}"
            session = create_session(self.root / session_id, session_id=session_id,
                                     wall=utc_now)
            session.open(intent, attestations)
            self._live[session_id] = session
            return session

    def get(self, session_id: str) -> WritingSession:
        with self._lock:
            if session_id in self._live:
                return self._live[session_id]
            path = self.root / session_id
            if not path.is_dir():
                raise KeyError(session_id)
            session = load_session(path, wall=utc_now)
            self._live[session_id] = session
            return session

    def list(self) -> list[dict]:
        rows = []
        for path in sorted(self.root.iterdir()) if self.root.is_dir() else []:
            if not path.is_dir():
                continue
            try:
                session = self.get(path.name)
            except (KeyError, SessionError):
                continue
            rows.append({"session_id": session.session_id,
                         "state": session.state.value,
                         "contributor": session.intent.contributor,
                         "words": session.facts().get("final_words", 0)
                         or len(session.text.split())})
        return rows


class Handler(BaseHTTPRequestHandler):
    server_version = "gauntlet-write"
    store: SessionStore = None  # type: ignore[assignment]

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt, *args):   # pragma: no cover - quiet by default
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The app renders only its own inline assets; nothing external loads.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; form-action 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, markup: str, status: int = 200) -> None:
        self._send(status, markup.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(status, (json.dumps(payload) + "\n").encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _authorised(self, session: WritingSession) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Session-Token", ""), session.token)

    # -- routing ---------------------------------------------------------

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            return self._html(page_start())
        if path == "/sessions":
            return self._json({"sessions": self.store.list()})
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "session":
            try:
                session = self.store.get(parts[1])
            except (KeyError, SessionError):
                return self._html(page_error("No such session."), 404)
            if len(parts) == 2:
                return self._html(page_write(session))
            if parts[2] == "review":
                return self._html(page_review(session))
            if parts[2] == "state":
                return self._json(session.to_dict())
        return self._html(page_error("Not found."), 404)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        body = self._body()

        if path == "/session":
            return self._create(body)

        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "session":
            return self._json({"error": "not found"}, 404)
        try:
            session = self.store.get(parts[1])
        except (KeyError, SessionError):
            return self._json({"error": "no such session"}, 404)
        if not self._authorised(session):
            return self._json({"error": "bad or missing session token"}, 403)

        action = parts[2]
        try:
            if action == "events":
                return self._events(session, body)
            if action == "close":
                return self._json({"verification": session.close().to_dict(),
                                   "state": session.state.value})
            if action == "export":
                return self._export(session)
            if action == "abandon":
                session.abandon(str(body.get("reason", "")))
                return self._json({"state": session.state.value})
        except SessionError as err:
            return self._json({"error": str(err)}, 409)
        return self._json({"error": "unknown action"}, 404)

    # -- actions ---------------------------------------------------------

    def _create(self, body: dict):
        intent = SessionIntent(
            contributor=str(body.get("contributor", "")).strip(),
            prompt=str(body.get("prompt", "")),
            intended_category=str(body.get("category", "")),
            intended_length_bucket=str(body.get("length_bucket", "")),
            language=str(body.get("language", "")))
        attestations = Attestations(
            consent_to_logging=bool(body.get("consent_to_logging")),
            environment_free_of_generative_tools=bool(
                body.get("environment_free_of_generative_tools")),
            spot_verification_acknowledged=bool(
                body.get("spot_verification_acknowledged")),
            declared_model_involvement=bool(body.get("declared_model_involvement")),
            tools_used=["gauntlet-write"],
            note=str(body.get("note", "")))
        try:
            session = self.store.create(intent, attestations)
        except SessionError as err:
            return self._json({"error": str(err)}, 400)
        return self._json({"session_id": session.session_id,
                           "token": session.token,
                           "url": f"/session/{session.session_id}"})

    def _events(self, session: WritingSession, body: dict):
        """Apply a batch of edit events.

        Applied in order and stopped at the first failure, so the journal
        never contains an event the document did not accept.
        """
        applied = 0
        for event in body.get("events", []):
            kind = str(event.get("kind", ""))
            if kind == "insert":
                session.insert(int(event.get("pos", 0)), str(event.get("text", "")))
            elif kind == "paste":
                session.paste(int(event.get("pos", 0)), str(event.get("text", "")))
            elif kind == "delete":
                session.delete(int(event.get("pos", 0)), int(event.get("length", 0)))
            elif kind == "focus_out":
                session.focus_out()
            elif kind == "focus_in":
                session.focus_in()
            elif kind == "note":
                session.note(str(event.get("text", "")))
            else:
                return self._json({"error": f"unknown event kind {kind!r}",
                                   "applied": applied}, 400)
            applied += 1
        return self._json({"applied": applied, "chars": len(session.text),
                           "words": len(session.text.split())})

    def _export(self, session: WritingSession):
        from ai_text_eval.lwe.export import export as run_export
        result = run_export(session, recorded_at=utc_now())
        return self._json({"tier": result.tier, "supported": result.ok,
                           "reasons": result.reasons,
                           "directory": str(session.root)})


# =====================================================================
# Pages
# =====================================================================

STYLE = """
:root { color-scheme: light dark; --fg:#1a1a1a; --bg:#fbfbfa; --mut:#6b6b6b;
        --line:#e0ddd8; --accent:#8a5a2b; --ok:#2e6b4f; --warn:#8a2b2b; }
@media (prefers-color-scheme: dark) {
  :root { --fg:#e8e6e3; --bg:#191918; --mut:#9a9a96; --line:#333230;
          --accent:#c9a227; --ok:#6fbf95; --warn:#e08585; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font: 16px/1.6
  ui-serif, Georgia, serif; }
main { max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; letter-spacing:-.01em; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; }
p.sub { color: var(--mut); margin:0 0 2rem; }
label { display:block; margin: 1rem 0 .25rem; font-weight:600; font-size:.9rem; }
input[type=text], textarea, select { width:100%; padding:.6rem .7rem;
  border:1px solid var(--line); border-radius:6px; background:var(--bg);
  color:var(--fg); font:inherit; }
.check { display:flex; gap:.7rem; align-items:flex-start; margin:1.1rem 0;
  padding:.9rem; border:1px solid var(--line); border-radius:8px; }
.check input { margin-top:.35rem; }
.check span { font-size:.9rem; color:var(--mut); display:block; margin-top:.2rem; }
button { margin-top:1.5rem; padding:.65rem 1.2rem; border:0; border-radius:6px;
  background:var(--accent); color:#fff; font:inherit; cursor:pointer; }
button.ghost { background:transparent; color:var(--mut);
  border:1px solid var(--line); margin-left:.5rem; }
#surface { width:100%; min-height:24rem; padding:1.25rem; font: 17px/1.75
  ui-serif, Georgia, serif; resize:vertical; }
.bar { position:sticky; top:0; background:var(--bg); border-bottom:1px solid
  var(--line); padding:.7rem 0; margin-bottom:1.25rem; display:flex;
  gap:1.25rem; align-items:center; font-size:.85rem; color:var(--mut); }
.rec { color:var(--warn); font-weight:700; }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
td, th { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); }
th { color:var(--mut); font-weight:600; }
.ok { color:var(--ok); } .bad { color:var(--warn); }
.note { border-left:3px solid var(--line); padding:.6rem 0 .6rem 1rem;
  color:var(--mut); font-size:.9rem; margin:1.5rem 0; }
pre.text { white-space:pre-wrap; border:1px solid var(--line); border-radius:8px;
  padding:1.25rem; font:inherit; }
"""


def _shell(title: str, body: str, script: str = "") -> str:
    return (f"<!doctype html><html><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>{STYLE}</style></head><body><main>"
            f"{body}</main>{f'<script>{script}</script>' if script else ''}"
            f"</body></html>")


def page_error(message: str) -> str:
    return _shell("gauntlet-write", f"<h1>{message}</h1>")


def page_start() -> str:
    body = """
<h1>gauntlet-write</h1>
<p class=sub>A writing session that records how the text was made.</p>

<label>Your name<input type=text id=contributor autocomplete=off></label>
<label>What you were asked to write<textarea id=prompt rows=3></textarea></label>
<label>Intended category (optional)<input type=text id=category
  placeholder="e.g. H-01" autocomplete=off></label>
<label>Intended length (optional)<input type=text id=bucket
  placeholder="e.g. B250" autocomplete=off></label>

<h2>Before you begin</h2>
<div class=check><input type=checkbox id=consent>
  <label for=consent style="margin:0">I agree to this session being logged
  <span>Every edit you make in this window is recorded so the finished text can
  be shown to have been written, not generated. Nothing outside this window is
  captured — no screen recording, no other applications, no clipboard
  monitoring. Text you delete is recorded as a position and a length, never as
  content.</span></label></div>

<div class=check><input type=checkbox id=environment>
  <label for=environment style="margin:0">My writing environment has no
  generative writing tools
  <span>No assistant, autocomplete, or rewriting feature is helping with this
  text. You can decline this and still contribute — your session is then
  recorded at a lower evidence tier rather than being refused.</span></label></div>

<div class=check><input type=checkbox id=verification>
  <label for=verification style="margin:0">I understand my contribution may be
  spot-verified
  <span>Verification checks process facts — dates, tools, session records. It
  never involves anyone judging whether your writing "reads" human.</span></label></div>

<div class=note>The session record is evidence, not corpus content. Your
finished text may be published; the recording of how you wrote it is kept under
access control and used only to support the label and for audit. Once a sample
is accepted it cannot be deleted, only corrected or withdrawn from
circulation — so decide before you begin, not after.</div>

<button id=begin>Begin writing</button>
<p id=err class=bad></p>
"""
    script = """
document.getElementById('begin').onclick = async () => {
  const body = {
    contributor: document.getElementById('contributor').value.trim(),
    prompt: document.getElementById('prompt').value,
    category: document.getElementById('category').value.trim(),
    length_bucket: document.getElementById('bucket').value.trim(),
    consent_to_logging: document.getElementById('consent').checked,
    environment_free_of_generative_tools: document.getElementById('environment').checked,
    spot_verification_acknowledged: document.getElementById('verification').checked
  };
  const r = await fetch('/session', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok) { document.getElementById('err').textContent = data.error; return; }
  sessionStorage.setItem('token:' + data.session_id, data.token);
  location.href = data.url;
};
"""
    return _shell("gauntlet-write", body, script)


def page_write(session: WritingSession) -> str:
    if session.state is not SessionState.OPEN:
        return page_review(session)
    body = f"""
<div class=bar><span class=rec>● recording</span>
  <span id=count>0 words</span><span id=status></span></div>
<p class=sub>{_escape(session.intent.prompt) or "Write."}</p>
<textarea id=surface autocomplete=off autocorrect=off autocapitalize=off
  spellcheck=false></textarea>
<button id=finish>Finish and seal</button>
<button id=abandon class=ghost>Abandon</button>
<p id=err class=bad></p>
"""
    script = """
const ID = location.pathname.split('/')[2];
const TOKEN = sessionStorage.getItem('token:' + ID) || '';
const surface = document.getElementById('surface');
let last = surface.value, queue = [], pasted = false, sending = false;

surface.addEventListener('beforeinput', e => {
  if (e.inputType === 'insertFromPaste' || e.inputType === 'insertFromDrop')
    pasted = true;
});

surface.addEventListener('input', () => {
  const now = surface.value, wasPaste = pasted; pasted = false;
  // Common prefix/suffix diff: the smallest edit that explains the change.
  let a = 0; const max = Math.min(last.length, now.length);
  while (a < max && last[a] === now[a]) a++;
  let b = 0;
  while (b < max - a && last[last.length-1-b] === now[now.length-1-b]) b++;
  const removed = last.length - a - b, added = now.slice(a, now.length - b);
  if (removed > 0) queue.push({kind:'delete', pos:a, length:removed});
  if (added.length > 0)
    queue.push({kind: wasPaste ? 'paste' : 'insert', pos:a, text:added});
  last = now;
  document.getElementById('count').textContent =
    (now.trim() ? now.trim().split(/\\s+/).length : 0) + ' words';
  flush();
});

window.addEventListener('blur', () => { queue.push({kind:'focus_out'}); flush(); });
window.addEventListener('focus', () => { queue.push({kind:'focus_in'}); flush(); });

async function flush() {
  if (sending || !queue.length) return;
  sending = true;
  const batch = queue; queue = [];
  try {
    const r = await fetch(`/session/${ID}/events`, {method:'POST',
      headers:{'Content-Type':'application/json','X-Session-Token':TOKEN},
      body: JSON.stringify({events: batch})});
    if (!r.ok) {
      const d = await r.json();
      document.getElementById('err').textContent =
        'Not recorded: ' + (d.error || r.status) + ' — stop and report this.';
    } else {
      document.getElementById('status').textContent = 'saved';
      setTimeout(() => document.getElementById('status').textContent = '', 1200);
    }
  } catch (e) {
    queue = batch.concat(queue);   // keep unrecorded work for the next attempt
    document.getElementById('err').textContent = 'Not recorded — retrying.';
  }
  sending = false;
  if (queue.length) flush();
}
setInterval(flush, 2000);

document.getElementById('finish').onclick = async () => {
  await flush();
  const r = await fetch(`/session/${ID}/close`, {method:'POST',
    headers:{'X-Session-Token':TOKEN}});
  if (r.ok) location.href = `/session/${ID}/review`;
  else document.getElementById('err').textContent = (await r.json()).error;
};
document.getElementById('abandon').onclick = async () => {
  if (!confirm('Abandon this session? The record is kept but exports nothing.')) return;
  await fetch(`/session/${ID}/abandon`, {method:'POST',
    headers:{'Content-Type':'application/json','X-Session-Token':TOKEN},
    body: JSON.stringify({reason:'abandoned by contributor'})});
  location.href = `/session/${ID}/review`;
};
"""
    return _shell("writing — gauntlet-write", body, script)


def page_review(session: WritingSession) -> str:
    """The reviewer view.

    Ordered verification → facts → attestations → text on purpose. A reviewer
    who sees the text first reasons backwards from how it reads, which CAS
    §6.1 makes a review defect.
    """
    from ai_text_eval.lwe.export import supported_tier

    verification = session.verify()
    facts = session.facts()
    tier, reasons = supported_tier(session)

    def flag(value: bool) -> str:
        return (f"<span class=ok>yes</span>" if value
                else f"<span class=bad>no</span>")

    checks = "".join(
        f"<tr><td>{label}</td><td>{flag(value)}</td></tr>" for label, value in [
            ("Hash chain intact", verification.chain_intact),
            ("Replay reproduces the sealed text", verification.replay_matches),
            ("Session opened over an empty document", verification.opened_empty),
            ("Session sealed", verification.sealed)])

    problems = "".join(f"<li>{_escape(p)}</li>" for p in verification.problems)
    reason_items = "".join(f"<li>{_escape(r)}</li>" for r in reasons)

    fact_rows = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>" for label, value in [
            ("Session duration", f"{facts['duration_ms'] // 1000} s"),
            ("Active", f"{facts['active_ms'] // 1000} s"),
            ("Idle", f"{facts['idle_ms'] // 1000} s"),
            ("Typed characters", facts["inserted_chars"]),
            ("Pasted characters", facts["pasted_chars"]),
            ("Paste events", facts["paste_count"]),
            ("Deleted characters", facts["deleted_chars"]),
            ("Window left", facts["focus_out_count"]),
            ("Final length", f"{facts['final_words']} words")])

    att = session.attestations
    att_rows = "".join(
        f"<tr><td>{label}</td><td>{flag(value)}</td></tr>" for label, value in [
            ("Consented to logging", att.consent_to_logging),
            ("Attested tool-free environment",
             att.environment_free_of_generative_tools),
            ("Acknowledged spot verification", att.spot_verification_acknowledged),
            ("Declared model involvement", att.declared_model_involvement)])

    body = f"""
<h1>Session {_escape(session.session_id)}</h1>
<p class=sub>{_escape(session.intent.contributor)} — state:
  {session.state.value} — evidence supports: <strong>{tier or "nothing"}</strong></p>

<h2>1. Verification</h2>
<table>{checks}</table>
{f"<ul>{problems}</ul>" if problems else ""}
<ul>{reason_items}</ul>

<h2>2. Process facts</h2>
<table>{fact_rows}</table>
<div class=note>These are counts and durations. This tool does not judge
whether a session looks genuine, and no number here should be read as a score:
judging a contributor's process would be the same circularity as judging their
prose, which CAS §5.4 makes inadmissible.</div>

<h2>3. What the contributor affirmed</h2>
<table>{att_rows}</table>
<p class=sub>Recorded at event {session.attestation_event_seq}.</p>

<h2>4. The text</h2>
<div class=note>Shown last, and it is <strong>not</strong> evidence for the
label. CAS §6.1: an opinion about whether prose "seems" consistent with its
label is inadmissible, and recording one is itself a review defect.</div>
<pre class=text>{_escape(session.text)}</pre>

<button id=export>Export evidence package</button>
<p id=out class=sub></p>
"""
    script = """
const ID = location.pathname.split('/')[2];
document.getElementById('export').onclick = async () => {
  const r = await fetch(`/session/${ID}/export`, {method:'POST',
    headers:{'X-Session-Token': sessionStorage.getItem('token:' + ID) || ''}});
  const d = await r.json();
  document.getElementById('out').textContent = r.ok
    ? `Exported at tier ${d.tier || 'none'} to ${d.directory}`
    : (d.error || 'export failed');
};
"""
    return _shell(f"review — {session.session_id}", body, script)


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# =====================================================================
# Entry point
# =====================================================================

def build_server(root: Path | str = DEFAULT_ROOT, port: int = 0) -> ThreadingHTTPServer:
    """Bind a server on loopback. Port 0 asks the OS for a free one."""
    handler = type("BoundHandler", (Handler,), {"store": SessionStore(root)})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - entry point
    import argparse
    import webbrowser

    parser = argparse.ArgumentParser(
        prog="gauntlet-write",
        description="A writing session that records how the text was made.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT),
                        help="directory for session records")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    httpd = build_server(args.root, args.port)
    host, port = httpd.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"gauntlet-write — {url}")
    print(f"sessions: {Path(args.root).resolve()}")
    print("loopback only; nothing leaves this machine. Ctrl-C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0
