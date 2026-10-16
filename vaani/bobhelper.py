"""Vaani Bob helper: the only part of Vaani that uses the internet.

It runs as its own service, outside vaani.service's network sandbox. The offline daemon sends it a
question over a local socket; it runs IBM Bob Shell in read-only ask mode from an empty folder and
returns the answer. It owns the API key (from its unit's EnvironmentFile) and the conversation.
Transcripts and replies are never logged.

Conversation: one Bob task per day, resumed for every question and kept across restarts, so Bob's
task list gets one Vaani entry a day instead of one per question.
"""
import datetime
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


class BobError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def build_argv(question, task_id, today):
    argv = ["bob", "run", "--mode", "ask", "--trust", "--accept-license", "-f", "json"]
    question = question.lstrip("-– ")  # never let a transcript be parsed as a flag
    if task_id:
        return argv + ["--resume", task_id, question]
    # First turn of the day: the opening words become the task title in Bob's task list.
    return argv + [f"Vaani voice session, {today}. {PERSONA} First question: {question}"]


def parse_result(out, err, returncode):
    """Return (reply, task_id) from `bob run -f json`, or raise BobError."""
    lines = [line for line in out.splitlines() if line.strip()]
    if returncode == 0 and lines:
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError:
            result = {}
        if result.get("status") == "success" and str(result.get("last_message", "")).strip():
            return result["last_message"].strip(), (result.get("stats") or {}).get("task_id")
    text = (err + "\n" + out).lower()
    if "api key" in text:
        raise BobError("auth", "Bob key expired or invalid")
    if any(w in text for w in ("enotfound", "econnrefused", "etimedout", "eai_again", "fetch failed", "network")):
        raise BobError("network", "Can't reach IBM Bob")
    raise BobError("bob-failed", "Bob couldn't answer")


class Helper:
    def __init__(self, cfg, workdir=WORKDIR, state_file=STATE_FILE):
        self.cfg, self.workdir, self.state_file = cfg, Path(workdir), Path(state_file)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.proc = None

    def _session(self):
        today = str(datetime.date.today())
        try:
            state = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            state = {}
        return state if state.get("date") == today else {"date": today}  # a new day starts a new task

    def _save(self, state):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state))

    def ask(self, question):
        self.cancel()  # a new question replaces one still running
        with self.lock:
            state = self._session()
            proc = self.proc = subprocess.Popen(
                build_argv(question, state.get("task_id"), state["date"]), cwd=self.workdir,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)  # own process group, so cancel kills node's children too
            try:
                out, err = proc.communicate(timeout=self.cfg["bob_timeout"])
            except subprocess.TimeoutExpired:
                self._kill(proc)
                proc.communicate()
                raise BobError("timeout", "Bob took too long")
            finally:
                self.proc = None
            if proc.returncode < 0:
                raise BobError("cancelled", "Cancelled")
            try:
                reply, task_id = parse_result(out, err, proc.returncode)
            except BobError as e:
                if e.code == "bob-failed" and state.get("task_id"):
                    self.reset()  # the saved task may be gone; the next question starts a fresh one
                key = os.environ.get("BOB_API_KEY")
                last = (err.strip().splitlines() or [""])[-1][:200]
                print(f"bob failed ({e.code}): {last.replace(key, '***') if key else last}", flush=True)
                raise
            if task_id:
                self._save({"date": state["date"], "task_id": task_id})
            return reply

    def cancel(self):
        if self.proc:
            self._kill(self.proc)

    def reset(self):
        self._save({"date": str(datetime.date.today())})

    @staticmethod
    def _kill(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def handle(self, req):
        op = req.get("op")
        if op == "ask":
            question = str(req.get("text", "")).strip()[:4000]
            if not question:
                raise BobError("empty", "Empty question")
            return {"ok": True, "reply": self.ask(question)}
        if op == "cancel":
            self.cancel()
            return {"ok": True}
        if op == "reset":
            self.reset()
            return {"ok": True}
        if op == "status":
            return {"ok": True, "conversation": bool(self._session().get("task_id"))}
        raise BobError("bad-op", "Unknown request")


def _serve_one(helper, conn):
    with conn:
        try:
            reply = helper.handle(json.loads(conn.makefile().readline() or "{}"))
        except BobError as e:
            reply = {"ok": False, "error": e.code, "message": str(e)}
        except Exception as e:  # noqa: BLE001 - one bad request must not kill the helper
            print(f"helper error: {type(e).__name__}", flush=True)
            reply = {"ok": False, "error": "internal", "message": "Bob helper error"}
        try:
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass


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
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_serve_one, args=(helper, conn), daemon=True).start()
