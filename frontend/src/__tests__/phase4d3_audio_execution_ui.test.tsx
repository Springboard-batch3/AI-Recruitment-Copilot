import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { CandidateInterviewWorkspace } from "../components/candidate/CandidateInterviewWorkspace";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("Phase 4D.3 Frontend: Local Audio Interview Execution UI", () => {
  let mockGetUserMedia: any;
  let mockAudioPlay: any;
  let mockAudioPause: any;

  const mockCapabilities = {
    speech_to_text_available: true,
    text_to_speech_available: true,
    microphone_required: true,
    secure_context_required: true,
    max_recording_seconds: 120,
    max_upload_bytes: 10485760,
    unavailable_reason: null,
  };

  const mockAudioSession = {
    session_id: "sess-aud-1",
    assignment_id: "rel-aud-1",
    job_id: "job-101",
    job_title: "Lead Audio Engineer",
    job_revision: 1,
    interview_mode: "AUDIO" as const,
    status: "IN_PROGRESS" as const,
    started_at: new Date().toISOString(),
    total_questions: 1,
    answered_questions: 0,
    questions: [
      {
        slot_id: "slot-aud-1",
        question_format: "CONVERSATIONAL" as const,
        question_text: "How do you minimize audio latency in real-time embedded systems?",
        competency: "Audio Systems",
        options: [],
        is_supplemental: false,
        saved_text_answer: null,
        saved_selected_option_id: null,
        is_answered: false,
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();

    mockAudioPlay = vi.fn().mockResolvedValue(undefined);
    mockAudioPause = vi.fn();

    // Mock HTMLAudioElement class
    class MockAudio {
      src = "";
      currentTime = 0;
      play = mockAudioPlay;
      pause = mockAudioPause;
      onended: any = null;
      onerror: any = null;
      constructor(src?: string) {
        if (src) this.src = src;
      }
    }
    window.Audio = MockAudio as any;

    // Mock MediaStream and MediaRecorder
    const stopTrackMock = vi.fn();
    mockGetUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: stopTrackMock, kind: "audio" }],
    });

    Object.defineProperty(navigator, "mediaDevices", {
      writable: true,
      value: {
        getUserMedia: mockGetUserMedia,
      },
    });

    (window as any).MediaRecorder = vi.fn().mockImplementation(function (this: any, stream: any) {
      this.stream = stream;
      this.state = "recording";
      this.start = vi.fn();
      this.stop = vi.fn(() => {
        this.state = "inactive";
        if (this.ondataavailable) {
          this.ondataavailable({ data: new Blob(["chunk"], { type: "audio/webm" }) });
        }
        if (this.onstop) this.onstop();
      });
      return this;
    });
    (window as any).MediaRecorder.isTypeSupported = vi.fn().mockReturnValue(true);

    // Mock URL.createObjectURL and URL.revokeObjectURL
    window.URL.createObjectURL = vi.fn().mockReturnValue("blob:mock-audio-url");
    window.URL.revokeObjectURL = vi.fn();

    // Mock global fetch for audio blob
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      blob: vi.fn().mockResolvedValue(new Blob(["mock-wav-bytes"], { type: "audio/wav" })),
    }) as any;

    // Default apiFetch routing
    (clientModule.apiFetch as any).mockImplementation(async (endpoint: string) => {
      if (endpoint.includes("/capabilities")) {
        return mockCapabilities;
      }
      if (endpoint.includes("/start") || endpoint.includes("/sessions/sess-aud-1")) {
        return mockAudioSession;
      }
      return {};
    });
  });

  it("1. Audio preparation view shows readiness and does NOT request microphone on load", async () => {
    render(
      <CandidateInterviewWorkspace
        releaseId="rel-aud-1"
        jobTitle="Lead Audio Engineer"
        initialMode="AUDIO"
        conversationCount={1}
        onExit={vi.fn()}
      />
    );

    // Verify readiness indicators
    await waitFor(() => {
      expect(screen.getByText(/Speech-to-Text:/i)).toBeInTheDocument();
      expect(screen.getByText(/Ready \(whisper\.cpp\)/i)).toBeInTheDocument();
      expect(screen.getByText(/Question Speech:/i)).toBeInTheDocument();
      expect(screen.getByText(/Ready \(Piper TTS\)/i)).toBeInTheDocument();
    });

    // Verify microphone permission was NOT requested yet
    expect(mockGetUserMedia).not.toHaveBeenCalled();

    // Start button is disabled until acknowledgement is checked
    const startBtn = screen.getByRole("button", { name: /Start Audio Interview/i });
    expect(startBtn).toBeDisabled();

    // Checking the acknowledgement checkbox enables Start button
    const ackCheckbox = screen.getByRole("checkbox");
    fireEvent.click(ackCheckbox);
    expect(startBtn).toBeEnabled();
  });

  it("2. Starting Audio interview renders question workspace without MCQ options or editable textarea", async () => {
    render(
      <CandidateInterviewWorkspace
        releaseId="rel-aud-1"
        jobTitle="Lead Audio Engineer"
        initialMode="AUDIO"
        conversationCount={1}
        onExit={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("checkbox")).toBeInTheDocument();
    });

    // Acknowledge and start
    const ackCheckbox = screen.getByRole("checkbox");
    fireEvent.click(ackCheckbox);

    const startBtn = screen.getByRole("button", { name: /Start Audio Interview/i });
    fireEvent.click(startBtn);

    // Workspace transitions to question view
    await waitFor(() => {
      expect(screen.getByText("How do you minimize audio latency in real-time embedded systems?")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Start recording/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Play Question Speech/i })).toBeInTheDocument();
    });

    // Verify no MCQ radios and no editable textarea exist in Audio mode
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("3. Question audio Play triggers Piper audio fetch and Audio element play", async () => {
    render(
      <CandidateInterviewWorkspace
        releaseId="rel-aud-1"
        jobTitle="Lead Audio Engineer"
        initialMode="AUDIO"
        conversationCount={1}
        onExit={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("checkbox")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Audio Interview/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Play Question Speech/i })).toBeInTheDocument();
    });

    // Click Play Question Speech
    const playBtn = screen.getByRole("button", { name: /Play Question Speech/i });
    fireEvent.click(playBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/candidate/interviews/sessions/sess-aud-1/questions/slot-aud-1/audio",
        expect.objectContaining({ credentials: "include" })
      );
      expect(mockAudioPlay).toHaveBeenCalled();
    });
  });

  it("4. Recording, read-only transcript review, and Use This Answer persists official answer", async () => {
    (clientModule.apiFetch as any).mockImplementation(async (endpoint: string, options?: any) => {
      if (endpoint.includes("/capabilities")) {
        return mockCapabilities;
      }
      if (endpoint.includes("/start")) {
        return mockAudioSession;
      }
      if (endpoint.includes("/transcribe")) {
        return {
          session_id: "sess-aud-1",
          slot_id: "slot-aud-1",
          transcript: "I design lock-free audio buffers and use direct hardware interrupts.",
          is_empty: false,
        };
      }
      if (endpoint.includes("/answers/")) {
        return {
          session_id: "sess-aud-1",
          slot_id: "slot-aud-1",
          saved_at: new Date().toISOString(),
          is_answered: true,
          answered_questions: 1,
          total_questions: 1,
        };
      }
      return {};
    });

    render(
      <CandidateInterviewWorkspace
        releaseId="rel-aud-1"
        jobTitle="Lead Audio Engineer"
        initialMode="AUDIO"
        conversationCount={1}
        onExit={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("checkbox")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Start Audio Interview/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Start recording/i })).toBeInTheDocument();
    });

    // Start recording
    fireEvent.click(screen.getByRole("button", { name: /Start recording/i }));

    await waitFor(() => {
      expect(mockGetUserMedia).toHaveBeenCalledWith({ audio: true });
      expect(screen.getByText(/Recording in progress/i)).toBeInTheDocument();
    });

    // Stop recording and transcribe
    const stopBtn = screen.getByRole("button", { name: /Stop and transcribe/i });
    fireEvent.click(stopBtn);

    // Read-only transcript review panel appears
    await waitFor(() => {
      expect(screen.getByText("Review your transcript")).toBeInTheDocument();
      expect(screen.getByText("Recording deleted after transcription")).toBeInTheDocument();
      expect(screen.getByText("I design lock-free audio buffers and use direct hardware interrupts.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Use this answer/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Record again/i })).toBeInTheDocument();
    });

    // Click "Use this answer"
    fireEvent.click(screen.getByRole("button", { name: /Use this answer/i }));

    await waitFor(() => {
      expect(clientModule.apiFetch).toHaveBeenCalledWith(
        "/api/v1/candidate/interviews/sessions/sess-aud-1/answers/slot-aud-1",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({
            text_answer: "I design lock-free audio buffers and use direct hardware interrupts.",
          }),
        })
      );
      expect(screen.getByText(/Answer Accepted & Saved/i)).toBeInTheDocument();
    });
  });
});
