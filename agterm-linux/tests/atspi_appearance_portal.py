"""A stand-in for xdg-desktop-portal's Settings interface on the runner's private session bus.

libadwaita reads `org.freedesktop.appearance color-scheme` from `org.freedesktop.portal.Desktop` once at
startup and then follows `SettingChanged` signals, so this process must own the name BEFORE the app
launches. It refuses to queue behind another owner: a real portal answering instead would leave every
appearance flip unobserved. The app is unmodified; this only plays the desktop's part.

Protocol: argv[1] is the initial scheme (`light` / `dark`). After the name is owned it prints `READY`.
Each stdin line `light` / `dark` updates the value, emits `SettingChanged`, flushes, and prints `SET <x>`.
EOF on stdin exits.
"""

import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.portal.Settings"
NAMESPACE, KEY = "org.freedesktop.appearance", "color-scheme"
NOT_FOUND = "org.freedesktop.portal.Error.NotFound"
# The freedesktop values: 0 no preference, 1 prefer dark, 2 prefer light.
SCHEMES = {"dark": 1, "light": 2}
INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Read">
      <arg type="s" name="namespace" direction="in"/>
      <arg type="s" name="key" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="ReadOne">
      <arg type="s" name="namespace" direction="in"/>
      <arg type="s" name="key" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="ReadAll">
      <arg type="as" name="namespaces" direction="in"/>
      <arg type="a{{sa{{sv}}}}" name="value" direction="out"/>
    </method>
    <signal name="SettingChanged">
      <arg type="s" name="namespace"/>
      <arg type="s" name="key"/>
      <arg type="v" name="value"/>
    </signal>
  </interface>
</node>
"""


def main():
    state = {"scheme": SCHEMES[sys.argv[1]]}
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def method_call(_connection, _sender, _path, _interface, method, parameters, invocation):
        value = GLib.Variant("u", state["scheme"])
        if method == "ReadAll":
            wanted = parameters.unpack()[0]
            found = {NAMESPACE: {KEY: value}} if not wanted or NAMESPACE in wanted else {}
            invocation.return_value(GLib.Variant("(a{sa{sv}})", (found,)))
            return
        namespace, key = parameters.unpack()
        if (namespace, key) != (NAMESPACE, KEY):
            invocation.return_dbus_error(NOT_FOUND, f"{namespace}.{key} is not served by this fixture")
        elif method == "Read":
            # The legacy Read wraps the value in one more variant; libadwaita 1.5 unwraps both.
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("v", value),)))
        else:
            invocation.return_value(GLib.Variant("(v)", (value,)))

    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
    # libadwaita 1.5 (the runner image) never reads the `version` property, so none is served.
    # PyGObject exposes g_dbus_connection_register_object_with_closures under its rename-to name,
    # `register_object`; the C name is not an attribute in any PyGObject.
    bus.register_object(OBJECT_PATH, node.interfaces[0], method_call, None, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)),  # DO_NOT_QUEUE
                          GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, -1, None)
    if reply.unpack()[0] != 1:  # PRIMARY_OWNER
        print(f"FAIL: {BUS_NAME} already has an owner on this bus", flush=True)
        return 3
    print("READY", flush=True)

    loop = GLib.MainLoop()

    def on_stdin(_channel, _condition):
        line = sys.stdin.readline()
        if not line:
            loop.quit()
            return False
        name = line.strip()
        state["scheme"] = SCHEMES[name]
        bus.emit_signal(None, OBJECT_PATH, INTERFACE, "SettingChanged",
                        GLib.Variant("(ssv)", (NAMESPACE, KEY, GLib.Variant("u", state["scheme"]))))
        bus.flush_sync(None)
        print(f"SET {name}", flush=True)
        return True

    GLib.io_add_watch(GLib.IOChannel.unix_new(sys.stdin.fileno()), GLib.PRIORITY_DEFAULT,
                      GLib.IOCondition.IN | GLib.IOCondition.HUP, on_stdin)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
