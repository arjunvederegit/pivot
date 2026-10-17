"use client";

/**
 * Last-resort boundary.
 *
 * This renders only when the root layout itself failed, which means the fonts, the design
 * tokens in globals.css, the query client and the navigation are all unavailable. So it
 * renders its own document and carries its own styling inline — including the palette,
 * since `var(--signal)` would resolve to nothing here. Everything it needs is in this file;
 * it imports no component and no stylesheet, because anything it imported could be the
 * thing that broke.
 *
 * The colours below are the literal values of the editorial tokens — the near-black
 * ground (#0B0E0D), the charcoal card (#151A18), its hairlines (#222A26 / #2D3531), warm
 * chalk text (#F0EEE8), muted (#A3ACA7) and faint (#808A85) text, the mint accent
 * (#86C9A5) and the understated coral for failure (#E08A77) — duplicated here on purpose.
 * The headline uses Georgia, the system serif closest to the product's editorial face,
 * which cannot be loaded from here.
 *
 * Links are full document loads to `/`: the router and root layout this boundary replaced
 * are the things that failed, so a client-side transition would restore the broken state.
 */

const GROUND = "#0B0E0D";
const CARD = "#151A18";
const RAISED = "#1C2220";
const HAIRLINE_SOFT = "#222A26";
const HAIRLINE = "#2D3531";
const CHALK = "#F0EEE8";
const MUTED = "#A3ACA7";
const FAINT = "#808A85";
const SIGNAL = "#86C9A5";
const CORAL = "#E08A77";
const SANS = "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif";
const SERIF = "Georgia, 'Times New Roman', serif";
const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

const CSS = `
  *, *::before, *::after { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  .ge-btn { cursor: pointer; transition: background-color 150ms ease, border-color 150ms ease, color 150ms ease; }
  .ge-primary:hover { background: #9bd6b6; }
  .ge-secondary:hover { background: #252C29; border-color: #3a433f; }
  .ge-btn:focus-visible {
    outline: 2px solid ${SIGNAL};
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .ge-btn { transition: none; }
  }
`;

const page: React.CSSProperties = {
  minHeight: "100vh",
  margin: 0,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: "2rem 1rem",
  background: GROUND,
  color: CHALK,
  fontFamily: SANS,
  fontSize: "15px",
  lineHeight: 1.55,
  colorScheme: "dark",
};

const card: React.CSSProperties = {
  position: "relative",
  width: "100%",
  maxWidth: "36rem",
  background: CARD,
  color: CHALK,
  border: `1px solid ${HAIRLINE_SOFT}`,
  borderRadius: "12px",
  boxShadow: "0 24px 64px -24px rgba(0,0,0,0.8)",
  overflow: "hidden",
};

const edge: React.CSSProperties = {
  position: "absolute",
  top: 0,
  left: "14px",
  right: "14px",
  height: "1px",
  background: `linear-gradient(90deg, transparent, ${CORAL}, transparent)`,
};

const section: React.CSSProperties = { padding: "1.75rem 1.5rem 1.5rem" };

const eyebrow: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "0.5rem",
  margin: 0,
  fontSize: "11px",
  fontWeight: 500,
  letterSpacing: "0.09em",
  textTransform: "uppercase",
  color: CORAL,
};

const heading: React.CSSProperties = {
  margin: "0.875rem 0 0",
  fontFamily: SERIF,
  fontSize: "2.1rem",
  fontWeight: 400,
  lineHeight: 1.08,
  letterSpacing: "-0.01em",
  color: CHALK,
};

const body: React.CSSProperties = {
  margin: "0.875rem 0 0",
  color: MUTED,
  fontSize: "0.9375rem",
};

const detailLabel: React.CSSProperties = {
  margin: "1.25rem 0 0.5rem",
  fontSize: "12.5px",
  fontWeight: 600,
  color: CHALK,
};

const detailBox: React.CSSProperties = {
  margin: 0,
  padding: "0.75rem 1rem",
  border: `1px solid ${HAIRLINE}`,
  borderRadius: "8px",
  background: GROUND,
  fontFamily: MONO,
  fontSize: "12.5px",
  lineHeight: 1.6,
  color: CHALK,
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const actions: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.5rem",
  padding: "1rem 1.5rem",
  borderTop: `1px solid ${HAIRLINE_SOFT}`,
};

const primaryButton: React.CSSProperties = {
  appearance: "none",
  border: `1px solid ${SIGNAL}`,
  borderRadius: "8px",
  background: SIGNAL,
  color: GROUND,
  font: "inherit",
  fontWeight: 500,
  fontSize: "0.875rem",
  padding: "0.5625rem 1rem",
};

const secondaryLink: React.CSSProperties = {
  border: `1px solid ${HAIRLINE}`,
  borderRadius: "8px",
  background: RAISED,
  color: CHALK,
  textDecoration: "none",
  fontSize: "0.875rem",
  padding: "0.5625rem 1rem",
  display: "inline-block",
};

const footnote: React.CSSProperties = {
  margin: 0,
  padding: "0.875rem 1.5rem",
  borderTop: `1px solid ${HAIRLINE_SOFT}`,
  color: FAINT,
  fontSize: "12.5px",
  lineHeight: 1.6,
};

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const detail = error.message?.trim();

  return (
    <html lang="en">
      <body style={page}>
        <style>{CSS}</style>
        <main style={card}>
          <span aria-hidden="true" style={edge} />
          <div style={section}>
            <p style={eyebrow}>
              <span
                aria-hidden="true"
                style={{ width: 6, height: 6, borderRadius: "50%", background: CORAL, display: "inline-block" }}
              />
              Application error
              <span style={{ marginLeft: "auto", color: FAINT, letterSpacing: "0.02em", textTransform: "none" }}>
                Pivot
              </span>
            </p>

            <h1 style={heading}>Pivot could not start this page</h1>

            <p style={body}>
              The failure happened outside any single view, so the interface around it — navigation,
              fonts, saved state — is not available either. This screen is what is left, and it is
              deliberately plain. No data was changed.
            </p>

            {detail && (
              <>
                <p style={detailLabel}>What failed</p>
                <pre style={detailBox}>{detail}</pre>
              </>
            )}
          </div>

          <div style={actions}>
            <button type="button" className="ge-btn ge-primary" style={primaryButton} onClick={() => reset()}>
              Try again
            </button>
            {/* A plain anchor, not <Link>: a full document load is the point here — the
                router and root layout this boundary replaced are the things that failed, so
                a client-side transition would restore the broken state. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages -- see above: a full document load is deliberate. */}
            <a href="/" className="ge-btn ge-secondary" style={secondaryLink}>
              Reload Command Center
            </a>
          </div>

          <p style={footnote}>
            {error.digest ? (
              <>
                <span style={{ fontWeight: 600, color: MUTED }}>Digest</span>{" "}
                <code style={{ fontFamily: MONO, color: CHALK }}>{error.digest}</code> · quote this when
                reporting the failure. If reloading does not clear it, the frontend build or the API it
                depends on is the place to look.
              </>
            ) : (
              <>
                If reloading does not clear this, the frontend build or the API it depends on is the
                place to look.
              </>
            )}
          </p>
        </main>
      </body>
    </html>
  );
}
