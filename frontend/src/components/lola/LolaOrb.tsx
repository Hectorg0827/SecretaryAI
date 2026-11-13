import { useRef, useState, type CSSProperties, type PointerEvent } from 'react';
import { useLolaVoiceStore } from '../../stores/lolaVoiceStore';
import { useMicLevel } from '../../hooks/useMicLevel';
import './lola-orb.css';

export type LolaOrbState =
  | 'idle' | 'hover' | 'pressed' | 'listening'
  | 'thinking' | 'speaking' | 'muted' | 'error';

type LolaOrbProps = {
  /** Explicit state takes priority over inferred streaming and voice states. */
  state?: LolaOrbState;
  /** Backward-compatible state used by existing screens. */
  active?: boolean;
  /** Actual microphone or output signal, normalized 0..1. */
  audioLevel?: number;
  micEnabled?: boolean;
  onActivate?: () => void;
  className?: string;
};

const clamp = (value: number) =>
  Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;

/**
 * The approved orb art remains an unmodified transparent image, at its natural
 * aspect ratio. CSS only animates light, depth and interactions around it.
 * No opaque tile, mix-blend-mode, radial clipping or picture substitution.
 */
export function LolaOrb({
  state, active = false, audioLevel = 0, micEnabled = false, onActivate, className = '',
}: LolaOrbProps) {
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);
  const elementRef = useRef<HTMLElement | null>(null);
  // Analyze live audio inside this leaf component, never re-render the entire home at 30 fps.
  const micLevel = useMicLevel(micEnabled);
  const voiceStatus = useLolaVoiceStore((s) => s.status);
  const voiceLevel = useLolaVoiceStore((s) => s.outputLevel);
  const mode: LolaOrbState = state ??
    (voiceStatus === 'speaking' ? 'speaking'
      : active ? 'thinking' : pressed ? 'pressed' : hovered ? 'hover' : 'idle');
  const level = clamp(Math.max(audioLevel, micEnabled ? micLevel : 0, mode === 'speaking' ? voiceLevel : 0));

  const resetTilt = () => {
    const element = elementRef.current;
    if (!element) return;
    element.style.setProperty('--lola-x', '0deg');
    element.style.setProperty('--lola-y', '0deg');
    element.style.setProperty('--lola-spot-x', '50%');
    element.style.setProperty('--lola-spot-y', '50%');
  };

  const onMove = (event: PointerEvent<HTMLElement>) => {
    const element = event.currentTarget;
    if (event.pointerType === 'touch' && !pressed) return;
    const box = element.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const x = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const y = Math.max(0, Math.min(1, (event.clientY - box.top) / box.height));
    element.style.setProperty('--lola-x', `${((0.5 - y) * 7).toFixed(2)}deg`);
    element.style.setProperty('--lola-y', `${((x - 0.5) * 7).toFixed(2)}deg`);
    element.style.setProperty('--lola-spot-x', `${(x * 100).toFixed(1)}%`);
    element.style.setProperty('--lola-spot-y', `${(y * 100).toFixed(1)}%`);
  };

  const visual = (
    <>
      <span className="lola-orb__aura" aria-hidden="true" />
      <span className="lola-orb__body" aria-hidden="true">
        <img src="/brand/lola-orb-approved.png" className="lola-orb__art"
          alt="" draggable={false} decoding="async" />
        <span className="lola-orb__refraction" />
        <span className="lola-orb__glint" />
      </span>
      <span className="lola-orb__focus" aria-hidden="true" />
      <span className="lola-orb__status sr-only">Lola is {mode}</span>
    </>
  );

  const classes = `lola-orb lola-orb--${mode} ${onActivate ? 'lola-orb--interactive' : ''} ${className}`;
  const css = { '--lola-audio': String(level) } as CSSProperties;

  if (onActivate) {
    return (
      <button ref={(element) => { elementRef.current = element; }} type="button"
        className={classes} style={css}
        data-state={mode}
        aria-label={mode === 'listening' ? 'Lola is listening' : 'Open Lola conversation'}
        onClick={onActivate}
        onPointerEnter={() => setHovered(true)}
        onPointerMove={onMove}
        onPointerLeave={() => { setHovered(false); setPressed(false); resetTilt(); }}
        onPointerDown={(event) => { setPressed(true); onMove(event); }}
        onPointerUp={() => setPressed(false)}
        onPointerCancel={() => { setPressed(false); resetTilt(); }}>
        {visual}
      </button>
    );
  }

  return (
    <div ref={(element) => { elementRef.current = element; }}
      className={classes} style={css} data-state={mode}
      role="img" aria-label={`Lola assistant — ${mode}`}>
      {visual}
    </div>
  );
}
