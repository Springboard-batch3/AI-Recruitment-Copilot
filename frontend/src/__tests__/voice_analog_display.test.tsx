import React from "react";
import { render, screen, cleanup } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { VoiceAnalogDisplay } from "../components/candidate/VoiceAnalogDisplay";

describe("VoiceAnalogDisplay Component", () => {
  let mockRafId = 1;
  let rafCallbacks: Array<(time: number) => void> = [];

  beforeEach(() => {
    rafCallbacks = [];
    vi.stubGlobal("requestAnimationFrame", (cb: (time: number) => void) => {
      rafCallbacks.push(cb);
      return mockRafId++;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders voice analog display with default 28 dynamic bars", () => {
    render(<VoiceAnalogDisplay isActive={true} />);

    const visualizer = screen.getByTestId("voice-analog-display");
    expect(visualizer).toBeInTheDocument();
    expect(visualizer.getAttribute("aria-label")).toBe("Dynamic audio voice visualizer");

    // Check that 28 equalizer bar elements were rendered
    expect(visualizer.children.length).toBe(28);
  });

  it("renders custom number of bars and custom height when provided", () => {
    render(<VoiceAnalogDisplay barCount={32} height={60} barColor="#2563eb" />);

    const visualizer = screen.getByTestId("voice-analog-display");
    expect(visualizer.children.length).toBe(32);
    expect(visualizer.style.height).toBe("60px");
  });

  it("sets resting state opacity and heights when isActive is false", () => {
    render(<VoiceAnalogDisplay isActive={false} barCount={16} />);

    const visualizer = screen.getByTestId("voice-analog-display");
    const firstBar = visualizer.children[0] as HTMLElement;
    expect(firstBar.style.opacity).toBe("0.45");
  });

  it("executes animation frame update and handles stream audio node lifecycle", () => {
    const cancelSpy = vi.fn();
    vi.stubGlobal("cancelAnimationFrame", cancelSpy);

    const mockTrack = { stop: vi.fn(), kind: "audio" };
    const mockStream = {
      getTracks: () => [mockTrack],
    } as unknown as MediaStream;

    const { unmount } = render(<VoiceAnalogDisplay stream={mockStream} isActive={true} />);

    // Step through the rAF callback
    expect(rafCallbacks.length).toBeGreaterThan(0);
    rafCallbacks[0](100);

    // Verify clean unmount cancels animation frame
    unmount();
    expect(cancelSpy).toHaveBeenCalled();
  });
});
