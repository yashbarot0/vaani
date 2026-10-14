"""Settings: defaults below, overridden by ~/.config/vaani/config.toml (same keys)."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
BIN = ROOT / "bin"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
SOCKET = RUNTIME / "vaani.sock"
CONFIG_FILE = Path.home() / ".config/vaani/config.toml"
HISTORY_FILE = Path.home() / ".local/share/vaani/history.jsonl"

DEFAULTS = {
    "whisper_model": "ggml-large-v3-turbo-q5_0.bin",
    "llm_model": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",  # faster, weaker: qwen2.5-1.5b-instruct-q4_k_m.gguf via ./setup.sh bench-models
    "mode": "flow",              # "flow" = LLM cleanup, "raw" = Whisper text + rules only
    "whisper_port": 47811,       # loopback only, inside the private network namespace
    "llm_port": 47812,
    "vocabulary": [],            # names/jargon passed to Whisper as a spelling hint
    "replacements": {},          # {"misheard": "correct"}, whole words, case-insensitive
    "remove_fillers": True,
    "trailing_space": True,
    "max_seconds": 300,
    "silence_rms": 0.04,         # measured mic noise floor is ~0.017 RMS
    "min_speech_seconds": 0.25,
    "llm_timeout": 3.0,          # seconds, plus 1 s per 20 words; on timeout the Whisper text is pasted
    "paste_keys": ["KEY_LEFTSHIFT", "KEY_INSERT"],  # pastes clipboard or primary in GUI apps and terminals
    "restore_delay": 0.25,       # Electron apps read the clipboard late
    "live_preview": True,        # show words in the pill while recording; the paste still uses the full recording
    "preview_seconds": 8,        # window re-transcribed for the preview, keeps each pass ~0.3 s on the GPU
    "sounds": True,
    "show_idle_pill": True,
    "history": False,
}


def load():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        user = tomllib.loads(CONFIG_FILE.read_text())
        unknown = set(user) - set(DEFAULTS)
        if unknown:
            raise SystemExit(f"{CONFIG_FILE}: unknown keys {sorted(unknown)}")
        cfg.update(user)
    return cfg
