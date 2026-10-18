/**
 * Build-time feature flags.
 *
 * `NEXT_PUBLIC_*` variables are **inlined when the bundle is built** (or when `next dev`
 * starts): Next replaces every `process.env.NEXT_PUBLIC_X` with the literal value it saw,
 * in both the server and the browser bundles. So a flag here is a property of the build,
 * not of the request — flipping it on a running server does nothing until a rebuild or a
 * dev-server restart. That is the point for an experimental surface: a production build
 * made without the flag carries no nav entry and serves the app's ordinary 404 at the
 * route, rather than a page that explains a feature nobody turned on.
 *
 * The frontend flag only decides whether the page *exists*. Whether the assistant can
 * answer is the backend's call (`ASSISTANT_ENABLED` there), and the page reads it at
 * runtime from `GET /api/v1/assistant/status`. Both must be on for a working chat.
 */

/** Ask Pivot / Pivot Intelligence — the experimental research assistant (fictional data). */
export const ASSISTANT_ENABLED = process.env.NEXT_PUBLIC_ASSISTANT_ENABLED === "true";
