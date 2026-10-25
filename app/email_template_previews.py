"""Render the email template catalog using synthetic preview data only."""

from __future__ import annotations

from dataclasses import dataclass

from app.infrastructure.email.applicant_templates import (
    ApplicantEmailTemplateRenderer,
)
from app.infrastructure.email.admin_account_templates import (
    AdminAccountEmailTemplateRenderer,
)
from app.infrastructure.email.mobile_card_templates import (
    MobileCardEmailTemplateRenderer,
)
from app.infrastructure.email.password_reset_templates import (
    PasswordResetEmailTemplateRenderer,
)


@dataclass(frozen=True, slots=True)
class EmailPreview:
    slug: str
    title: str
    subject: str
    html_body: str


def build_previews() -> list[EmailPreview]:
    applicant_renderer = ApplicantEmailTemplateRenderer()
    admin_account_renderer = AdminAccountEmailTemplateRenderer()
    mobile_card_renderer = MobileCardEmailTemplateRenderer()
    password_reset_renderer = PasswordResetEmailTemplateRenderer()

    applicant_invitation_email = applicant_renderer.render_invitation_email(
        invitation_url="https://personal.samfunnetibergen.no/apply/invite-token-preview",
    )
    applicant_received_email = applicant_renderer.render_application_received_email()
    applicant_profile_email = applicant_renderer.render_profile_completion_email(
        invitation_url="https://personal.samfunnetibergen.no/apply/profile-token-preview",
    )
    applicant_friend_email = applicant_renderer.render_friend_invitation_email(
        invitation_url="https://personal.samfunnetibergen.no/apply/friend-token-preview",
        inviter_name="Inga Inviter",
    )
    recruiter_digest_email = applicant_renderer.render_recruiter_daily_digest_email(
        sections_html="<h2>Nye søkere</h2><ul><li>Inga Inviter</li></ul>",
        item_count=1,
    )
    mobile_card_email = mobile_card_renderer.render_access_code_email(
        access_code="483921",
        expires_in_minutes=10,
    )
    personal_email = mobile_card_renderer.render_personal_access_code_email(access_code="483921", expires_in_minutes=10)
    admin_account_email = admin_account_renderer.render_onboarding_email(
        setup_url="https://personal.samfunnetibergen.no/set-password#access_token=preview-token",
        display_name="New Admin",
        username="new.admin",
        role_name="Admin",
    )
    password_reset_email = password_reset_renderer.render_password_reset_email(
        setup_url="https://personal.samfunnetibergen.no/set-password#token_hash=preview-token&type=recovery",
    )

    return [
        EmailPreview(
            slug="applicant_application_received",
            title="Søknad mottatt",
            subject=applicant_received_email.subject,
            html_body=applicant_received_email.html_body,
        ),
        EmailPreview(
            slug="applicant_invitation",
            title="Invitasjon til å søke",
            subject=applicant_invitation_email.subject,
            html_body=applicant_invitation_email.html_body,
        ),
        EmailPreview(
            slug="applicant_profile_completion",
            title="Fullfør profilen",
            subject=applicant_profile_email.subject,
            html_body=applicant_profile_email.html_body,
        ),
        EmailPreview(
            slug="applicant_friend_invitation",
            title="Venneinvitasjon",
            subject=applicant_friend_email.subject,
            html_body=applicant_friend_email.html_body,
        ),
        EmailPreview(
            slug="recruiter_daily_digest",
            title="Daglig rekrutteringsoppsummering",
            subject=recruiter_digest_email.subject,
            html_body=recruiter_digest_email.html_body,
        ),
        EmailPreview(
            slug="admin_account_onboarding",
            title="Opprett admin-konto",
            subject=admin_account_email.subject,
            html_body=admin_account_email.html_body,
        ),
        EmailPreview(
            slug="mobile_card_access_code",
            title="Innloggingskode til Internkort",
            subject=mobile_card_email.subject,
            html_body=mobile_card_email.html_body,
        ),
        EmailPreview(slug="personal_access_code", title="Innloggingskode til Personal", subject=personal_email.subject, html_body=personal_email.html_body),
        EmailPreview(
            slug="password_reset",
            title="Tilbakestill passord",
            subject=password_reset_email.subject,
            html_body=password_reset_email.html_body,
        ),
    ]
