"""
Glas Settings — GTK4/libadwaita control panel for the Glas dictation daemon.

Edits the daemon's own config.json (see config_store); most changes need a
daemon restart, surfaced via an "Apply" banner. Also provides daemon
start/stop/restart, a live-status indicator, a test-dictation runner and
an icon switcher.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .config_store import ConfigStore  # noqa: E402
from . import system  # noqa: E402

# Must match the installed desktop file name (dev.demiurg.Glas.desktop) so
# KDE/GNOME associate the window with the launcher entry and its icon.
APP_ID = 'dev.demiurg.Glas'

LANGS = ['auto', 'en', 'bg', 'de', 'fr', 'es', 'it', 'ru', 'uk', 'nl', 'pl', 'pt', 'tr']
PASTE_MODES = ['auto', 'ctrl', 'ctrl_shift', 'super']
OSD_STYLES = ['wave', 'waveform', 'vu_meter']
OSD_ANCHORS = ['bottom', 'top']
RECORDING_MODES = ['push_to_talk', 'toggle', 'auto', 'continuous']

# Overlay colors exposed in the GUI: (theme key, label, default hex)
OSD_COLOR_KEYS = [
    ('bar-color-left', 'Bars / strand left', '#fabd2f'),
    ('bar-color-right', 'Bars / strand right', '#fe8019'),
    ('background-color', 'Scrim', '#1d2021'),
]


def _combo_row(title, options, current, on_select, subtitle=None):
    row = Adw.ComboRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_model(Gtk.StringList.new([str(o) for o in options]))
    try:
        row.set_selected(options.index(current))
    except ValueError:
        row.set_selected(0)
    row.connect('notify::selected', lambda r, _: on_select(options[r.get_selected()]))
    return row


def _switch_row(title, value, on_toggle, subtitle=None):
    row = Adw.SwitchRow(title=title, active=bool(value))
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect('notify::active', lambda r, _: on_toggle(r.get_active()))
    return row


def _spin_row(title, value, lo, hi, step, on_change, subtitle=None):
    adj = Gtk.Adjustment(value=float(value), lower=lo, upper=hi,
                         step_increment=step, page_increment=step * 5)
    row = Adw.SpinRow(title=title, adjustment=adj, digits=0 if step >= 1 else 2)
    if subtitle:
        row.set_subtitle(subtitle)
    row.connect('notify::value', lambda r, _: on_change(
        int(r.get_value()) if step >= 1 else round(r.get_value(), 2)))
    return row


def _entry_row(title, value, on_change):
    row = Adw.EntryRow(title=title, text=str(value or ''))
    row.connect('apply', lambda r: on_change(r.get_text()))
    row.set_show_apply_button(True)
    return row


def _text_row(title, value, on_change, subtitle=None):
    """Expander row with a proper multiline editor + Save button — for
    prompts that don't fit a single-line entry."""
    row = Adw.ExpanderRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)

    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                        top_margin=8, bottom_margin=8,
                        left_margin=10, right_margin=10)
    view.get_buffer().set_text(str(value or ''))
    scroll = Gtk.ScrolledWindow(min_content_height=110, max_content_height=240,
                                hexpand=True, propagate_natural_height=True)
    scroll.set_child(view)

    save = Gtk.Button(label='Save', halign=Gtk.Align.END,
                      margin_top=6, margin_bottom=8, margin_end=8)
    save.add_css_class('suggested-action')

    def on_save(_b):
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        on_change(text.strip())

    save.connect('clicked', on_save)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(scroll)
    box.append(save)
    item = Gtk.ListBoxRow(child=box, activatable=False, selectable=False)
    row.add_row(item)
    return row


class GlasWindow(Adw.PreferencesWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Glas Settings')
        self.set_default_size(760, 720)
        self.set_search_enabled(True)
        self.cfg = ConfigStore()

        self._restart_banner_added = False
        self._build_general()
        self._build_stt()
        self._build_cleanup()
        self._build_injection()
        self._build_overlay()
        self._build_commands()
        self._build_icon()

        GLib.timeout_add_seconds(2, self._poll_status)

    # ------------------------ Helpers ------------------------

    def _set(self, key, value):
        self.cfg.set(key, value)
        self._needs_restart()

    def _needs_restart(self):
        self.add_toast(Adw.Toast(
            title='Saved — restart the daemon to apply',
            button_label='Restart',
            action_name='app.restart-daemon',
            timeout=4,
        ))

    # ------------------------ General page ------------------------

    def _build_general(self):
        page = Adw.PreferencesPage(title='General', icon_name='audio-input-microphone-symbolic')

        # Daemon status + controls
        grp = Adw.PreferencesGroup(title='Daemon')
        self.status_row = Adw.ActionRow(title='Status', subtitle='checking…')
        box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for label, action in (('Start', 'start'), ('Stop', 'stop'), ('Restart', 'restart')):
            btn = Gtk.Button(label=label)
            btn.connect('clicked', self._on_daemon_ctl, action)
            box.append(btn)
        self.status_row.add_suffix(box)
        grp.add(self.status_row)

        test_row = Adw.ActionRow(
            title='Test dictation',
            subtitle='Records ~4s from the mic, shows raw and cleaned text (nothing is typed)')
        test_btn = Gtk.Button(label='Run test', valign=Gtk.Align.CENTER)
        test_btn.connect('clicked', self._on_test)
        test_row.add_suffix(test_btn)
        grp.add(test_row)
        page.add(grp)

        # Input device
        grp = Adw.PreferencesGroup(title='Microphone')
        sources = system.list_sources()
        options = ['System default'] + [desc for _, desc in sources]
        self._source_names = [None] + [name for name, _ in sources]
        current_name = self.cfg.get('audio_device_name')
        current = 'System default'
        for name, desc in sources:
            if current_name and current_name.lower() in (name.lower(), desc.lower()):
                current = desc
            elif current_name and current_name.lower() in desc.lower():
                current = desc
        grp.add(_combo_row('Input device', options, current, self._on_mic_select,
                           subtitle='Enumerated from PipeWire/PulseAudio'))
        page.add(grp)

        # Hotkey
        grp = Adw.PreferencesGroup(title='Push-to-talk')
        self.hotkey_row = Adw.ActionRow(
            title='Hotkey', subtitle=str(self.cfg.get('primary_shortcut', 'F9')))
        btn = Gtk.Button(label='Rebind…', valign=Gtk.Align.CENTER)
        btn.connect('clicked', self._on_rebind)
        self.hotkey_row.add_suffix(btn)
        grp.add(self.hotkey_row)

        grp.add(_combo_row('Recording mode', RECORDING_MODES,
                           self.cfg.get('recording_mode', 'push_to_talk'),
                           lambda v: self._set('recording_mode', v),
                           subtitle='push_to_talk = hold to record'))
        page.add(grp)

        self.add(page)

    def _on_daemon_ctl(self, _btn, action):
        ok = system.daemon_ctl(action)
        self.add_toast(Adw.Toast(title=f'Daemon {action}: {"ok" if ok else "FAILED"}', timeout=3))
        GLib.timeout_add(1500, lambda: (self._poll_status(), False)[1])

    def _poll_status(self):
        state = system.daemon_state()
        pretty = {'active': 'Running', 'inactive': 'Stopped', 'failed': 'Failed'}.get(state, state)
        self.status_row.set_subtitle(pretty)
        return True

    def _on_mic_select(self, desc):
        idx = None
        sources = system.list_sources()
        for i, (_name, d) in enumerate(sources):
            if d == desc:
                idx = i
        if desc == 'System default' or idx is None:
            self.cfg.unset('audio_device_name')
        else:
            # Store a stable substring of the device description — the daemon
            # matches by name substring (audio_device_name)
            self.cfg.set('audio_device_name', sources[idx][1])
        self._needs_restart()

    def _on_rebind(self, _btn):
        dlg = Adw.MessageDialog(transient_for=self, heading='Press the new hotkey',
                                body='Press a key (with optional modifiers). Esc cancels.')
        dlg.add_response('cancel', 'Cancel')
        ctl = Gtk.EventControllerKey()

        def on_key(_c, keyval, _keycode, state):
            name = Gdk.keyval_name(keyval) or ''
            if name in ('Escape',):
                dlg.close()
                return True
            if name in ('Control_L', 'Control_R', 'Shift_L', 'Shift_R',
                        'Alt_L', 'Alt_R', 'Super_L', 'Super_R', 'Meta_L', 'Meta_R'):
                return True  # wait for a real key
            mods = []
            if state & Gdk.ModifierType.SUPER_MASK:
                mods.append('SUPER')
            if state & Gdk.ModifierType.CONTROL_MASK:
                mods.append('CTRL')
            if state & Gdk.ModifierType.ALT_MASK:
                mods.append('ALT')
            if state & Gdk.ModifierType.SHIFT_MASK:
                mods.append('SHIFT')
            shortcut = '+'.join(mods + [name.upper()])
            self.cfg.set('primary_shortcut', shortcut)
            self.hotkey_row.set_subtitle(shortcut)
            self._needs_restart()
            dlg.close()
            return True

        ctl.connect('key-pressed', on_key)
        dlg.add_controller(ctl)
        dlg.present()

    def _on_test(self, btn):
        if system.daemon_state() != 'active':
            self.add_toast(Adw.Toast(title='Daemon not running — start it first', timeout=4))
            return
        btn.set_sensitive(False)
        self.add_toast(Adw.Toast(title='Recording 4s — speak now…', timeout=4))

        import threading

        def worker():
            raw = system.capture_dictation(seconds=4.0)
            cleaned = None
            if raw and self.cfg.get('llm_cleanup', False):
                try:
                    import sys as _sys
                    from pathlib import Path as _Path
                    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / 'src'))
                    from llm_cleanup import LLMCleanup

                    class _Cfg:
                        def __init__(s, store):
                            s.store = store

                        def get_setting(s, key, default=None):
                            return s.store.get(key, default)

                    cleaned = LLMCleanup(_Cfg(self.cfg)).cleanup(raw)
                except Exception:
                    cleaned = None
            GLib.idle_add(self._show_test_result, btn, raw, cleaned)

        threading.Thread(target=worker, daemon=True).start()

    def _show_test_result(self, btn, raw, cleaned):
        btn.set_sensitive(True)
        if not raw:
            self.add_toast(Adw.Toast(
                title='No transcript (too quiet, or another capture is active)', timeout=5))
            return False
        body = f'Raw transcript:\n{raw}'
        if cleaned and cleaned != raw:
            body += f'\n\nCleaned (LLM):\n{cleaned}'
        elif self.cfg.get('llm_cleanup', False):
            body += '\n\n(cleanup made no changes)'
        else:
            body += '\n\n(LLM cleanup is off — raw would be typed)'
        dlg = Adw.MessageDialog(transient_for=self, heading='Test dictation result', body=body)
        dlg.add_response('ok', 'Close')
        dlg.present()
        return False

    # ------------------------ STT page ------------------------

    def _build_stt(self):
        page = Adw.PreferencesPage(title='Speech', icon_name='media-record-symbolic')
        grp = Adw.PreferencesGroup(
            title='Whisper (local STT)',
            description='Models marked ✓ are on disk; others download on first use')
        models, on_disk = system.list_models()
        labels = [f'{m} ✓' if m in on_disk else m for m in models]
        current = self.cfg.get('model', 'base')
        current_label = f'{current} ✓' if current in on_disk else current

        def on_model(label):
            self._set('model', label.replace(' ✓', ''))

        grp.add(_combo_row('Model', labels, current_label, on_model))
        grp.add(_combo_row('Language', LANGS,
                           self.cfg.get('language') or 'auto',
                           lambda v: self._set('language', None if v == 'auto' else v)))
        grp.add(_text_row('Custom vocabulary / prompt',
                          self.cfg.get('whisper_prompt', ''),
                          lambda v: self._set('whisper_prompt', v),
                          subtitle='Names, domains, jargon — primes the decoder'))
        page.add(grp)

        grp = Adw.PreferencesGroup(
            title='Compute',
            description='CUDA is used automatically when the pywhispercpp build supports it; '
                        'threads apply to CPU decoding')
        grp.add(_spin_row('Threads', self.cfg.get('threads', 8), 1, 32, 1,
                          lambda v: self._set('threads', v)))
        page.add(grp)
        self.add(page)

    # ------------------------ Cleanup page ------------------------

    def _build_cleanup(self):
        page = Adw.PreferencesPage(title='Cleanup', icon_name='edit-clear-all-symbolic')
        grp = Adw.PreferencesGroup(
            title='LLM cleanup (Ollama)',
            description='Fixes punctuation and strips filler. Off = raw transcript is typed. '
                        'Any failure falls back to the raw text.')
        grp.add(_switch_row('Enable cleanup', self.cfg.get('llm_cleanup', False),
                            lambda v: self._set('llm_cleanup', v)))
        grp.add(_entry_row('Ollama model', self.cfg.get('llm_cleanup_model', 'gemma3:4b'),
                           lambda v: self._set('llm_cleanup_model', v)))
        grp.add(_entry_row('Endpoint URL', self.cfg.get('llm_cleanup_url', 'http://localhost:11434'),
                           lambda v: self._set('llm_cleanup_url', v)))
        grp.add(_spin_row('Timeout (s)', float(self.cfg.get('llm_cleanup_timeout', 8.0)),
                          1, 60, 0.5, lambda v: self._set('llm_cleanup_timeout', v)))
        grp.add(_spin_row('Temperature', float(self.cfg.get('llm_cleanup_temperature', 0.0)),
                          0, 1, 0.05, lambda v: self._set('llm_cleanup_temperature', v),
                          subtitle='0 = deterministic (recommended)'))
        grp.add(_text_row('Cleanup prompt',
                          self.cfg.get('llm_cleanup_prompt') or '',
                          lambda v: self._set('llm_cleanup_prompt', v or None),
                          subtitle='Blank = built-in (fix formatting, never rewrite)'))
        page.add(grp)
        self.add(page)

    # ------------------------ Injection page ------------------------

    def _build_injection(self):
        page = Adw.PreferencesPage(title='Injection', icon_name='input-keyboard-symbolic')
        grp = Adw.PreferencesGroup(
            title='Paste behavior',
            description='Text is injected via clipboard + paste keystroke '
                        '(ydotool on KDE; wtype where the compositor supports it)')
        grp.add(_combo_row('Paste chord', PASTE_MODES,
                           self.cfg.get('paste_mode') or 'auto',
                           lambda v: self._set('paste_mode', None if v == 'auto' else v),
                           subtitle='auto: terminals get Ctrl+Shift+V, apps get Ctrl+V'))
        grp.add(_switch_row('Clear clipboard after paste',
                            self.cfg.get('clipboard_behavior', False),
                            lambda v: self._set('clipboard_behavior', v)))
        grp.add(_spin_row('Clear delay (s)', float(self.cfg.get('clipboard_clear_delay', 5.0)),
                          1, 60, 1, lambda v: self._set('clipboard_clear_delay', float(v))))
        grp.add(_switch_row('Auto-submit (press Enter after paste)',
                            self.cfg.get('auto_submit', False),
                            lambda v: self._set('auto_submit', v)))
        page.add(grp)
        self.add(page)

    # ------------------------ Overlay page ------------------------

    def _build_overlay(self):
        page = Adw.PreferencesPage(title='Overlay', icon_name='view-reveal-symbolic')
        grp = Adw.PreferencesGroup(title='Mic visualizer')
        grp.add(_switch_row('Show overlay while recording',
                            self.cfg.get('mic_osd_enabled', True),
                            lambda v: self._set('mic_osd_enabled', v),
                            subtitle='Needs layer-shell (KDE, Hyprland, Sway); '
                                     'dictation works fine without it'))
        grp.add(_combo_row('Style', OSD_STYLES, self.cfg.get('mic_osd_style', 'wave'),
                           lambda v: self._set('mic_osd_style', v)))
        grp.add(_spin_row('Width (px)', self.cfg.get('mic_osd_width', 400), 120, 2000, 10,
                          lambda v: self._set('mic_osd_width', v)))
        grp.add(_spin_row('Height (px)', self.cfg.get('mic_osd_height', 68), 40, 400, 4,
                          lambda v: self._set('mic_osd_height', v)))
        grp.add(_combo_row('Anchor edge', OSD_ANCHORS, self.cfg.get('mic_osd_anchor', 'bottom'),
                           lambda v: self._set('mic_osd_anchor', v)))
        grp.add(_spin_row('Margin from edge (px)', self.cfg.get('mic_osd_margin', 130),
                          0, 2000, 10, lambda v: self._set('mic_osd_margin', v)))
        grp.add(_spin_row('Resolution / bar count', self.cfg.get('mic_osd_bars', 48),
                          8, 128, 4, lambda v: self._set('mic_osd_bars', v)))
        page.add(grp)

        grp = Adw.PreferencesGroup(title='Colors',
                                   description='Overrides the built-in gruvbox palette')
        colors = dict(self.cfg.get('mic_osd_colors') or {})
        for key, label, default in OSD_COLOR_KEYS:
            grp.add(self._color_row(key, label, colors.get(key, default)))
        reset = Adw.ActionRow(title='Reset colors to defaults')
        btn = Gtk.Button(label='Reset', valign=Gtk.Align.CENTER)

        def do_reset(_b):
            self.cfg.set('mic_osd_colors', {})
            self._needs_restart()
        btn.connect('clicked', do_reset)
        reset.add_suffix(btn)
        grp.add(reset)
        page.add(grp)
        self.add(page)

    def _color_row(self, key, label, hex_value):
        row = Adw.ActionRow(title=label)
        rgba = Gdk.RGBA()
        rgba.parse(hex_value)
        btn = Gtk.ColorDialogButton(valign=Gtk.Align.CENTER)
        btn.set_dialog(Gtk.ColorDialog())
        btn.set_rgba(rgba)

        def on_color(b, _p):
            c = b.get_rgba()
            hexv = '#%02x%02x%02x' % (int(c.red * 255), int(c.green * 255), int(c.blue * 255))
            colors = dict(self.cfg.get('mic_osd_colors') or {})
            colors[key] = hexv
            self.cfg.set('mic_osd_colors', colors)
            self._needs_restart()

        btn.connect('notify::rgba', on_color)
        row.add_suffix(btn)
        return row

    # ------------------------ Voice commands page ------------------------

    def _build_commands(self):
        page = Adw.PreferencesPage(title='Commands', icon_name='format-text-rich-symbolic')

        grp = Adw.PreferencesGroup(
            title='Spoken symbols',
            description='"new line", "period", "comma", "open paren"… become the symbol')
        grp.add(_switch_row('Enable spoken symbols', self.cfg.get('symbol_replacements', True),
                            lambda v: self._set('symbol_replacements', v)))
        page.add(grp)

        grp = Adw.PreferencesGroup(title='Filler words')
        grp.add(_switch_row('Strip filler words without the LLM',
                            self.cfg.get('filter_filler_words', False),
                            lambda v: self._set('filter_filler_words', v),
                            subtitle='Regex removal of uh/um/er — useful when cleanup is off'))
        page.add(grp)

        self.overrides_grp = Adw.PreferencesGroup(
            title='Word overrides',
            description='Applied before AND after LLM cleanup — fix mishears, '
                        'expand phrases, force spellings')
        self._override_rows = []
        self._rebuild_overrides()

        add_row = Adw.ActionRow(title='Add override')
        self._new_phrase = Gtk.Entry(placeholder_text='heard phrase', valign=Gtk.Align.CENTER)
        self._new_repl = Gtk.Entry(placeholder_text='replacement', valign=Gtk.Align.CENTER)
        add_btn = Gtk.Button(icon_name='list-add-symbolic', valign=Gtk.Align.CENTER)
        add_btn.connect('clicked', self._on_add_override)
        add_row.add_suffix(self._new_phrase)
        add_row.add_suffix(self._new_repl)
        add_row.add_suffix(add_btn)
        self.overrides_grp.add(add_row)
        page.add(self.overrides_grp)
        self.add(page)

    def _rebuild_overrides(self):
        for row in self._override_rows:
            self.overrides_grp.remove(row)
        self._override_rows = []
        overrides = dict(self.cfg.get('word_overrides') or {})
        for phrase, repl in overrides.items():
            row = Adw.ActionRow(title=phrase, subtitle=f'→ {repl}')
            btn = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER)
            btn.connect('clicked', self._on_del_override, phrase)
            row.add_suffix(btn)
            self.overrides_grp.add(row)
            self._override_rows.append(row)

    def _on_add_override(self, _btn):
        phrase = self._new_phrase.get_text().strip()
        repl = self._new_repl.get_text().strip()
        if not phrase:
            return
        overrides = dict(self.cfg.get('word_overrides') or {})
        overrides[phrase] = repl
        self.cfg.set('word_overrides', overrides)
        self._new_phrase.set_text('')
        self._new_repl.set_text('')
        self._rebuild_overrides()
        self._needs_restart()

    def _on_del_override(self, _btn, phrase):
        overrides = dict(self.cfg.get('word_overrides') or {})
        overrides.pop(phrase, None)
        self.cfg.set('word_overrides', overrides)
        self._rebuild_overrides()
        self._needs_restart()

    # ------------------------ Icon page ------------------------

    def _build_icon(self):
        page = Adw.PreferencesPage(title='Icon', icon_name='applications-graphics-symbolic')
        grp = Adw.PreferencesGroup(
            title='App / tray icon',
            description='Applies to the launcher and tray. May need a relogin '
                        'or icon-cache refresh to show everywhere.')
        for name, path in system.list_bundled_icons():
            row = Adw.ActionRow(title=name.replace('-', ' ').title())
            img = Gtk.Image.new_from_file(str(path))
            img.set_pixel_size(40)
            row.add_prefix(img)
            btn = Gtk.Button(label='Use', valign=Gtk.Align.CENTER)
            btn.connect('clicked', self._on_icon_pick, path)
            row.add_suffix(btn)
            grp.add(row)

        custom = Adw.ActionRow(title='Custom SVG…')
        btn = Gtk.Button(label='Choose file', valign=Gtk.Align.CENTER)
        btn.connect('clicked', self._on_icon_custom)
        custom.add_suffix(btn)
        grp.add(custom)
        page.add(grp)
        self.add(page)

    def _on_icon_pick(self, _btn, path):
        ok = system.install_icon(path)
        self.add_toast(Adw.Toast(
            title='Icon installed' if ok else 'Icon install FAILED', timeout=3))

    def _on_icon_custom(self, _btn):
        dialog = Gtk.FileDialog(title='Pick an SVG icon')

        def done(dlg, result):
            try:
                f = dlg.open_finish(result)
            except GLib.Error:
                return
            if f:
                self._on_icon_pick(None, f.get_path())

        dialog.open(self, None, done)


class GlasApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect('activate', self._on_activate)
        from gi.repository import Gio
        restart = Gio.SimpleAction.new('restart-daemon', None)
        restart.connect('activate', lambda *_: system.daemon_ctl('restart'))
        self.add_action(restart)
        self.tray = None

    def _on_activate(self, app):
        if self.tray is None:
            try:
                from .tray import GlasTray
                self.tray = GlasTray(on_open_settings=self._open_window,
                                     on_quit=self._quit)
                # Tray keeps the app alive with the window closed
                self.hold()
            except Exception as e:
                print(f'[GLAS] Tray unavailable: {e}', flush=True)
        self._open_window()

    def _open_window(self):
        win = self.get_active_window() or GlasWindow(self)
        win.present()

    def _quit(self):
        self.release()
        self.quit()


def main():
    app = GlasApp()
    return app.run(None)
