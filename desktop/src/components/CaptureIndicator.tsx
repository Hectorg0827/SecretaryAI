import React, { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

/**
 * Persistent, unmistakable banner shown whenever a screen-capture (Computer Use)
 * session is active. Polls the native `computer_use_active` command and lets the
 * user stop capture immediately. Required visibility/consent control (#10).
 */
export default function CaptureIndicator() {
  const [active, setActive] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const a = await invoke<boolean>('computer_use_active');
        if (alive) setActive(Boolean(a));
      } catch {
        if (alive) setActive(false);
      }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!active) return null;

  return (
    <div style={styles.bar} role="status" aria-live="assertive">
      <span style={styles.dot} aria-hidden="true" />
      <span style={styles.text}>SecretaryAI is viewing your screen</span>
      <button
        style={styles.stop}
        onClick={() => invoke('deactivate_computer_use').catch(() => {})}
      >
        Stop
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    position: 'fixed',
    top: 0,
    left: 0,
    right: 0,
    zIndex: 2147483647,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    background: '#b91c1c',
    color: '#fff',
    padding: '6px 12px',
    fontFamily: "'Inter', system-ui, sans-serif",
    fontSize: 13,
    fontWeight: 600,
    boxShadow: '0 1px 6px rgba(0,0,0,0.4)',
  },
  dot: { width: 10, height: 10, borderRadius: '50%', background: '#fff' },
  text: { letterSpacing: '0.02em' },
  stop: {
    background: 'rgba(255,255,255,0.18)',
    border: '1px solid rgba(255,255,255,0.5)',
    color: '#fff',
    borderRadius: 6,
    padding: '3px 10px',
    fontSize: 12,
    cursor: 'pointer',
  },
};
