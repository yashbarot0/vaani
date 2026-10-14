# Vaani

Local, offline voice dictation for Fedora GNOME on Wayland. You speak, and the text is pasted at your cursor. Nothing leaves the laptop.

For everyday use and troubleshooting, see [USER_GUIDE.md](USER_GUIDE.md).

## Use

- **Super+Alt+D** starts recording. Press it again to stop, transcribe, and paste.
- **Click the pill** at the bottom of the screen to do the same thing. Right-click it to cancel a recording, or, while idle, to switch between Flow and Raw.
- **Super+Alt+E** is command mode. Select some text, press it, speak an instruction such as "make this more formal", and press it again. The rewrite replaces your selection. With nothing selected, the instruction writes new text.
- **Tray icon menu**: start or stop, cancel, Flow mode on or off, show pill when idle, copy the last raw transcript, quit.

While you record, the pill shows a live caption of your last few seconds of speech. It is a preview only. When you stop, the whole recording is transcribed again, cleaned, and pasted. Turn the caption off with `live_preview = false`.

Flow mode cleans each dictation with a local language model. It removes fillers, applies self-corrections such as "at ten, no wait, eleven", fixes punctuation and formats spoken lists. Raw mode pastes Whisper's text with only rule-based filler removal.

## How it works

- **Speech to text:** OpenAI's Whisper large-v3-turbo, run by [whisper.cpp](https://github.com/ggml-org/whisper.cpp) on the GPU.
- **Cleanup and command mode:** Qwen3-4B-Instruct-2507, run by [llama.cpp](https://github.com/ggml-org/llama.cpp) on the GPU.
- **App:** a Python background service owns the pill, the tray icon, the microphone and pasting. It starts both model servers as child processes inside its own network-isolated sandbox.

Both models together use about 4 GB of VRAM.

## Requirements

Built and tested on this setup:

- Fedora 43 Workstation, GNOME 49 on Wayland
- NVIDIA RTX 1000 Ada laptop GPU, 6 GB, with the NVIDIA driver installed
- CUDA toolkit 13.2 installed in `/usr/local/cuda-13.2`
- The system Python 3.14

Install the packages:

```sh
sudo dnf install git cmake gcc-c++ curl python3-gobject python3-numpy python3-pyaudio \
  python3-requests libappindicator-gtk3 gnome-shell-extension-appindicator
```

Turn on the **AppIndicator and KStatusNotifierItem Support** extension in the Extensions app. Without it there's no tray icon, but the pill and shortcuts still work.

For a different NVIDIA GPU, change `CUDA_ARCH` at the top of `setup.sh` to match the card.

## Install

```sh
git clone git@github.com:yashbarot0/vaani.git ~/projects/vaani
cd ~/projects/vaani
./setup.sh all       # checks prerequisites, builds the engines (about 15 min), downloads models (about 3 GB), adds service and shortcuts
./setup.sh uinput    # needs sudo once: lets Vaani press the paste key
systemctl --user start vaani
```

Without the `uinput` step, the text is copied to the clipboard and the pill tells you to press Ctrl+V.

The setup also does two things to your desktop:

- It adds the shortcuts Super+Alt+D and Super+Alt+E. If you already use them, change Vaani's in Settings, Keyboard, Custom Shortcuts.
- If you use the Clipboard Indicator extension, it sets it to save only favourites to disk. Dictated text passes through the clipboard.

The individual steps are `check`, `sources`, `build`, `models`, `desktop` and `uinput`. `./setup.sh bench-models` downloads the other candidate models, about 6.7 GB, used only by the benchmark scripts.

## Configure

Put any of the keys from `DEFAULTS` in `vaani/config.py` into `~/.config/vaani/config.toml`, then run `systemctl --user restart vaani`. For example:

```toml
vocabulary = ["CUDA", "PyTorch", "Fedora", "Ghostty"]
replacements = { "pie torch" = "PyTorch" }
history = false
```

## Privacy

- The service runs with `PrivateNetwork=yes`. Its only network interface is loopback, which it shares with the two model servers it starts. Check with `ls -l /proc/$(systemctl --user show vaani -p MainPID --value)/ns/net`.
- Audio stays in memory and is never written to disk. Server logs stay in memory too.
- Transcript history is off by default.
- Clipboard Indicator is set to save only favourites to disk.

## Commands

```sh
bin/vaani status | toggle | command | cancel | mode flow | mode raw | quit
journalctl --user -u vaani -f        # logs
PYTHONPATH=. /usr/bin/python3 tests/test_text.py
PYTHONPATH=. /usr/bin/python3 scripts/bench_whisper.py tests/jfk.wav   # 16 kHz mono WAV
PYTHONPATH=. /usr/bin/python3 scripts/bench_llm.py
```

## Known limits

- Pasting sends Shift+Insert after putting the text in both the clipboard and the primary selection, because GNOME on Wayland gives apps no way to know which app is focused. If an app doesn't paste, set `paste_keys = ["KEY_LEFTCTRL", "KEY_V"]` or bind Shift+Insert to paste in that app.
- Only the text or image form of your previous clipboard is restored after a paste. Files and rich text are not.
- Toggle only. GNOME shortcuts can't detect a key being held down.
