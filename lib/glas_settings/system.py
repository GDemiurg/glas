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
