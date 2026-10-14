"""whisper.cpp and llama.cpp servers, run as child processes bound to loopback."""
import collections
import ctypes
import io
import signal
import subprocess
import threading
import time
import wave
import requests
from . import config


def _die_with_parent():
    # PR_SET_PDEATHSIG with SIGKILL: whisper-server ignores SIGTERM, and orphans would hold VRAM.
    ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)


class Server:
    def __init__(self, argv, port, health):
        self.base = f"http://127.0.0.1:{port}"
        self.health = self.base + health
        self.log = collections.deque(maxlen=20)  # kept in memory only; servers can echo transcripts
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, errors="replace", preexec_fn=_die_with_parent)
        threading.Thread(target=lambda: self.log.extend(self.proc.stderr), daemon=True).start()

    def wait_ready(self, timeout=180):
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError(f"{self.proc.args[0]} exited:\n" + "".join(self.log))
            try:
                if requests.get(self.health, timeout=1).status_code == 200:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"{self.proc.args[0]} not ready after {timeout}s")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            self.proc.kill()  # whisper-server ignores SIGTERM
            self.proc.wait()


def start_whisper(cfg):
    return Server([str(config.BIN / "whisper-server"), "-m", str(config.MODELS / cfg["whisper_model"]),
                   "--host", "127.0.0.1", "--port", str(cfg["whisper_port"]), "-l", "en", "-nt",
                   "-t", "4", "-sns", "-nth", "0.6"], cfg["whisper_port"], "/")


def start_llm(cfg):
    return Server([str(config.BIN / "llama-server"), "-m", str(config.MODELS / cfg["llm_model"]),
                   "--host", "127.0.0.1", "--port", str(cfg["llm_port"]), "-ngl", "99", "-c", "4096",
                   "-np", "1", "--no-webui"], cfg["llm_port"], "/health")


def wav_bytes(pcm, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def transcribe(server, pcm, prompt=""):
    r = requests.post(server.base + "/inference", timeout=120,
                      files={"file": ("audio.wav", wav_bytes(pcm), "audio/wav")},
                      data={"response_format": "text", "temperature": "0.0", "prompt": prompt})
    r.raise_for_status()
    return r.text.strip()


def chat(server, messages, max_tokens, timeout):
    r = requests.post(server.base + "/v1/chat/completions", timeout=timeout,
                      json={"messages": messages, "temperature": 0, "max_tokens": max_tokens})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
