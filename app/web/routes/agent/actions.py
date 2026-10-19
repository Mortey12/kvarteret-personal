"""Catalog of the admin web actions Skonk may propose, built from the live routes.

Parity by construction: the agent proposes calls to the same routes the admin
UI uses, and they run through those routes with the admin's own session, so
authorization, validation, audit logging and cache invalidation all apply.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.dependencies import require_admin_user, require_management_user

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}
_ADMIN_GUARDS = {require_admin_user, require_management_user}
# Session takeover, file uploads, self-service and the agent itself stay manual.
_EXCLUDED = re.compile(r"/impersonat|/photo$|^/agent|^/my-account|^/api/|^/spotify/login")
# Routes that read the raw form carry fields FastAPI cannot see.
_EXTRA_FIELDS = {
    ("PATCH", "/volunteers/{volunteer_id}/relations"): [
        ("card_number", "str", False),
        ("next_of_kin_name", "str", False),
        ("next_of_kin_phone", "str", False),
    ],
}


def _guards(dependant) -> set:
    found = {dependant.call}
    for sub in dependant.dependencies:
        found |= _guards(sub)
    return found


def _type_name(annotation: Any) -> str:
    text = str(annotation).replace("typing.", "").replace("<class '", "").replace("'>", "")
    return text[:30]


class AdminActionCatalog:
    def __init__(self, app: FastAPI) -> None:
        self.routes: list[APIRoute] = [
            r
            for r in app.routes
            if isinstance(r, APIRoute)
            and r.methods & _WRITE
            and _guards(r.dependant) & _ADMIN_GUARDS
            and not _EXCLUDED.search(r.path)
        ]

    def _fields(self, route: APIRoute, method: str) -> list[dict]:
        fields = [
            {"name": b.name, "type": _type_name(b.field_info.annotation),
             "required": b.field_info.is_required()}
            for b in route.dependant.body_params
        ]
        for name, kind, required in _EXTRA_FIELDS.get((method, route.path), []):
            fields.append({"name": name, "type": f"list[{kind}]", "required": required})
        return fields

    def list(self, keyword: str | None) -> list[dict]:
        out = []
        for route in self.routes:
            for method in sorted(route.methods & _WRITE):
                entry = {
                    "method": method,
                    "path": route.path,
                    "action": route.name,
                    "fields": self._fields(route, method),
                    "destructive": method == "DELETE",
                }
                if keyword is None or keyword.lower() in f"{route.path} {route.name}".lower():
                    out.append(entry)
        return out

    def resolve(self, method: str, path: str, form: dict[str, list[str]]) -> dict:
        for route in self.routes:
            if method in route.methods and route.path_regex.match(path):
                break
        else:
            return {"error": "No such admin action; use list_admin_actions."}
        fields = {f["name"]: f for f in self._fields(route, method)}
        unknown = set(form) - set(fields)
        missing = [n for n, f in fields.items() if f["required"] and n not in form]
        if unknown or missing:
            return {"error": f"unknown fields {sorted(unknown)}, missing required {missing}"}
        return {
            "method": method,
            "path": path,
            "title": f"{route.name.replace('_', ' ')}: {method} {path}",
            "danger": method == "DELETE",
        }
