/**
 * Development-only availability probe for official NBA headshots — see lib/headshotProbe.ts.
 *
 * `GET /api/headshot-status?ids=2544,201939` -> `{ "available": { "2544": true, "201939": true } }`.
 * Answers 404 whenever the official source is disabled (every production build), so the route
 * cannot be used to reach the CDN from a deployed Pivot. The host is fixed and ids are validated
 * integers, so a caller cannot steer the request anywhere else.
 */

import { officialHeadshotsEnabled } from "@/lib/headshots";
import { parseProbeIds, probeMany } from "@/lib/headshotProbe";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  if (!officialHeadshotsEnabled()) return new Response(null, { status: 404 });
  const ids = parseProbeIds(new URL(request.url).searchParams.get("ids"));
  const available = await probeMany(ids);
  return Response.json({ available }, { headers: { "Cache-Control": "private, max-age=300" } });
}
