import { useEffect, useState } from 'react';
import { deriveOrbState, useLolaStore } from '../store';
import type { OrbState } from '../contract';

export function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false));
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  return online;
}

export function useOrbState(): OrbState {
  const online = useOnline();
  const messages = useLolaStore((s) => s.messages);
  const streamingId = useLolaStore((s) => s.streamingId);
  const voiceState = useLolaStore((s) => s.voiceState);
  const workingStep = useLolaStore((s) => s.workingStep);
  return deriveOrbState({ messages, streamingId, voiceState, workingStep }, online);
}
