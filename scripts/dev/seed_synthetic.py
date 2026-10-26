"""Synthetic seed for the local development database.

Deterministic (seeded RNG) and entirely fake: groups and courses with
real-looking structure, ~40 volunteers with role history and course
completions, and a handful of pending applications including a
    inviter and friend applications that can be processed independently.

Used by ``scripts/dev/bootstrap.py`` when no anonymized snapshot exists.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import insert, select

from app.auth.dev_auth import dev_auth_user_id
from app.auth.repository import DatabaseAuthRepository
from app.auth.roles import UserRole
from app.config import Settings
from app.db.session import build_database_runtime, session_scope
from app.db.tables import (
    assignment_roles,
    course_completions,
    courses,
    group_course_requirements,
    groups,
    role_assignments,
    user_accounts,
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
    volunteer_records,
)
from app.shared.semester import get_current_semester_code, get_previous_semester_code
from app.shared.slugs import slugify

DEV_ADMIN_EMAIL = os.environ.get("DEV_ADMIN_EMAIL", "dev@kvarteret.dev")

FIRST_NAMES = [
    "Ada",
    "Birk",
    "Clara",
    "Didrik",
    "Eira",
    "Frida",
    "Gustav",
    "Hedda",
    "Iver",
    "Jenny",
    "Kasper",
    "Live",
    "Mats",
    "Nora",
    "Oskar",
    "Pernille",
    "Quint",
    "Ronja",
    "Sander",
    "Tuva",
    "Ulrik",
    "Vilde",
    "William",
    "Ylva",
]
LAST_NAMES = [
    "Andersen",
    "Berg",
    "Christiansen",
    "Dahl",
    "Eriksen",
    "Fjeld",
    "Gundersen",
    "Haugen",
    "Iversen",
    "Johansen",
    "Knutsen",
    "Lie",
    "Moen",
    "Nilsen",
    "Olsen",
    "Pedersen",
    "Rasmussen",
    "Solberg",
    "Tangen",
    "Vik",
]
STREETS = [
    "Olav Kyrres gate",
    "Nygårdsgaten",
    "Christies gate",
    "Fosswinckels gate",
    "Strandgaten",
    "Marken",
    "Kong Oscars gate",
    "Sydnesplassen",
]
POSTAL_CODES = ["5006", "5007", "5011", "5014", "5015", "5018", "5020"]

GROUPS = [
    ("Skjenkegruppen", "Bar og skjenking", 2),
    ("Vaktetaten", "Vakthold og sikkerhet", 2),
    ("Kraftetaten", "Teknisk drift", 2),
    ("Eksponeringsetaten", "Promo og media", 1),
    ("Lydgruppen", "Lyd og sceneteknikk", 1),
    ("Styret", "Kvarterstyret", 3),
]
ROLES_PER_GROUP = [("Frivillig", 2), ("Skiftleder", 4), ("Gruppeleder", 6)]
COURSES = ["Skjenkekurs", "Brannvernkurs", "Førstehjelpskurs"]


def _seed_token(label: str) -> str:
    return f"dev-{label}"


async def seed(database_url: str) -> None:
    rng = random.Random(42)
    settings = Settings(database_url=database_url, app_env="development")
    runtime = build_database_runtime(settings)
    now = datetime.now(UTC)
    current_semester = get_current_semester_code()
    previous_semester = get_previous_semester_code(current_semester)
    semesters = sorted(
        {get_previous_semester_code(previous_semester), previous_semester, current_semester}
    )

    try:
        async with session_scope(runtime) as session:
            existing = await session.scalar(select(groups.c.id).limit(1))
            if existing is not None:
                print("Database already seeded; skipping (use make dev-reset to start over).")
                return

            group_ids: dict[str, int] = {}
            role_ids: dict[str, list[int]] = {}
            for name, description, tier in GROUPS:
                group_id = (
                    await session.execute(
                        insert(groups)
                        .values(
                            slug=slugify(name),
                            name=name,
                            description=description,
                            is_active=True,
                            active_through_semester=20991,
                            discount_tier=tier,
                            created_at=now,
                        )
                        .returning(groups.c.id)
                    )
                ).scalar_one()
                group_ids[name] = group_id
                role_ids[name] = []
                for role_name, points in ROLES_PER_GROUP:
                    role_id = (
                        await session.execute(
                            insert(assignment_roles)
                            .values(
                                group_id=group_id,
                                name=role_name,
                                penguin_points=points,
                            )
                            .returning(assignment_roles.c.id)
                        )
                    ).scalar_one()
                    role_ids[name].append(role_id)

            course_ids = []
            for course_name in COURSES:
                course_id = (
                    await session.execute(
                        insert(courses).values(name=course_name, created_at=now).returning(courses.c.id)
                    )
                ).scalar_one()
                course_ids.append(course_id)
            await session.execute(
                insert(group_course_requirements),
                [
                    {"group_id": group_ids["Skjenkegruppen"], "course_id": course_ids[0]},
                    {"group_id": group_ids["Vaktetaten"], "course_id": course_ids[1]},
                ],
            )

            volunteer_ids = []
            for index in range(40):
                first = rng.choice(FIRST_NAMES)
                last = rng.choice(LAST_NAMES)
                volunteer_id = (
                    await session.execute(
                        insert(volunteer_records)
                        .values(
                            first_name=first,
                            last_name=last,
                            email=f"{first}.{last}.{index}@example.dev".lower(),
                            phone="4" + "".join(rng.choices("0123456789", k=7)),
                            birth_date=date(rng.randint(1995, 2006), rng.randint(1, 12), rng.randint(1, 28)),
                            gender=rng.choice(["M", "K", "A"]),
                            street_address=f"{rng.choice(STREETS)} {rng.randint(1, 80)}",
                            postal_code=rng.choice(POSTAL_CODES),
                            created_at=now - timedelta(days=rng.randint(30, 1500)),
                        )
                        .returning(volunteer_records.c.id)
                    )
                ).scalar_one()
                volunteer_ids.append(volunteer_id)

                group_name = rng.choice(list(group_ids))
                for semester in rng.sample(semesters, rng.randint(1, len(semesters))):
                    await session.execute(
                        insert(role_assignments).values(
                            volunteer_id=volunteer_id,
                            group_id=group_ids[group_name],
                            role_id=rng.choice(role_ids[group_name]),
                            semester=semester,
                            contract_signed=rng.random() > 0.2,
                        )
                    )
                if rng.random() > 0.5:
                    await session.execute(
                        insert(course_completions).values(
                            volunteer_id=volunteer_id,
                            course_id=rng.choice(course_ids),
                            completed_semester=rng.choice(semesters),
                        )
                    )

            # Applications: one new admin invite, one solo trial applicant,
            # and a two-friend group ready for atomic promotion.
            await session.execute(
                insert(volunteer_application_invites).values(
                    token=_seed_token("invite-1"),
                    email="invited.person@example.dev",
                    source="invite",
                    status="new",
                    initial_group_id=group_ids["Lydgruppen"],
                    initial_role_id=role_ids["Lydgruppen"][0],
                    trial_shift_attended=False,
                    created_at=now - timedelta(days=2),
                )
            )
            prospect_invite_id = (
                await session.execute(
                    insert(volunteer_application_invites)
                    .values(
                        token=_seed_token("prospect-1"),
                        email="solo.prospect@example.dev",
                        source="public_signup",
                        status="trial",
                        first_choice_group_id=group_ids["Vaktetaten"],
                        trial_started_at=now - timedelta(days=1),
                        trial_ends_at=now + timedelta(days=29),
                        created_at=now - timedelta(days=5),
                    )
                    .returning(volunteer_application_invites.c.id)
                )
            ).scalar_one()
            await session.execute(
                insert(volunteer_application_submissions).values(
                    invite_id=prospect_invite_id,
                    first_name="Solo",
                    last_name="Prospect",
                    email="solo.prospect@example.dev",
                    gender="A",
                    phone="40000001",
                    created_at=now - timedelta(days=5),
                )
            )

            friend_application_ids: dict[str, int] = {}
            for offset, (token, email, first, role) in enumerate(
                [
                    ("dev-friends-inviter", "inviter.friend@example.dev", "Inga", "inviter"),
                    ("dev-friends-invitee", "invitee.friend@example.dev", "Frida", "invitee"),
                ]
            ):
                invite_id = (
                    await session.execute(
                        insert(volunteer_application_invites)
                        .values(
                            token=token,
                            email=email,
                            source="public_signup" if role == "inviter" else "friend_invite",
                            status="trial",
                            first_choice_group_id=group_ids["Skjenkegruppen"],
                            trial_started_at=now - timedelta(days=1),
                            trial_ends_at=now + timedelta(days=29),
                            full_profile_submitted_at=now - timedelta(days=1, hours=offset),
                            created_at=now - timedelta(days=3),
                        )
                        .returning(volunteer_application_invites.c.id)
                    )
                ).scalar_one()
                friend_application_ids[role] = invite_id
                await session.execute(
                    insert(volunteer_application_submissions).values(
                        invite_id=invite_id,
                        first_name=first,
                        last_name="Vennesøker",
                        email=email,
                        gender="K",
                        phone=f"4000010{offset}",
                        birth_date=datetime(2003, 5, 14, tzinfo=UTC),
                        street_address="Nygårdsgaten 5",
                        postal_code="5015",
                        created_at=now - timedelta(days=1),
                    )
                )
            await session.execute(
                insert(volunteer_application_friend_invitations).values(
                    inviter_application_id=friend_application_ids["inviter"],
                    invitee_application_id=friend_application_ids["invitee"],
                    inviter_name_snapshot="Inga Vennesøker",
                    inviter_email_snapshot="inviter.friend@example.dev",
                    invitee_email_snapshot="invitee.friend@example.dev",
                    created_at=now - timedelta(days=3),
                )
            )

            print(f"Seeded {len(volunteer_ids)} volunteers, {len(GROUPS)} groups, 4 applications.")
        await ensure_dev_admin(runtime)
    finally:
        await runtime.aclose()


async def ensure_dev_admin(runtime) -> None:
    """Idempotently create the dev admin matching the DevAuthGateway login."""
    async with session_scope(runtime) as session:
        existing = await session.scalar(select(user_accounts.c.id).where(user_accounts.c.email == DEV_ADMIN_EMAIL))
        if existing is not None:
            return
    async with session_scope(runtime):
        repository = DatabaseAuthRepository()
        await repository.create_direct_user_account(
            auth_user_id=dev_auth_user_id(DEV_ADMIN_EMAIL),
            username="dev",
            email=DEV_ADMIN_EMAIL,
            display_name="Dev Admin",
            role=UserRole.ADMIN,
        )
    print(f"Dev admin ready: {DEV_ADMIN_EMAIL}")


if __name__ == "__main__":
    url = os.environ.get(
        "DEV_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:55432/kvarteret_personal_dev",
    )
    asyncio.run(seed(url))
