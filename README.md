<h1 align="center">Glas</h1>

<p align="center">
    <b>Local, private push-to-talk dictation for Linux</b><br>
    Hold a key → speak → release → clean text at your cursor. Nothing leaves your machine.
</p>

<p align="center">
    <i>глас — Bulgarian for "voice". A self-hosted Wispr Flow alternative,<br>
    forked from <a href="https://github.com/goodroot/hyprwhspr">hyprwhspr</a>.</i>
</p>

<p align="center">
    <!-- DEMO GIF PLACEHOLDER: hold F9 → wave overlay → text lands in editor -->
    <img src="docs/screenshots/demo.gif" alt="demo (placeholder)" width="640">
</p>

---

## Features

- **Push-to-talk**: hold a key (default `F9`), speak, release — text is injected at the cursor of whatever app has focus. Works in editors, browsers, terminals, Electron apps.
- **Fully local**: whisper.cpp STT (CUDA-accelerated) + optional [Ollama](https://ollama.com) LLM cleanup. No cloud, no telemetry, no accounts.
- **LLM cleanup** (toggle): a small local model (default `gemma3:4b`) fixes punctuation and capitalization and strips filler words — it never rewrites your meaning, and any failure falls back to the raw transcript.
- **Live mic visualizer**: a click-through layer-shell overlay — braided wave driven by real capture amplitude, pixel field whose *density* follows your volume and *color* follows your pitch (FFT).
- **Settings GUI + tray**: GTK4/libadwaita control panel for every option below, live daemon status, a test-dictation runner, and a system-tray icon with quick toggles.
- **Voice commands**: "new line", "period", "comma", "open paren"… plus your own word overrides (fix mishears, force spellings — applied before *and* after LLM cleanup).
- **Custom vocabulary**: prime whisper with your names/domains/jargon via its initial prompt.

## Architecture

```
            hold hotkey                    release
                 │                            │
   ┌─────────────▼────────────────────────────▼──────────────┐
   │  Glas daemon (Python, systemd --user service)            │
   │                                                           │
   │  evdev hotkey ──► PipeWire capture ──► whisper.cpp        │
   │  (compositor-     (sounddevice)        (pywhispercpp,     │
   │   agnostic)            │               CUDA/CPU — the     │
   │                        │               ONLY STT engine)   │
   │                        ▼                    │             │
   │                  mic-osd overlay            ▼             │
   │                  (GTK4 layer-shell,   LLM cleanup         │
   │                   reads level+pitch   (Ollama /api/       │
   │                   from the daemon —   generate — text     │
   │                   no second mic       polish ONLY:        │
   │                   stream)             Ollama does NOT     │
   │                                       and CANNOT do STT)  │
   │                                            │              │
   │                              clipboard + paste keystroke  │
   │                              (wl-copy → ydotool / wtype)  │
   └───────────────────────────────────────────────────────────┘
```

Two separate models, two separate jobs: **whisper.cpp turns audio into text; Ollama only polishes text**. Turning cleanup off wires the raw transcript straight to injection.

## Supported environments

| Environment | Dictation | Visualizer overlay |
|---|---|---|
| ✅ KDE Plasma (Wayland) | full | full (primary target) |
| ✅ Hyprland / Sway / wlroots | full | full |
| ⚠️ GNOME (Wayland) | full | no (no layer-shell) — falls back to desktop notifications |
| ⚠️ X11 | untested | no |
| ❌ Windows / macOS | no | no — Linux-only by design |

## Install

```sh
git clone <this-repo> && cd glas
sh scripts/install-glas.sh          # add --cpu to skip the CUDA build
```

The installer detects your package manager (pacman/apt/dnf), checks every dependency, and **prints the exact sudo commands it can't run itself** — typically:

```sh
# system packages (once)
sudo pacman -S --needed python python-gobject python-cairo gtk4 gtk4-layer-shell libadwaita ydotool wl-clipboard pipewire portaudio cmake

# input access: the hotkey listener reads /dev/input, ydotool writes /dev/uinput
sudo usermod -aG input $USER        # permanent, needs re-login
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/99-glas-uinput.rules
sudo udevadm control --reload && sudo udevadm trigger --sysname-match=uinput
# grant access for the CURRENT session (no re-login needed):
sudo setfacl -m u:$USER:rw /dev/uinput /dev/input/event*
```

Then:

```sh
systemctl --user start hyprwhspr    # start the daemon
systemctl --user enable hyprwhspr   # optional: autostart at login
```

Launch **Glas** from your app menu for settings, hold **F9** to dictate.

> **GPU**: with an NVIDIA card + CUDA toolkit the installer builds whisper bindings from source (PyPI wheels are CPU-only) and patches the vendored `libcuda` (a symlink to the system `libcuda.so.1` — the bundled copy breaks CUDA init). AMD/Intel or no GPU: CPU decoding via `--cpu`, still fast with `small`.
>
> **Cleanup**: install [Ollama](https://ollama.com/download) and `ollama pull gemma3:4b`, then enable cleanup in settings. Without it Glas types the raw whisper transcript.

## Configuration

Everything lives in `~/.config/hyprwhspr/config.json` and is editable in the GUI:

| Setting (GUI) | Config key | Notes |
|---|---|---|
| Input device | `audio_device_name` | substring match; unset = system default |
| Hotkey | `primary_shortcut` | e.g. `F9`, `SUPER+ALT+D`. Use a non-typing key on KDE |
| Recording mode | `recording_mode` | `push_to_talk` / `toggle` / `auto` / `continuous` |
| Whisper model | `model` | `tiny`…`large-v3`, `distil-large-v3`; ✓ = on disk, others auto-download |
| Language | `language` | `null` = auto-detect. `distil-large-v3` is English-only |
| Custom vocabulary | `whisper_prompt` | names/jargon fed to whisper as initial prompt |
| Threads | `threads` | CPU decoding threads |
| Cleanup on/off | `llm_cleanup` | **the toggle** — off = raw transcript |
| Cleanup model | `llm_cleanup_model` | any Ollama model (`gemma3:4b`, `qwen2.5:3b`, …) |
| Endpoint | `llm_cleanup_url` | default `http://localhost:11434` |
| Timeout | `llm_cleanup_timeout` | seconds before falling back to raw |
| Temperature | `llm_cleanup_temperature` | 0 = deterministic (recommended) |
| Cleanup prompt | `llm_cleanup_prompt` | `null` = built-in (fix formatting, never rewrite) |
| Paste chord | `paste_mode` | `auto`: terminals get Ctrl+Shift+V |
| Clear clipboard | `clipboard_behavior` + `clipboard_clear_delay` | |
| Auto-submit | `auto_submit` | press Enter after paste |
| Overlay on/off | `mic_osd_enabled` | headless mode when `false` |
| Overlay style | `mic_osd_style` | `wave` / `waveform` / `vu_meter` |
| Overlay geometry | `mic_osd_width/height/margin/anchor` | anchored bottom or top, centered |
| Overlay resolution | `mic_osd_bars` | curve points / bar count |
| Overlay colors | `mic_osd_colors` | name→hex overrides of the gruvbox palette |
| Spoken symbols | `symbol_replacements` | "new line" → ↵, "period" → `.` … |
| Filler filter | `filter_filler_words` + `filler_words` | regex removal without the LLM |
| Word overrides | `word_overrides` | phrase→replacement, applied before *and* after cleanup |

## Troubleshooting

- **Typing pastes nothing (KDE)** — KWin has no `zwp_virtual_keyboard_v1`, so `wtype` cannot work; Glas detects this and uses `ydotool` automatically. Make sure `/dev/uinput` is accessible (see install).
- **Hotkey does nothing** — you're not in the `input` group yet (needs re-login), or the session-scoped `setfacl` wasn't run.
- **Held letter keys spam into apps (KDE)** — nothing swallows a letter combo on KDE; use a non-typing key like `F9`. Do **not** set `grab_keys: true` — the exclusive-grab re-emit path can drop keystrokes.
- **CUDA "initialization error"** — the pywhispercpp build vendored `libcuda`. Fix: replace `…/site-packages/pywhispercpp.libs/libcuda-*.so.*` with a symlink to `/usr/lib/libcuda.so.1` (the installer does this).
- **Empty transcripts / "Thank you."** — whisper hallucinating on silence; your mic isn't the one Glas records from. Pick the right input device in settings.
- **First cleaned dictation is slow** — Ollama cold-loads the model (~25s); the daemon pre-warms it at startup and `keep_alive` keeps it hot for 30min between dictations.
- **Overlay not showing** — your compositor lacks layer-shell (GNOME) or `gtk4-layer-shell` isn't installed; dictation is unaffected.
- **Live logs** — `journalctl --user -u hyprwhspr -f` (`[PIPELINE]` lines show raw → preprocessed → cleaned per dictation).

## Measured performance

RTX 3060 Ti, 11s speech clip: STT 0.17s (`distil-large-v3`) / 0.11s (`small`); gemma3:4b cleanup 0.68s hot. **End-to-end ≈0.9s cleaned, ≈0.2s raw.** Details: [docs/LATENCY.md](docs/LATENCY.md).

## Screenshots

<!-- SCREENSHOT PLACEHOLDERS -->
| Settings GUI | Wave overlay |
|---|---|
| ![settings](docs/screenshots/settings.png) | ![overlay](docs/screenshots/overlay.png) |

## Credits & license

Glas is a fork of [**hyprwhspr**](https://github.com/goodroot/hyprwhspr) by [goodroot](https://github.com/goodroot) — the daemon core (hotkeys, capture, whisper integration, injection, OSD scaffolding) is theirs. This fork adds the Ollama cleanup stage, the pitch/volume-reactive wave visualizer, KDE/KWin fixes, the settings GUI + tray, and the standalone packaging.

MIT — see [LICENSE](LICENSE) (© 2025 goodroot, with modifications © 2026 Georgi Demirov).
