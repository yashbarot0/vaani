"""Background program: state machine, control socket, workers, paste. GTK owns the main thread."""
import json
import os
import signal
import socket
import subprocess
import threading
import time
import traceback
import requests
from .ui import Pill, Tray  # first: it pins GTK 3 before anything imports gi.repository
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from . import audio, config, engines, text  # noqa: E402
from .keyboard import Keyboard  # noqa: E402


def notify(msg):
    subprocess.Popen(["notify-send", "-a", "Vaani", "-i", "audio-input-microphone", "Vaani", msg])


class Daemon:
    def __init__(self):
        self.cfg = config.load()
        self.mode = self.cfg["mode"]
        self.state, self.kind, self.selection = "loading", "dictate", ""
        self.job = 0  # bumped on every start/cancel so stale worker results are dropped
        self.last_raw = ""
        self.whisper = self.llm = None
        self.llm_ready = False
        self.llm_status = "loading"
        self.rec = audio.Recorder()
        self.clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        try:
            self.kb = Keyboard()
        except OSError as e:
            self.kb = None
            notify(f"Auto-paste is off: cannot open /dev/uinput ({e.strerror}). Run ./setup.sh uinput. "
                   "Text will be copied to the clipboard instead.")
        self.pill = Pill(self.toggle, self.right_click, lambda: list(self.rec.levels), self.mode)
        self.pill.show_idle = self.cfg["show_idle_pill"]
        self.tray = Tray(self)
        self._listen()
        self._start_engines()
        GLib.timeout_add(100, self._watchdog)

    # ---- state -------------------------------------------------------------------------
    def set_state(self, state, label=""):
        self.state = state
        self.pill.set_state(state, label)
        self.tray.set_state(state)
        if state in ("done", "error"):
            job = self.job
            GLib.timeout_add(400 if state == "done" else 3000,
                             lambda: self.job == job and self.state == state and self.set_state("idle") and False)

    def _ui(self, fn, *args):
        GLib.idle_add(lambda: fn(*args) and False)

    def _start_engines(self):
        # Spawned on the main thread: PR_SET_PDEATHSIG fires when the *spawning thread* exits.
        # Both load in parallel; a broken LLM only disables cleanup, never dictation.
        try:
            self.whisper = engines.start_whisper(self.cfg)
        except OSError as e:
            self.set_state("error", "Speech engine missing")
            notify(f"Speech engine failed to start: {e}")
            return
        try:
            self.llm = engines.start_llm(self.cfg)
        except OSError as e:
            self.llm_status = "off"
            notify(f"AI cleanup unavailable: {e}")
        threading.Thread(target=self._wait_engines, daemon=True).start()

    def _wait_engines(self):
        try:
            self.whisper.wait_ready()
            self._ui(self.set_state, "idle")
        except Exception as e:  # noqa: BLE001 - surface any startup failure to the user
            traceback.print_exc()
            self._ui(self.set_state, "error", "Speech engine failed to start")
            notify(f"Speech engine failed to start: {str(e)[:300]}")
            return
        if self.llm:
            try:
                self.llm.wait_ready()
                self.llm_ready, self.llm_status = True, "ready"
            except Exception as e:  # noqa: BLE001
                self.llm_status = "off"
                traceback.print_exc()
                notify(f"AI cleanup unavailable: {str(e)[:300]}")

    def _watchdog(self):
        if self.state == "recording" and self.rec.seconds >= self.cfg["max_seconds"]:
            self.toggle()
        return True

    # ---- user actions (main thread) ----------------------------------------------------
    def toggle(self, kind="dictate"):
        if self.state in ("idle", "done", "error"):
            self._start(kind)
        elif self.state == "recording":
            self._finish()
        elif self.state == "loading":
            notify("Still loading the speech model…")

    def right_click(self):
        if self.state in ("recording", "busy"):
            self.cancel()
        else:
            self.set_mode("raw" if self.mode == "flow" else "flow")

    def cancel(self):
        if self.state == "recording":
            self.rec.stop()
        if self.state in ("recording", "busy"):
            self.job += 1
            self.set_state("idle")

    def set_mode(self, mode):
        self.mode = self.pill.mode = mode
        self.tray.flow.set_active(mode == "flow")
        self.pill.queue_draw()

    def set_show_idle(self, on):
        self.pill.show_idle = on
        self.pill.set_state(self.state, self.pill.label)

    def copy_last(self):
        self.clip.set_text(self.last_raw, -1)

    def quit(self):
        Gtk.main_quit()

    def _start(self, kind):
        self.kind = kind
        # Command mode edits the current selection, which apps expose as the primary selection.
        self.selection = (self.primary.wait_for_text() or "") if kind == "command" else ""
        try:
            self.rec.start()
        except OSError as e:
            self.set_state("error", "Microphone unavailable")
            notify(f"Cannot open the microphone: {e}")
            return
        self.job += 1
        if self.cfg["sounds"]:
            audio.play("device-added")
        self.set_state("recording")
        if self.cfg["live_preview"]:
            threading.Thread(target=self._preview_loop, args=(self.job,), daemon=True).start()

    def _finish(self):
        pcm = self.rec.stop()
        if self.cfg["sounds"]:
            audio.play("device-removed")
        self.set_state("busy", "Transcribing…")
        threading.Thread(target=self._work, args=(self.job, pcm, self.kind, self.selection), daemon=True).start()

    # ---- worker threads ----------------------------------------------------------------
    def _preview_loop(self, job):
        """Re-transcribe the last few seconds while recording and show them in the pill.
        whisper-server handles one request at a time, so stopping waits for at most one preview pass."""
        cfg = self.cfg
        prompt = text.whisper_prompt(cfg["vocabulary"])
        done_at = 0.0
        while self.job == job and self.state == "recording":
            time.sleep(0.3)
            if self.rec.seconds - done_at < 0.5:
                continue  # wait for at least half a second of new audio
            done_at = self.rec.seconds
            pcm = self.rec.snapshot(cfg["preview_seconds"])
            speech = audio.speech_seconds(pcm, cfg["silence_rms"])
            if speech < cfg["min_speech_seconds"]:
                continue  # a pause: keep the last caption rather than show Whisper's silence guesses
            try:
                caption = text.clean_whisper(engines.transcribe(self.whisper, pcm, prompt), speech)
            except requests.RequestException:
                continue
            if caption:
                self._ui(self._set_caption, job, caption)

    def _work(self, job, pcm, kind, selection):
        cfg = self.cfg
        try:
            speech = audio.speech_seconds(pcm, cfg["silence_rms"])
            if speech < cfg["min_speech_seconds"]:
                return self._ui(self._fail, job, "No speech heard")
            raw = engines.transcribe(self.whisper, pcm, text.whisper_prompt(cfg["vocabulary"]))
            raw = text.clean_whisper(raw, speech)
            if not raw:
                return self._ui(self._fail, job, "No speech heard")
            if kind == "command":
                if not self.llm_ready:
                    return self._ui(self._fail, job, "AI model still loading")
                self._ui(self._busy_label, job, "Rewriting…")
                out = text.unwrap(engines.chat(self.llm, text.command_messages(raw, selection),
                                               1024, cfg["llm_timeout"] * 5))
            else:
                out = text.apply_rules(raw, cfg["replacements"], cfg["remove_fillers"])
                if self.mode == "flow" and self.llm_ready and text.should_polish(out):
                    self._ui(self._busy_label, job, "Polishing…")
                    try:
                        words = len(out.split())
                        # Qwen3-4B cleans ~60 words in ~1.5 s on this GPU, so allow time per word.
                        cand = text.unwrap(engines.chat(self.llm, text.clean_messages(out), words * 3 + 60,
                                                        cfg["llm_timeout"] + words / 20))
                        if text.plausible_cleanup(out, cand):
                            out = text.apply_rules(cand, cfg["replacements"], False)
                    except requests.RequestException:
                        pass  # timeout or LLM down: paste the Whisper text rather than nothing
            self._ui(self._deliver, job, raw, out, kind)
        except Exception as e:  # noqa: BLE001 - one bad dictation must not kill the daemon
            traceback.print_exc()
            self._ui(self._fail, job, "Transcription failed")
            notify(f"Transcription failed: {str(e)[:200]}")

    # ---- results (main thread) ---------------------------------------------------------
    def _set_caption(self, job, caption):
        if job == self.job and self.state == "recording":
            self.pill.set_caption(caption)

    def _busy_label(self, job, label):
        if job == self.job and self.state == "busy":
            self.pill.set_state("busy", label)

    def _fail(self, job, msg):
        if job == self.job:
            self.set_state("error", msg)

    def _deliver(self, job, raw, out, kind):
        if job != self.job:
            return  # cancelled while processing
        self.last_raw = raw
        self.tray.set_last(raw)
        if self.cfg["history"]:
            config.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(config.HISTORY_FILE, "a") as f:
                f.write(json.dumps({"time": time.time(), "raw": raw, "text": out}) + "\n")
        if self.cfg["trailing_space"] and kind == "dictate":
            out += " "
        self._paste(out, set_primary=kind == "dictate")

    def _paste(self, out, set_primary):
        saved = self._save_clipboard()
        self.clip.set_text(out, -1)
        if set_primary:  # terminals paste the primary selection on Shift+Insert
            self.primary.set_text(out, -1)
        if not self.kb:
            self.set_state("done", "Copied: Ctrl+V")
            return

        def press():
            self.kb.combo(self.cfg["paste_keys"])
            GLib.timeout_add(int(self.cfg["restore_delay"] * 1000), lambda: self._restore(saved) and False)
            self.set_state("done", "Pasted")
            return False
        GLib.timeout_add(60, press)

    def _save_clipboard(self):
        # shortcut: only the text or image form of the old clipboard is restored (not files or rich text).
        if self.clip.wait_is_image_available():
            return "image", self.clip.wait_for_image()
        if self.clip.wait_is_text_available():
            return "text", self.clip.wait_for_text()
        return None

    def _restore(self, saved):
        if saved is None:
            self.clip.clear()
        elif saved[0] == "image" and saved[1] is not None:
            self.clip.set_image(saved[1])
        elif saved[0] == "text" and saved[1] is not None:
            self.clip.set_text(saved[1], -1)

    # ---- control socket ----------------------------------------------------------------
    def _listen(self):
        path = str(config.SOCKET)
        if os.path.exists(path):
            try:
                socket.socket(socket.AF_UNIX).connect(path)
                raise SystemExit("vaani is already running")
            except ConnectionRefusedError:
                os.unlink(path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        os.chmod(path, 0o600)
        self.sock.listen()
        GLib.io_add_watch(self.sock.fileno(), GLib.IO_IN, self._accept)

    def _accept(self, *_):
        conn, _ = self.sock.accept()
        with conn:
            conn.settimeout(1)
            try:
                reply = self.command(conn.recv(256).decode().split())
            except Exception as e:  # noqa: BLE001
                reply = f"error: {e}"
            try:
                conn.sendall(reply.encode())
            except OSError:
                pass
        return True

    def command(self, args):
        cmd = args[0] if args else ""
        if cmd in ("toggle", "command"):
            self.toggle("command" if cmd == "command" else "dictate")
        elif cmd == "cancel":
            self.cancel()
        elif cmd == "mode" and len(args) == 2 and args[1] in ("flow", "raw"):
            self.set_mode(args[1])
        elif cmd == "quit":
            GLib.idle_add(self.quit)
        elif cmd != "status":
            return "error: unknown command"
        return f"{self.state} mode={self.mode} llm={self.llm_status}"

    def shutdown(self):
        for s in (self.whisper, self.llm):
            if s:
                s.stop()
        if self.kb:
            self.kb.close()
        if os.path.exists(config.SOCKET):
            os.unlink(config.SOCKET)


def run():
    d = Daemon()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: Gtk.main_quit() or False)
    try:
        Gtk.main()
    finally:
        d.shutdown()
