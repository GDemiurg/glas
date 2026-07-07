"""
System helpers for the settings GUI: daemon control, mic enumeration,
whisper model discovery, icon installation, and the test-dictation runner.
"""

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

SERVICE = 'hyprwhspr.service'
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ICONS_DIR = REPO_ROOT / 'share' / 'assets' / 'icons'
INSTALLED_ICON = Path.home() / '.local/share/icons/hicolor/scalable/apps/glas.svg'
MODELS_DIR = Path.home() / '.local/share/pywhispercpp/models'
CONTROL_FIFO = Path.home() / '.config/hyprwhspr/recording_control'

STOCK_MODELS = [
    'tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en',
    'medium', 'medium.en', 'large-v2', 'large-v3', 'large-v3-turbo',
    'distil-large-v3',
]


def _run(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ------------------------ Daemon ------------------------

def daemon_state() -> str:
    """'active' | 'inactive' | 'failed' | ..."""
    try:
        return _run(['systemctl', '--user', 'is-active', SERVICE]).stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def daemon_ctl(action: str) -> bool:
    """action: start|stop|restart"""
    try:
        return _run(['systemctl', '--user', action, SERVICE], timeout=30).returncode == 0
    except Exception:
        return False


# ------------------------ Microphones ------------------------

def list_sources():
    """[(source_name, description)] from PipeWire/PulseAudio, monitors excluded."""
    out = []
    try:
        result = _run(['pactl', 'list', 'sources'])
        name, desc = None, None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('Name:'):
                name = line.split(':', 1)[1].strip()
            elif line.startswith('Description:'):
                desc = line.split(':', 1)[1].strip()
                if name and not name.endswith('.monitor'):
                    out.append((name, desc))
                name, desc = None, None
    except Exception:
        pass
    return out


# ------------------------ Whisper models ------------------------

def list_models():
    """Stock model names + any ggml-*.bin already on disk, deduped."""
    found = []
    for f in sorted(glob.glob(str(MODELS_DIR / 'ggml-*.bin'))):
        name = Path(f).stem
        if name.startswith('ggml-'):
            name = name[len('ggml-'):]
        found.append(name)
    merged = list(dict.fromkeys(found + STOCK_MODELS))
    return merged, set(found)


# ------------------------ Icons ------------------------

def list_bundled_icons():
    """[(variant_name, path)] of shipped icon variants."""
    out = []
    if ICONS_DIR.is_dir():
        for f in sorted(ICONS_DIR.glob('*.svg')):
            out.append((f.stem, f))
    return out


def install_icon(path) -> bool:
    """Copy an SVG into the user hicolor theme as the Glas icon."""
    try:
        INSTALLED_ICON.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, INSTALLED_ICON)
        subprocess.run(['gtk4-update-icon-cache', '-q',
                        str(Path.home() / '.local/share/icons/hicolor')],
                       capture_output=True)
        return True
    except Exception:
        return False


# ------------------------ Themed app icon ------------------------

# Accent per theme — mirrors DemiCrm's presets so the two apps share a
# palette. Add a row here and it appears in the settings theme dropdown.
ICON_THEMES = {
    'gruvbox':        '#fb4934',   # red — default
    'gruvbox-yellow': '#fabd2f',
    'nord':           '#88c0d0',
    'minimal-dark':   '#6c8ee0',
    'cream':          '#e6d8b8',
}
DEFAULT_ICON_THEME = 'gruvbox'

# Waveform-G traced in a single flat accent on a transparent canvas
# (DemiCrm line-glyph family). {accent} is filled at render time.
ICON_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <!-- Glas — waveform-G, tinted to the active theme's accent. -->
  <g fill="none" stroke="{accent}" stroke-width="26" stroke-linecap="round">
    <line x1="123" y1="195" x2="123" y2="315"/>
    <line x1="159" y1="146" x2="159" y2="364"/>
    <line x1="195" y1="123" x2="195" y2="195"/>
    <line x1="195" y1="315" x2="195" y2="387"/>
    <line x1="231" y1="112" x2="231" y2="173"/>
    <line x1="231" y1="337" x2="231" y2="398"/>
    <line x1="267" y1="110" x2="267" y2="171"/>
    <line x1="267" y1="339" x2="267" y2="400"/>
    <line x1="303" y1="118" x2="303" y2="185"/>
    <line x1="303" y1="325" x2="303" y2="392"/>
    <line x1="303" y1="245" x2="303" y2="295"/>
    <line x1="339" y1="240" x2="339" y2="373"/>
    <line x1="375" y1="240" x2="375" y2="336"/>
  </g>
</svg>
"""


def render_themed_icon(theme: str) -> bool:
    """Write the app/tray glyph tinted to THEME's accent, refresh the icon
    cache. Unknown theme -> gruvbox default. Never raises — the launcher
    keeps the last-installed icon when this returns False."""
    try:
        accent = ICON_THEMES.get(theme, ICON_THEMES[DEFAULT_ICON_THEME])
        INSTALLED_ICON.parent.mkdir(parents=True, exist_ok=True)
        INSTALLED_ICON.write_text(ICON_TEMPLATE.format(accent=accent),
                                  encoding='utf-8')
        subprocess.run(['gtk4-update-icon-cache', '-q',
                        str(Path.home() / '.local/share/icons/hicolor')],
                       capture_output=True)
        return True
    except Exception:
        return False


# ------------------------ Test dictation ------------------------

def fifo_command(cmd: str) -> bool:
    """Write start/stop/cancel to the daemon's control FIFO (non-blocking)."""
    try:
        fd = os.open(str(CONTROL_FIFO), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (cmd + '\n').encode())
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def capture_dictation(seconds=4.0):
    """Record + transcribe WITHOUT injecting, via the daemon's capture socket
    (the daemon routes the transcript to the socket subscriber instead of
    pasting it). Blocking — call from a worker thread. Returns the raw
    transcript string, or None."""
    import socket as _socket
    import time as _time

    sock_path = Path.home() / '.config/hyprwhspr/hyprwhspr.sock'
    try:
        conn = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        conn.settimeout(seconds + 25)
        conn.connect(str(sock_path))
        conn.sendall(b'capture\n')
    except OSError:
        return None

    try:
        _time.sleep(seconds)
        fifo_command('stop')
        chunks = []
        while True:
            try:
                data = conn.recv(4096)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        # Wire protocol is plain UTF-8 transcript bytes, no framing
        text = b''.join(chunks).decode('utf-8', 'replace').strip()
        return text or None
    finally:
        try:
            conn.close()
        except OSError:
            pass
