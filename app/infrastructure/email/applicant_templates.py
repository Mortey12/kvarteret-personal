from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jinja2 import Environment, FileSystemLoader, select_autoescape


@dataclass(frozen=True, slots=True)
class ApplicantEmail:
    subject: str
    html_body: str


class ApplicantEmailTemplateRendererProtocol(Protocol):
    def render_application_received_email(self) -> ApplicantEmail: ...

    def render_invitation_email(self, *, invitation_url: str) -> ApplicantEmail: ...

    def render_friend_invitation_email(
        self,
        *,
        invitation_url: str,
        inviter_name: str,
    ) -> ApplicantEmail: ...

    def render_profile_completion_email(
        self, *, invitation_url: str
    ) -> ApplicantEmail: ...

    def render_recruiter_daily_digest_email(
        self, *, sections_html: str, item_count: int
    ) -> ApplicantEmail: ...


class ApplicantEmailTemplateRenderer:
    def __init__(self, template_dir: Path | None = None) -> None:
        resolved_dir = template_dir or (
            Path(__file__).resolve().parents[2] / "templates" / "emails" / "compiled"
        )
        self._environment = Environment(
            loader=FileSystemLoader(str(resolved_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render_application_received_email(self) -> ApplicantEmail:
        return ApplicantEmail(
            subject="Den første døren er nå åpen",
            html_body=self._render("applicant_application_received.html"),
        )

    def render_invitation_email(self, *, invitation_url: str) -> ApplicantEmail:
        return ApplicantEmail(
            subject="Complete your Samfunnet i Bergen registration / Fullfør registreringen din hos Samfunnet i Bergen",
            html_body=self._render(
                "applicant_invitation.html",
                invitation_url=invitation_url,
            ),
        )

    def render_friend_invitation_email(
        self,
        *,
        invitation_url: str,
        inviter_name: str,
    ) -> ApplicantEmail:
        return ApplicantEmail(
            subject=f"{inviter_name} har invitert deg til å bli frivillig sammen på Samfunnet i Bergen!",
            html_body=self._render(
                "applicant_friend_invitation.html",
                invitation_url=invitation_url,
                inviter_name=inviter_name,
            ),
        )

    def render_profile_completion_email(self, *, invitation_url: str) -> ApplicantEmail:
        return ApplicantEmail(
            subject="Din reise starter nå / Your journey starts now",
            html_body=self._render(
                "applicant_profile_completion.html",
                invitation_url=invitation_url,
            ),
        )

    def render_recruiter_daily_digest_email(
        self, *, sections_html: str, item_count: int
    ) -> ApplicantEmail:
        return ApplicantEmail(
            subject=f"Rekruttering: {item_count} {'sak' if item_count == 1 else 'saker'} trenger oppfølging",
            html_body=self._render(
                "recruiter_daily_digest.html", sections_html=sections_html
            ),
        )

    def _render(self, template_name: str, **context: object) -> str:
        template = self._environment.get_template(template_name)
        return template.render(**context)
