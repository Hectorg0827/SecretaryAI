import { Link } from 'react-router-dom';

/** Approved Lion BI lion emblem with the matching premium brand wordmark. */
export function LionBrand({ compact = false }: { compact?: boolean }) {
  return (
    <Link to="/" className="lionbi-brand" aria-label="Lion BI home">
      <img
        src="/brand/lion-bi-emblem-approved.webp"
        alt=""
        aria-hidden="true"
        draggable={false}
        className="lionbi-brand__mark"
      />
      {!compact && (
        <span className="lionbi-brand__copy">
          <span className="lionbi-brand__name">LION <b>BI</b></span>
          <span className="lionbi-brand__subtitle">BUSINESS INTELLIGENCE</span>
        </span>
      )}
    </Link>
  );
}
