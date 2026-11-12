import { useId } from 'react';

type LolaGlyphProps = {
  size?: number;
  className?: string;
};

/** Bespoke orbital insignia for Lola. Not a generic sparkle or robot icon. */
export function LolaGlyph({ size = 18, className = '' }: LolaGlyphProps) {
  const id = useId().replace(/:/g, '');
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 36 36"
      fill="none"
      className={className}
    >
      <defs>
        <linearGradient id={`${id}-orb`} x1="2" y1="3" x2="33" y2="33" gradientUnits="userSpaceOnUse">
          <stop stopColor="#6CD9FF" />
          <stop offset=".44" stopColor="#9D85F8" />
          <stop offset=".78" stopColor="#D4B2FC" />
          <stop offset="1" stopColor="#F4CD91" />
        </linearGradient>
        <linearGradient id={`${id}-ribbon`} x1="4" y1="27" x2="31" y2="9" gradientUnits="userSpaceOnUse">
          <stop stopColor="#EBC38A" />
          <stop offset=".42" stopColor="#F8D7AE" />
          <stop offset="1" stopColor="#81CFFF" />
        </linearGradient>
      </defs>
      <circle cx="18" cy="18" r="15.3" stroke={`url(#${id}-orb)`} strokeWidth="1.65" />
      <path d="M6.9 14.4C9.9 8.7 16.4 6.5 22.2 8.1c4 1.1 6.1 3.7 7.3 6.8-6.2-.1-10.9 3.8-15 8.8-3.2 1.8-5.2 1.9-7.7.6" stroke={`url(#${id}-orb)`} strokeWidth="2" strokeLinecap="round" />
      <path d="M6.5 24.8c4.6-6.5 9.3-6.8 15.3-9.2 4.7-1.8 6-4.9 7.1-6.7" stroke={`url(#${id}-ribbon)`} strokeWidth="2.3" strokeLinecap="round" />
      <circle cx="17.5" cy="18.5" r="2.9" fill="#F4CB92" />
      <circle cx="17.5" cy="18.5" r="5" stroke="#E7C68E" opacity=".24" />
    </svg>
  );
}
