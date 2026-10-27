"""Game library scanner: lists installed Steam games, launches via steam://.

Read-only. Mock root via OMEN_STEAM_ROOT for tests.
"""
import os
import re

STEAM_ROOT = os.environ.get(
    "OMEN_STEAM_ROOT", os.path.expanduser("~/.steam/steam"))


def _parse_vdf_blocks(text, key):
    """Return list of {attr: value} dicts for named blocks in a VDF-ish file."""
    blocks = []
    for m in re.finditer(
            r'"%s"\s*\{([^}]*)\}' % re.escape(key), text, re.S):
        body = m.group(1)
        attrs = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', body))
        blocks.append(attrs)
    return blocks


def library_folders():
    path = os.path.join(STEAM_ROOT, "steamapps", "libraryfolders.vdf")
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return [os.path.join(STEAM_ROOT, "steamapps")]
    folders = []
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        folders.append(os.path.join(m.group(1).replace("\\\\", "\\"),
                                    "steamapps"))
    return folders or [os.path.join(STEAM_ROOT, "steamapps")]


def list_games():
    """Return [{'appid': str, 'name': str, 'installed': True}]."""
    games = []
    for folder in library_folders():
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        for entry in sorted(entries):
            if not (entry.startswith("appmanifest_") and
                    entry.endswith(".acf")):
                continue
            try:
                with open(os.path.join(folder, entry),
                          errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            blocks = _parse_vdf_blocks(text, "AppState")
            if not blocks:
                continue
            appid = blocks[0].get("appid", entry[12:-4])
            name = blocks[0].get("name", "Unknown")
            state = int(blocks[0].get("StateFlags", "4") or "4")
            # StateFlags bit 2 (4) = fully installed
            if state & 4:
                games.append({"appid": appid, "name": name})
    games.sort(key=lambda g: g["name"].lower())
    return games


def launch_command(appid):
    return ["xdg-open", "steam://rungameid/%s" % appid]
