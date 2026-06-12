"""contacts.* — Contacts.app via AppleScript. Also hosts handle resolution for
messages/calls (name -> phone/email, phone preferred)."""

from __future__ import annotations

import re

from . import _util
from ._util import ToolError, as_quote

MAX_RESULTS = 20

_PHONE = re.compile(r"^\+?[\d\s().\-]{5,}$")

_PERSON_ROW = (
    '    set e to ""\n'
    "    try\n"
    "      set e to value of first email of p\n"
    "    end try\n"
    '    set ph to ""\n'
    "    try\n"
    "      set ph to value of first phone of p\n"
    "    end try\n"
    '    set out to out & (id of p) & "\\t" & (name of p) & "\\t" & e & "\\t" & ph & "\\n"\n'
)


def _rows_to_people(out: str) -> list[dict]:
    people = []
    for row in out.splitlines():
        p = row.split("\t")
        if len(p) >= 4:
            people.append({"id": p[0], "name": p[1], "email": p[2], "phone": p[3]})
    return people


def _person_ref(ident: str) -> str:
    q = as_quote(ident)
    return f"(every person whose id is {q} or name contains {q})"


def looks_like_handle(s: str) -> bool:
    return "@" in s or bool(_PHONE.match(s.strip()))


def resolve_handle(recipient: str) -> str:
    """Resolve a contact name to a phone (preferred) or email via Contacts.app.

    Strings that already look like a phone number or email pass through untouched.
    """
    r = str(recipient or "").strip()
    if not r:
        raise ToolError("a recipient is required")
    if looks_like_handle(r):
        return r
    script = (
        'tell application "Contacts"\n'
        f"  set ps to (every person whose name contains {as_quote(r)})\n"
        '  if (count of ps) = 0 then return ""\n'
        "  set p to item 1 of ps\n"
        "  try\n"
        "    return value of first phone of p\n"
        "  end try\n"
        "  try\n"
        "    return value of first email of p\n"
        "  end try\n"
        '  return ""\n'
        "end tell"
    )
    handle = _util.run_osascript(script, timeout=30)
    if not handle:
        raise ToolError(f"No contact named {r!r} with a phone number or email")
    return handle


def find(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("a name to search for is required")
    script = (
        'set out to ""\n'
        'tell application "Contacts"\n'
        f"  set ps to (every person whose name contains {as_quote(name)})\n"
        "  set n to count of ps\n"
        f"  if n > {MAX_RESULTS} then set n to {MAX_RESULTS}\n"
        "  repeat with i from 1 to n\n"
        "    set p to item i of ps\n"
        f"{_PERSON_ROW}"
        "  end repeat\n"
        "end tell\n"
        "return out"
    )
    return {"contacts": _rows_to_people(_util.run_osascript(script, timeout=30))}


def get_details(args: dict) -> dict:
    ident = str(args.get("contact_id") or "").strip()
    if not ident:
        raise ToolError("contact_id (id or name) is required")
    script = (
        'set out to ""\n'
        'tell application "Contacts"\n'
        f"  set ps to {_person_ref(ident)}\n"
        '  if (count of ps) = 0 then return ""\n'
        "  set p to item 1 of ps\n"
        f"{_PERSON_ROW}"
        "end tell\n"
        "return out"
    )
    people = _rows_to_people(_util.run_osascript(script, timeout=30))
    if not people:
        raise ToolError(f"contact not found: {ident}")
    return {"contact": people[0]}


def list_groups(args: dict) -> dict:
    out = _util.run_osascript(
        'tell application "Contacts" to return name of every group', timeout=30)
    return {"groups": [g.strip() for g in out.split(",") if g.strip()]}


def create(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("a full name is required")
    first, _, last = name.partition(" ")
    props = [f"first name:{as_quote(first)}"]
    if last:
        props.append(f"last name:{as_quote(last)}")
    extras = []
    if args.get("email"):
        extras.append("  make new email at end of emails of p with properties "
                      f'{{label:"home", value:{as_quote(str(args["email"]))}}}\n')
    if args.get("phone"):
        extras.append("  make new phone at end of phones of p with properties "
                      f'{{label:"mobile", value:{as_quote(str(args["phone"]))}}}\n')
    script = (
        'tell application "Contacts"\n'
        f"  set p to make new person with properties {{{', '.join(props)}}}\n"
        f"{''.join(extras)}"
        "  save\n"
        "  return id of p\n"
        "end tell"
    )
    cid = _util.run_osascript(script, timeout=30)
    return {"contact_id": cid, "name": name}


def update(args: dict) -> dict:
    ident = str(args.get("contact_id") or "").strip()
    if not ident:
        raise ToolError("contact_id is required")
    body = []
    if args.get("name"):
        first, _, last = str(args["name"]).partition(" ")
        body.append(f"  set first name of p to {as_quote(first)}\n")
        if last:
            body.append(f"  set last name of p to {as_quote(last)}\n")
    if args.get("email"):
        body.append("  make new email at end of emails of p with properties "
                    f'{{label:"home", value:{as_quote(str(args["email"]))}}}\n')
    if args.get("phone"):
        body.append("  make new phone at end of phones of p with properties "
                    f'{{label:"mobile", value:{as_quote(str(args["phone"]))}}}\n')
    if not body:
        raise ToolError("nothing to update — pass name, email, or phone")
    script = (
        'tell application "Contacts"\n'
        f"  set ps to {_person_ref(ident)}\n"
        '  if (count of ps) = 0 then return ""\n'
        "  set p to item 1 of ps\n"
        f"{''.join(body)}"
        "  save\n"
        '  return "done"\n'
        "end tell"
    )
    if not _util.run_osascript(script, timeout=30):
        raise ToolError(f"contact not found: {ident}")
    return {"contact_id": ident, "updated": True}


def delete(args: dict) -> dict:
    ident = str(args.get("contact_id") or "").strip()
    if not ident:
        raise ToolError("contact_id is required")
    script = (
        'tell application "Contacts"\n'
        f"  set ps to {_person_ref(ident)}\n"
        '  if (count of ps) = 0 then return ""\n'
        "  delete item 1 of ps\n"
        "  save\n"
        '  return "done"\n'
        "end tell"
    )
    if not _util.run_osascript(script, timeout=30):
        raise ToolError(f"contact not found: {ident}")
    return {"deleted": ident}


HANDLERS = {
    "contacts.find": find,
    "contacts.get_details": get_details,
    "contacts.list_groups": list_groups,
    "contacts.create": create,
    "contacts.update": update,
    "contacts.delete": delete,
}
