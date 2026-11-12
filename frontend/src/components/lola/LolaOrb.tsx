type LolaOrbProps = {
  active?: boolean;
  className?: string;
};

/** Uses the actual Lola orb approved for Lion BI, without redrawing its design. */
export function LolaOrb({ active = false, className = '' }: LolaOrbProps) {
  return (
    <div
      className={`lola-orb ${active ? 'lola-orb--active' : ''} ${className}`}
      role="img"
      aria-label="Lola, your AI business assistant"
    >
      <img
        src="/brand/lola-orb-approved.webp"
        alt=""
        className="lola-orb__art"
        draggable={false}
        aria-hidden="true"
      />
    </div>
  );
}
