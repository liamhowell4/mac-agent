"""Deterministic, state-backed mock of the tool backend.

Nothing real happens. A fixed seed → reproducible results, so multi-turn chains stay
consistent ("am I free at 3?" agrees with the seeded calendar). We grade the *call*
(name + args), not side effects — but mutations are reflected in state so later turns
in a chain see a consistent world.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..types import ToolCall, ToolSchema


def _matches(query: str, text: str) -> bool:
    """Token-OR matching, like real search. Raw substring punished models for adding a
    plausible filter (query "gym workout" missed the event titled "Gym")."""
    tokens = re.findall(r"\w+", query.lower())
    return any(t in text.lower() for t in tokens) if tokens else True


def _ok(**kw: Any) -> str:
    return json.dumps({"status": "ok", **kw})


def _err(message: str, **kw: Any) -> str:
    return json.dumps({"status": "error", "message": message, **kw})


@dataclass
class SimState:
    """Seeded fake state. Stable across runs for a given seed."""

    contacts: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    inbox: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    system: dict = field(default_factory=dict)

    @classmethod
    def seeded(cls, seed: int = 42) -> SimState:
        # `seed` is accepted for API symmetry; state is fixed (deterministic by construction).
        return cls(
            contacts=[
                {"id": "c_sarah_chen", "name": "Sarah Chen", "email": "sarah.chen@example.com",
                 "phone": "+1-555-0101"},
                {"id": "c_sarah_kim", "name": "Sarah Kim", "email": "sarah.kim@example.com",
                 "phone": "+1-555-0102"},
                {"id": "c_alex", "name": "Alex Rivera", "email": "alex@example.com",
                 "phone": "+1-555-0110"},
                {"id": "c_daisy", "name": "Daisy Wong", "email": "daisy@example.com",
                 "phone": "+1-555-0120"},
                {"id": "c_mom", "name": "Mom", "email": "mom@family.example.com",
                 "phone": "+1-555-0130"},
                # holdout-set entities (2026-06-10) — names/queries chosen to not collide
                # with any dev-task lookup ("Sarah", "Alex", "Daisy", "Mom")
                {"id": "c_marcus", "name": "Marcus Lee", "email": "marcus@example.com",
                 "phone": "+1-555-0140"},
                {"id": "c_priya", "name": "Priya Patel", "email": "priya@example.com",
                 "phone": "+1-555-0150"},
                {"id": "c_dad", "name": "Dad", "email": "dad@family.example.com",
                 "phone": "+1-555-0160"},
            ],
            events=[
                {"id": "evt_standup", "title": "Team standup", "start": "10:00", "end": "10:30"},
                {"id": "evt_2pm", "title": "Sync with Alex", "start": "14:00", "end": "15:00"},
                {"id": "evt_gym", "title": "Gym", "start": "17:00", "end": "18:00"},
            ],
            inbox=[
                {"id": "m_001", "from": "alex@example.com", "subject": "Q3 budget review",
                 "unread": True,
                 "body": "Hi — can you send over your numbers for the Q3 budget review by "
                         "Friday? Want to consolidate before the leadership sync. — Alex"},
                {"id": "m_002", "from": "daisy@example.com", "subject": "Lunch tomorrow?",
                 "unread": True,
                 "body": "Are we still on for lunch tomorrow at 12:30? The Thai place near "
                         "the office? — Daisy"},
                {"id": "m_003", "from": "newsletter@news.example.com", "subject": "Weekly digest",
                 "unread": False, "body": "This week's top stories..."},
                {"id": "m_004", "from": "marcus@example.com", "subject": "Offsite plan - June",
                 "unread": True,
                 "body": "Draft plan for the June offsite attached — does the agenda work "
                         "for your team? — Marcus"},
                {"id": "m_005", "from": "bookings@airline.example.com",
                 "subject": "Your flight confirmation", "unread": False,
                 "body": "Your flight BCN-2207 is confirmed for June 22, 09:40 departure."},
            ],
            files=[
                "~/Documents/Q3_budget.xlsx",
                "~/Documents/notes/standup.md",
                "~/Downloads/invoice_0425.pdf",
                "~/Documents/resume_2026.pdf",
                "~/Downloads/flight_itinerary.pdf",
                "~/Pictures/screenshot_mon.png",
                "~/Pictures/vacation_photos/IMG_2201.jpg",
            ],
            system={"volume": 40, "brightness": 70, "dnd": False, "dark_mode": False,
                    "wifi": True, "bluetooth": True, "focus": "off", "battery": 82,
                    "charging": False},
        )


class Simulator:
    """Dispatches a ToolCall against seeded state and returns a result string (JSON)."""

    def __init__(self, catalog: list[ToolSchema], seed: int = 42):
        self.state = SimState.seeded(seed)
        self._schema = {t.name: t for t in catalog}
        self._handlers = {
            "calendar.list_events": self._cal_list,
            "calendar.get_event": self._cal_get,
            "calendar.find_free_slots": self._cal_free,
            "calendar.create_event": self._cal_create,
            "calendar.update_event": self._cal_update,
            "calendar.delete_event": self._cal_delete,
            "contacts.find": self._contacts_find,
            "contacts.get_details": self._contacts_find,
            "messages.send": self._msg_send,
            "mail.search": self._mail_search,
            "mail.read_message": self._mail_read,
            "mail.list_unread": self._mail_unread,
            "mail.send": self._mail_send,
            "files.search": self._files_search,
            "files.list_dir": self._files_list,
            "files.recent": self._files_recent,
            "calendar.list_calendars": self._cal_calendars,
            "reminders.list": self._rem_list,
            "reminders.list_lists": self._rem_lists,
            "contacts.list_groups": self._contacts_groups,
            "clipboard.history": self._clip_history,
            "system.get_battery": self._sys_battery,
            "system.set_volume": self._sys_set("volume"),
            "system.set_brightness": self._sys_set("brightness"),
            "system.toggle_dnd": self._sys_toggle("dnd"),
            "system.toggle_dark_mode": self._sys_toggle("dark_mode"),
            "music.now_playing": self._music_now,
            "music.search": self._music_search,
        }

    def execute(self, call: ToolCall) -> str:
        if call.name not in self._schema:
            return _err(f"unknown tool: {call.name}", hint="not in offered catalog")
        handler = self._handlers.get(call.name)
        if handler is not None:
            return handler(call.arguments)
        return self._default(call)

    # ---- default fallback ----
    def _default(self, call: ToolCall) -> str:
        schema = self._schema[call.name]
        if schema.read_only:
            # An empty `result: []` here LIES ("you have no files/calendars") and sends
            # models down dead ends; say explicitly that this tool has no data instead.
            return json.dumps(
                {"status": "ok", "tool": call.name, "result": None,
                 "note": "no data available from this tool; try a more specific one"}
            )
        return _ok(tool=call.name, applied=call.arguments)

    # ---- calendar ----
    def _cal_list(self, args: dict) -> str:
        q = args.get("query") or ""
        evs = [e for e in self.state.events if _matches(q, e["title"])] if q else self.state.events
        return json.dumps({"status": "ok", "events": evs})

    def _cal_get(self, args: dict) -> str:
        e = self._find_event(args.get("event_id"))
        return json.dumps({"status": "ok", "event": e}) if e else _err("event not found")

    def _cal_free(self, args: dict) -> str:
        busy = [{"start": e["start"], "end": e["end"]} for e in self.state.events]
        return json.dumps({"status": "ok", "busy": busy,
                           "free_examples": ["11:00-12:00", "15:00-17:00", "18:00-21:00"]})

    def _cal_create(self, args: dict) -> str:
        new_id = f"evt_new_{len(self.state.events)}"
        self.state.events.append({"id": new_id, "title": args.get("title", ""),
                                  "start": args.get("start", ""), "end": args.get("end", "")})
        return _ok(event_id=new_id)

    def _cal_update(self, args: dict) -> str:
        e = self._find_event(args.get("event_id"))
        if not e:
            return _err("event not found")
        if "new_start" in args:
            e["start"] = args["new_start"]
        if "new_end" in args:
            e["end"] = args["new_end"]
        if "new_title" in args:
            e["title"] = args["new_title"]
        return _ok(event_id=e["id"], event=e)

    def _cal_delete(self, args: dict) -> str:
        e = self._find_event(args.get("event_id"))
        if not e:
            return _err("event not found")
        self.state.events.remove(e)
        return _ok(deleted=e["id"])

    def _find_event(self, event_id: Any) -> dict | None:
        return next((e for e in self.state.events if e["id"] == event_id), None)

    # ---- contacts ----
    def _contacts_find(self, args: dict) -> str:
        name = (args.get("name") or args.get("contact_id") or "").lower()
        matches = [c for c in self.state.contacts if name and name in c["name"].lower()]
        return json.dumps({"status": "ok", "matches": matches, "count": len(matches)})

    # ---- messages ----
    def _msg_send(self, args: dict) -> str:
        # Real Messages.app refuses an ambiguous recipient — so does the sim. A name
        # matching 2+ contacts errors with the candidates instead of silently "sending".
        recipient = str(args.get("recipient") or "")
        if recipient and "@" not in recipient and not any(ch.isdigit() for ch in recipient):
            matches = [c["name"] for c in self.state.contacts
                       if recipient.lower() in c["name"].lower()]
            if len(matches) > 1:
                return _err(f"multiple contacts match {recipient!r}: {', '.join(matches)} "
                            "— specify which one")
        return _ok(sent_to=args.get("recipient"), body=args.get("body"))

    # ---- mail ----
    def _mail_search(self, args: dict) -> str:
        q = args.get("query") or ""
        hits = [m for m in self.state.inbox
                if _matches(q, m["subject"]) or _matches(q, m["from"])] if q else self.state.inbox
        return json.dumps({"status": "ok", "messages": hits})

    def _mail_read(self, args: dict) -> str:
        # No handler meant "no data available" — which stalled reply/forward chains
        # mid-flight ("let me try a different approach" → spiral). Real bodies fix it.
        mid = args.get("message_id")
        m = next((x for x in self.state.inbox if x["id"] == mid), None)
        return json.dumps({"status": "ok", "message": m}) if m else _err("message not found")

    def _mail_unread(self, args: dict) -> str:
        return json.dumps({"status": "ok",
                           "messages": [m for m in self.state.inbox if m["unread"]]})

    def _mail_send(self, args: dict) -> str:
        return _ok(sent_to=args.get("to"), subject=args.get("subject"))

    # ---- files ----
    def _files_search(self, args: dict) -> str:
        q = args.get("query") or ""
        hits = [f for f in self.state.files if _matches(q, f)] if q else self.state.files
        return json.dumps({"status": "ok", "files": hits})

    def _files_list(self, args: dict) -> str:
        path = (args.get("path") or args.get("directory") or "").lower().rstrip("/")
        files = self.state.files
        if path:
            files = [f for f in files if path.split("/")[-1] in f.lower()]
        return json.dumps({"status": "ok", "files": files})

    def _files_recent(self, args: dict) -> str:
        return json.dumps({"status": "ok", "files": self.state.files})

    # ---- seeded read-only data for tools models actually reach for (empty-default
    # responses sent them down dead ends: "no calendars", "no recent files") ----
    def _cal_calendars(self, args: dict) -> str:
        return json.dumps({"status": "ok", "calendars": [
            {"id": "cal_personal", "name": "Personal"}, {"id": "cal_work", "name": "Work"},
        ]})

    def _rem_list(self, args: dict) -> str:
        return json.dumps({"status": "ok", "reminders": [], "note": "no reminders set"})

    def _rem_lists(self, args: dict) -> str:
        return json.dumps({"status": "ok", "lists": [{"id": "rl_default", "name": "Reminders"}]})

    def _contacts_groups(self, args: dict) -> str:
        return json.dumps({"status": "ok", "groups": [{"name": "All Contacts", "count": 5}]})

    def _clip_history(self, args: dict) -> str:
        return json.dumps({"status": "ok", "items": [], "note": "clipboard history empty"})

    # ---- system ----
    def _sys_battery(self, args: dict) -> str:
        return json.dumps({"status": "ok", "battery": self.state.system["battery"],
                           "charging": self.state.system["charging"]})

    def _sys_set(self, key: str):
        def handler(args: dict) -> str:
            val = args.get("level", args.get(key))
            self.state.system[key] = val
            return _ok(**{key: val})
        return handler

    def _sys_toggle(self, key: str):
        def handler(args: dict) -> str:
            val = bool(args.get("enabled", not self.state.system.get(key, False)))
            self.state.system[key] = val
            return _ok(**{key: val})
        return handler

    # ---- music ----
    def _music_now(self, args: dict) -> str:
        return json.dumps({"status": "ok", "now_playing": None, "note": "nothing playing"})

    def _music_search(self, args: dict) -> str:
        # Seeded results — the honest-default "no data" sent models into search spirals
        # (6 retries → turn limit) instead of proceeding to play.
        q = str(args.get("query") or "")
        return json.dumps({"status": "ok", "results": [
            {"type": "playlist", "name": f"{q} Mix".strip(), "id": "pl_mix"},
            {"type": "track", "name": f"Best of {q}".strip(), "artist": "Various",
             "id": "tr_001"},
        ]})
