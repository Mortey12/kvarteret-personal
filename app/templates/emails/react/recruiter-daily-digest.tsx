import { EmailLayout, Paragraph } from "./components"

export type RecruiterDigestProps = { heading: string; greeting: string; sections_html: string }

// The Python renderer builds the (escaped) section markup; this template owns
// the layout and branding around it.
export default function RecruiterDigest({ heading, greeting, sections_html }: RecruiterDigestProps) {
  return <EmailLayout title={heading} heading={heading} lang="nb">
    <Paragraph>{greeting}</Paragraph>
    <div dangerouslySetInnerHTML={{ __html: sections_html }} />
  </EmailLayout>
}

RecruiterDigest.PreviewProps = {
  heading: "Rekruttering for Barkomiteen",
  greeting: "Hei Ola,",
  sections_html: "<h2>Søkere som venter på svar</h2><ul><li>Inga Inviter</li></ul>",
} satisfies RecruiterDigestProps
