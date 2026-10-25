from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from app.config import Settings
from app.db.session import current_session
from app.domain.volunteer_applications.tables import (
    volunteer_application_friend_invitations,
    volunteer_application_invites,
    volunteer_application_submissions,
)
from app.email_delivery import (
    APPLICANT_APPLICATION_RECEIVED,
    APPLICANT_INVITATION,
    APPLICANT_PROFILE_COMPLETION,
    RECRUITER_DAILY_DIGEST,
    VOLUNTEER_TEMPLATE_KEYS,
)
from app.infrastructure.email.applicant_templates import (
    ApplicantEmailTemplateRendererProtocol,
)


class EmailPreparationFailure(RuntimeError):
    def __init__(
        self, category: str, *, expired: bool = False, retryable: bool = False
    ) -> None:
        super().__init__(category)
        self.category = category
        self.expired = expired
        self.retryable = retryable


class PreparedEmail:
    def __init__(self, recipient_email: str, subject: str, html_body: str) -> None:
        self.recipient_email = recipient_email
        self.subject = subject
        self.html_body = html_body


class EmailMessagePreparer:
    """Resolve one queued email into the existing transport's message shape.

    This component owns template-specific reads and temporary secret handling.
    It does not enqueue, lease, retry, or send messages.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        applicant_renderer: ApplicantEmailTemplateRendererProtocol,
    ) -> None:
        self.settings = settings
        self.applicant_renderer = applicant_renderer

    async def prepare(self, delivery: Mapping[str, Any]) -> PreparedEmail:
        if delivery["template_version"] != 1:
            raise EmailPreparationFailure("unsupported_template_version")
        template_key = delivery["template_key"]
        if template_key in VOLUNTEER_TEMPLATE_KEYS:
            return await self._prepare_volunteer_email(delivery)
        if template_key == RECRUITER_DAILY_DIGEST:
            return await self._prepare_recruiter_digest(delivery)
        raise EmailPreparationFailure("unknown_template")

    async def _prepare_recruiter_digest(
        self, delivery: Mapping[str, Any]
    ) -> PreparedEmail:
        from app.domain.recruitment_digest.service import render_digest

        base_url = (self.settings.app_public_base_url or "").rstrip("/")
        if not base_url:
            raise EmailPreparationFailure("public_base_url_missing")
        raw = delivery["encrypted_context"]
        if not raw:
            raise EmailPreparationFailure("business_record_missing")
        context = json.loads(bytes(raw))
        dry_run_for = context.pop("_dry_run_for", None)
        sections_html, group_name, first_name, count = await render_digest(
            context, base_url=base_url, now=datetime.now(UTC)
        )
        if count == 0 and not context.get("_missing_groups"):
            # Everything listed was deleted since enqueue: nothing to report,
            # so end the delivery quietly instead of sending an empty email.
            raise EmailPreparationFailure("nothing_to_report", expired=True)
        if dry_run_for:
            sections_html = (
                '<p style="background:#fff3cd;padding:12px;margin:0 0 16px">'
                f"Prøvekjøring: denne e-posten ville gått til {escape(dry_run_for)}."
                "</p>" + sections_html
            )
        rendered = self.applicant_renderer.render_recruiter_digest_email(
            heading=f"Rekruttering for {group_name}" if group_name else "Grupper uten mottaker",
            greeting=f"Hei {first_name}," if first_name else "Hei,",
            sections_html=sections_html,
            subject_prefix=f"[Prøvekjøring for {dry_run_for}] " if dry_run_for else "",
        )
        return PreparedEmail(
            delivery["recipient_email"], rendered.subject, rendered.html_body
        )

    async def _prepare_volunteer_email(
        self, delivery: Mapping[str, Any]
    ) -> PreparedEmail:
        registration_id = delivery["registration_id"]
        if registration_id is None:
            raise EmailPreparationFailure("business_record_missing")
        invite = (
            (
                await _session().execute(
                    select(
                        volunteer_application_invites.c.token,
                        volunteer_application_invites.c.email,
                    )
                    .where(volunteer_application_invites.c.id == registration_id)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if invite is None:
            raise EmailPreparationFailure("business_record_missing")
        if delivery["template_key"] == APPLICANT_APPLICATION_RECEIVED:
            rendered = self.applicant_renderer.render_application_received_email()
            return PreparedEmail(
                invite["email"], rendered.subject, rendered.html_body
            )
        base_url = (self.settings.app_public_base_url or "").rstrip("/")
        if not base_url:
            raise EmailPreparationFailure("public_base_url_missing")
        invitation_url = f"{base_url}/apply/{invite['token']}"
        if delivery["template_key"] == APPLICANT_INVITATION:
            rendered = self.applicant_renderer.render_invitation_email(
                invitation_url=invitation_url
            )
        elif delivery["template_key"] == APPLICANT_PROFILE_COMPLETION:
            rendered = self.applicant_renderer.render_profile_completion_email(
                invitation_url=invitation_url
            )
        else:
            inviter = await self._load_friend_inviter(registration_id)
            if inviter is None:
                raise EmailPreparationFailure("inviter_record_missing")
            rendered = self.applicant_renderer.render_friend_invitation_email(
                invitation_url=invitation_url,
                inviter_name=inviter["name"],
            )
        return PreparedEmail(invite["email"], rendered.subject, rendered.html_body)

    async def _load_friend_inviter(self, invitee_application_id: int) -> dict[str, str] | None:
        inviter_submission = volunteer_application_submissions.alias("friend_inviter_submission")
        row = (
            (
                await _session().execute(
                    select(
                        volunteer_application_friend_invitations.c.inviter_name_snapshot,
                        volunteer_application_friend_invitations.c.inviter_email_snapshot,
                        inviter_submission.c.first_name,
                        inviter_submission.c.last_name,
                    )
                    .select_from(
                        volunteer_application_friend_invitations.outerjoin(
                            inviter_submission,
                            inviter_submission.c.invite_id
                            == volunteer_application_friend_invitations.c.inviter_application_id,
                        )
                    )
                    .where(
                        volunteer_application_friend_invitations.c.invitee_application_id
                        == invitee_application_id
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        current_name = " ".join(
            filter(None, [row["first_name"], row["last_name"]])
        ).strip()
        return {
            "name": current_name
            or row["inviter_name_snapshot"]
            or row["inviter_email_snapshot"]
        }

def _session():
    session = current_session()
    if session is None:
        raise RuntimeError("Email preparation requires an active database session.")
    return session
