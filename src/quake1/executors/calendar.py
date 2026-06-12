"""calendar.* — Calendar.app via AppleScript.

Dates: the catalog's tasks use loose strings ("today", "2pm", "16:00", ISO). _parse_when
normalizes to a datetime; AppleScript gets an unambiguous `date "..."` literal built from
month-name formatting (locale-safe enough for v1; flagged as a known risk).
"""

from __future__ import annotations

import datetime as dt
import json
import re

from ._util import ToolError, as_quote, run_osascript

_TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", re.I)


def _parse_when(s: str | None, default_hour: int = 9) -> dt.datetime:
    now = dt.datetime.now()
    if not s:
        return now.replace(hour=default_hour, minute=0, second=0, microsecond=0)
    s = str(s).strip()
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        pass
    day = now
    low = s.lower()
    for word, delta in (("today", 0), ("tomorrow", 1)):
        if word in low:
            day = now + dt.timedelta(days=delta)
            low = low.replace(word, "").strip(" ,@at")
    m = _TIME.match(low.strip())
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = (m.group(3) or "").lower()
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    raise ToolError(f"Couldn't parse the date/time {s!r} — try '2pm', '16:00', or ISO format")


def _as_date(d: dt.datetime) -> str:
    # month-name literal avoids DD/MM vs MM/DD ambiguity in AppleScript date parsing
    return f'date "{d.strftime("%B %d, %Y %H:%M")}"'


def _events_window(day: dt.datetime) -> tuple[str, str]:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)
    return _as_date(start), _as_date(end)


def list_events(args: dict) -> dict:
    day = _parse_when(args.get("date") or "today", default_hour=0)
    lo, hi = _events_window(day)
    script = (
        'set out to ""\n'
        'tell application "Calendar"\n'
        "  repeat with c in calendars\n"
        f"    repeat with e in (every event of c whose start date ≥ {lo} "
        f"and start date < {hi})\n"
        '      set out to out & (uid of e) & "\\t" & (summary of e) & "\\t" '
        '& ((start date of e) as string) & "\\t" & ((end date of e) as string) & "\\n"\n'
        "    end repeat\n"
        "  end repeat\n"
        "end tell\n"
        "return out"
    )
    rows = [r for r in run_osascript(script, timeout=30).splitlines() if r.strip()]
    events = []
    query = str(args.get("query") or "").lower()
    for r in rows:
        parts = r.split("\t")
        if len(parts) >= 4:
            ev = {"id": parts[0], "title": parts[1], "start": parts[2], "end": parts[3]}
            if not query or any(t in ev["title"].lower() for t in query.split()):
                events.append(ev)
    return {"events": events}


def get_event(args: dict) -> dict:
    eid = args.get("event_id")
    if not eid:
        raise ToolError("event_id is required")
    script = (
        'tell application "Calendar"\n'
        "  repeat with c in calendars\n"
        f"    set hits to (every event of c whose uid is {as_quote(str(eid))})\n"
        "    if (count of hits) > 0 then\n"
        "      set e to item 1 of hits\n"
        '      return (uid of e) & "\\t" & (summary of e) & "\\t" & '
        '((start date of e) as string) & "\\t" & ((end date of e) as string)\n'
        "    end if\n"
        "  end repeat\n"
        "end tell\n"
        'return ""'
    )
    out = run_osascript(script, timeout=30)
    if not out:
        raise ToolError(f"event not found: {eid}")
    p = out.split("\t")
    return {"event": {"id": p[0], "title": p[1], "start": p[2], "end": p[3]}}


def find_free_slots(args: dict) -> dict:
    listed = list_events({"date": args.get("date") or "today"})
    busy = [{"start": e["start"], "end": e["end"]} for e in listed["events"]]
    return {"busy": busy,
            "note": "free slots are gaps between busy blocks within working hours"}


def create_event(args: dict) -> dict:
    title = str(args.get("title") or "").strip()
    if not title:
        raise ToolError("a title is required")
    start = _parse_when(args.get("start"))
    end_arg = args.get("end")
    end = _parse_when(end_arg) if end_arg else start + dt.timedelta(hours=1)
    script = (
        'tell application "Calendar" to tell calendar 1\n'
        f"  set e to make new event with properties {{summary:{as_quote(title)}, "
        f"start date:{_as_date(start)}, end date:{_as_date(end)}}}\n"
        "  return uid of e\n"
        "end tell"
    )
    uid = run_osascript(script, timeout=30)
    return {"event_id": uid, "title": title, "start": start.isoformat()}


def create_recurring_event(args: dict) -> dict:
    base = create_event(args)
    rec = str(args.get("recurrence") or "FREQ=WEEKLY")
    if not rec.upper().startswith("FREQ="):
        freq = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY",
                "weekday": "WEEKLY;BYDAY=MO,TU,WE,TH,FR",
                "weekdays": "WEEKLY;BYDAY=MO,TU,WE,TH,FR"}.get(rec.lower(), "WEEKLY")
        rec = f"FREQ={freq}"
    script = (
        'tell application "Calendar" to tell calendar 1\n'
        f"  set e to (first event whose uid is {as_quote(base['event_id'])})\n"
        f"  set recurrence of e to {as_quote(rec)}\n"
        "end tell"
    )
    run_osascript(script, timeout=30)
    return {**base, "recurrence": rec}


def update_event(args: dict) -> dict:
    eid = args.get("event_id")
    if not eid:
        raise ToolError("event_id is required")
    sets = []
    if args.get("new_title"):
        sets.append(f"set summary of e to {as_quote(str(args['new_title']))}")
    if args.get("new_start"):
        start = _parse_when(args["new_start"])
        sets.append(f"set start date of e to {_as_date(start)}")
        if not args.get("new_end"):
            sets.append(f"set end date of e to {_as_date(start + dt.timedelta(hours=1))}")
    if args.get("new_end"):
        sets.append(f"set end date of e to {_as_date(_parse_when(args['new_end']))}")
    if not sets:
        raise ToolError("nothing to update — pass new_start, new_end, or new_title")
    body = "\n      ".join(sets)
    script = (
        'tell application "Calendar"\n'
        "  repeat with c in calendars\n"
        f"    set hits to (every event of c whose uid is {as_quote(str(eid))})\n"
        "    if (count of hits) > 0 then\n"
        "      set e to item 1 of hits\n"
        f"      {body}\n"
        '      return "updated"\n'
        "    end if\n"
        "  end repeat\n"
        "end tell\n"
        'return ""'
    )
    if not run_osascript(script, timeout=30):
        raise ToolError(f"event not found: {eid}")
    return {"event_id": eid, "updated": True}


def respond_to_invite(args: dict) -> dict:
    raise ToolError(
        "Responding to invites isn't scriptable in Calendar.app; open the event instead.",
        hint="I can open Calendar so you can respond by hand",
    )


def delete_event(args: dict) -> dict:
    eid = args.get("event_id")
    if not eid:
        raise ToolError("event_id is required")
    script = (
        'tell application "Calendar"\n'
        "  repeat with c in calendars\n"
        f"    set hits to (every event of c whose uid is {as_quote(str(eid))})\n"
        "    if (count of hits) > 0 then\n"
        "      delete item 1 of hits\n"
        '      return "deleted"\n'
        "    end if\n"
        "  end repeat\n"
        "end tell\n"
        'return ""'
    )
    if not run_osascript(script, timeout=30):
        raise ToolError(f"event not found: {eid}")
    return {"deleted": eid}


def list_calendars(args: dict) -> dict:
    out = run_osascript('tell application "Calendar" to return name of every calendar',
                        timeout=30)
    return {"calendars": [c.strip() for c in out.split(",") if c.strip()]}


HANDLERS = {
    "calendar.list_events": list_events,
    "calendar.get_event": get_event,
    "calendar.find_free_slots": find_free_slots,
    "calendar.list_calendars": list_calendars,
    "calendar.create_event": create_event,
    "calendar.create_recurring_event": create_recurring_event,
    "calendar.update_event": update_event,
    "calendar.respond_to_invite": respond_to_invite,
    "calendar.delete_event": delete_event,
}

_ = json  # reserved for future structured AppleScript output
