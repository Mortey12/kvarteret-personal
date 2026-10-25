import { brandFontCss } from "./fonts"
import type { CSSProperties, ReactNode } from "react"
import {
  Body, Button, Container, Head, Heading, Html, Img, Link, Preview,
  Tailwind, Text, pixelBasedPreset,
} from "react-email"

const bodyFont = "DM Sans, Arial, Helvetica, sans-serif"
const textStyle: CSSProperties = {
  color: "#26211e", fontFamily: bodyFont, fontSize: "16px",
  lineHeight: "26px", margin: "0 0 18px",
}

export function EmailLayout({ title, heading, preview, lang = "en", children }: {
  title: string; heading: string; preview?: string; lang?: string; children: ReactNode
}) {
  return (
    <Html lang={lang} dir="ltr">
      <Tailwind config={{ presets: [pixelBasedPreset] }}>
        <Head>
          <title>{title}</title>
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
          <style dangerouslySetInnerHTML={{ __html: brandFontCss }} />
        </Head>
        <Body style={{ backgroundColor: "#faf7f2", margin: 0, fontFamily: bodyFont, wordBreak: "break-word" }}>
          {preview && <Preview useTitleTag={false}>{preview}</Preview>}
          <Container style={{ width: "100%", maxWidth: "600px", tableLayout: "fixed", padding: "32px 24px 24px" }}>
            <Img
              src="https://personal.samfunnetibergen.no/static/email/samfunnet-logo-ce8e4542d858.png"
              alt="Studentersamfunnet i Bergen" width="200" height="73"
              style={{ display: "block", border: 0, marginBottom: "56px", maxWidth: "100%", height: "auto" }}
            />
            <Heading as="h1" style={{
              color: "#26211e", fontFamily: "Fraunces, Georgia, Times New Roman, serif",
              fontSize: "36px", lineHeight: "42px", fontWeight: 700,
              letterSpacing: "-1px", margin: "0 0 24px", overflowWrap: "anywhere",
            }}>{heading}</Heading>
            {children}
            <Paragraph muted>Med ❤️ fra e-tjenesten</Paragraph>
          </Container>
        </Body>
      </Tailwind>
    </Html>
  )
}

export function Paragraph({ children, muted = false }: { children: ReactNode; muted?: boolean }) {
  return <Text style={{ ...textStyle, ...(muted ? { color: "#6f655e", fontSize: "14px", lineHeight: "22px" } : {}) }}>{children}</Text>
}

export function Action({ href, children }: { href: string; children: ReactNode }) {
  return <Button href={href} className="box-border" style={{
    backgroundColor: "#db242a", color: "#ffffff", fontFamily: bodyFont,
    fontSize: "16px", lineHeight: "24px", fontWeight: 700,
    border: 0, borderRadius: "6px", padding: "16px 22px",
    margin: "12px 0 32px", maxWidth: "100%", whiteSpace: "normal", textAlign: "center",
  }}>{children}</Button>
}

export function DirectLink({ href }: { href: string }) {
  return <Paragraph muted><Link href={href} style={{
    color: "#b9252b", fontFamily: bodyFont, fontSize: "14px", lineHeight: "22px",
    wordBreak: "break-word", overflowWrap: "anywhere",
  }}>{href}</Link></Paragraph>
}

export function AccessCode({ children }: { children: ReactNode }) {
  return <Text style={{
    ...textStyle, fontFamily: "DM Mono, Courier New, Courier, monospace",
    fontSize: "36px", lineHeight: "44px", fontWeight: 700,
    letterSpacing: "8px", textAlign: "center", color: "#754315",
    padding: "24px 12px", margin: "8px 0 24px",
  }}>{children}</Text>
}
