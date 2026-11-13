import { useEffect, useState } from 'react';

/**
 * Reads real microphone RMS levels only while user-initiated dictation is on.
 * Microphone tracks and the AudioContext are stopped on every cleanup path.
 * No audio samples leave the device or enter application state.
 */
export function useMicLevel(enabled: boolean): number {
  const [level, setLevel] = useState(0);

  useEffect(() => {
    if (!enabled || typeof navigator === 'undefined' ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof AudioContext === 'undefined') return;

    let cancelled = false;
    let frame = 0;
    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let analyser: AnalyserNode | null = null;
    let smoothed = 0;
    let lastSample = 0;

    const start = async () => {
      try {
        const captured = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false,
        });
        if (cancelled) {
          captured.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = captured;
        context = new AudioContext();
        source = context.createMediaStreamSource(stream);
        analyser = context.createAnalyser();
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.65;
        source.connect(analyser);
        const data = new Float32Array(analyser.fftSize);

        const measure = (time: number) => {
          if (cancelled || !analyser) return;
          frame = requestAnimationFrame(measure);
          if (time - lastSample < 32) return;
          lastSample = time;
          analyser.getFloatTimeDomainData(data);
          let energy = 0;
          for (let i = 0; i < data.length; i += 1) energy += data[i] * data[i];
          const rms = Math.sqrt(energy / data.length);
          const actual = Math.max(0, Math.min(1, (rms - 0.014) * 7));
          smoothed += (actual - smoothed) * (actual > smoothed ? 0.48 : 0.18);
          setLevel(smoothed < 0.008 ? 0 : smoothed);
        };
        frame = requestAnimationFrame(measure);
      } catch {
        // Recognition may still operate independently; never block dictation.
        if (!cancelled) setLevel(0);
      }
    };

    void start();
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      source?.disconnect();
      analyser?.disconnect();
      stream?.getTracks().forEach((track) => track.stop());
      if (context) void context.close().catch(() => undefined);
      setLevel(0);
    };
  }, [enabled]);

  return enabled ? level : 0;
}
