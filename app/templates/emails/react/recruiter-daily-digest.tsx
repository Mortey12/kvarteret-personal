import { EmailLayout, Paragraph } from "./components"

export type RecruiterDigestProps = { sections_html: string }

// The Python renderer builds the (escaped) section markup; this template owns
// the layout and branding around it.
export default function RecruiterDailyDigest({ sections_html }: RecruiterDigestProps) {
  return <EmailLayout title="Daglig rekrutteringsoppsummering" heading="Rekruttering i dag" preview="Søkere og prøvetider som trenger oppfølging." lang="nb">
    <Paragraph>Her er dagens oppsummering for gruppene du er rekrutteringsansvarlig for.</Paragraph>
    <div dangerouslySetInnerHTML={{ __html: sections_html }} />
  </EmailLayout>
}

RecruiterDailyDigest.PreviewProps = { sections_html: "<h2>Nye søkere</h2><ul><li>Inga Inviter</li></ul>" } satisfies RecruiterDigestProps
