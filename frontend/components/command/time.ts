/**
 * The backend stores every timestamp in UTC (`models.utcnow`), but some endpoints serialise
 * them without an offset — `/teams/{id}` standings, `/teams/{id}/roster`, `/trades`,
 * `/scenarios` — while `/data-health` serialises the same instant with `+00:00`. `new Date()`
 * reads an offset-less ISO string as *local* time, so the same NBA.com sync rendered as
 * 4:23 PM in the league-state line and 8:23 PM in the team card on one screen.
 *
 * This marks an offset-less timestamp as UTC before it is parsed. A string that already
 * carries `Z` or `±hh:mm` is returned unchanged.
 */
export function asUtc(iso: string): string;
export function asUtc(iso: string | null | undefined): string | null;
export function asUtc(iso: string | null | undefined): string | null {
  if (!iso) return null;
  // Date-time strings only; a bare date ("2026-08-24") is not an instant.
  if (!/T\d{2}:\d{2}/.test(iso)) return iso;
  return /(Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}Z`;
}
