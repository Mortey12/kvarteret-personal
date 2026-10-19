"""Write side of the volunteers module.

Reads live in ``app.domain.volunteers.queries``; this repository holds
the statements that change volunteer state (profile, relations, photo
records, position management, offboarding deletion).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, exists, insert, select, update

from app.db.repository import SqlAlchemyRepository
from app.domain.courses.tables import course_completions
from app.domain.role_assignments.tables import role_assignments
from app.domain.volunteer_applications.tables import volunteer_application_invites
from app.domain.volunteers.tables import (
    volunteer_cards,
    volunteer_next_of_kin,
    volunteer_photos,
    volunteer_records,
)


class VolunteersRepository(SqlAlchemyRepository):
    async def volunteer_exists(self, volunteer_id: int) -> bool:
        return bool(
            await self.fetch_scalar(
                select(exists().where(volunteer_records.c.id == volunteer_id))
            )
        )

    async def fetch_photo_record(self, volunteer_id: int):
        return await self.fetch_first_mapping(
            select(volunteer_photos.c.sha1, volunteer_photos.c.filetype)
            .where(volunteer_photos.c.volunteer_id == volunteer_id)
            .limit(1)
        )

    async def save_photo_record(
        self,
        *,
        volunteer_id: int,
        filename_hash: str,
        extension: str,
        existing: bool,
    ) -> None:
        if existing:
            await self.execute(
                update(volunteer_photos)
                .where(volunteer_photos.c.volunteer_id == volunteer_id)
                .values(sha1=filename_hash, filetype=extension)
            )
        else:
            await self.execute(
                insert(volunteer_photos).values(
                    volunteer_id=volunteer_id, sha1=filename_hash, filetype=extension
                )
            )

    async def update_volunteer_profile(
        self,
        *,
        volunteer_id: int,
        first_name: str | None,
        last_name: str,
        email: str | None,
        phone: str | None,
        birth_date,
        gender_code: str,
        address: str | None,
        postal_code: str | None,
    ) -> None:
        await self.execute(
            update(volunteer_records)
            .where(volunteer_records.c.id == volunteer_id)
            .values(
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                birth_date=birth_date,
                gender=gender_code,
                street_address=address,
                postal_code=postal_code,
            )
        )
        # Applications promoted to this profile keep their own email copy; keep it
        # in step so a corrected address is not left behind on the application.
        if email:
            await self.execute(
                update(volunteer_application_invites)
                .where(volunteer_application_invites.c.promoted_volunteer_id == volunteer_id)
                .values(email=email.lower())
            )

    async def replace_volunteer_relations(
        self,
        *,
        volunteer_id: int,
        card_numbers: list[str],
        next_of_kin: list[dict[str, str]],
    ) -> None:
        created_at = datetime.now(UTC)
        await self.execute(
            delete(volunteer_cards).where(
                volunteer_cards.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(volunteer_next_of_kin).where(
                volunteer_next_of_kin.c.volunteer_id == volunteer_id
            )
        )
        if card_numbers:
            await self.session.execute(
                insert(volunteer_cards),
                [
                    {
                        "volunteer_id": volunteer_id,
                        "card_number": card_number,
                        "created_at": created_at,
                    }
                    for card_number in card_numbers
                ],
            )
        if next_of_kin:
            await self.session.execute(
                insert(volunteer_next_of_kin),
                [
                    {
                        "volunteer_id": volunteer_id,
                        "name": item["name"],
                        "phone": item["phone"],
                        "created_at": created_at,
                    }
                    for item in next_of_kin
                ],
            )

    async def delete_photo_record(self, volunteer_id: int) -> None:
        await self.execute(
            delete(volunteer_photos).where(
                volunteer_photos.c.volunteer_id == volunteer_id
            )
        )

    async def create_from_application(
        self,
        *,
        first_name: str | None,
        last_name: str,
        email: str | None,
        gender: str,
        birth_date,
        street_address: str | None,
        postal_code: str | None,
        phone: str | None,
        photo_sha1: str | None,
        photo_filetype: str | None,
        group_id: int | None,
        role_id: int | None,
        semester_code: int,
        contract_signed: bool,
        volunteer_id: int | None = None,
        assignment_id: int | None = None,
    ) -> tuple[int, int | None]:
        """Create or update a volunteer record from an application.

        Trial applications get their volunteer record before approval. Approval
        reuses that record and finalizes its assignment instead of creating a
        second volunteer. Returns the volunteer ID and assignment ID.
        """
        if volunteer_id is None:
            inserted = await self.execute_one_mapping(
                insert(volunteer_records)
                .values(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    gender=gender,
                    birth_date=birth_date,
                    street_address=street_address,
                    postal_code=postal_code,
                    phone=phone,
                )
                .returning(volunteer_records.c.id)
            )
            volunteer_id = int(inserted["id"])
        else:
            await self.execute(
                update(volunteer_records)
                .where(volunteer_records.c.id == volunteer_id)
                .values(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    gender=gender,
                    birth_date=birth_date,
                    street_address=street_address,
                    postal_code=postal_code,
                    phone=phone,
                )
            )

        if photo_sha1 and photo_filetype:
            existing_photo = await self.fetch_first_mapping(
                select(volunteer_photos.c.volunteer_id).where(
                    volunteer_photos.c.volunteer_id == volunteer_id
                )
            )
            if existing_photo:
                await self.execute(
                    update(volunteer_photos)
                    .where(volunteer_photos.c.volunteer_id == volunteer_id)
                    .values(sha1=photo_sha1, filetype=photo_filetype)
                )
            else:
                await self.execute(
                    insert(volunteer_photos).values(
                        volunteer_id=volunteer_id,
                        sha1=photo_sha1,
                        filetype=photo_filetype,
                    )
                )

        return await self.add_application_assignment(
            volunteer_id=volunteer_id,
            group_id=group_id,
            role_id=role_id,
            semester_code=semester_code,
            contract_signed=contract_signed,
            assignment_id=assignment_id,
        )

    async def add_application_assignment(
        self,
        *,
        volunteer_id: int,
        group_id: int | None,
        role_id: int | None,
        semester_code: int,
        contract_signed: bool,
        assignment_id: int | None = None,
    ) -> tuple[int, int | None]:
        """Add or finalize only this application's assignment; never edit the profile."""
        if group_id is not None:
            existing_assignment = None
            if assignment_id is not None:
                existing_assignment = await self.fetch_first_mapping(
                    select(role_assignments.c.id).where(
                        role_assignments.c.id == assignment_id,
                        role_assignments.c.volunteer_id == volunteer_id,
                    )
                )
                if existing_assignment is None:
                    raise ValueError(
                        "The trial assignment no longer belongs to this volunteer."
                    )
            assignment_values = {
                "group_id": group_id,
                "role_id": role_id,
                "semester": semester_code,
                "contract_signed": contract_signed,
            }
            if existing_assignment:
                await self.execute(
                    update(role_assignments)
                    .where(role_assignments.c.id == existing_assignment["id"])
                    .values(**assignment_values)
                )
            else:
                assignment = await self.execute_one_mapping(
                    insert(role_assignments)
                    .values(
                        volunteer_id=volunteer_id,
                        **assignment_values,
                    )
                    .returning(role_assignments.c.id)
                )
                assignment_id = int(assignment["id"])

        return volunteer_id, assignment_id

    async def remove_trial_assignment(
        self, *, volunteer_id: int, assignment_id: int
    ) -> None:
        await self.execute(
            delete(role_assignments).where(
                role_assignments.c.id == assignment_id,
                role_assignments.c.volunteer_id == volunteer_id,
                role_assignments.c.contract_signed.is_(False),
            )
        )

    async def delete_volunteer(self, volunteer_id: int) -> None:
        # Detach the application record instead of deleting it: the
        # invite row is lifecycle history. Status stays 'promoted',
        # so the application does not resurface as pending.
        await self.execute(
            update(volunteer_application_invites)
            .where(
                volunteer_application_invites.c.promoted_volunteer_id == volunteer_id
            )
            .values(promoted_volunteer_id=None)
        )
        await self.execute(
            delete(role_assignments).where(
                role_assignments.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(course_completions).where(
                course_completions.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(volunteer_cards).where(
                volunteer_cards.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(volunteer_next_of_kin).where(
                volunteer_next_of_kin.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(volunteer_photos).where(
                volunteer_photos.c.volunteer_id == volunteer_id
            )
        )
        await self.execute(
            delete(volunteer_records).where(volunteer_records.c.id == volunteer_id)
        )
