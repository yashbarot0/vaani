"""Bob helper and speech-splitting tests. The helper runs against a fake `bob acp` that speaks the same
JSON-RPC protocol, so no network, key or cost is involved. The fake refuses any prompt outside ask mode."""
import datetime
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from vaani import config
from vaani.bobhelper import PERSONA, BobError, Helper, _serve_one, classify
from vaani.speech import SentenceStream, pieces

FAKE_ACP = r'''#!/usr/bin/env python3
import json, os, sys, threading
log = open(os.environ["FAKE_BOB_LOG"], "a")
out_lock = threading.Lock()
modes, turns, cancel = {}, {}, threading.Event()
def send(m):
    with out_lock:
        sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
def ok(i, result): send({"jsonrpc": "2.0", "id": i, "result": result})
def err(i, message): send({"jsonrpc": "2.0", "id": i, "error": {"code": -32000, "message": message}})
def handle(msg):
    i, method, p = msg.get("id"), msg.get("method"), msg.get("params", {})
    log.write(json.dumps({"method": method, "params": p}) + "\n"); log.flush()
    if method == "initialize":
        return ok(i, {"protocolVersion": 1, "authMethods": [{"id": "sso"}]})
    if method == "session/new":
        sid = f"task-{os.getpid()}-{len(modes)}"; modes[sid] = "agent"
        return ok(i, {"sessionId": sid, "modes": {"currentModeId": "agent"}})
    if method == "session/resume":
        if p["sessionId"] == "task-gone":
            return err(i, "Session not found")
        modes[p["sessionId"]] = "ask"
        return ok(i, {"modes": {"currentModeId": "ask"}})
    if method == "session/set_mode":
        modes[p["sessionId"]] = p["modeId"]; return ok(i, {})
    if method == "session/cancel":
        return cancel.set()
    if method == "session/prompt":
        sid, text = p["sessionId"], p["prompt"][0]["text"]
        if modes.get(sid) != "ask":
            return err(i, "FAKE: prompt outside ask mode")
        if "badkey" in text:
            return err(i, "Request Failed. Invalid or expired API key.")
        if "perm" in text:
            send({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission", "params": {"sessionId": sid}})
        if "sleep" in text:
            cancel.clear(); cancel.wait(30); return ok(i, {"stopReason": "cancelled"})
        if "switch" in text:
            cancel.clear()
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": "current_mode_update", "currentModeId": "agent"}}})
            cancel.wait(30); return ok(i, {"stopReason": "cancelled"})
        turns[sid] = turns.get(sid, 0) + 1
        for w in f"turn {turns[sid]}: {text[-20:]}".split(" "):
            send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": w + " "}}}})
        return ok(i, {"stopReason": "end_turn"})
for line in sys.stdin:
    msg = json.loads(line)
    if "method" in msg:
        threading.Thread(target=handle, args=(msg,), daemon=True).start()  # so cancel can arrive mid-prompt
    else:
        log.write(json.dumps({"client_reply": msg}) + "\n"); log.flush()
'''

CFG = dict(config.DEFAULTS)


def make_helper(tmp, **overrides):
    bindir = Path(tmp, "bin")
    bindir.mkdir(exist_ok=True)
    (bindir / "bob").write_text(FAKE_ACP)
    (bindir / "bob").chmod(0o755)
    os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
    os.environ["FAKE_BOB_LOG"] = str(Path(tmp, "bob.log"))
    return Helper({**CFG, **overrides}, workdir=Path(tmp, "work"), state_file=Path(tmp, "session.json"))


def calls(tmp, method=None):
    entries = [json.loads(line) for line in Path(tmp, "bob.log").read_text().splitlines()]
    return [e for e in entries if method is None or e.get("method") == method]


def expect_error(code, fn, *args):
    try:
        fn(*args)
    except BobError as e:
        assert e.code == code, (e.code, code)
        return
    raise AssertionError(f"expected BobError {code}")


def test_classify_errors():
    assert classify("Request Failed. Invalid or expired API key.").code == "auth"
    assert classify("TypeError: fetch failed").code == "network"
    assert classify("something else").code == "bob-failed"


def test_streams_answer_in_ask_mode_with_persona_only_first():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        deltas = []
        reply = h.ask("first question", deltas.append)
        assert reply.startswith("turn 1") and len(deltas) > 2 and "".join(deltas).strip() == reply
        assert h.ask("second").startswith("turn 2")
        prompts = [c["params"]["prompt"][0]["text"] for c in calls(tmp, "session/prompt")]
        assert prompts[0].startswith("Vaani voice session, ") and PERSONA in prompts[0] and prompts[1] == "second"
        methods = [c["method"] for c in calls(tmp) if "method" in c]
        assert methods.index("session/set_mode") < methods.index("session/prompt")  # ask mode before any prompt
        assert len(calls(tmp, "session/new")) == 1 and len(calls(tmp, "initialize")) == 1  # one Bob, one session


def test_resume_after_restart_new_day_reset_and_lost_session():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        h.ask("first")
        saved = json.loads(Path(tmp, "session.json").read_text())["task_id"]
        h2 = make_helper(tmp)
        h2.warm_up()                                   # reopens today's session before any question
        assert calls(tmp, "session/resume")[-1]["params"]["sessionId"] == saved
        assert h2.ask("after restart").startswith("turn 1") and len(calls(tmp, "session/new")) == 1
        Path(tmp, "session.json").write_text(json.dumps({"date": "2000-01-01", "task_id": saved}))
        make_helper(tmp).ask("new day")
        assert len(calls(tmp, "session/new")) == 2      # yesterday's session is not reused
        h2.reset()
        h2.ask("after reset")
        assert len(calls(tmp, "session/new")) == 3
        Path(tmp, "session.json").write_text(json.dumps({"date": str(datetime.date.today()), "task_id": "task-gone"}))
        assert make_helper(tmp).ask("lost").startswith("turn 1")  # a vanished session is replaced
        expect_error("auth", h2.ask, "badkey")


def test_cancel_and_leaving_ask_mode_stop_the_answer():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        h.ask("warm")
        for prompt, by_user in (("sleep please", True), ("switch to agent", False)):
            errors = []
            t = threading.Thread(target=lambda: errors.append(_code(h.ask, prompt)))
            t.start()
            time.sleep(0.5)
            start = time.time()
            if by_user:
                h.cancel()
            t.join(5)
            assert errors == ["cancelled"] and time.time() - start < 3, (prompt, errors)
        assert h.ask("still works").startswith("turn")


def test_permission_requests_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        h.ask("perm please")
        time.sleep(0.2)
        replies = [e["client_reply"] for e in calls(tmp) if "client_reply" in e]
        assert replies and replies[0]["id"] == "perm-1" and replies[0]["result"]["outcome"]["outcome"] == "cancelled"


def test_socket_protocol_streams_deltas():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        path = str(Path(tmp, "bob.sock"))
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen()
        threading.Thread(target=lambda: [_serve_one(h, srv.accept()[0]) for _ in range(3)], daemon=True).start()

        def call(req):
            with socket.socket(socket.AF_UNIX) as c:
                c.connect(path)
                c.sendall((json.dumps(req) + "\n").encode())
                return [json.loads(line) for line in c.makefile()]
        lines = call({"op": "ask", "text": "hello"})
        assert all("delta" in m for m in lines[:-1]) and len(lines) > 2
        assert lines[-1]["ok"] and lines[-1]["reply"].startswith("turn 1")
        assert call({"op": "ask", "text": "  "}) == [{"ok": False, "error": "empty", "message": "Empty question"}]
        assert call({"op": "nope"})[0]["error"] == "bad-op"


def test_speech_pieces_from_whole_text():
    assert pieces("A mutex locks. A semaphore counts, though! Does that help?") == \
        ["A mutex locks.", "A semaphore counts, though!", "Does that help?"]
    assert pieces("Version 3.5 is out, e.g. today. Use it.") == ["Version 3.5 is out,", "e.g. today.", "Use it."]
    long = ("word " * 30 + "end, ") * 3 + "done."
    parts = pieces(long)
    assert len(parts) > 1 and all(len(p) <= 250 for p in parts) and " ".join(parts).split() == long.split()
    assert pieces("   ") == []
    assert pieces("TCP is connection-oriented and guarantees order. UDP is not, and that is fine.") == \
        ["TCP is connection-oriented", "and guarantees order.", "UDP is not, and that is fine."]  # only the first piece is cut early
    assert pieces("So and then it works.")[0] == "So and then it works."  # needs three words before a joining word


def test_sentence_stream_speaks_early_and_waits_for_code():
    s = SentenceStream()
    got = []
    for tok in ["A mutex", " is a lock", ", and only", " one thread", " holds it.", " A sema", "phore counts", "."]:
        got.append(s.push(tok))
    assert got[2] == ["A mutex is a lock,"]                  # first phrase spoken at its comma
    assert got[5] == ["and only one thread holds it."]       # a sentence ends once the next one starts
    assert s.flush() == ["A semaphore counts."]
    s = SentenceStream()
    assert s.push("Run this. ```sh\ngit log. Then") == ["Run this."]   # nothing inside an open code block
    assert s.push(" more\n``` Done. Next") == ["(code shown on screen)", "Done."]
    assert s.flush() == ["Next"]


def _code(fn, *args):
    try:
        fn(*args)
    except BobError as e:
        return e.code
    return "ok"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all bob tests passed")
