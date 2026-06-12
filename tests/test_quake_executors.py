"""Tests for the quake1 real-executor domains (osascript/CLI calls are monkeypatched).

Covers: exact script/argv content (incl. as_quote escaping of a shell-injection string),
ToolError on missing required args, honest unsupported stubs, full catalog coverage,
and the Executor error envelope.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

import quake1.executors._util as _util
from quake1.executor import Executor
from quake1.executors import (
    apps,
    calls,
    clipboard,
    contacts,
    devices,
    mail,
    messages,
    missing_tools,
    music,
    network,
    notes,
    reminders,
    screen,
    shell,
    web,
)
from quake1.executors._util import ToolError, as_quote
from tooleval.tools.catalog import load_catalog
from tooleval.types import ToolCall

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "tools" / "catalog.json"

MAL = 'say "hi"; rm -rf /'
MAL_Q = '"say \\"hi\\"; rm -rf /"'  # what as_quote must emit for MAL


@pytest.fixture
def osa(monkeypatch):
    """Record AppleScript sources; return rec['ret'] (default 'done')."""
    rec = {"scripts": [], "ret": "done"}

    def fake(script, timeout=_util.OSASCRIPT_TIMEOUT):
        rec["scripts"].append(script)
        return rec["ret"]

    monkeypatch.setattr(_util, "run_osascript", fake)
    return rec


@pytest.fixture
def cmd(monkeypatch):
    """Record CLI argv (+ stdin); return rec['ret'] (str or callable(argv))."""
    rec = {"calls": [], "ret": ""}

    def fake(argv, timeout=_util.CMD_TIMEOUT, input_=None):
        rec["calls"].append((list(argv), input_))
        return rec["ret"](argv) if callable(rec["ret"]) else rec["ret"]

    monkeypatch.setattr(_util, "run_cmd", fake)
    return rec


def test_as_quote_escapes_malicious_string():
    assert as_quote(MAL) == MAL_Q


# ---------------------------------------------------------------- reminders

def test_reminders_create_quotes_malicious_text(osa):
    out = reminders.create({"text": MAL})
    script = osa["scripts"][0]
    assert f"make new reminder with properties {{name:{MAL_Q}}}" in script
    assert script.startswith('tell application "Reminders" to tell default list')
    assert out["reminder_id"] == "done"


def test_reminders_complete_by_id_and_missing_arg(osa):
    reminders.complete({"reminder_id": "Buy milk"})
    assert 'whose id is "Buy milk" or name is "Buy milk"' in osa["scripts"][0]
    assert "set completed of r to true" in osa["scripts"][0]
    with pytest.raises(ToolError):
        reminders.complete({})


def test_reminders_complete_not_found(osa):
    osa["ret"] = ""
    with pytest.raises(ToolError, match="not found"):
        reminders.delete({"reminder_id": "nope"})


# ----------------------------------------------------------------- contacts

def test_contacts_find_quotes_malicious_name(osa):
    contacts.find({"name": MAL})
    assert f"every person whose name contains {MAL_Q}" in osa["scripts"][0]
    with pytest.raises(ToolError):
        contacts.find({})


def test_contacts_create_requires_name(osa):
    with pytest.raises(ToolError):
        contacts.create({})
    contacts.create({"name": "Ada Lovelace", "phone": "+1555"})
    s = osa["scripts"][0]
    assert 'first name:"Ada"' in s and 'last name:"Lovelace"' in s
    assert 'value:"+1555"' in s


def test_resolve_handle_passthrough_and_lookup(osa):
    assert contacts.resolve_handle("+1 (555) 123-4567") == "+1 (555) 123-4567"
    assert contacts.resolve_handle("ada@example.com") == "ada@example.com"
    assert osa["scripts"] == []  # no Contacts roundtrip for raw handles
    osa["ret"] = "+15551234567"
    assert contacts.resolve_handle("Ada") == "+15551234567"
    assert 'every person whose name contains "Ada"' in osa["scripts"][0]


# ----------------------------------------------------------------- messages

def test_messages_send_exact_script_with_malicious_body(osa):
    messages.send({"recipient": "+15551234567", "body": MAL})
    assert osa["scripts"] == [
        f'tell application "Messages" to send {MAL_Q} '
        'to participant "+15551234567" of (1st account whose service type = iMessage)'
    ]


def test_messages_send_requires_body():
    with pytest.raises(ToolError):
        messages.send({"recipient": "+15551234567"})


def test_messages_read_side_is_unsupported():
    for name in ("read_thread", "list_unread", "search", "mark_read", "react"):
        with pytest.raises(ToolError, match="Not supported yet"):
            messages.HANDLERS[f"messages.{name}"]({"contact": "Ada", "query": "x",
                                                   "message_id": "1", "reaction": "like"})


# --------------------------------------------------------------------- mail

def test_mail_search_quotes_query(osa):
    osa["ret"] = "7\tLunch?\tada@example.com\tfalse"
    out = mail.search({"query": MAL})
    s = osa["scripts"][0]
    assert f"whose subject contains {MAL_Q} or sender contains {MAL_Q}" in s
    assert "messages of inbox" in s
    assert out["messages"] == [
        {"id": "7", "subject": "Lunch?", "sender": "ada@example.com", "read": False}]
    with pytest.raises(ToolError):
        mail.search({})


def test_mail_read_message_requires_integer_id():
    with pytest.raises(ToolError, match="integer"):
        mail.read_message({"message_id": "DROP TABLE"})
    with pytest.raises(ToolError):
        mail.read_message({})


def test_mail_send_builds_recipients_and_requires_to(osa):
    mail.send({"to": ["a@x.com"], "subject": "Hi", "body": MAL})
    s = osa["scripts"][0]
    assert f"content:{MAL_Q}" in s
    assert 'with properties {address:"a@x.com"}' in s
    assert s.rstrip().endswith("send m\nend tell")
    with pytest.raises(ToolError):
        mail.send({"subject": "no recipients"})


def test_mail_mark_read_references_by_integer_id(osa):
    mail.mark_read({"message_id": "42", "read": False})
    assert osa["scripts"] == [
        'tell application "Mail" to set read status of '
        "(first message of inbox whose id is 42) to false"
    ]


# -------------------------------------------------------------------- music

def test_music_play_playlist_quotes_name(osa):
    music.play_playlist({"name": MAL})
    assert osa["scripts"] == [
        f'tell application "Music" to play playlist {MAL_Q}']
    with pytest.raises(ToolError):
        music.play_playlist({})


def test_music_set_volume_requires_int_level(osa):
    with pytest.raises(ToolError):
        music.set_volume({})
    music.set_volume({"level": 250})  # clamped
    assert osa["scripts"] == ['tell application "Music" to set sound volume to 100']


def test_music_play_podcast_unsupported():
    with pytest.raises(ToolError, match="Not supported yet"):
        music.HANDLERS["music.play_podcast"]({"query": "Hard Fork"})


# -------------------------------------------------------------------- notes

def test_notes_create_quotes_body(osa):
    notes.create({"title": "T", "body": MAL})
    assert osa["scripts"] == [
        'tell application "Notes" to make new note with properties '
        f'{{name:"T", body:{MAL_Q}}}']
    with pytest.raises(ToolError):
        notes.create({})


# --------------------------------------------------------------------- apps

def test_apps_open_and_quit(osa, cmd):
    apps.open_({"name": "Safari"})
    assert cmd["calls"] == [(["open", "-a", "Safari"], None)]
    apps.quit_({"name": MAL})
    assert osa["scripts"] == [f"tell application {MAL_Q} to quit"]
    with pytest.raises(ToolError):
        apps.switch_to({})


# -------------------------------------------------------------------- screen

def test_screen_screenshot_argv_and_tcc_hint(cmd, monkeypatch):
    monkeypatch.setattr(screen, "_file_exists", lambda p: True)
    out = screen.screenshot({"save_path": "/tmp/shot.png"})
    assert cmd["calls"] == [(["screencapture", "-x", "/tmp/shot.png"], None)]
    assert out == {"saved": "/tmp/shot.png"}
    monkeypatch.setattr(screen, "_file_exists", lambda p: False)
    with pytest.raises(ToolError) as ei:
        screen.screenshot({"save_path": "/tmp/shot.png"})
    assert "Screen Recording" in (ei.value.hint or "")


def test_screen_capture_region_args(cmd, monkeypatch):
    monkeypatch.setattr(screen, "_file_exists", lambda p: True)
    screen.capture_region({"x": 1, "y": 2, "width": 3, "height": 4,
                           "save_path": "/tmp/r.png"})
    assert cmd["calls"][0][0] == ["screencapture", "-x", "-R", "1,2,3,4", "/tmp/r.png"]
    with pytest.raises(ToolError):
        screen.capture_region({"x": 1, "y": 2})


def test_screen_unsupported_stubs():
    for name in ("screen.start_recording", "screen.ocr"):
        with pytest.raises(ToolError, match="Not supported yet"):
            screen.HANDLERS[name]({})


# ------------------------------------------------------------------- network

def test_network_get_ip_local_and_connect(cmd):
    cmd["ret"] = "192.168.1.5"
    assert network.get_ip({"scope": "local"}) == {"scope": "local", "ip": "192.168.1.5"}
    assert cmd["calls"][0][0] == ["ipconfig", "getifaddr", "en0"]
    cmd["ret"] = ""
    network.connect_wifi({"ssid": "HomeNet", "password": "hunter2"})
    assert cmd["calls"][1][0] == [
        "networksetup", "-setairportnetwork", "en0", "HomeNet", "hunter2"]
    with pytest.raises(ToolError):
        network.connect_wifi({})


def test_network_get_ip_public(monkeypatch):
    resp = SimpleNamespace(text="8.8.8.8\n", raise_for_status=lambda: None)
    monkeypatch.setattr(network.httpx, "get", lambda url, timeout: resp)
    assert network.get_ip({"scope": "public"}) == {"scope": "public", "ip": "8.8.8.8"}


def test_network_list_wifi_unsupported():
    with pytest.raises(ToolError, match="Not supported yet"):
        network.HANDLERS["network.list_wifi"]({})


# ----------------------------------------------------------------- clipboard

def test_clipboard_get_set_history(cmd):
    cmd["ret"] = "hello"
    assert clipboard.get({}) == {"text": "hello"}
    assert cmd["calls"][0][0] == ["pbpaste"]
    clipboard.set_({"text": MAL})
    assert cmd["calls"][1] == (["pbcopy"], MAL)  # raw stdin, never shell-interpolated
    with pytest.raises(ToolError):
        clipboard.set_({})
    with pytest.raises(ToolError, match="Not supported yet"):
        clipboard.HANDLERS["clipboard.history"]({})


# ------------------------------------------------------------------- devices

BLUEUTIL_LINE = ('address: 00-11-22-33-44-55, connected (master, -60 dBm), '
                 'name: "AirPods Pro", recent access date: today')


def test_devices_list_and_connect_by_name(cmd):
    cmd["ret"] = BLUEUTIL_LINE
    out = devices.list_bluetooth({})
    assert cmd["calls"][0][0] == ["blueutil", "--paired"]
    assert out["devices"] == [
        {"address": "00-11-22-33-44-55", "name": "AirPods Pro", "connected": True}]
    devices.connect_bluetooth({"device": "airpods"})
    assert cmd["calls"][-1][0] == ["blueutil", "--connect", "00-11-22-33-44-55"]
    with pytest.raises(ToolError):
        devices.connect_bluetooth({})


def test_devices_blueutil_missing_hint(cmd):
    def boom(argv):
        raise ToolError("`blueutil` is not installed")

    cmd["ret"] = boom
    with pytest.raises(ToolError) as ei:
        devices.list_bluetooth({})
    assert ei.value.hint == "brew install blueutil"


def test_devices_airdrop_unsupported():
    with pytest.raises(ToolError, match="Not supported yet"):
        devices.HANDLERS["devices.airdrop"]({"file_path": "/tmp/x", "recipient": "Ada"})


# --------------------------------------------------------------------- shell

def test_shell_run_command_zsh_argv(monkeypatch):
    seen = {}

    def fake_run(argv, capture_output, text, timeout):
        seen["argv"], seen["timeout"] = argv, timeout
        return SimpleNamespace(stdout="hi\n", stderr="", returncode=3)

    monkeypatch.setattr(shell.subprocess, "run", fake_run)
    out = shell.run_command({"command": "echo hi; exit 3"})
    assert seen["argv"] == ["/bin/zsh", "-c", "echo hi; exit 3"]
    assert seen["timeout"] == 30
    assert out == {"stdout": "hi\n", "stderr": "", "exit_code": 3}
    with pytest.raises(ToolError):
        shell.run_command({})


# ----------------------------------------------------------------------- web

def test_web_search_urlencodes_query(cmd):
    web.search({"query": MAL})
    expected = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(MAL)
    assert cmd["calls"] == [(["open", expected], None)]
    with pytest.raises(ToolError):
        web.search({})


def test_web_open_url_validates_scheme(cmd):
    web.open_url({"url": "https://example.com"})
    assert cmd["calls"][0][0] == ["open", "https://example.com"]
    web.open_url({"url": "example.com"})  # bare host gets https:// prepended
    assert cmd["calls"][1][0] == ["open", "https://example.com"]
    for bad in ("file:///etc/passwd", "javascript:alert(1)", ""):
        with pytest.raises(ToolError):
            web.open_url({"url": bad})


def test_web_fetch_url_strips_tags(monkeypatch):
    resp = SimpleNamespace(
        text="<html><script>evil()</script><p>Hello <b>world</b></p></html>",
        url="https://example.com/", raise_for_status=lambda: None)
    monkeypatch.setattr(
        web.httpx, "get", lambda url, timeout, follow_redirects: resp)
    out = web.fetch_url({"url": "https://example.com"})
    assert out["text"] == "Hello world"
    assert "evil" not in out["text"]


# --------------------------------------------------------------------- calls

def test_calls_facetime_and_phone(cmd, osa):
    calls.facetime({"contact": "+15551234567"})
    assert cmd["calls"][0][0] == ["open", "facetime://+15551234567"]
    osa["ret"] = "+1 (555) 123-4567"  # Contacts resolution for a name
    calls.phone({"contact": "Ada"})
    assert cmd["calls"][1][0] == ["open", "tel://+15551234567"]
    with pytest.raises(ToolError):
        calls.phone({})


# ------------------------------------------------------- coverage + envelope

def test_full_catalog_coverage():
    assert missing_tools(load_catalog(CATALOG)) == []


def test_executor_wraps_tool_error_as_json(cmd):
    ex = Executor(load_catalog(CATALOG))
    out = json.loads(ex.execute(ToolCall("clipboard.history", {})))
    assert out["status"] == "error"
    assert "Not supported yet" in out["message"]

    def boom(argv):
        raise ToolError("`blueutil` is not installed")

    cmd["ret"] = boom
    out = json.loads(ex.execute(ToolCall("devices.list_bluetooth", {})))
    assert out == {"status": "error",
                   "message": "Bluetooth device control needs the `blueutil` CLI.",
                   "hint": "brew install blueutil"}


def test_executor_passes_success_through(osa):
    ex = Executor(load_catalog(CATALOG))
    out = json.loads(ex.execute(ToolCall("notes.create", {"body": "hi"})))
    assert out["status"] == "ok"


def test_calendar_parse_when_bare_day_words():
    # regression: "today" with no time component burned 6 turns in production
    from quake1.executors.calendar import _parse_when

    today = _parse_when("today", default_hour=0)
    assert today.hour == 0 and today.date() == __import__("datetime").date.today()
    tomorrow = _parse_when("tomorrow")
    assert (tomorrow.date() - today.date()).days == 1
