# Local fork — voice dictation on CachyOS / KDE Plasma Wayland

Push-to-talk dictation daemon: hold a hotkey → speak → release → transcribed
locally (whisper.cpp, CUDA), optionally cleaned by a local LLM (Ollama), and
pasted at the cursor of whatever app is focused.

Fork of [goodroot/hyprwhspr](https://github.com/goodroot/hyprwhspr). Despite
the name it does **not** require Hyprland — the hotkey is captured at the
evdev (`/dev/input`) level, which works under any Wayland compositor,
including KWin (KDE Plasma).

## What this fork adds

- **LLM transcript cleanup** (`lib/src/llm_cleanup.py`): optional stage
  between transcription and injection that calls a local Ollama server to fix
  punctuation/capitalization and strip filler words ("um", "uh"). Strictly
  non-rewriting; any failure (Ollama down, timeout, suspicious output) falls
  back to the raw transcript. Multi-line dictations are cleaned per line in
  parallel so `"new line"` voice commands survive.
- **Custom-model fix** (`whisper_manager.py`): pywhispercpp is given the
  resolved model *file path* instead of the model name, so non-stock models
  (distil-large-v3) load instead of erroring.
- **KWin paste fix** (`text_injector.py`): when the compositor rejects
  wtype's virtual-keyboard protocol (KWin does), wtype is disabled for the
  session instead of failing before every single paste.

## KDE Plasma gotchas (learned the hard way)

- **wtype does not work under KWin** — no `zwp_virtual_keyboard_v1`. All
  paste chords go through ydotool (uinput). Keep both installed; wtype just
  self-disables.
- **`grab_keys: true` is broken here** — the exclusive-grab + uinput re-emit
  path dropped keystrokes and made the keyboard unusable. Leave it `false`.
- **Use a non-typing hotkey.** Nothing swallows the key on KDE (no compositor
  bind like Hyprland), so a letter combo (`SUPER+ALT+D`) key-repeats into the
  focused app while held. `F9` types nothing — clean hold-to-talk.

## Machine-specific facts (this install)

- GPU: RTX 3060 Ti → pywhispercpp built from source with CUDA
  (`GGML_CUDA=1`, host compiler `g++-15`, nvcc from `/opt/cuda`).
  **Post-build fix required**: the build vendors `libcuda` into
  `site-packages/pywhispercpp.libs/`, which breaks CUDA init. Replace it with
  a symlink to the system driver library:
  ```bash
  cd .venv/lib/python3.14/site-packages/pywhispercpp.libs/
  mv libcuda-*.so.* vendored.bak && ln -s /usr/lib/libcuda.so.1 <original-libcuda-filename>
  ```
  (symlink to `libcuda.so.1` survives driver updates)
- Python 3.14 → no prebuilt wheels; everything lives in `.venv/`.
- Mic: **Trust GXT 232** (`audio_device_name: "Trust"`, source volume 150%).
  The Scarlett Solo input carries no signal on this desk; the Redragon
  camera mic clips.
- Hotkey: **F9** hold-to-talk (see KDE gotchas above).
- Models on disk: `~/.local/share/pywhispercpp/models/`
  (`ggml-distil-large-v3.bin` — fast, **English only**; `ggml-small.bin` —
  multilingual, use for Bulgarian).

## Install (from scratch)

```bash
# 1. System packages (sudo)
sudo pacman -S --needed wtype ydotool wl-clipboard

# 2. Input access — evdev hotkey listener + ydotool need these (sudo)
sudo usermod -aG input $USER          # permanent; takes effect after re-login
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
  | sudo tee /etc/udev/rules.d/99-hyprwhspr-uinput.rules
sudo udevadm control --reload; sudo udevadm trigger --sysname-match=uinput
# grant access for the CURRENT session without re-login:
sudo setfacl -m u:$USER:rw /dev/uinput
sudo setfacl -m u:$USER:r /dev/input/event*   # re-run after reboot, or just re-login once

# 3. Python env
cd ~/Projects/hyprwhspr
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Rebuild whisper bindings with CUDA (PyPI wheel is CPU-only)
.venv/bin/pip install cmake ninja
env GGML_CUDA=1 CUDACXX=/opt/cuda/bin/nvcc CUDAHOSTCXX=/usr/bin/g++-15 \
    CMAKE_ARGS="-DGGML_CUDA=1 -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15" \
    .venv/bin/pip install --force-reinstall --no-cache-dir --no-binary pywhispercpp pywhispercpp==1.4.1

# 5. Models
mkdir -p ~/.local/share/pywhispercpp/models && cd ~/.local/share/pywhispercpp/models
curl -LO https://huggingface.co/distil-whisper/distil-large-v3-ggml/resolve/main/ggml-distil-large-v3.bin
curl -LO https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin

# 6. Cleanup model
ollama pull gemma3:4b
```

## Run

```fish
set -Ux HYPRWHSPR_ROOT ~/Projects/hyprwhspr   # once
~/Projects/hyprwhspr/.venv/bin/python ~/Projects/hyprwhspr/lib/main.py
```

Or as a systemd user service — see `config/systemd/hyprwhspr.service`
(point `ExecStart` at the venv python + `lib/main.py`, set
`Environment=HYPRWHSPR_ROOT=%h/Projects/hyprwhspr`).

## Usage

Hold **F9**, speak, release. Text appears at the cursor
(clipboard-paste injection: `wl-copy` + paste chord via `ydotool` on KDE —
survives Electron apps and terminals; terminals get Ctrl+Shift+V
automatically).

Voice commands (built-in, `symbol_replacements: true`): "new line", "period",
"comma", "question mark", "open paren", "tab", … full list in
`lib/src/text_injector.py`.

## Config — `~/.config/hyprwhspr/config.json`

```json
{
  "recording_mode": "push_to_talk",
  "primary_shortcut": "F9",
  "model": "distil-large-v3",
  "language": "en",
  "llm_cleanup": true,
  "llm_cleanup_model": "gemma3:4b",
  "audio_feedback": true,
  "grab_keys": false,
  "audio_device_name": "Trust"
}
```

| Key | Meaning |
|---|---|
| `llm_cleanup` | **the toggle** — `false` = raw transcript straight to inject |
| `llm_cleanup_model` | any Ollama model, e.g. `qwen2.5:3b` as fallback |
| `llm_cleanup_timeout` | seconds before giving up and using raw text (default 8) |
| `llm_cleanup_prompt` | override the built-in instruction (default: fix formatting, never rewrite) |
| `model` | whisper model name → `~/.local/share/pywhispercpp/models/ggml-<name>.bin` |
| `whisper_prompt` | custom vocabulary / spelling hints fed to whisper.cpp as initial prompt |
| `word_overrides` | post-hoc regex word replacements, e.g. `{"demiurg": "Demiurg"}` |

### Swapping models

- **STT**: drop any `ggml-*.bin` into `~/.local/share/pywhispercpp/models/`,
  set `"model"` to the name without the `ggml-`/`.bin` parts. For Bulgarian
  use `"model": "small"` (or download `large-v3-turbo`) and `"language": "bg"`
  or `null` for auto-detect. `distil-large-v3` is English-only.
- **Cleanup LLM**: `ollama pull <model>`, set `llm_cleanup_model`.

## Architecture (for the future visualizer run)

```
GlobalShortcuts (evdev press/release)          lib/src/global_shortcuts.py
  → AudioCapture (sounddevice/PipeWire)        lib/src/audio_capture.py
  → WhisperManager.transcribe_audio (CUDA)     lib/src/whisper_manager.py
  → TextInjector.inject_text                   lib/src/text_injector.py
      _preprocess_text   (voice commands, word overrides, filler filter)
      LLMCleanup.cleanup (Ollama, optional)    lib/src/llm_cleanup.py
      post_transcription_hook (user shell hook)
      clipboard + paste chord (wl-copy → wtype/ydotool)
```

Mic-visualizer hook points already exist upstream: `lib/mic_osd/` (layer-shell
OSD, wlroots-oriented — needs a KDE-friendly window backend) and the audio
level callbacks in `AudioCapture`. No refactoring of the dictation path needed.

## Latency (RTX 3060 Ti, measured)

See `docs/LATENCY.md` — regenerate with `utils/benchmark_latency.py`.
