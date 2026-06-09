"""Deterministic, state-backed mock of the tool backend.

Nothing real happens. A fixed seed → reproducible results, so multi-turn chains stay
consistent ("am I free at 3?" agrees with the seeded calendar). We grade the *call*
(name + args), not side effects — but mutations are reflected in state so later turns
in a chain see a consistent world.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..types import ToolCall, ToolSchema


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
            ],
            events=[
                {"id": "evt_standup", "title": "Team standup", "start": "10:00", "end": "10:30"},
                {"id": "evt_2pm", "title": "Sync with Alex", "start": "14:00", "end": "15:00"},
                {"id": "evt_gym", "title": "Gym", "start": "17:00", "end": "18:00"},
            ],
            inbox=[
                {"id": "m_001", "from": "alex@example.com", "subject": "Q3 budget review",
                 "unread": True},
                {"id": "m_002", "from": "daisy@example.com", "subject": "Lunch tomorrow?",
                 "unread": True},
                {"id": "m_003", "from": "newsletter@news.example.com", "subject": "Weekly digest",
                 "unread": False},
            ],
            files=[
                "~/Documents/Q3_budget.xlsx",
                "~/Documents/notes/standup.md",
                "~/Downloads/invoice_0425.pdf",
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
            "mail.list_unread": self._mail_unread,
            "mail.send": self._mail_send,
            "files.search": self._files_search,
            "files.list_dir": self._files_list,
            "system.get_battery": self._sys_battery,
            "system.set_volume": self._sys_set("volume"),
            "system.set_brightness": self._sys_set("brightness"),
            "system.toggle_dnd": self._sys_toggle("dnd"),
            "system.toggle_dark_mode": self._sys_toggle("dark_mode"),
            "music.now_playing": self._music_now,
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
            return json.dumps(
                {"status": "ok", "tool": call.name, "result": [], "note": "simulated"}
            )
        return _ok(tool=call.name, applied=call.arguments)

    # ---- calendar ----
    def _cal_list(self, args: dict) -> str:
        q = (args.get("query") or "").lower()
        evs = [e for e in self.state.events if q in e["title"].lower()] if q else self.state.events
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
        return _ok(sent_to=args.get("recipient"), body=args.get("body"))

    # ---- mail ----
    def _mail_search(self, args: dict) -> str:
        q = (args.get("query") or "").lower()
        hits = [m for m in self.state.inbox if q in m["subject"].lower() or q in m["from"].lower()]
        return json.dumps({"status": "ok", "messages": hits})

    def _mail_unread(self, args: dict) -> str:
        return json.dumps({"status": "ok",
                           "messages": [m for m in self.state.inbox if m["unread"]]})

    def _mail_send(self, args: dict) -> str:
        return _ok(sent_to=args.get("to"), subject=args.get("subject"))

    # ---- files ----
    def _files_search(self, args: dict) -> str:
        q = (args.get("query") or "").lower()
        hits = [f for f in self.state.files if q in f.lower()]
        return json.dumps({"status": "ok", "files": hits})

    def _files_list(self, args: dict) -> str:
        return json.dumps({"status": "ok", "files": self.state.files})

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
