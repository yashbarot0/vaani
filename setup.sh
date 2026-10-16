#!/usr/bin/env bash
# One-time setup for Vaani. Steps that use the network: sources, models, bench-models.
# Usage: ./setup.sh check | sources | build | models | bench-models | desktop | bob | voice | uinput (needs sudo) | all
# Optional: ./setup.sh bob adds Super+Alt+B (ask IBM Bob), the only feature that goes online.
# Optional: ./setup.sh voice gives Bob a natural voice (Kokoro, offline, about 340 MB, runs on the CPU).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CUDA_ARCH=89   # RTX 1000 Ada
WHISPER_TAG=v1.9.5
LLAMA_TAG=b11618
export PATH="/usr/local/cuda/bin:/usr/local/cuda-13.2/bin:$PATH"
HF=https://huggingface.co

check() {
  local missing=0
  for c in git cmake g++ curl nvcc; do
    command -v "$c" >/dev/null || { echo "missing command: $c"; missing=1; }
  done
  /usr/bin/python3 -c "
import gi, numpy, pyaudio, requests
gi.require_version('Gtk', '3.0'); gi.require_version('AppIndicator3', '0.1')
from gi.repository import AppIndicator3, Gtk" 2>/dev/null || { echo "missing Python packages"; missing=1; }
  if [ "$missing" = 1 ]; then
    echo "Install with: sudo dnf install git cmake gcc-c++ curl python3-gobject python3-numpy python3-pyaudio python3-requests libappindicator-gtk3 gnome-shell-extension-appindicator"
    echo "CUDA 13.2 must be installed in /usr/local/cuda-13.2 (nvcc)."
    exit 1
  fi
  echo "prerequisites ok"
}

sources() {
  mkdir -p "$ROOT/vendor"
  [ -d "$ROOT/vendor/whisper.cpp" ] ||
    git -c advice.detachedHead=false clone --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp "$ROOT/vendor/whisper.cpp"
  [ -d "$ROOT/vendor/llama.cpp" ] ||
    git -c advice.detachedHead=false clone --depth 1 --branch "$LLAMA_TAG" https://github.com/ggml-org/llama.cpp "$ROOT/vendor/llama.cpp"
}

build() {
  sources
  # Static builds so whisper and llama each carry their own ggml and never clash.
  local common=(-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON
                -DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH -DGGML_NATIVE=ON)
  cmake -S "$ROOT/vendor/whisper.cpp" -B "$ROOT/vendor/whisper.cpp/build" "${common[@]}" -DWHISPER_BUILD_TESTS=OFF
  cmake --build "$ROOT/vendor/whisper.cpp/build" -j"$(nproc)" --target whisper-server whisper-cli
  # LLAMA_CURL=OFF: the server binary cannot download anything.
  cmake -S "$ROOT/vendor/llama.cpp" -B "$ROOT/vendor/llama.cpp/build" "${common[@]}" -DLLAMA_CURL=OFF \
        -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF
  cmake --build "$ROOT/vendor/llama.cpp/build" -j"$(nproc)" --target llama-server llama-bench
  # Relative links, so the project folder can be moved.
  ln -sfr "$ROOT/vendor/whisper.cpp/build/bin/whisper-server" "$ROOT/bin/whisper-server"
  ln -sfr "$ROOT/vendor/whisper.cpp/build/bin/whisper-cli" "$ROOT/bin/whisper-cli"
  ln -sfr "$ROOT/vendor/llama.cpp/build/bin/llama-server" "$ROOT/bin/llama-server"
  ln -sfr "$ROOT/vendor/llama.cpp/build/bin/llama-bench" "$ROOT/bin/llama-bench"
}

fetch() { # url name
  mkdir -p "$ROOT/models"
  [ -s "$ROOT/models/$2" ] && return
  curl -fL --retry 3 -o "$ROOT/models/$2.part" "$1" && mv "$ROOT/models/$2.part" "$ROOT/models/$2"
}

models() {  # the two models Vaani uses, about 3.1 GB
  fetch $HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin ggml-large-v3-turbo-q5_0.bin
  fetch $HF/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf Qwen3-4B-Instruct-2507-Q4_K_M.gguf
}

voice() {  # optional: Bob's natural voice. Installs the Python packages for your user, then downloads the model
  /usr/bin/python3 -m pip install --user kokoro-onnx onnxruntime soundfile
  local k=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
  fetch $k/kokoro-v1.0.onnx kokoro-v1.0.onnx
  fetch $k/voices-v1.0.bin voices-v1.0.bin
  echo "Bob's voice installed. Restart Vaani: systemctl --user restart vaani"
}

bench-models() {  # optional: the other candidates compared by scripts/bench_*.py, about 6.7 GB
  fetch $HF/distil-whisper/distil-large-v3.5-ggml/resolve/main/ggml-model.bin ggml-distil-large-v3.5.bin
  fetch $HF/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin ggml-small.en.bin
  fetch $HF/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin ggml-medium.en.bin
  fetch $HF/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf qwen2.5-1.5b-instruct-q4_k_m.gguf
  fetch $HF/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf Llama-3.2-3B-Instruct-Q4_K_M.gguf
}

shortcut() {  # slug name vaani-subcommand binding: a GNOME custom shortcut, appended to existing ones
  /usr/bin/python3 - "$ROOT" "$@" <<'PY'
import ast, subprocess, sys
root, slug, name, cmd, key = sys.argv[1:]
base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
schema = "org.gnome.settings-daemon.plugins.media-keys"
def gs(*a): return subprocess.run(["gsettings", *a], check=True, capture_output=True, text=True).stdout.strip()
paths = ast.literal_eval(gs("get", schema, "custom-keybindings").removeprefix("@as ")) or []
path = base + slug + "/"
rel = f"{schema}.custom-keybinding:{path}"
gs("set", rel, "name", name)
gs("set", rel, "command", f"{root}/bin/vaani {cmd}")
gs("set", rel, "binding", key)
if path not in paths:
    gs("set", schema, "custom-keybindings", str(paths + [path]))
print(f"shortcut: {key} runs vaani {cmd}")
PY
}

desktop() {
  # Autostart at login, with no network access (only loopback, shared with the GPU servers).
  mkdir -p ~/.config/systemd/user
  cat > ~/.config/systemd/user/vaani.service <<EOF
[Unit]
Description=Vaani: local voice dictation
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$ROOT/bin/vaani daemon
PrivateNetwork=yes
Restart=on-failure
RestartSec=3
TimeoutStopSec=5

[Install]
WantedBy=graphical-session.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable vaani.service

  shortcut vaani-toggle "Vaani dictation" toggle "<Super><Alt>d"
  shortcut vaani-command "Vaani command mode" command "<Super><Alt>e"

  # Clipboard Indicator keeps history on disk; keep only favourites there (dictations pass through the clipboard).
  dconf write /org/gnome/shell/extensions/clipboard-indicator/cache-only-favorites true || true
}

bob_setup() {
  # Optional: Super+Alt+B asks IBM Bob Shell. vaani-bob.service is the only part of Vaani with
  # network access; vaani.service stays offline and reaches it through a local socket.
  local envfile=~/.config/vaani/bob.env
  PATH="$HOME/.local/bin:$PATH" command -v bob >/dev/null || { echo "Bob Shell not found: install it first"; exit 1; }
  PATH="$HOME/.local/bin:$PATH" bob run --help 2>/dev/null | grep -q -- "--max-cost" ||
    { echo "Bob Shell 2.x is required (bob run --mode ask -f json)"; exit 1; }
  if ! grep -q '^BOB_API_KEY=.' "$envfile" 2>/dev/null; then
    echo "Create $envfile containing the line BOB_API_KEY=<your key>, then run: ./setup.sh bob"
    exit 1
  fi
  chmod 600 "$envfile"
  mkdir -p ~/.local/share/vaani/bob ~/.config/systemd/user
  cat > ~/.config/systemd/user/vaani-bob.service <<EOF
[Unit]
Description=Vaani Bob helper: sends Super+Alt+B questions to IBM Bob Shell

[Service]
EnvironmentFile=$envfile
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$ROOT/bin/vaani bob-helper
Restart=on-failure
RestartSec=3
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable vaani-bob.service
  systemctl --user restart vaani-bob.service
  shortcut vaani-bob "Vaani ask Bob" bob "<Super><Alt>b"
}

uinput() {
  # The only root step: let the logged-in user create a virtual keyboard for auto-paste.
  echo 'KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"' |
    sudo tee /etc/udev/rules.d/70-vaani-uinput.rules >/dev/null
  sudo udevadm control --reload-rules
  sudo udevadm trigger --name-match=uinput
  sleep 1
  ls -l /dev/uinput; getfacl -p /dev/uinput 2>/dev/null | grep "user:$USER" || echo "ACL not applied yet: log out and back in"
}

case "${1:-all}" in
  check|sources|build|models|bench-models|desktop|uinput) "$1" ;;
  bob) bob_setup ;;
  voice) voice ;;
  all) check; build; models; desktop ;;
  *) echo "usage: $0 check|sources|build|models|bench-models|desktop|bob|voice|uinput|all"; exit 1 ;;
esac
