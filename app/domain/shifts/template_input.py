"""Validation of a shift template submitted from the templates page (pure rules)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time

from app.domain.shifts.errors import ShiftPlacementError
from app.domain.shifts.models import TemplateSlot
from app.domain.shifts.planning import SLOT_KINDS

TEXT_MAX = 100
SLOT_COUNT_MAX = 20
SLOT_ROWS = 8


@dataclass(slots=True)
class TemplateInput:
    venue: str
    title: str
    weekday: int
    start_time: time
    end_time: time
    training_replaces_slot: bool
    slots: list[TemplateSlot]


def _text(form: Mapping[str, str], key: str, label: str) -> str:
    value = (form.get(key) or "").strip()
    if not value:
        raise ShiftPlacementError(f"{label} må fylles ut.")
    if len(value) > TEXT_MAX:
        raise ShiftPlacementError(f"{label} kan ha opptil {TEXT_MAX} tegn.")
    return value


def _time(form: Mapping[str, str], key: str, label: str) -> time:
    try:
        return time.fromisoformat((form.get(key) or "").strip())
    except ValueError:
        raise ShiftPlacementError(f"{label} må være et klokkeslett, for eksempel 17:00.") from None


def _flag(form: Mapping[str, str], key: str) -> bool:
    return (form.get(key) or "").strip().lower() in {"true", "on", "1"}


def parse_template_form(form: Mapping[str, str]) -> TemplateInput:
    """Read the fields of the template form; raise a Norwegian message when invalid.

    Places are submitted as ``slot_kind_N`` / ``slot_count_N`` / ``slot_samf_N`` /
    ``slot_order_N``. A row without a kind is empty and ignored.
    """
    venue = _text(form, "venue", "Sted")
    title = _text(form, "title", "Tittel")
    try:
        weekday = int(form.get("weekday", ""))
    except ValueError:
        weekday = -1
    if not 0 <= weekday <= 6:
        raise ShiftPlacementError("Velg ukedag.")
    start = _time(form, "start_time", "Starttid")
    end = _time(form, "end_time", "Sluttid")
    if start == end:
        raise ShiftPlacementError("Start og slutt kan ikke være samme klokkeslett.")
    slots: list[TemplateSlot] = []
    for index in range(SLOT_ROWS):
        kind = (form.get(f"slot_kind_{index}") or "").strip()
        if not kind:
            continue
        if kind not in SLOT_KINDS:
            raise ShiftPlacementError(f"Ukjent type plass: {kind}.")
        try:
            count = int(form.get(f"slot_count_{index}") or "1")
        except ValueError:
            count = 0
        if not 1 <= count <= SLOT_COUNT_MAX:
            raise ShiftPlacementError(f"Antall plasser må være mellom 1 og {SLOT_COUNT_MAX}.")
        try:
            order = int(form.get(f"slot_order_{index}") or index)
        except ValueError:
            raise ShiftPlacementError("Rekkefølge må være et tall.") from None
        slots.append(TemplateSlot(kind, count, _flag(form, f"slot_samf_{index}"), order))
    if not slots:
        raise ShiftPlacementError("Legg til minst én plass.")
    slots.sort(key=lambda slot: slot.sort_order)
    return TemplateInput(
        venue=venue,
        title=title,
        weekday=weekday,
        start_time=start,
        end_time=end,
        training_replaces_slot=_flag(form, "training_replaces_slot"),
        slots=slots,
    )
