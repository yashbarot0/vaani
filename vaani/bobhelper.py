"""Vaani Bob helper: the only part of Vaani that uses the internet.

It runs as its own service, outside vaani.service's network sandbox. It keeps one IBM Bob Shell
running in server mode (`bob acp`, the Agent Client Protocol: JSON-RPC over stdin/stdout), so a
question skips Bob's ~2 s startup, and it streams Bob's answer back to the offline daemon over a
local socket as Bob writes it. Every question runs in read-only ask mode: the helper sets it before
prompting, checks it, cancels if Bob ever leaves it, and refuses anything Bob asks the client to do.
It owns the API key (from its unit's EnvironmentFile) and the conversation. Transcripts and
replies are never logged.

Conversation: one Bob session per day, resumed for every question and kept across restarts, so
Bob's task list gets one Vaani entry a day instead of one per question.
"""
import collections
import datetime
import itertools
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from . import config

PERSONA = ("Answer my questions in at most three short plain sentences: your answers are shown in a small "
           "on-screen card and read aloud. No markdown, lists, headings or code blocks unless I ask.")
WORKDIR = Path.home() / ".local/share/vaani/bob"          # empty folder = Bob's read-only workspace
STATE_FILE = Path.home() / ".local/state/vaani/bob-session.json"
NETWORK_WORDS = ("enotfound", "econnrefused", "etimedout", "eai_again", "fetch failed", "network")


class BobError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def classify(message):
    """Map a Bob error message to a short, key-free BobError."""
    m = message.lower()
    if "api key" in m or "unauthorized" in m or "401" in m:
        return BobError("auth", "Bob key expired or invalid")
    if any(w in m for w in NETWORK_WORDS):
        return BobError("network", "Can't reach IBM Bob")
    return BobError("bob-failed", "Bob couldn't answer")


def first_prompt(question, today):
    # The opening words become the session title in Bob's task list.
    return f"Vaani voice session, {today}. {PERSONA} First question: {question}"


class Acp:
    """One long-running `bob acp` process, spoken to with JSON-RPC 2.0 over its stdin and stdout."""

    def __init__(self, workdir, on_update):
        self.proc = subprocess.Popen(["bob", "acp", "--trust", "--accept-license"], cwd=workdir,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, start_new_session=True)
        self.on_update = on_update
        self.write_lock = threading.Lock()
        self.ids = itertools.count(1)
        self.pending = {}
        self.err_tail = collections.deque(maxlen=5)  # in memory only; redacted before any log line
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: self.err_tail.extend(self.proc.stderr), daemon=True).start()
        self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}, 60)

    def alive(self):
        return self.proc.poll() is None

    def _send(self, msg):
        with self.write_lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                slot = self.pending.get(msg["id"])
                if slot:
                    slot[1] = msg
                    slot[0].set()
            elif "id" in msg and "method" in msg:
                # Bob asking the client to act (permissions, files, terminals): always refuse.
                reply = {"jsonrpc": "2.0", "id": msg["id"]}
                if msg["method"] == "session/request_permission":
                    reply["result"] = {"outcome": {"outcome": "cancelled"}}
                else:
                    reply["error"] = {"code": -32601, "message": "not supported by Vaani"}
                try:
                    self._send(reply)
                except OSError:
                    pass
            elif msg.get("method") == "session/update":
                self.on_update(msg.get("params") or {})
        for slot in list(self.pending.values()):  # the process ended: wake every waiting call
            slot[0].set()

    def call(self, method, params, timeout):
        with self.write_lock:
            i = next(self.ids)
        slot = self.pending[i] = [threading.Event(), None]
        try:
            self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
            if not slot[0].wait(timeout):
                raise TimeoutError(method)
        except OSError:
            raise BobError("bob-failed", "Bob stopped unexpectedly")
        finally:
            self.pending.pop(i, None)
        msg = slot[1]
        if msg is None:
            raise BobError("bob-failed", "Bob stopped unexpectedly")
        if "error" in msg:
            err = msg["error"] or {}
            raise classify(f"{err.get('message', '')} {json.dumps(err.get('data', ''))}")
        return msg.get("result") or {}

    def notify(self, method, params):
        try:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})
        except OSError:
            pass

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def log_failure(self, code):
        key = os.environ.get("BOB_API_KEY")
        last = (list(self.err_tail) or [""])[-1].strip()[:200]
        print(f"bob failed ({code}): {last.replace(key, '***') if key else last}", flush=True)


class Helper:
    def __init__(self, cfg, workdir=WORKDIR, state_file=STATE_FILE):
        self.cfg, self.workdir, self.state_file = cfg, Path(workdir), Path(state_file)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.acp = None
        self.session = None   # the session open in self.acp
        self.mode = None      # its current mode, kept up to date from Bob's updates
        self.fresh = False    # no question asked in it yet: the next one carries the persona
        self.on_delta = None
        self.cancelled = threading.Event()

    # ---- daily conversation ------------------------------------------------------------
    def _state(self):
        today = str(datetime.date.today())
        try:
            state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            state = {}
        return state if state.get("date") == today else {"date": today}  # a new day starts a new session

    def _save(self, state):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state))

    def reset(self):
        self._save({"date": str(datetime.date.today())})

    # ---- Bob process and session -------------------------------------------------------
    def _on_update(self, params):
        if params.get("sessionId") != self.session:
            return
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "current_mode_update":
            self.mode = update.get("currentModeId")
            if self.mode != "ask":
                self.cancel()  # never let a voice question run outside read-only ask mode
        elif kind == "agent_message_chunk" and self.on_delta:
            content = update.get("content") or {}
            if content.get("type") == "text" and content.get("text"):
                self.on_delta(content["text"])

    def _ensure(self, create):
        """A running `bob acp` with today's session open in ask mode. Caller holds self.lock."""
        if not (self.acp and self.acp.alive()):
            if self.acp:
                self.acp.close()
            self.acp, self.session = None, None
            self.acp = Acp(self.workdir, self._on_update)
        state = self._state()
        if self.session and self.session == state.get("task_id"):
            pass
        else:
            self.session, result = None, None
            if state.get("task_id"):
                try:
                    result = self.acp.call("session/resume", {"sessionId": state["task_id"], "cwd": str(self.workdir),
                                                              "mcpServers": []}, 60)
                    self.session, self.fresh = state["task_id"], False
                except BobError as e:
                    if e.code == "auth":
                        raise
            if self.session is None:
                if not create:
                    return None
                result = self.acp.call("session/new", {"cwd": str(self.workdir), "mcpServers": []}, 60)
                self.session, self.fresh = result["sessionId"], True
                self._save({"date": state["date"], "task_id": self.session})
            self.mode = (result.get("modes") or {}).get("currentModeId")
        if self.mode != "ask":
            self.acp.call("session/set_mode", {"sessionId": self.session, "modeId": "ask"}, 30)
            self.mode = "ask"
        return self.session

    def warm_up(self):
        """Start Bob and reopen today's session before the first question, so it pays no startup."""
        with self.lock:
            try:
                self._ensure(create=False)
            except (BobError, TimeoutError, OSError) as e:
                print(f"warm-up failed: {type(e).__name__}", flush=True)

    # ---- questions ---------------------------------------------------------------------
    def ask(self, question, on_delta=lambda _t: None):
        """Bob's full answer; on_delta(text) receives it piece by piece as Bob writes."""
        self.cancel()  # a new question replaces one still running
        with self.lock:
            self.cancelled.clear()
            try:
                sid = self._ensure(create=True)
                parts = []
                prompt = first_prompt(question, self._state()["date"]) if self.fresh else question
                self.on_delta = lambda t: (parts.append(t), on_delta(t))
                if self.mode != "ask":
                    raise BobError("bob-failed", "Bob is not in ask mode")
                result = self.acp.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
                                       self.cfg["bob_timeout"])
            except TimeoutError:
                if self.acp and self.session:
                    self.acp.notify("session/cancel", {"sessionId": self.session})
                raise BobError("timeout", "Bob took too long")
            except BobError as e:
                if self.acp:
                    self.acp.log_failure(e.code)
                if e.code == "bob-failed" and self.acp and not self.acp.alive():
                    self.acp = None  # restarted on the next question
                raise
            finally:
                self.on_delta = None
            if self.cancelled.is_set() or result.get("stopReason") == "cancelled":
                raise BobError("cancelled", "Cancelled")
            self.fresh = False
            reply = "".join(parts).strip()
            if not reply:
                raise BobError("bob-failed", "Bob couldn't answer")
            return reply

    def cancel(self):
        self.cancelled.set()
        if self.acp and self.session:
            self.acp.notify("session/cancel", {"sessionId": self.session})

    def handle(self, req, emit=lambda _m: None):
        op = req.get("op")
        if op == "ask":
            question = str(req.get("text", "")).strip()[:4000]
            if not question:
                raise BobError("empty", "Empty question")
            return {"ok": True, "reply": self.ask(question, lambda t: emit({"delta": t}))}
        if op == "cancel":
            self.cancel()
            return {"ok": True}
        if op == "reset":
            self.reset()
            return {"ok": True}
        if op == "status":
            return {"ok": True, "conversation": bool(self._state().get("task_id")),
                    "bob_running": bool(self.acp and self.acp.alive())}
        raise BobError("bad-op", "Unknown request")


def _serve_one(helper, conn):
    def emit(msg):
        try:
            conn.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            pass  # the daemon went away (cancelled or timed out); keep going quietly

    with conn:
        try:
            reply = helper.handle(json.loads(conn.makefile().readline() or "{}"), emit)
        except BobError as e:
            reply = {"ok": False, "error": e.code, "message": str(e)}
        except Exception as e:  # noqa: BLE001 - one bad request must not kill the helper
            print(f"helper error: {type(e).__name__}", flush=True)
            reply = {"ok": False, "error": "internal", "message": "Bob helper error"}
        emit(reply)


def main():
    if not os.environ.get("BOB_API_KEY"):
        sys.exit("BOB_API_KEY is not set: put it in ~/.config/vaani/bob.env")
    helper = Helper(config.load())
    path = str(config.BOB_SOCKET)
    if os.path.exists(path):
        os.unlink(path)  # systemd runs a single instance
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen()
    print(f"listening on {path}", flush=True)
    threading.Thread(target=helper.warm_up, daemon=True).start()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_serve_one, args=(helper, conn), daemon=True).start()
