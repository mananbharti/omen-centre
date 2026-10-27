"""Bind the Omen key to launch this app.

The kernel driver maps the Omen key to KEY_MSDOS, which XKB/GNOME sees as
the XF86DOS accelerator. Binding writes a GNOME custom shortcut in the
user's own session settings - no root, fully reversible.
"""
import ast
import subprocess

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/omen-centre/"
BINDING = "XF86DOS"
COMMAND = "/usr/share/omen-centre/omen-centre.py"


def _gsettings(*args):
    proc = subprocess.run(["gsettings"] + list(args),
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def schema_available():
    proc = subprocess.run(
        ["gsettings", "get", SCHEMA, "custom-keybindings"],
        capture_output=True, text=True)
    return proc.returncode == 0


def _parse_path_list(out):
    try:
        val = ast.literal_eval(out)
    except (ValueError, SyntaxError):
        return []
    return list(val) if isinstance(val, list) else []


def is_bound():
    rc, out = _gsettings("get", SCHEMA, "custom-keybindings")
    if rc != 0 or CUSTOM_PATH not in out:
        return False
    rc, binding = _gsettings(
        "get", CUSTOM_SCHEMA + ":" + CUSTOM_PATH, "binding")
    return BINDING in binding


def bind():
    rc, out = _gsettings("get", SCHEMA, "custom-keybindings")
    if rc != 0:
        return False, "gsettings unavailable"
    paths = _parse_path_list(out)
    if CUSTOM_PATH not in paths:
        paths.append(CUSTOM_PATH)
        rc, _ = _gsettings("set", SCHEMA, "custom-keybindings", str(paths))
        if rc != 0:
            return False, "could not register shortcut path"
    for key, value in (("name", "'Omen Control Centre'"),
                       ("command", "'%s'" % COMMAND),
                       ("binding", "'%s'" % BINDING)):
        rc, _ = _gsettings("set", CUSTOM_SCHEMA + ":" + CUSTOM_PATH, key, value)
        if rc != 0:
            return False, "could not set %s" % key
    return True, ""


def unbind():
    rc, out = _gsettings("get", SCHEMA, "custom-keybindings")
    if rc != 0:
        return False, "gsettings unavailable"
    paths = _parse_path_list(out)
    if CUSTOM_PATH in paths:
        paths.remove(CUSTOM_PATH)
        _gsettings("set", SCHEMA, "custom-keybindings", str(paths))
    for key in ("name", "command", "binding"):
        _gsettings("reset", CUSTOM_SCHEMA + ":" + CUSTOM_PATH, key)
    return True, ""
