import React, { useEffect, useRef } from "react";

export interface VoiceAnalogDisplayProps {
  /**
   * Active media stream from candidate's microphone
   */
  stream?: MediaStream | null;
  /**
   * Whether the audio capture / speaking is active
   */
  isActive?: boolean;
  /**
   * Number of equalizer bars (default: 28)
   */
  barCount?: number;
  /**
   * Height of the visualizer container in px (default: 48)
   */
  height?: number;
  /**
   * Primary color or gradient for the bars
   */
  barColor?: string;
  /**
   * Additional custom CSS class names
   */
  className?: string;
  /**
   * Optional custom style override
   */
  style?: React.CSSProperties;
}

export function VoiceAnalogDisplay({
  stream,
  isActive = true,
  barCount = 28,
  height = 44,
  barColor = "#2563eb",
  className = "",
  style,
}: VoiceAnalogDisplayProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const barsRef = useRef<(HTMLDivElement | null)[]>([]);
  const animationFrameRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);

  // Initialize bars array
  const bars = Array.from({ length: barCount }, (_, i) => i);

  useEffect(() => {
    if (!isActive) {
      // Reset all bars to minimal resting state
      barsRef.current.forEach((bar, idx) => {
        if (bar) {
          const centerDist = Math.abs(idx - (barCount - 1) / 2) / ((barCount - 1) / 2);
          const baseHeight = Math.max(4, Math.round(12 * (1 - centerDist * 0.6)));
          bar.style.height = `${baseHeight}px`;
          bar.style.opacity = "0.45";
        }
      });
      return;
    }

    let isAudioContextRunning = false;
    let time = 0;

    // Try setting up Web Audio API if stream is present and supported
    if (stream && typeof window !== "undefined" && (window.AudioContext || (window as any).webkitAudioContext)) {
      try {
        const AudioCtxClass = window.AudioContext || (window as any).webkitAudioContext;
        const ctx = new AudioCtxClass();
        audioContextRef.current = ctx;

        const analyser = ctx.createAnalyser();
        analyser.fftSize = 64; // 32 frequency bins
        analyser.smoothingTimeConstant = 0.78;
        analyserRef.current = analyser;

        const source = ctx.createMediaStreamSource(stream);
        source.connect(analyser);
        sourceNodeRef.current = source;

        isAudioContextRunning = true;
      } catch (err) {
        console.warn("Could not initialize Web Audio Analyser, falling back to dynamic wave:", err);
      }
    }

    const frequencyData = new Uint8Array(32);

    const updateVisualizer = () => {
      time += 0.08;

      let hasLiveData = false;
      if (isAudioContextRunning && analyserRef.current) {
        try {
          analyserRef.current.getByteFrequencyData(frequencyData);
          // Check if there is actual sound input
          const sum = frequencyData.reduce((acc, val) => acc + val, 0);
          if (sum > 10) {
            hasLiveData = true;
          }
        } catch {
          hasLiveData = false;
        }
      }

      const half = (barCount - 1) / 2;
      const minHeight = 4;
      const maxHeight = height - 6;

      barsRef.current.forEach((bar, idx) => {
        if (!bar) return;

        // Symmetric distance from center (0 at center, 1 at ends)
        const centerDist = Math.abs(idx - half) / half;
        const bellCurve = Math.cos((centerDist * Math.PI) / 2); // 1 at center, 0 at ends

        let barH = minHeight;

        if (hasLiveData) {
          // Map index to frequency bins with human vocal emphasis (low-mid frequencies)
          const binIndex = Math.min(
            frequencyData.length - 1,
            Math.floor((1 - centerDist) * 16)
          );
          const rawVal = frequencyData[binIndex] || 0;
          const normalized = rawVal / 255; // 0 to 1

          // Calculate height with bell curve shape and dynamic jitter
          const dynamicBoost = Math.sin(time * 3 + idx * 0.4) * 0.1;
          const level = Math.min(1, Math.max(0, normalized * 1.5 + dynamicBoost));
          barH = minHeight + level * (maxHeight - minHeight) * (0.35 + 0.65 * bellCurve);
          bar.style.opacity = `${0.65 + level * 0.35}`;
        } else {
          // Dynamic organic waveform fallback (e.g. while recording, active or simulated)
          const wave1 = Math.sin(time * 2.2 + idx * 0.32);
          const wave2 = Math.cos(time * 1.5 - idx * 0.25);
          const wave3 = Math.sin(time * 3.8 + idx * 0.5) * 0.5;
          const combined = (wave1 + wave2 + wave3 + 2.5) / 5; // 0 to 1

          const amplitude = (0.25 + 0.75 * combined) * bellCurve;
          barH = minHeight + amplitude * (maxHeight - minHeight);
          bar.style.opacity = `${0.6 + amplitude * 0.4}`;
        }

        bar.style.height = `${Math.round(barH)}px`;
      });

      animationFrameRef.current = requestAnimationFrame(updateVisualizer);
    };

    animationFrameRef.current = requestAnimationFrame(updateVisualizer);

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      if (sourceNodeRef.current) {
        try {
          sourceNodeRef.current.disconnect();
        } catch {
          // ignore cleanup errors
        }
        sourceNodeRef.current = null;
      }
      if (audioContextRef.current) {
        try {
          audioContextRef.current.close();
        } catch {
          // ignore cleanup errors
        }
        audioContextRef.current = null;
      }
    };
  }, [stream, isActive, barCount, height]);

  return (
    <div
      ref={containerRef}
      className={`voice-analog-display ${className}`}
      data-testid="voice-analog-display"
      role="img"
      aria-label="Dynamic audio voice visualizer"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        height: `${height}px`,
        padding: "0 16px",
        backgroundColor: "rgba(239, 246, 255, 0.7)",
        border: "1px solid #dbeafe",
        borderRadius: "24px",
        boxShadow: "inset 0 1px 2px rgba(37, 99, 235, 0.05)",
        gap: "3px",
        ...style,
      }}
    >
      {bars.map((idx) => {
        const centerDist = Math.abs(idx - (barCount - 1) / 2) / ((barCount - 1) / 2);
        const initialHeight = Math.max(4, Math.round(14 * (1 - centerDist * 0.6)));

        return (
          <div
            key={idx}
            ref={(el) => {
              barsRef.current[idx] = el;
            }}
            style={{
              width: "3.5px",
              height: `${initialHeight}px`,
              backgroundColor: barColor,
              backgroundImage: "linear-gradient(180deg, #3b82f6 0%, #1d4ed8 100%)",
              borderRadius: "4px",
              transition: "height 0.06s ease-out, opacity 0.08s ease",
              transformOrigin: "center center",
              opacity: isActive ? 0.9 : 0.45,
            }}
          />
        );
      })}
    </div>
  );
}
