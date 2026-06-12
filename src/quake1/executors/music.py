"""music.* — Music.app via AppleScript. Podcasts.app is not scriptable (honest stub)."""

from __future__ import annotations

from . import _util
from ._util import ToolError, as_quote, unsupported

MAX_RESULTS = 10


def now_playing(args: dict) -> dict:
    script = (
        'tell application "Music"\n'
        "  if player state is playing then\n"
        '    return (name of current track) & "\\t" & (artist of current track)\n'
        "  end if\n"
        "end tell\n"
        'return ""'
    )
    out = _util.run_osascript(script, timeout=30)
    if "\t" not in out:
        return {"playing": False}
    name, _, artist = out.partition("\t")
    return {"playing": True, "track": name, "artist": artist}


def search(args: dict) -> dict:
    q = str(args.get("query") or "").strip()
    if not q:
        raise ToolError("a search query is required")
    script = (
        'set out to ""\n'
        'tell application "Music"\n'
        f"  set ts to (tracks of library playlist 1 whose name contains {as_quote(q)})\n"
        "  set n to count of ts\n"
        f"  if n > {MAX_RESULTS} then set n to {MAX_RESULTS}\n"
        "  repeat with i from 1 to n\n"
        "    set t to item i of ts\n"
        '    set out to out & (name of t) & "\\t" & (artist of t) & "\\n"\n'
        "  end repeat\n"
        "end tell\n"
        "return out"
    )
    tracks = []
    for row in _util.run_osascript(script, timeout=30).splitlines():
        if "\t" in row:
            name, _, artist = row.partition("\t")
            tracks.append({"track": name, "artist": artist})
    return {"tracks": tracks}


def play(args: dict) -> dict:
    q = str(args.get("query") or "").strip()
    if q:
        script = ('tell application "Music" to play '
                  f"(first track of library playlist 1 whose name contains {as_quote(q)})")
        _util.run_osascript(script, timeout=30)
        return {"playing": q}
    _util.run_osascript('tell application "Music" to play', timeout=30)
    return {"playing": True}


def pause(args: dict) -> dict:
    _util.run_osascript('tell application "Music" to pause', timeout=30)
    return {"paused": True}


def next_track(args: dict) -> dict:
    _util.run_osascript('tell application "Music" to next track', timeout=30)
    return {"skipped": "next"}


def previous_track(args: dict) -> dict:
    _util.run_osascript('tell application "Music" to previous track', timeout=30)
    return {"skipped": "previous"}


def play_playlist(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("a playlist name is required")
    _util.run_osascript(
        f'tell application "Music" to play playlist {as_quote(name)}', timeout=30)
    return {"playing_playlist": name}


def set_volume(args: dict) -> dict:
    try:
        level = int(args["level"])
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError("an integer level (0-100) is required") from e
    level = max(0, min(100, level))
    _util.run_osascript(
        f'tell application "Music" to set sound volume to {level}', timeout=30)
    return {"volume": level}


HANDLERS = {
    "music.now_playing": now_playing,
    "music.search": search,
    "music.play": play,
    "music.pause": pause,
    "music.next_track": next_track,
    "music.previous_track": previous_track,
    "music.play_playlist": play_playlist,
    "music.play_podcast": unsupported(
        "Podcasts.app has no AppleScript dictionary or search-and-play URL scheme"),
    "music.set_volume": set_volume,
}
