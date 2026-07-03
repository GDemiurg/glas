"""
Glas tray icon — StatusNotifierItem + DBusMenu over GDBus (no extra deps).

Quick toggles: dictation daemon on/off, LLM cleanup on/off, open settings.
Runs inside the settings app process; the window can be closed while the
tray keeps the app alive.
"""

from gi.repository import Gio, GLib

from . import system
from .config_store import ConfigStore

SNI_XML = """
<node>
  <interface name='org.kde.StatusNotifierItem'>
    <property name='Category' type='s' access='read'/>
    <property name='Id' type='s' access='read'/>
    <property name='Title' type='s' access='read'/>
    <property name='Status' type='s' access='read'/>
    <property name='IconName' type='s' access='read'/>
    <property name='Menu' type='o' access='read'/>
    <property name='ItemIsMenu' type='b' access='read'/>
    <method name='Activate'>
      <arg type='i' direction='in' name='x'/>
      <arg type='i' direction='in' name='y'/>
    </method>
    <method name='SecondaryActivate'>
      <arg type='i' direction='in' name='x'/>
      <arg type='i' direction='in' name='y'/>
    </method>
    <method name='ContextMenu'>
      <arg type='i' direction='in' name='x'/>
      <arg type='i' direction='in' name='y'/>
    </method>
    <signal name='NewStatus'><arg type='s'/></signal>
    <signal name='NewIcon'/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name='com.canonical.dbusmenu'>
    <property name='Version' type='u' access='read'/>
    <property name='Status' type='s' access='read'/>
    <method name='GetLayout'>
      <arg type='i' direction='in' name='parentId'/>
      <arg type='i' direction='in' name='recursionDepth'/>
      <arg type='as' direction='in' name='propertyNames'/>
      <arg type='u' direction='out' name='revision'/>
      <arg type='(ia{sv}av)' direction='out' name='layout'/>
    </method>
    <method name='GetGroupProperties'>
      <arg type='ai' direction='in' name='ids'/>
      <arg type='as' direction='in' name='propertyNames'/>
      <arg type='a(ia{sv})' direction='out' name='properties'/>
    </method>
    <method name='Event'>
      <arg type='i' direction='in' name='id'/>
      <arg type='s' direction='in' name='eventId'/>
      <arg type='v' direction='in' name='data'/>
      <arg type='u' direction='in' name='timestamp'/>
    </method>
    <method name='AboutToShow'>
      <arg type='i' direction='in' name='id'/>
      <arg type='b' direction='out' name='needUpdate'/>
    </method>
    <signal name='LayoutUpdated'>
      <arg type='u' name='revision'/>
      <arg type='i' name='parent'/>
    </signal>
  </interface>
</node>
"""

MENU_PATH = '/GlasMenu'
ITEM_PATH = '/StatusNotifierItem'

# Menu item ids
M_DAEMON = 1
M_CLEANUP = 2
M_SETTINGS = 3
M_QUIT = 4


class GlasTray:
    def __init__(self, on_open_settings, on_quit):
        self.on_open_settings = on_open_settings
        self.on_quit = on_quit
        self.cfg = ConfigStore()
        self.revision = 1
        self.conn = None
        self._owner_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            f'org.kde.StatusNotifierItem-{GLib.getenv("USER") or "glas"}-glas',
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired, self._on_name_acquired, None)

    # ------------------------ DBus scaffolding ------------------------

    def _on_bus_acquired(self, conn, _name):
        self.conn = conn
        sni_node = Gio.DBusNodeInfo.new_for_xml(SNI_XML)
        menu_node = Gio.DBusNodeInfo.new_for_xml(MENU_XML)
        conn.register_object(ITEM_PATH, sni_node.interfaces[0],
                             self._sni_call, self._sni_get, None)
        conn.register_object(MENU_PATH, menu_node.interfaces[0],
                             self._menu_call, self._menu_get, None)

    def _on_name_acquired(self, conn, name):
        # Register with the watcher so the tray host picks us up
        conn.call(
            'org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
            'org.kde.StatusNotifierWatcher', 'RegisterStatusNotifierItem',
            GLib.Variant('(s)', (name,)), None,
            Gio.DBusCallFlags.NONE, -1, None, None)

    # ------------------------ SNI ------------------------

    def _sni_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            'Category': GLib.Variant('s', 'ApplicationStatus'),
            'Id': GLib.Variant('s', 'glas'),
            'Title': GLib.Variant('s', 'Glas'),
            'Status': GLib.Variant('s', 'Active'),
            'IconName': GLib.Variant('s', 'glas'),
            'Menu': GLib.Variant('o', MENU_PATH),
            'ItemIsMenu': GLib.Variant('b', False),
        }
        return values.get(prop)

    def _sni_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method == 'Activate':
            self.on_open_settings()
        elif method == 'SecondaryActivate':
            self._toggle_daemon()
        # ContextMenu: the host renders our DBusMenu itself
        invocation.return_value(None)

    # ------------------------ Menu ------------------------

    def _menu_items(self):
        daemon_on = system.daemon_state() == 'active'
        self.cfg.load()
        cleanup_on = bool(self.cfg.get('llm_cleanup', False))
        return [
            (M_DAEMON, {
                'label': GLib.Variant('s', 'Dictation daemon'),
                'toggle-type': GLib.Variant('s', 'checkmark'),
                'toggle-state': GLib.Variant('i', 1 if daemon_on else 0),
            }),
            (M_CLEANUP, {
                'label': GLib.Variant('s', 'LLM cleanup'),
                'toggle-type': GLib.Variant('s', 'checkmark'),
                'toggle-state': GLib.Variant('i', 1 if cleanup_on else 0),
            }),
            (M_SETTINGS, {'label': GLib.Variant('s', 'Settings…')}),
            (M_QUIT, {'label': GLib.Variant('s', 'Quit tray')}),
        ]

    def _menu_get(self, _conn, _sender, _path, _iface, prop):
        if prop == 'Version':
            return GLib.Variant('u', 3)
        if prop == 'Status':
            return GLib.Variant('s', 'normal')
        return None

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == 'GetLayout':
            children = []
            for mid, props in self._menu_items():
                children.append(GLib.Variant('v', GLib.Variant(
                    '(ia{sv}av)', (mid, props, []))))
            layout = GLib.Variant('(u(ia{sv}av))',
                                  (self.revision, (0, {'children-display':
                                   GLib.Variant('s', 'submenu')}, children)))
            invocation.return_value(layout)
        elif method == 'GetGroupProperties':
            props = [(mid, p) for mid, p in self._menu_items()]
            invocation.return_value(GLib.Variant('(a(ia{sv}))', (props,)))
        elif method == 'Event':
            mid, event_id, _data, _ts = params.unpack()
            if event_id == 'clicked':
                GLib.idle_add(self._on_menu_clicked, mid)
            invocation.return_value(None)
        elif method == 'AboutToShow':
            # Layout is computed fresh in every GetLayout call; answering
            # "needs update" here makes Plasma refetch forever and render
            # nothing, so always say no.
            invocation.return_value(GLib.Variant('(b)', (False,)))
        else:
            invocation.return_value(None)

    def _bump_revision(self):
        self.revision += 1
        if self.conn:
            self.conn.emit_signal(None, MENU_PATH, 'com.canonical.dbusmenu',
                                  'LayoutUpdated',
                                  GLib.Variant('(ui)', (self.revision, 0)))

    def _on_menu_clicked(self, mid):
        if mid == M_DAEMON:
            self._toggle_daemon()
        elif mid == M_CLEANUP:
            self.cfg.load()
            self.cfg.set('llm_cleanup', not bool(self.cfg.get('llm_cleanup', False)))
            system.daemon_ctl('restart')
        elif mid == M_SETTINGS:
            self.on_open_settings()
        elif mid == M_QUIT:
            self.on_quit()
        self._bump_revision()
        return False

    def _toggle_daemon(self):
        action = 'stop' if system.daemon_state() == 'active' else 'start'
        system.daemon_ctl(action)
        self._bump_revision()
