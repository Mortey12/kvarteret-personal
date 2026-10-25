import { mkdir, writeFile } from "node:fs/promises"
import path from "node:path"
import type { ReactElement } from "react"
import { pretty, render } from "@react-email/render"
import ApplicationReceived from "../app/templates/emails/react/applicant-application-received"
import ApplicantInvitation from "../app/templates/emails/react/applicant-invitation"
import ProfileCompletion from "../app/templates/emails/react/applicant-profile-completion"
import FriendInvitation from "../app/templates/emails/react/applicant-friend-invitation"
import AdminOnboarding from "../app/templates/emails/react/admin-account-onboarding"
import MobileCardAccessCode from "../app/templates/emails/react/mobile-card-access-code"
import PersonalAccessCode from "../app/templates/emails/react/personal-access-code"
import PasswordReset from "../app/templates/emails/react/password-reset"
import RecruiterDigest from "../app/templates/emails/react/recruiter-daily-digest"

// React authors use normal typed props. Only this build adapter knows about
// Python's Jinja placeholders, which remain autoescaped at request time.
const variable = (name: string) => `__EMAIL_PROP_${name.toUpperCase()}__`
const invitation_url = variable("invitation_url")
const setup_url = variable("setup_url")
const templates: Array<[string, ReactElement, string[]]> = [
  ["applicant_application_received", <ApplicationReceived />, []],
  ["applicant_invitation", <ApplicantInvitation invitation_url={invitation_url} />, ["invitation_url"]],
  ["applicant_profile_completion", <ProfileCompletion invitation_url={invitation_url} />, ["invitation_url"]],
  ["applicant_friend_invitation", <FriendInvitation invitation_url={invitation_url} inviter_name={variable("inviter_name")} />, ["invitation_url", "inviter_name"]],
  ["admin_account_onboarding", <AdminOnboarding setup_url={setup_url} display_name={variable("display_name")} username={variable("username")} role_name={variable("role_name")} />, ["setup_url", "display_name", "username", "role_name"]],
  ["mobile_card_access_code", <MobileCardAccessCode access_code={variable("access_code")} expires_in_minutes={variable("expires_in_minutes")} />, ["access_code", "expires_in_minutes"]],
  ["personal_access_code", <PersonalAccessCode access_code={variable("access_code")} expires_in_minutes={variable("expires_in_minutes")} />, ["access_code", "expires_in_minutes"]],
  ["password_reset", <PasswordReset setup_url={setup_url} />, ["setup_url"]],
  ["recruiter_daily_digest", <RecruiterDigest heading={variable("heading")} greeting={variable("greeting")} sections_html={variable("sections_html")} />, ["heading", "greeting", "sections_html"]],
]

const outputDir = path.resolve("app/templates/emails/compiled")
await mkdir(outputDir, { recursive: true })
for (const [name, component, variables] of templates) {
  let html = (await render(component)).replaceAll("<!-- -->", "")
  if (name === "admin_account_onboarding") {
    const greeting = ` ${variable("display_name")}`
    if (!html.includes(greeting)) throw new Error("Optional display-name greeting was lost")
    html = html.replace(greeting, "{% if display_name %} {{ display_name }}{% endif %}")
  }
  for (const key of variables) {
    const marker = variable(key)
    if (key !== "display_name" && !html.includes(marker)) throw new Error(`${name}: missing ${key}`)
    // *_html values are escaped by the Python renderer before they reach Jinja.
    html = html.replaceAll(marker, key.endsWith("_html") ? `{{ ${key}|safe }}` : `{{ ${key} }}`)
  }
  if (html.includes("__EMAIL_PROP_")) throw new Error(`${name}: unresolved build marker`)
  await writeFile(path.join(outputDir, `${name}.html`), `${(await pretty(html)).trimEnd()}\n`)
}
