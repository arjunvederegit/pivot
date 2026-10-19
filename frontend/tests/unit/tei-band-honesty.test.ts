/**
 * R9.2 — the TEI band is not an 80% interval, and the interface must not say it is.
 *
 * R9.1's out-of-time backtest measured the band's coverage at 60.8% (development) and 57.2%
 * (holdout) against a nominal 80%. These pins fail if the plain-language wording that qualifies
 * the band is removed, or if the label that implied a probability ("Likely range") comes back.
 * They read the source because the wording lives in two presentation files and a rendered test
 * would need the whole app shell for a sentence.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(join(__dirname, "..", "..", rel), "utf8");

describe("the TEI band is labelled as uncalibrated", () => {
  const impact = read("components/impact.tsx");
  const methodology = read("app/methodology/page.tsx");

  it("does not call the band a 'Likely range'", () => {
    expect(impact).not.toContain(">Likely range<");
    expect(methodology).not.toContain('term: "Likely range"');
  });

  it("says, where the band is explained, that it is not a calibrated 80% interval", () => {
    expect(impact).toMatch(/not a\s+calibrated 80% interval/);
    expect(methodology).toMatch(/not a calibrated\s+80% interval/);
  });

  it("states the measured coverage it was tested at", () => {
    expect(impact).toContain("57–61%");
    expect(methodology).toContain("57–61%");
  });
});
