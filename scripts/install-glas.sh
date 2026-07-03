#!/bin/sh
# Glas installer — sets up the dictation daemon + settings GUI for the
# current user. POSIX sh. Safe to re-run (idempotent); prints every sudo
# step it cannot do itself instead of failing.
#
# Usage: sh scripts/install-glas.sh [--cpu]
#   --cpu   skip the CUDA source build, use the CPU wheel

set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CPU_ONLY=0
[ "${1:-}" = "--cpu" ] && CPU_ONLY=1

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[x]\033[0m %s\n' "$*"; }

MISSING_SUDO=""
need_sudo() { MISSING_SUDO="${MISSING_SUDO}
  $*"; }

# ------------------------ Package manager ------------------------

if command -v pacman >/dev/null 2>&1; then
    PM=pacman
    PKGS="python python-gobject python-cairo gtk4 gtk4-layer-shell libadwaita ydotool wl-clipboard pipewire portaudio cmake"
    INSTALL_CMD="sudo pacman -S --needed $PKGS"
elif command -v apt-get >/dev/null 2>&1; then
    PM=apt
    PKGS="python3 python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4layershell-1.0 ydotool wl-clipboard pipewire cmake portaudio19-dev"
    INSTALL_CMD="sudo apt-get install -y $PKGS"
elif command -v dnf >/dev/null 2>&1; then
    PM=dnf
    PKGS="python3 python3-gobject python3-cairo gtk4 gtk4-layer-shell libadwaita ydotool wl-clipboard pipewire cmake portaudio-devel"
    INSTALL_CMD="sudo dnf install -y $PKGS"
else
    PM=unknown
    INSTALL_CMD=""
fi
say "Package manager: $PM"

# ------------------------ System packages ------------------------

check_bin() { command -v "$1" >/dev/null 2>&1; }

PKG_OK=1
for b in python3 ydotool wl-copy; do
    check_bin "$b" || PKG_OK=0
done
python3 -c 'import gi' 2>/dev/null || PKG_OK=0

if [ "$PKG_OK" = 0 ]; then
    if [ -n "$INSTALL_CMD" ]; then
        warn "System packages missing."
        need_sudo "$INSTALL_CMD"
    else
        fail "Unknown package manager — install equivalents of: $PKGS"
    fi
else
    say "System packages present"
fi

# ------------------------ Audio ------------------------

if check_bin pw-record; then
    say "PipeWire present"
elif check_bin parecord; then
    warn "PipeWire not found — falling back to PulseAudio (parecord). Capture works; PipeWire is recommended."
else
    fail "Neither PipeWire nor PulseAudio found — install pipewire."
fi

# ------------------------ Input access (evdev hotkey + ydotool) ------------------------

if id -nG | tr ' ' '\n' | grep -qx input; then
    say "User is in the 'input' group"
else
    warn "User not in 'input' group (needed for the hotkey listener + ydotool)."
    need_sudo "sudo usermod -aG input \$USER   # then log out and back in"
    need_sudo "sudo setfacl -m u:\$USER:rw /dev/input/event*   # grants access NOW, current session only"
fi

if [ -e /dev/uinput ] && [ -w /dev/uinput ]; then
    say "/dev/uinput writable"
else
    warn "/dev/uinput not writable (ydotool needs it)."
    need_sudo "printf 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\", OPTIONS+=\"static_node=uinput\"\\n' | sudo tee /etc/udev/rules.d/99-glas-uinput.rules"
    need_sudo "sudo udevadm control --reload && sudo udevadm trigger --sysname-match=uinput"
    need_sudo "sudo setfacl -m u:\$USER:rw /dev/uinput"
fi

# ------------------------ Python venv ------------------------

say "Setting up Python venv (system site-packages for GTK bindings)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$ROOT/.venv" || { fail "venv creation failed"; exit 1; }
else
    # Ensure GTK bindings from the system are visible
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' \
        "$ROOT/.venv/pyvenv.cfg" 2>/dev/null || true
fi
"$ROOT/.venv/bin/pip" install --quiet --upgrade pip
"$ROOT/.venv/bin/pip" install --quiet -r "$ROOT/requirements.txt" || {
    fail "pip install failed — see output above"; exit 1; }

# ------------------------ Whisper CUDA build ------------------------

if [ "$CPU_ONLY" = 0 ] && check_bin nvidia-smi; then
    if "$ROOT/.venv/bin/python" -c 'import pywhispercpp' 2>/dev/null && \
       ls "$ROOT/.venv"/lib/python*/site-packages/pywhispercpp.libs/libggml-cuda* >/dev/null 2>&1; then
        say "CUDA pywhispercpp already built"
    else
        say "NVIDIA GPU detected — building pywhispercpp with CUDA (several minutes)"
        NVCC="$(command -v nvcc || echo /opt/cuda/bin/nvcc)"
        HOSTCXX="$(command -v g++-15 || command -v g++-14 || command -v g++)"
        if [ -x "$NVCC" ]; then
            "$ROOT/.venv/bin/pip" install --quiet cmake ninja
            GGML_CUDA=1 CUDACXX="$NVCC" CUDAHOSTCXX="$HOSTCXX" \
                CMAKE_ARGS="-DGGML_CUDA=1 -DCMAKE_CUDA_HOST_COMPILER=$HOSTCXX" \
                "$ROOT/.venv/bin/pip" install --force-reinstall --no-cache-dir \
                --no-binary pywhispercpp "pywhispercpp==1.4.1" || warn "CUDA build failed — CPU wheel stays"
            # The build vendors libcuda, which breaks CUDA init — swap for the system one
            for f in "$ROOT"/.venv/lib/python*/site-packages/pywhispercpp.libs/libcuda-*.so.*; do
                [ -e "$f" ] || continue
                case "$f" in *.vendored) continue ;; esac
                if [ ! -L "$f" ] && [ -e /usr/lib/libcuda.so.1 ]; then
                    mv "$f" "$f.vendored" && ln -s /usr/lib/libcuda.so.1 "$f"
                    say "Patched vendored libcuda → system libcuda.so.1"
                fi
            done
        else
            warn "nvcc not found — skipping CUDA build (install the cuda package or use --cpu)"
        fi
    fi
else
    say "CPU mode (no NVIDIA GPU or --cpu given)"
fi

# ------------------------ Whisper model ------------------------

MODELS_DIR="$HOME/.local/share/pywhispercpp/models"
mkdir -p "$MODELS_DIR"
if [ ! -f "$MODELS_DIR/ggml-small.bin" ] && [ ! -f "$MODELS_DIR/ggml-distil-large-v3.bin" ]; then
    say "Downloading whisper model 'small' (466MB, multilingual)"
    curl -L --progress-bar -o "$MODELS_DIR/ggml-small.bin" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin" \
        || warn "Model download failed — pick a model in the settings GUI later"
else
    say "Whisper model present"
fi

# ------------------------ Ollama (optional, for LLM cleanup) ------------------------

if check_bin ollama; then
    if ollama list 2>/dev/null | grep -q '^gemma3:4b'; then
        say "Ollama + gemma3:4b ready"
    else
        say "Pulling gemma3:4b for LLM cleanup (~3.3GB)"
        ollama pull gemma3:4b || warn "Pull failed — cleanup will fall back to raw text"
    fi
else
    warn "Ollama not installed — LLM cleanup disabled (dictation still works raw)."
    warn "  Install: https://ollama.com/download  then: ollama pull gemma3:4b"
fi

# ------------------------ Config ------------------------

CFG="$HOME/.config/hyprwhspr/config.json"
if [ ! -f "$CFG" ]; then
    mkdir -p "$(dirname "$CFG")"
    cp "$ROOT/config.example.json" "$CFG"
    say "Installed default config → $CFG"
else
    say "Keeping existing config ($CFG)"
fi

# ------------------------ systemd unit, desktop entry, icon ------------------------

UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|@ROOT@|$ROOT|g" "$ROOT/config/systemd/glas.service.in" > "$UNIT_DIR/hyprwhspr.service"
systemctl --user daemon-reload
say "Installed systemd user unit 'hyprwhspr.service'"

APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
sed "s|@ROOT@|$ROOT|g" "$ROOT/config/glas.desktop.in" > "$APPS_DIR/dev.demiurg.Glas.desktop"
update-desktop-database "$APPS_DIR" 2>/dev/null || true

ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$ICON_DIR"
cp "$ROOT/share/assets/glas.svg" "$ICON_DIR/glas.svg"
say "Installed desktop entry + icon"

# ------------------------ Summary ------------------------

echo
if [ -n "$MISSING_SUDO" ]; then
    warn "Run these sudo steps yourself, then re-run this script:"
    printf '%s\n' "$MISSING_SUDO"
else
    say "All checks passed."
    echo "Start:    systemctl --user start hyprwhspr"
    echo "Autostart: systemctl --user enable hyprwhspr"
    echo "Settings: launch 'Glas' from the app menu (or bin/glas-settings)"
    echo "Dictate:  hold F9, speak, release"
fi
