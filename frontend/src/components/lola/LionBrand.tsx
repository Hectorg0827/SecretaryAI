import { Link } from 'react-router-dom';

/** Lion BI lockup: premium gold lion silhouette and high-contrast wordmark. */
export function LionBrand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className="lionbi-brand" aria-label="Lion BI home">
      <svg className="lionbi-brand__mark" viewBox="0 0 96 96" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id="lion-mark-gold" x1="16" y1="6" x2="72" y2="90" gradientUnits="userSpaceOnUse">
            <stop stopColor="#fff1ba" />
            <stop offset=".38" stopColor="#e6b765" />
            <stop offset=".74" stopColor="#c79242" />
            <stop offset="1" stopColor="#f7d691" />
          </linearGradient>
        </defs>
        <path d="M21 32C27 10 53 3 74 15C49 6 31 17 21 32ZM13 45C18 23 39 13 57 14C29 30 28 40 20 58C17 53 15 49 13 45ZM17 67C20 47 31 31 47 22C38 43 31 65 33 86C24 79 19 73 17 67Z" fill="url(#lion-mark-gold)" />
        <path d="M35 27C49 13 64 15 74 28C69 27 64 25 60 25L55 30C62 27 74 29 79 38L88 50L80 55L79 65L72 75L61 72L55 78C55 65 52 59 43 54C42 44 45 34 51 29C45 30 39 34 35 39Z" fill="url(#lion-mark-gold)" />
        <path d="M68 39L81 41L75 45L67 43Z" fill="#0b1224" />
        <path d="M78 57Q72 63 64 59" stroke="#0b1224" strokeWidth="2.5" strokeLinecap="round" />
        <path d="M40 47C45 58 57 70 57 87C48 80 43 75 39 66C36 74 37 80 40 90C27 78 29 59 40 47Z" fill="url(#lion-mark-gold)" />
        <path d="M8 43A45 45 0 0 1 86 27" stroke="#d4a657" strokeWidth="1.5" opacity=".75" />
      </svg>
      {!compact && (
        <span className="lionbi-brand__copy">
          <span className="lionbi-brand__name">LION <b>BI</b></span>
          <span className="lionbi-brand__subtitle">BUSINESS INTELLIGENCE</span>
        </span>
      )}
    </Link>
  );
}
