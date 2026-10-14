"""Microphone capture into memory. Audio never touches disk."""
import collections
import subprocess
import numpy as np
import pyaudio

RATE, CHUNK = 16000, 800  # 50 ms chunks drive the pill's level bars at 20 Hz
SOUNDS = "/usr/share/sounds/freedesktop/stereo/"


class Recorder:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self.chunks = []
        self.levels = collections.deque([0.0] * 5, maxlen=5)

    def start(self):
        self.chunks = []
        self.stream = self.pa.open(format=pyaudio.paInt16, channels=1, rate=RATE, input=True,
                                   frames_per_buffer=CHUNK, stream_callback=self._on_chunk)

    def _on_chunk(self, data, frames, time_info, status):
        self.chunks.append(data)
        a = np.frombuffer(data, np.int16).astype(np.float32) / 32768
        self.levels.append(float(np.sqrt(np.mean(a * a))))
        return None, pyaudio.paContinue

    @property
    def seconds(self):
        return len(self.chunks) * CHUNK / RATE

    def snapshot(self, seconds):
        """The last `seconds` of the ongoing recording, for the live preview."""
        n = max(1, int(seconds * RATE / CHUNK))
        return np.frombuffer(b"".join(self.chunks[-n:]), np.int16)

    def stop(self):
        """Close the mic and return the recording as int16 samples."""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
        return np.frombuffer(b"".join(self.chunks), np.int16)


def speech_seconds(pcm, threshold):
    """Seconds of audio whose 50 ms RMS is above the silence threshold."""
    n = len(pcm) // CHUNK
    if n == 0:
        return 0.0
    w = pcm[: n * CHUNK].astype(np.float32).reshape(n, CHUNK) / 32768
    return float((np.sqrt((w * w).mean(1)) > threshold).sum()) * CHUNK / RATE


def play(name):
    subprocess.Popen(["paplay", SOUNDS + name + ".oga"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
