"""Bob helper tests against a fake `bob` executable, so no network, key or cost is involved."""
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from vaani import bobhelper, config
from vaani.bobhelper import BobError, Helper, build_argv, parse_result

FAKE_BOB = r'''#!/usr/bin/env python3
import json, sys, time
args = sys.argv[1:]
prompt = args[-1]
if "sleep" in prompt:
    time.sleep(30)
if "badkey" in prompt:
    sys.stderr.write("Error: Request Failed. Invalid or expired API key.\n"); sys.exit(1)
task = args[args.index("--resume") + 1] if "--resume" in args else "task-1"
turn = 2 if "--resume" in args else 1
print(json.dumps({"type": "result", "status": "success",
                  "stats": {"task_id": task},
                  "last_message": f"turn {turn}: {prompt[-20:]}"}))
'''

CFG = dict(config.DEFAULTS)


def make_helper(tmp, **overrides):
    bindir = Path(tmp, "bin")
    bindir.mkdir(exist_ok=True)
    (bindir / "bob").write_text(FAKE_BOB)
    (bindir / "bob").chmod(0o755)
    os.environ["PATH"] = f"{bindir}:{os.environ['PATH']}"
    return Helper({**CFG, **overrides}, workdir=Path(tmp, "work"), state_file=Path(tmp, "session.json"))


def expect_error(code, fn, *args):
    try:
        fn(*args)
    except BobError as e:
        assert e.code == code, (e.code, code)
        return
    raise AssertionError(f"expected BobError {code}")


def test_parse_result():
    ok = '{"type":"result","status":"success","stats":{"task_id":"t1","session_costs":0.023},"last_message":" Hi. "}'
    assert parse_result("noise\n" + ok + "\n", "", 0) == ("Hi.", "t1")
    expect_error("auth", parse_result, "", "Error: Request Failed. Invalid or expired API key.", 1)
    expect_error("auth", parse_result, "", "Error: Bob API key is required.", 1)
    expect_error("network", parse_result, "", "TypeError: fetch failed", 1)
    expect_error("bob-failed", parse_result, "not json", "", 0)


def test_title_and_persona_only_on_first_turn_and_no_flag_injection():
    first = build_argv("what is a mutex", None, "2026-10-16")
    assert first[-1].startswith("Vaani voice session, 2026-10-16. ") and bobhelper.PERSONA in first[-1]
    assert "--resume" not in first and first[first.index("--mode") + 1] == "ask"
    follow = build_argv("--yolo and then?", "t1", "2026-10-16")
    assert follow[-3:] == ["--resume", "t1", "yolo and then?"]
    assert not any("cost" in a or "turns" in a for a in first)  # no spending limits


def test_one_conversation_per_day_kept_across_restarts():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        assert h.ask("first").startswith("turn 1")
        assert h.ask("second").startswith("turn 2")
        assert make_helper(tmp).ask("after restart").startswith("turn 2")   # resumed from the saved task
        state = Path(tmp, "session.json")
        state.write_text(json.dumps({"date": "2000-01-01", "task_id": "task-old"}))
        assert make_helper(tmp).ask("new day").startswith("turn 1")         # yesterday's task is not reused
        h.reset()
        assert h.ask("after reset").startswith("turn 1")
        expect_error("auth", h.ask, "badkey")


def test_cancel_kills_running_call():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        errors = []
        t = threading.Thread(target=lambda: errors.append(_code(h.ask, "sleep please")))
        t.start()
        time.sleep(1)
        start = time.time()
        h.cancel()
        t.join(5)
        assert errors == ["cancelled"] and time.time() - start < 3


def test_socket_protocol():
    with tempfile.TemporaryDirectory() as tmp:
        h = make_helper(tmp)
        path = str(Path(tmp, "bob.sock"))
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen()
        threading.Thread(target=lambda: [bobhelper._serve_one(h, srv.accept()[0]) for _ in range(3)], daemon=True).start()

        def call(req):
            with socket.socket(socket.AF_UNIX) as c:
                c.connect(path)
                c.sendall((json.dumps(req) + "\n").encode())
                return json.loads(c.makefile().readline())
        assert call({"op": "ask", "text": "hello"})["reply"].startswith("turn 1")
        assert call({"op": "ask", "text": "  "}) == {"ok": False, "error": "empty", "message": "Empty question"}
        assert call({"op": "nope"})["error"] == "bad-op"


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
