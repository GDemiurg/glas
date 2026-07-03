# Contributing to Glas

Thanks for looking in. Glas is a personal fork of
[hyprwhspr](https://github.com/goodroot/hyprwhspr) focused on KDE/KWin —
general daemon improvements probably belong upstream first.

## Ground rules

- **Linux-only.** PRs adding Windows/macOS backends will be closed.
- **Don't touch the dictation pipeline lightly.** The record → transcribe →
  cleanup → inject path works; changes there need a manual end-to-end test
  (see below) in the PR description.
- **One GUI stack.** Everything visual is GTK4/PyGObject (+ libadwaita for
  the settings app). No Qt, no Electron.
- **The LLM never rewrites meaning.** Cleanup-stage changes must preserve
  the fallback-to-raw guarantee: any error, timeout, or suspicious output
  returns the original transcript.

## Dev setup

```sh
sh scripts/install-glas.sh          # or --cpu without an NVIDIA GPU
systemctl --user start hyprwhspr
journalctl --user -u hyprwhspr -f   # watch [PIPELINE] lines while testing
```

Run the daemon in the foreground instead of systemd when iterating:

```sh
systemctl --user stop hyprwhspr
HYPRWHSPR_ROOT=$PWD PYTHONPATH=$PWD/lib .venv/bin/python lib/main.py
```

Settings GUI: `bin/glas-settings`.

## Manual test checklist (pipeline changes)

1. Hold the hotkey, speak, release → text lands in a text editor.
2. Same in a browser text field.
3. Say "new line" mid-sentence → real line break.
4. Toggle `llm_cleanup` off, dictate with an "um" → "um" survives (raw mode).
5. Overlay appears on hold, vanishes on release; clicks pass through it.

## Code style

Match the surrounding file. Comments explain *why*, not *what*. Config keys
are snake_case and must appear in three places: `config_manager.py`
defaults, `share/config.schema.json`, and the README config table.
