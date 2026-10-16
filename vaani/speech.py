"""Bob's voice: espeak-ng audio played through PyAudio, so the Bob face can move its mouth in sync."""
import io
import subprocess
import threading
import wave
import numpy as np
import pyaudio


class Speaker:
    def __init__(self, voice, rate):
        self.pa = pyaudio.PyAudio()
        self.voice, self.rate = voice, rate
        self.level = 0.0  # loudness of the audio being played now, drives the mouth
        self._stop = threading.Event()
        self._thread = None

    @property
    def speaking(self):
        return bool(self._thread and self._thread.is_alive())

    def say(self, text, on_done):
        """Speak in the background; on_done() runs on the speech thread when finished or stopped."""
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(text, on_done), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(2)

    def _run(self, text, on_done):
        try:
            wav = subprocess.run(["espeak-ng", "--stdout", "--stdin", "-v", self.voice, "-s", str(self.rate)],
                                 input=text.encode(), capture_output=True, timeout=60, check=True).stdout
            w = wave.open(io.BytesIO(wav))  # espeak's streamed header has a bogus length; reading to EOF is fine
            stream = self.pa.open(format=self.pa.get_format_from_width(w.getsampwidth()),
                                  channels=w.getnchannels(), rate=w.getframerate(), output=True)
            try:
                while not self._stop.is_set():
                    data = w.readframes(1024)
                    if not data:
                        break
                    a = np.frombuffer(data, np.int16).astype(np.float32) / 32768
                    self.level = float(np.sqrt(np.mean(a * a)))
                    stream.write(data)
            finally:
                stream.stop_stream()
                stream.close()
        except (OSError, subprocess.SubprocessError, wave.Error) as e:
            print(f"speech failed: {e}", flush=True)
        finally:
            self.level = 0.0
            on_done()
