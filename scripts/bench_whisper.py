"""Warm transcription speed per Whisper model. Usage: bench_whisper.py clip.wav [more.wav ...]
Clips must be 16 kHz mono 16-bit WAV (record your own with: arecord -f S16_LE -r 16000 -c 1 me.wav)."""
import sys
import time
import wave
import numpy as np
from vaani import config, engines

MODELS = ["ggml-small.en.bin", "ggml-large-v3-turbo-q5_0.bin", "ggml-distil-large-v3.5.bin", "ggml-medium.en.bin"]
clips = []
for path in sys.argv[1:]:
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, f"{path}: need 16 kHz mono"
        clips.append((path, np.frombuffer(w.readframes(w.getnframes()), np.int16)))
cfg = dict(config.DEFAULTS)
for model in MODELS:
    if not (config.MODELS / model).exists():
        print(f"skip {model}: not downloaded (./setup.sh bench-models)")
        continue
    cfg["whisper_model"] = model
    s = engines.start_whisper(cfg)
    try:
        s.wait_ready()
        engines.transcribe(s, clips[0][1])  # warm-up
        for path, pcm in clips:
            t = time.perf_counter()
            for _ in range(3):
                out = engines.transcribe(s, pcm)
            ms = (time.perf_counter() - t) / 3 * 1000
            print(f"{model:32s} {path.split('/')[-1]:14s} audio {len(pcm)/16000:5.1f}s  {ms:6.0f} ms  | {out[:90]}")
    finally:
        s.stop()
        s.proc.wait()
