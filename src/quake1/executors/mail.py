"""mail.* — Mail.app via AppleScript. Message ids are Mail's integer `id` property,
as returned by mail.search / mail.list_unread."""

from __future__ import annotations

from . import _util
from ._util import ToolError, as_quote

MAX_RESULTS = 20


def _msg_id(args: dict) -> int:
    raw = args.get("message_id")
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as e:
        raise ToolError(
            f"message_id must be an integer id from mail.search, got {raw!r}") from e


def _mailbox(folder: str | None) -> str:
    if not folder or str(folder).strip().lower() == "inbox":
        return "inbox"
    return f"mailbox {as_quote(str(folder))}"


def _msg_ref(args: dict, folder: str | None = None) -> str:
    return f"(first message of {_mailbox(folder)} whose id is {_msg_id(args)})"


def _rows_to_messages(out: str) -> list[dict]:
    msgs = []
    for row in out.splitlines():
        p = row.split("\t")
        if len(p) >= 4:
            msgs.append({"id": p[0], "subject": p[1], "sender": p[2], "read": p[3] == "true"})
    return msgs


def _listing_script(source: str, where: str, cap: int) -> str:
    return (
        'set out to ""\n'
        'tell application "Mail"\n'
        f"  set ms to (messages of {source}{where})\n"
        "  set n to count of ms\n"
        f"  if n > {cap} then set n to {cap}\n"
        "  repeat with i from 1 to n\n"
        "    set m to item i of ms\n"
        '    set out to out & (id of m) & "\\t" & (subject of m) & "\\t" '
        '& (sender of m) & "\\t" & (read status of m) & "\\n"\n'
        "  end repeat\n"
        "end tell\n"
        "return out"
    )


def search(args: dict) -> dict:
    q = str(args.get("query") or "").strip()
    if not q:
        raise ToolError("a search query is required")
    qq = as_quote(q)
    where = f" whose subject contains {qq} or sender contains {qq}"
    script = _listing_script(_mailbox(args.get("folder")), where, MAX_RESULTS)
    return {"messages": _rows_to_messages(_util.run_osascript(script, timeout=30))}


def read_message(args: dict) -> dict:
    script = (
        'tell application "Mail"\n'
        f"  set m to {_msg_ref(args)}\n"
        '  return (subject of m) & "\\n" & (sender of m) & "\\n" & (content of m)\n'
        "end tell"
    )
    out = _util.run_osascript(script, timeout=30)
    subject, _, rest = out.partition("\n")
    sender, _, content = rest.partition("\n")
    return {"id": _msg_id(args), "subject": subject, "sender": sender,
            "content": content[:8000]}


def list_unread(args: dict) -> dict:
    limit = min(int(args.get("limit") or 10), MAX_RESULTS)
    script = _listing_script(
        _mailbox(args.get("folder")), " whose read status is false", limit)
    return {"messages": _rows_to_messages(_util.run_osascript(script, timeout=30))}


def list_folders(args: dict) -> dict:
    out = _util.run_osascript(
        'tell application "Mail" to return name of every mailbox', timeout=30)
    return {"folders": [f.strip() for f in out.split(",") if f.strip()]}


def _recipients(args: dict, required: bool) -> list[str]:
    to = args.get("to")
    if isinstance(to, str):
        to = [to]
    to = [str(a).strip() for a in (to or []) if str(a).strip()]
    if required and not to:
        raise ToolError("at least one recipient address is required")
    return to


def _compose_script(args: dict, final: str) -> str:
    to = _recipients(args, required=final.startswith("send"))
    subject = as_quote(str(args.get("subject") or ""))
    body = as_quote(str(args.get("body") or ""))
    rcpts = "".join(
        "    make new to recipient at end of to recipients "
        f"with properties {{address:{as_quote(a)}}}\n" for a in to)
    return (
        'tell application "Mail"\n'
        "  set m to make new outgoing message with properties "
        f"{{subject:{subject}, content:{body}, visible:false}}\n"
        "  tell m\n"
        f"{rcpts}"
        "  end tell\n"
        f"  {final}\n"
        "end tell"
    )


def send(args: dict) -> dict:
    _util.run_osascript(_compose_script(args, "send m"), timeout=30)
    return {"sent_to": _recipients(args, required=True),
            "subject": str(args.get("subject") or "")}


def create_draft(args: dict) -> dict:
    _util.run_osascript(_compose_script(args, "save m"), timeout=30)
    return {"draft": True, "to": _recipients(args, required=False),
            "subject": str(args.get("subject") or "")}


def reply(args: dict) -> dict:
    body = str(args.get("body") or "").strip()
    if not body:
        raise ToolError("a reply body is required")
    script = (
        'tell application "Mail"\n'
        f"  set m to {_msg_ref(args)}\n"
        "  set r to reply m without opening window\n"
        f"  set content of r to {as_quote(body)}\n"
        "  send r\n"
        "end tell"
    )
    _util.run_osascript(script, timeout=30)
    return {"replied_to": _msg_id(args)}


def forward(args: dict) -> dict:
    to = _recipients(args, required=True)
    note = str(args.get("body") or "").strip()
    rcpts = "".join(
        "    make new to recipient at end of to recipients "
        f"with properties {{address:{as_quote(a)}}}\n" for a in to)
    set_note = (f"  set content of f to ({as_quote(note)} & return & (content of f))\n"
                if note else "")
    script = (
        'tell application "Mail"\n'
        f"  set m to {_msg_ref(args)}\n"
        "  set f to forward m without opening window\n"
        "  tell f\n"
        f"{rcpts}"
        "  end tell\n"
        f"{set_note}"
        "  send f\n"
        "end tell"
    )
    _util.run_osascript(script, timeout=30)
    return {"forwarded": _msg_id(args), "to": to}


def mark_read(args: dict) -> dict:
    read = args.get("read", True)
    flag = "true" if read in (None, True, "true", "True", 1) else "false"
    _util.run_osascript(
        f'tell application "Mail" to set read status of {_msg_ref(args)} to {flag}',
        timeout=30)
    return {"message_id": _msg_id(args), "read": flag == "true"}


def move_to_folder(args: dict) -> dict:
    folder = str(args.get("folder") or "").strip()
    if not folder:
        raise ToolError("a destination folder is required")
    script = (
        'tell application "Mail" to set mailbox of '
        f"{_msg_ref(args)} to mailbox {as_quote(folder)}"
    )
    _util.run_osascript(script, timeout=30)
    return {"message_id": _msg_id(args), "moved_to": folder}


def delete(args: dict) -> dict:
    _util.run_osascript(
        f'tell application "Mail" to delete {_msg_ref(args)}', timeout=30)
    return {"deleted": _msg_id(args)}


HANDLERS = {
    "mail.search": search,
    "mail.read_message": read_message,
    "mail.list_unread": list_unread,
    "mail.list_folders": list_folders,
    "mail.send": send,
    "mail.create_draft": create_draft,
    "mail.reply": reply,
    "mail.forward": forward,
    "mail.mark_read": mark_read,
    "mail.move_to_folder": move_to_folder,
    "mail.delete": delete,
}
