import { useId } from 'react';

type LolaOrbProps = {
  active?: boolean;
  className?: string;
};

/** Lightweight vector rendering of Lola's approved blue-violet-gold orb. */
export function LolaOrb({ active = false, className = '' }: LolaOrbProps) {
  const uid = useId().replace(/:/g, '');
  const shell = `lola-shell-${uid}`;
  const core = `lola-core-${uid}`;
  const blue = `lola-blue-${uid}`;
  const violet = `lola-violet-${uid}`;
  const gold = `lola-gold-${uid}`;
  const glow = `lola-glow-${uid}`;
  return (
    <div className={`lola-orb ${active ? 'lola-orb--active' : ''} ${className}`} role="img" aria-label="Lola, your AI business assistant">
      <svg viewBox="0 0 360 360" className="lola-orb__art" aria-hidden="true">
        <defs>
          <radialGradient id={shell} cx="38%" cy="20%" r="83%">
            <stop offset="0%" stopColor="#5879fc" stopOpacity=".7" />
            <stop offset="40%" stopColor="#241a74" stopOpacity=".92" />
            <stop offset="79%" stopColor="#140c47" />
            <stop offset="100%" stopColor="#0a153b" />
          </radialGradient>
          <radialGradient id={core} cx="50%" cy="51%" r="55%">
            <stop offset="0%" stopColor="#ffc985" stopOpacity=".95" />
            <stop offset="18%" stopColor="#735fe7" stopOpacity=".68" />
            <stop offset="62%" stopColor="#283aaf" stopOpacity=".2" />
            <stop offset="100%" stopColor="#0f174c" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={blue} x1="8%" y1="10%" x2="95%" y2="82%">
            <stop offset="0%" stopColor="#a4efff" />
            <stop offset="38%" stopColor="#4fa5ff" />
            <stop offset="70%" stopColor="#7670ff" />
            <stop offset="100%" stopColor="#d7c5ff" />
          </linearGradient>
          <linearGradient id={violet} x1="3%" y1="86%" x2="92%" y2="5%">
            <stop offset="0%" stopColor="#a26aff" />
            <stop offset="43%" stopColor="#edc7ff" />
            <stop offset="70%" stopColor="#8e5afa" />
            <stop offset="100%" stopColor="#665efb" />
          </linearGradient>
          <linearGradient id={gold} x1="5%" y1="85%" x2="90%" y2="20%">
            <stop offset="0%" stopColor="#ffd4b1" />
            <stop offset="42%" stopColor="#ffc770" />
            <stop offset="68%" stopColor="#fce2bf" />
            <stop offset="100%" stopColor="#f4a978" />
          </linearGradient>
          <filter id={glow} x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="8" />
          </filter>
          <clipPath id={`lola-clip-${uid}`}><circle cx="180" cy="180" r="137" /></clipPath>
        </defs>
        <circle cx="180" cy="180" r="148" fill="#5764ff" opacity=".22" filter={`url(#${glow})`} />
        <circle cx="180" cy="180" r="137" fill={`url(#${shell})`} stroke="#9cb6ff" strokeOpacity=".72" strokeWidth="2" />
        <g clipPath={`url(#lola-clip-${uid})`}>
          <ellipse cx="180" cy="170" rx="120" ry="110" fill={`url(#${core})`} />
          <path d="M44 180C78 43 196 28 297 103C324 126 310 184 265 198C208 214 136 197 98 248C68 284 98 317 146 323" fill="none" stroke={`url(#${blue})`} strokeWidth="34" opacity=".42" />
          <path d="M32 202C83 127 137 119 201 117C279 113 310 87 337 50" fill="none" stroke={`url(#${blue})`} strokeWidth="26" opacity=".65" />
          <path d="M35 274C83 166 196 222 263 164C309 122 308 103 330 77" fill="none" stroke={`url(#${violet})`} strokeWidth="32" opacity=".64" />
          <path d="M46 265C107 181 170 223 224 207C295 189 319 142 350 126" fill="none" stroke={`url(#${gold})`} strokeWidth="10" opacity=".88" />
          <path d="M51 278C103 203 151 257 236 250C279 247 312 219 340 192" fill="none" stroke={`url(#${violet})`} strokeWidth="29" opacity=".58" />
          <path d="M68 79C156 23 260 54 307 113" fill="none" stroke="#a5dbff" strokeWidth="8" opacity=".7" />
          <path d="M53 254C84 333 226 345 295 273" fill="none" stroke="#e5a5ff" strokeWidth="8" opacity=".62" />
        </g>
        <circle cx="180" cy="180" r="136" fill="none" stroke="#d8c4ff" strokeWidth="1.5" opacity=".72" />
        <path d="M70 89A136 136 0 0 1 285 96" fill="none" stroke="#e5f5ff" strokeWidth="7" strokeLinecap="round" opacity=".6" />
        <ellipse cx="181" cy="329" rx="75" ry="8" fill="#9671ff" opacity=".18" filter={`url(#${glow})`} />
      </svg>
    </div>
  );
}
