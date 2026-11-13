import { useId, useRef, useState, type CSSProperties, type PointerEvent } from 'react';
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
 * Lola is drawn as a living, layered optical object rather than a flat image.
 * The CSS animation runs independently of React; only live audio changes its
 * visual energy. The focusable button and all voice state wiring are preserved.
 */
export function LolaOrb({
  state, active = false, audioLevel = 0, micEnabled = false, onActivate, className = '',
}: LolaOrbProps) {
  // Stable, instance-scoped SVG gradient IDs; multiple Lola surfaces can coexist.
  const orbitId = useId().replace(/:/g, '');
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
        <span className="lola-orb__glass">
          <span className="lola-orb__plasma lola-orb__plasma--a" />
          <span className="lola-orb__plasma lola-orb__plasma--b" />
          <span className="lola-orb__plasma lola-orb__plasma--c" />
          <span className="lola-orb__caustic" />
          {/* Signature intertwined sapphire / amber energy — vector paths remain
              sharply rendered and independent of viewport or device density. */}
          <svg className="lola-orb__ribbons" viewBox="0 0 300 300"
            preserveAspectRatio="xMidYMid meet" fill="none" focusable="false" aria-hidden="true">
            <defs>
              <linearGradient id={`${orbitId}-blue`} x1="55" y1="66" x2="246" y2="173" gradientUnits="userSpaceOnUse">
                <stop stopColor="#5378FF" stopOpacity=".06" />
                <stop offset=".18" stopColor="#5EC8FF" />
                <stop offset=".47" stopColor="#8CF2FF" />
                <stop offset=".76" stopColor="#647CFF" />
                <stop offset="1" stopColor="#AD78FF" stopOpacity=".2" />
              </linearGradient>
              <linearGradient id={`${orbitId}-gold`} x1="79" y1="258" x2="229" y2="121" gradientUnits="userSpaceOnUse">
                <stop stopColor="#D874D5" stopOpacity=".1" />
                <stop offset=".27" stopColor="#E3A1E6" />
                <stop offset=".51" stopColor="#FFD3A0" />
                <stop offset=".75" stopColor="#FFE9B2" />
                <stop offset="1" stopColor="#FFAF7A" stopOpacity=".25" />
              </linearGradient>
              <linearGradient id={`${orbitId}-violet`} x1="58" y1="245" x2="238" y2="61" gradientUnits="userSpaceOnUse">
                <stop stopColor="#8C69F2" stopOpacity=".2" />
                <stop offset=".48" stopColor="#C191FF" stopOpacity=".7" />
                <stop offset="1" stopColor="#5D8BFF" stopOpacity=".1" />
              </linearGradient>
              <radialGradient id={`${orbitId}-heart`}>
                <stop stopColor="#FFFFFF" stopOpacity=".98" />
                <stop offset=".15" stopColor="#FFE9A9" stopOpacity=".96" />
                <stop offset=".38" stopColor="#FFA767" stopOpacity=".58" />
                <stop offset=".72" stopColor="#E59D8B" stopOpacity=".12" />
                <stop offset="1" stopColor="#E1A6B4" stopOpacity="0" />
              </radialGradient>
            </defs>
            <g className="lola-orb__ribbons-motion">
              <path className="lola-orb__ribbon-haze lola-orb__ribbon-haze--blue"
                d="M56 174 C43 117 77 69 130 57 C181 44 233 74 240 115 C245 144 212 162 177 159 C145 156 123 166 112 182"
                stroke={`url(#${orbitId}-blue)`} strokeWidth="24" strokeLinecap="round" />
              <path className="lola-orb__ribbon lola-orb__ribbon--blue"
                d="M56 174 C43 117 77 69 130 57 C181 44 233 74 240 115 C245 144 212 162 177 159 C145 156 123 166 112 182"
                stroke={`url(#${orbitId}-blue)`} strokeWidth="8.5" strokeLinecap="round" />
              <path className="lola-orb__ribbon-haze lola-orb__ribbon-haze--gold"
                d="M237 121 C226 147 195 165 155 175 C112 186 88 207 102 233 C122 267 185 257 218 226"
                stroke={`url(#${orbitId}-gold)`} strokeWidth="22" strokeLinecap="round" />
              <path className="lola-orb__ribbon lola-orb__ribbon--gold"
                d="M237 121 C226 147 195 165 155 175 C112 186 88 207 102 233 C122 267 185 257 218 226"
                stroke={`url(#${orbitId}-gold)`} strokeWidth="9" strokeLinecap="round" />
              <path className="lola-orb__ribbon lola-orb__ribbon--violet"
                d="M79 221 C98 261 160 275 211 239 C245 215 258 162 246 118 C232 72 183 50 148 59"
                stroke={`url(#${orbitId}-violet)`} strokeWidth="3.5" strokeLinecap="round" />
              <path className="lola-orb__ribbon-shimmer lola-orb__ribbon-shimmer--blue"
                d="M56 174 C43 117 77 69 130 57 C181 44 233 74 240 115 C245 144 212 162 177 159 C145 156 123 166 112 182"
                stroke="#F0FBFF" strokeWidth="3" strokeDasharray="28 510" strokeLinecap="round" />
              <path className="lola-orb__ribbon-shimmer lola-orb__ribbon-shimmer--gold"
                d="M237 121 C226 147 195 165 155 175 C112 186 88 207 102 233 C122 267 185 257 218 226"
                stroke="#FFF5D8" strokeWidth="3.5" strokeDasharray="22 440" strokeLinecap="round" />
            </g>
            <circle className="lola-orb__heart-light" cx="164" cy="163" r="72"
              fill={`url(#${orbitId}-heart)`} />
          </svg>
          <span className="lola-orb__core" />
          <span className="lola-orb__refraction" />
          <span className="lola-orb__glint" />
        </span>
        <span className="lola-orb__orbit lola-orb__orbit--first" />
        <span className="lola-orb__orbit lola-orb__orbit--second" />
        <span className="lola-orb__orbit lola-orb__orbit--third" />
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
