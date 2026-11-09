import type { OrbState } from '../contract';
import '../lola.css';

interface Props {
  state: OrbState;
  size?: number;
  /** Accessible label. Omit when a visible label sits next to the orb. */
  label?: string;
  className?: string;
}

/** Lola's animated presence. Decorative unless `label` is given. */
export function LolaOrb({ state, size = 44, label, className = '' }: Props) {
  return (
    <span
      className={`lola-orb ${className}`}
      data-state={state}
      data-testid="lola-orb"
      style={{ width: size, height: size, ['--lola-r' as string]: `${size / 2}px` }}
      {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}
    >
      <span className="lola-orb__ring" />
      <span className="lola-orb__ring lola-orb__ring--2" />
      <span className="lola-orb__core" />
      <span className="lola-orb__sheen" />
      <span className="lola-orb__dot" />
      <span className="lola-orb__badge" />
    </span>
  );
}
