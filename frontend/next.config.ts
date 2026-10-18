import type { NextConfig } from "next";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // The /api/v1 rewrite is a proxy, and Next aborts proxied requests after 30 s by default
    // with a bare 500. An Ask Pivot answer from a local model routinely takes 20-40 s (found
    // in the live UI test: the backend finished in 30.2 s, the browser got a 500). 180 s sits
    // above the backend's own 150 s answer budget, so the backend's enveloped 504 always wins.
    proxyTimeout: 180_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${API_URL}/api/v1/:path*`,
      },
    ];
  },
  async redirects() {
    // Module routes were renamed to match the product's own vocabulary. Every
    // previously published URL still resolves; Next forwards query strings, so
    // shared Trade Evaluator links (?state=…) survive the move.
    return [
      { source: "/trade-machine", destination: "/trade-evaluator", permanent: false },
      { source: "/trade-builder", destination: "/trade-evaluator", permanent: false },
      { source: "/compare", destination: "/strategy-lab", permanent: false },
      { source: "/player-lab", destination: "/player-explorer", permanent: false },
      { source: "/team-hub", destination: "/team-outlook", permanent: false },
      { source: "/team-hub/:teamId", destination: "/team-outlook/:teamId", permanent: false },
      { source: "/teams/:teamId", destination: "/team-outlook/:teamId", permanent: false },
      { source: "/decision-room", destination: "/team-outlook", permanent: false },
      { source: "/cap-lab", destination: "/salary-cap-center", permanent: false },
      { source: "/data-status", destination: "/data-health", permanent: false },
    ];
  },
};

export default nextConfig;
