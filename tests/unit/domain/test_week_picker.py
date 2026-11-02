"""Pure rules of the week page's picker and person-first state (L2a). No database."""

from __future__ import annotations

from datetime import date

from app.domain.shifts.planning import EligibilityContext, group_rules, person_eligibility
from app.domain.shifts.week_picker import (
    ALREADY_ON_SHIFT_REASON,
    CLOSED_REASON,
    Candidate,
    PickerRow,
    PlaceFacts,
    Warn,
    build_place_state,
    evaluate,
    live_message,
    picker_rows,
    sort_key,
    warnings_for,
)

PARTICIPATING = frozenset({7, 8})
PARENTS = {7: 1, 8: 1, 1: None}
RULES = group_rules(PARTICIPATING, PARENTS, {})
DAY = date(2026, 11, 3)


def person(vid, name, *quals, member=(7,), **kwargs) -> Candidate:
    eligibility = person_eligibility(member, list(quals), PARTICIPATING, PARENTS)
    return Candidate(vid, name, False, EligibilityContext(eligibility, RULES), **kwargs)


def place(kind="skjenker", **kwargs) -> PlaceFacts:
    base = dict(shift_id=1, group_id=7, venue="Stjernesalen", day=DAY, is_closed=False, kind=kind, open_for_samf=False, slot_id=11)
    return PlaceFacts(**{**base, **kwargs})


BAR = ("skjenker", "approved", 7)
SL = ("skiftleder", "approved", 7)


def test_hard_rules_use_the_old_boards_order_and_texts() -> None:
    p = person(1, "Ada Lie", BAR)
    assert evaluate(p, place(is_closed=True)).reason == CLOSED_REASON
    assert evaluate(p, place(on_shift=frozenset({1}))).reason == ALREADY_ON_SHIFT_REASON
    assert evaluate(p, place("skiftleder")).reason == "Kun for godkjente skiftledere"
    assert evaluate(person(2, "Bo Berg"), place()).reason == "Kun for godkjente skjenkere"
    assert evaluate(person(3, "Cy Ås", BAR, member=()), place()).reason == "Kun for medlemmer av gruppa"
    trainee = person(4, "Di Ek", ("skjenker", "training", 7))
    assert evaluate(trainee, place()).reason == "I opplæring: bruk opplæringsplassen"
    # Closed wins over everything else.
    assert evaluate(p, place(is_closed=True, on_shift=frozenset({1}))).reason == CLOSED_REASON


def test_halvtimen_under_20_is_hard_and_unknown_birth_date_is_a_warning() -> None:
    halvtimen = place(venue="Halvtimen")
    assert evaluate(person(1, "Ung", BAR, birth_date=date(2010, 1, 1)), halvtimen).reason == "Halvtimen krever 20 år"
    fit = evaluate(person(2, "Ukjent", BAR), halvtimen)
    assert fit.ok and Warn("fødselsdato mangler, sjekk alderen", "yellow") in fit.warnings
    assert evaluate(person(3, "Eldre", BAR, birth_date=date(1999, 1, 1)), halvtimen).warnings == ()


def test_warnings_never_block_and_are_red_first() -> None:
    busy = person(1, "Ada", BAR, week_count=2, shifts_per_day={DAY: 1}, answers={1: "nei"}, swap_requested=True)
    fit = evaluate(busy, place())
    assert fit.ok and fit.level == "red"
    assert [w.text for w in fit.warnings] == [
        "svarte kan ikke",
        "to skift samme dag",
        "skift nummer 3 denne uka",
        "ønsker bytte",
    ]
    assert [w.level for w in fit.warnings] == ["red", "red", "yellow", "yellow"]


def test_no_warnings_for_a_free_person_and_the_week_count_is_the_next_shift_number() -> None:
    assert warnings_for(person(1, "Ada", BAR), place()) == ()
    assert warnings_for(person(1, "Ada", BAR, week_count=1), place()) == (Warn("skift nummer 2 denne uka", "yellow"),)
    # Another day is not "to skift samme dag".
    assert warnings_for(person(1, "Ada", BAR, shifts_per_day={date(2026, 11, 4): 1}), place()) == ()


def test_picker_sorts_by_answer_then_week_then_month_then_name() -> None:
    people = [
        person(1, "Nei Nils", BAR, answers={1: "nei"}),
        person(2, "Ikke Ida", BAR),
        person(3, "Kan Kari", BAR, answers={1: "kan"}),
        person(4, "Vil Vera", BAR, answers={1: "vil"}, week_count=1),
        person(5, "Vil Aksel", BAR, answers={1: "vil"}, week_count=1, month_count=2),
        person(6, "Vil Bente", BAR, answers={1: "vil"}, week_count=0, month_count=9),
        person(7, "Vil Åse", BAR, answers={1: "vil"}, week_count=1),
    ]
    names = [row.name for row in picker_rows(people, place())]
    assert names == ["Vil Bente", "Vil Vera", "Vil Åse", "Vil Aksel", "Kan Kari", "Ikke Ida", "Nei Nils"]


def test_picker_leaves_out_people_who_break_a_hard_rule_and_only_skiftledere_fill_skiftleder_places() -> None:
    people = [person(1, "Sigrid", BAR, SL), person(2, "Sander", BAR), person(3, "Tor", ("skjenker", "training", 7))]
    assert [r.name for r in picker_rows(people, place("skiftleder"))] == ["Sigrid"]
    assert [r.name for r in picker_rows(people, place())] == ["Sander", "Sigrid"]
    assert [r.name for r in picker_rows(people, place(on_shift=frozenset({1})))] == ["Sander"]


def test_picker_row_texts_and_level() -> None:
    row = picker_rows([person(1, "Ada", BAR, answers={1: "kan"}, week_count=1)], place())[0]
    assert (row.answer, row.answer_word, row.level) == ("kan", "kan", "yellow")
    silent = picker_rows([person(2, "Bo", BAR)], place())[0]
    assert silent.answer_word == "" and silent.level == ""
    assert sort_key(PickerRow(1, "A", False, "", "", 0, 0, ())) < sort_key(PickerRow(2, "A", False, "nei", "kan ikke", 0, 0, ()))


def test_place_state_says_fit_or_reason_for_every_person_and_open_place() -> None:
    people = [person(1, "Sigrid", BAR, SL), person(2, "Sander", BAR, answers={1: "nei"})]
    state = build_place_state(people, [place("skjenker", slot_id=11), place("skiftleder", slot_id=10)])

    assert state["1"]["10"] == {"ok": 1, "w": []}
    assert state["2"]["10"] == {"ok": 0, "why": "Kun for godkjente skiftledere"}
    assert state["2"]["11"] == {"ok": 1, "w": [["svarte kan ikke", "red"]]}


def test_live_message_replaces_the_services_hint_with_the_pages_warnings() -> None:
    base = "Ada Lie er satt opp som skjenker, dagskift tirsdag 3. november."
    assert live_message(base, []) == base
    assert live_message(f"{base} Merk: har allerede 1 skift denne uka.", []) == base
    assert live_message(base, ["svarte kan ikke", "2 skift denne uka"]) == f"{base} Advarsel: svarte kan ikke, 2 skift denne uka."
