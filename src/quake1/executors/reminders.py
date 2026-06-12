"""reminders.* — Reminders.app via AppleScript. Items referenced by id or name."""

from __future__ import annotations

from . import _util
from ._util import ToolError, as_quote
from .calendar import _as_date, _parse_when

MAX_RESULTS = 50


def _ref(args: dict) -> str:
    rid = str(args.get("reminder_id") or "").strip()
    if not rid:
        raise ToolError("reminder_id (the reminder's id or name) is required")
    q = as_quote(rid)
    return f"(every reminder whose id is {q} or name is {q})"


def _find_block(args: dict, body: str) -> str:
    return (
        'tell application "Reminders"\n'
        f"  set hits to {_ref(args)}\n"
        '  if (count of hits) = 0 then return ""\n'
        "  set r to item 1 of hits\n"
        f"  {body}\n"
        '  return "done"\n'
        "end tell"
    )


def _run_find(args: dict, body: str) -> None:
    if not _util.run_osascript(_find_block(args, body), timeout=30):
        raise ToolError(f"reminder not found: {args.get('reminder_id')}")


def list_(args: dict) -> dict:
    list_name = args.get("list_name")
    source = f"reminders of list {as_quote(str(list_name))}" if list_name else "reminders"
    where = "" if args.get("include_completed") else " whose completed is false"
    script = (
        'set out to ""\n'
        'tell application "Reminders"\n'
        f"  set rs to ({source}{where})\n"
        "  set n to count of rs\n"
        f"  if n > {MAX_RESULTS} then set n to {MAX_RESULTS}\n"
        "  repeat with i from 1 to n\n"
        "    set r to item i of rs\n"
        '    set d to ""\n'
        "    try\n"
        "      set d to (due date of r) as string\n"
        "    end try\n"
        '    set out to out & (id of r) & "\\t" & (name of r) & "\\t" '
        '& (completed of r) & "\\t" & d & "\\n"\n'
        "  end repeat\n"
        "end tell\n"
        "return out"
    )
    rows = [r for r in _util.run_osascript(script, timeout=30).splitlines() if "\t" in r]
    items = []
    for row in rows:
        p = row.split("\t")
        if len(p) >= 4:
            items.append({"id": p[0], "text": p[1], "completed": p[2] == "true", "due": p[3]})
    return {"reminders": items}


def list_lists(args: dict) -> dict:
    out = _util.run_osascript(
        'tell application "Reminders" to return name of every list', timeout=30)
    return {"lists": [x.strip() for x in out.split(",") if x.strip()]}


def create(args: dict) -> dict:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ToolError("reminder text is required")
    props = [f"name:{as_quote(text)}"]
    due_note = None
    if args.get("due"):
        try:
            props.append(f"due date:{_as_date(_parse_when(str(args['due'])))}")
        except ToolError:
            due_note = f"couldn't parse due date {args['due']!r}; created without one"
    target = (f"list {as_quote(str(args['list_name']))}" if args.get("list_name")
              else "default list")
    script = (
        f'tell application "Reminders" to tell {target}\n'
        f"  set r to make new reminder with properties {{{', '.join(props)}}}\n"
        "  return id of r\n"
        "end tell"
    )
    rid = _util.run_osascript(script, timeout=30)
    out = {"reminder_id": rid, "text": text}
    if due_note:
        out["note"] = due_note
    return out


def complete(args: dict) -> dict:
    _run_find(args, "set completed of r to true")
    return {"completed": args["reminder_id"]}


def update(args: dict) -> dict:
    sets = []
    if args.get("text"):
        sets.append(f"set name of r to {as_quote(str(args['text']))}")
    if args.get("due"):
        sets.append(f"set due date of r to {_as_date(_parse_when(str(args['due'])))}")
    if not sets:
        raise ToolError("nothing to update — pass text or due")
    _run_find(args, "\n  ".join(sets))
    return {"reminder_id": args["reminder_id"], "updated": True}


def delete(args: dict) -> dict:
    _run_find(args, "delete r")
    return {"deleted": args["reminder_id"]}


HANDLERS = {
    "reminders.list": list_,
    "reminders.list_lists": list_lists,
    "reminders.create": create,
    "reminders.complete": complete,
    "reminders.update": update,
    "reminders.delete": delete,
}
