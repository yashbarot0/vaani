"""Bob's voice, played through PyAudio so the Bob face can move its mouth in sync.

Kokoro (a small neural voice that runs on the CPU, offline) is used when its model files are in
models/. Otherwise Bob falls back to the robotic espeak-ng. Text arrives as a stream while Bob is
still writing: SentenceStream cuts it into speakable pieces (the first phrase as early as possible),
and Speaker makes the next piece while the current one plays.
"""
import io
import queue
import re
import subprocess
import threading
import wave
import numpy as np
import pyaudio
from . import config, text

KOKORO_RATE = 24000
# A sentence ends at . ! ? followed by space and a new sentence, at a blank line, or before a list item.
_BOUNDARY = re.compile(r"[.!?](?=\s+[A-Z0-9\"'(\[*#`-])|\n\s*\n|\n(?=\s*(?:[-*+]|\d+[.)])\s)")
_COMMA = re.compile(r"[,;:](?=\s)")
_JOIN = re.compile(r"\s(?=(?:and|but|or|so|because|which|that|while|with|when|where|if|then)\s)")
MAX_PIECE = 250        # characters; longer stretches are cut at a comma or space so Kokoro never stalls
FIRST_PHRASE_WORDS = 4  # speak the first phrase at its first comma once it has this many words
FIRST_JOIN_WORDS = 3    # ... or before a joining word ("and", "which", ...) once it has this many
# Kokoro on the CPU needs ~1.2 s for a 14-word sentence but ~0.4 s for 3 words, so a short first
# piece lets Bob start talking while Bob Shell is still writing.


class SentenceStream:
    """Turns streamed text into speakable pieces as soon as each one is complete."""

    def __init__(self):
        self.buf, self.first = "", True

    def push(self, delta):
        self.buf += delta
        return self._drain(final=False)

    def flush(self):
        return self._drain(final=True)

    def _drain(self, final):
        out = []
        while self.buf:
            body = self.buf.lstrip()
            if body.startswith("```"):  # a code block is one piece, once it has closed
                close = self.buf.find("```", len(self.buf) - len(body) + 3)
                if close < 0 and not final:
                    break
                cut = len(self.buf) if close < 0 else close + 3
            else:
                fence = self.buf.find("```")
                safe = self.buf if fence < 0 else self.buf[:fence]
                m = _BOUNDARY.search(safe)
                cut = m.end() if m else None
                if self.first:  # the first phrase goes out early: at a comma or before a joining word
                    for rx, words in ((_COMMA, FIRST_PHRASE_WORDS), (_JOIN, FIRST_JOIN_WORDS)):
                        c = next((c for c in rx.finditer(safe) if len(safe[:c.start()].split()) >= words), None)
                        if c and (cut is None or c.end() < cut):
                            cut = c.end()
                if cut is None and len(safe) > MAX_PIECE:
                    c = safe.rfind(", ", 0, MAX_PIECE)
                    sp = safe.rfind(" ", 0, MAX_PIECE)
                    cut = c + 1 if c > 40 else (sp if sp > 0 else None)
                if cut is None and fence >= 0 and safe.strip():
                    cut = fence  # the text before a code block is complete
                if cut is None:
                    if not final:
                        break
                    cut = len(self.buf)
            piece, self.buf = self.buf[:cut], self.buf[cut:]
            self.first = False
            spoken = text.speakable(piece)
            if spoken:
                out.append(spoken)
        return out


def pieces(whole):
    """All speakable pieces of a finished text."""
    s = SentenceStream()
    return s.push(whole) + s.flush()


def kokoro_available(cfg):
    return (config.MODELS / cfg["bob_kokoro_model"]).exists() and (config.MODELS / "voices-v1.0.bin").exists()


def load_kokoro(cfg):
    """The Kokoro engine, or None if it isn't installed (the caller then uses espeak-ng)."""
    if cfg["bob_tts"] != "kokoro" or not kokoro_available(cfg):
        return None
    try:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = cfg["bob_tts_threads"]  # leave CPU for Whisper and Qwen
        session = ort.InferenceSession(str(config.MODELS / cfg["bob_kokoro_model"]), sess_options=opts,
                                       providers=["CPUExecutionProvider"])
        engine = Kokoro.from_session(session, str(config.MODELS / "voices-v1.0.bin"))
        engine.create("Ready.", voice=cfg["bob_kokoro_voice"], lang="en-us")  # warm up: the first call is slow
        return engine
    except Exception as e:  # noqa: BLE001 - any failure means "use the old voice", never "no voice"
        print(f"Kokoro unavailable, using espeak-ng: {type(e).__name__}: {e}", flush=True)
        return None


class Speaker:
    """start() an utterance, feed() it pieces as they arrive, finish() it. say() does all three."""

    def __init__(self, cfg, engine=None):
        self.pa = pyaudio.PyAudio()
        self.cfg = cfg
        self.engine = engine  # a loaded Kokoro, or None for espeak-ng
        self.level = 0.0  # loudness of the audio being played now, drives the mouth
        self._stop = threading.Event()  # one per utterance, so a stopped one can never be revived by the next
        self._pieces = queue.Queue()
        self._thread = None

    @property
    def speaking(self):
        return bool(self._thread and self._thread.is_alive())

    @property
    def name(self):
        return "kokoro" if self.engine else "espeak-ng"

    def start(self, on_done):
        """Begin an utterance; on_done() runs on the speech thread when it has finished or was stopped."""
        self.stop()
        self._stop, self._pieces = threading.Event(), queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(self._pieces, on_done, self._stop), daemon=True)
        self._thread.start()

    def feed(self, piece):
        self._pieces.put(piece)

    def finish(self):
        self._pieces.put(None)

    def say(self, whole, on_done):
        self.start(on_done)
        for p in pieces(whole):
            self.feed(p)
        self.finish()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(3)

    # ---- speech thread -----------------------------------------------------------------
    @staticmethod
    def _next(src, stop):
        """Next piece from a queue, or None when the utterance is finished or stopped."""
        while not stop.is_set():
            try:
                return src.get(timeout=0.1)
            except queue.Empty:
                pass
        return None

    def _run(self, src, on_done, stop):
        try:
            if self.engine:
                self._speak_kokoro(src, stop)
            else:
                self._speak_espeak(src, stop)
        except Exception as e:  # noqa: BLE001 - a speech failure must never reach the GTK thread
            print(f"speech failed: {type(e).__name__}: {e}", flush=True)
        finally:
            self.level = 0.0
            on_done()

    def _speak_kokoro(self, src, stop):
        ready = queue.Queue(maxsize=2)  # at most two pieces made ahead

        def put(item):
            while not stop.is_set():
                try:
                    ready.put(item, timeout=0.2)
                    return
                except queue.Full:
                    pass

        def make():
            try:
                while (piece := self._next(src, stop)) is not None:
                    audio, _ = self.engine.create(piece, voice=self.cfg["bob_kokoro_voice"],
                                                  speed=self.cfg["bob_kokoro_speed"], lang="en-us")
                    put(audio)
            except Exception as e:  # noqa: BLE001
                print(f"Kokoro failed: {type(e).__name__}: {e}", flush=True)
            finally:
                put(None)

        threading.Thread(target=make, daemon=True).start()
        stream = self.pa.open(format=pyaudio.paInt16, channels=1, rate=KOKORO_RATE, output=True)
        try:
            while (audio := self._next(ready, stop)) is not None:
                pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
                gap = np.zeros(int(KOKORO_RATE * 0.12), np.int16)  # a short breath between pieces
                self._play(stream, np.concatenate([pcm, gap]), pcm.size, stop)
        finally:
            stream.stop_stream()
            stream.close()

    def _play(self, stream, samples, voiced, stop):
        for i in range(0, samples.size, 1024):
            if stop.is_set():
                return
            chunk = samples[i:i + 1024]
            self.level = float(np.sqrt(np.mean((chunk[:max(0, voiced - i)].astype(np.float32) / 32768) ** 2))) \
                if i < voiced else 0.0
            stream.write(chunk.tobytes())

    def _speak_espeak(self, src, stop):
        while (piece := self._next(src, stop)) is not None:
            wav = subprocess.run(["espeak-ng", "--stdout", "--stdin", "-v", self.cfg["bob_voice"],
                                  "-s", str(self.cfg["bob_speech_rate"])],
                                 input=piece.encode(), capture_output=True, timeout=60, check=True).stdout
            w = wave.open(io.BytesIO(wav))  # espeak's streamed header has a bogus length; reading to EOF is fine
            stream = self.pa.open(format=self.pa.get_format_from_width(w.getsampwidth()),
                                  channels=w.getnchannels(), rate=w.getframerate(), output=True)
            try:
                while not stop.is_set():
                    data = w.readframes(1024)
                    if not data:
                        break
                    a = np.frombuffer(data, np.int16).astype(np.float32) / 32768
                    self.level = float(np.sqrt(np.mean(a * a)))
                    stream.write(data)
            finally:
                stream.stop_stream()
                stream.close()
