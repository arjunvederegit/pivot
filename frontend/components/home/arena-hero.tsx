/**
 * The arena, after hours — an illustrated broadcast view used behind the homepage hero.
 *
 * Drawn, not photographed: there is no licensed arena image in the repository, and a
 * rendered scene keeps the page self-contained. Everything here is atmosphere — the
 * bowl, the light ring, the center-hung board, the hardwood in perspective — and nothing
 * in it is data. It is static by design: the brief rules out animated backgrounds.
 */

/** Points along the upper arc of the light ring. */
function ringLights(count: number) {
  const out: { x: number; y: number; r: number }[] = [];
  for (let i = 0; i < count; i++) {
    const t = Math.PI + (Math.PI * (i + 0.5)) / count; // upper half of the ellipse
    const x = 800 + Math.cos(t) * 700;
    const y = 112 + Math.sin(t) * 64;
    out.push({ x, y, r: i % 4 === 0 ? 2.6 : 1.7 });
  }
  return out;
}

const LIGHTS = ringLights(44);
const CONES = [300, 520, 800, 1080, 1300];
/** Horizontal floorboard seams, spaced wider as they come toward the camera. */
const PLANKS = Array.from({ length: 22 }, (_, i) => 452 + Math.pow(i, 1.55) * 3.4);

export function ArenaScene({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 1600 720"
      preserveAspectRatio="xMidYMid slice"
      className={className}
      aria-hidden
      focusable="false"
    >
      <defs>
        <radialGradient id="arena-air" cx="50%" cy="34%" r="70%">
          <stop offset="0%" stopColor="#1f2724" />
          <stop offset="45%" stopColor="#121614" />
          <stop offset="100%" stopColor="#0b0e0d" />
        </radialGradient>
        <linearGradient id="arena-floor" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#7a5634" />
          <stop offset="45%" stopColor="#5b3f26" />
          <stop offset="100%" stopColor="#2a1e14" />
        </linearGradient>
        <linearGradient id="arena-cone" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#f6efe0" stopOpacity="0.24" />
          <stop offset="100%" stopColor="#f0eee8" stopOpacity="0" />
        </linearGradient>
        <radialGradient id="arena-pool" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#f8e6c2" stopOpacity="0.38" />
          <stop offset="100%" stopColor="#f6e7c8" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="arena-board" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stopColor="#86c9a5" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#86c9a5" stopOpacity="0" />
        </radialGradient>
        <filter id="arena-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="2.4" />
        </filter>
        <filter id="arena-haze" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="18" />
        </filter>
        <clipPath id="arena-floor-clip">
          <path d="M440 440 L1160 440 L1900 720 L-300 720 Z" />
        </clipPath>
      </defs>

      {/* the air in the building */}
      <rect width="1600" height="720" fill="url(#arena-air)" />

      {/* seat tiers: rows of faint specks rising into the dark */}
      <g fill="none" stroke="#f0eee8" strokeLinecap="round">
        {Array.from({ length: 11 }).map((_, i) => (
          <ellipse
            key={i}
            cx="800"
            cy="470"
            rx={520 + i * 78}
            ry={150 + i * 26}
            strokeOpacity={0.05 + (i % 3) * 0.018}
            strokeWidth={1.4}
            strokeDasharray={`1 ${5 + (i % 4)}`}
          />
        ))}
        <ellipse cx="800" cy="470" rx="470" ry="120" strokeOpacity="0.08" strokeWidth="1" />
      </g>

      {/* light cones falling to the floor */}
      <g filter="url(#arena-haze)">
        {CONES.map((x) => (
          <path
            key={x}
            d={`M${x - 18} 120 L${x + 18} 120 L${800 + (x - 800) * 0.55 + 120} 520 L${800 + (x - 800) * 0.55 - 120} 520 Z`}
            fill="url(#arena-cone)"
          />
        ))}
      </g>

      {/* the light ring */}
      <ellipse cx="800" cy="112" rx="700" ry="64" fill="none" stroke="#f0eee8" strokeOpacity="0.06" />
      <g fill="#f6efe0">
        {LIGHTS.map((l, i) => (
          <circle key={i} cx={l.x} cy={l.y} r={l.r * 2.2} opacity="0.18" filter="url(#arena-glow)" />
        ))}
        {LIGHTS.map((l, i) => (
          <circle key={`c${i}`} cx={l.x} cy={l.y} r={l.r} opacity="0.85" />
        ))}
      </g>

      {/* the center-hung board */}
      <g>
        <path d="M720 0V96M880 0V96" stroke="#f0eee8" strokeOpacity="0.08" />
        <ellipse cx="800" cy="150" rx="190" ry="70" fill="url(#arena-board)" />
        <rect x="712" y="96" width="176" height="92" rx="6" fill="#141a17" stroke="#f0eee8" strokeOpacity="0.14" />
        <rect x="724" y="108" width="152" height="54" rx="3" fill="#1b2420" />
        <rect x="724" y="108" width="152" height="54" rx="3" fill="#86c9a5" opacity="0.14" />
        <path d="M740 128h48M740 142h30M812 128h48M830 142h30" stroke="#f0eee8" strokeOpacity="0.28" strokeWidth="3" strokeLinecap="round" />
        <path d="M740 176h120" stroke="#f0eee8" strokeOpacity="0.12" />
      </g>

      {/* the floor */}
      <g clipPath="url(#arena-floor-clip)">
        <rect x="-300" y="440" width="2200" height="280" fill="url(#arena-floor)" />
        <g stroke="#0b0e0d" strokeOpacity="0.3" strokeWidth="1">
          {PLANKS.map((y, i) => (
            <path key={i} d={`M-300 ${y.toFixed(1)}H1900`} />
          ))}
        </g>
        {/* pools of light on the wood */}
        {CONES.map((x) => (
          <ellipse
            key={`pool${x}`}
            cx={800 + (x - 800) * 0.55}
            cy="520"
            rx="160"
            ry="34"
            fill="url(#arena-pool)"
          />
        ))}
        {/* varnish sheen across the far half */}
        <rect x="-300" y="440" width="2200" height="120" fill="#f6efe0" opacity="0.05" />
        {/* court lines, in broadcast perspective */}
        <g fill="none" stroke="#f0eee8" strokeOpacity="0.62" strokeWidth="1.8">
          <path d="M440 440 L1160 440" />
          <path d="M800 440 L800 720" />
          <ellipse cx="800" cy="548" rx="132" ry="34" />
          <ellipse cx="800" cy="548" rx="42" ry="11" strokeOpacity="0.3" />
          {/* left end: key and three-point arc */}
          <path d="M440 440 L262 720" strokeOpacity="0.3" />
          <path d="M392 476 L560 476 L520 592 L318 592" />
          <path d="M392 476 C640 490 700 600 300 650" strokeOpacity="0.3" />
          {/* right end */}
          <path d="M1160 440 L1338 720" strokeOpacity="0.3" />
          <path d="M1208 476 L1040 476 L1080 592 L1282 592" />
          <path d="M1208 476 C960 490 900 600 1300 650" strokeOpacity="0.3" />
        </g>
      </g>

      {/* atmosphere: haze over the bowl, then the vignette that seats the text */}
      <ellipse cx="800" cy="300" rx="760" ry="200" fill="#f0eee8" opacity="0.025" filter="url(#arena-haze)" />
    </svg>
  );
}
