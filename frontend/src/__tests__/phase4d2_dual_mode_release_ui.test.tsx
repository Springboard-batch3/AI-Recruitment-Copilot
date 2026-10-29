import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ReleaseInterviewSetModal } from "../components/ai_interviews/ReleaseInterviewSetModal";
import { CandidatePracticeDirectory } from "../components/candidate/CandidatePracticeDirectory";
import { CandidateInterviewWorkspace } from "../components/candidate/CandidateInterviewWorkspace";
import type { AiQuestionSetSummary, AiQuestionSetDetail, InterviewMode } from "../types/ai_question_set";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("Phase 4D.2 Frontend: Mode-Specific Interview Release and Candidate Session Isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockAudioDetail: AiQuestionSetDetail = {
    ai_set_id: "set-audio-1",
    template_id: null,
    job_id: "job-101",
    job_title: "Senior Backend Engineer",
    job_revision: 1,
    source_mode: "REQUIREMENT_GROUNDED",
    source_content_hash: "hash123",
    interview_mode: "AUDIO",
    set_version: 1,
    lifecycle_status: "APPROVED",
    is_immutable: true,
    is_source_stale: false,
    slots: [
      {
        slot_id: "slot-1",
        display_order: 1,
        competency_name: "System Design",
        question_type: "BEHAVIORAL",
        estimated_time_minutes: 4,
        permitted_probes: [],
        review_state: "KEPT",
        authoring_origin: "AI_GENERATED",
        validation_status: "PASSED",
        question_format: "CONVERSATION",
      },
      {
        slot_id: "slot-2",
        display_order: 2,
        competency_name: "Communication",
        question_type: "BEHAVIORAL",
        estimated_time_minutes: 4,
        permitted_probes: [],
        review_state: "KEPT",
        authoring_origin: "AI_GENERATED",
        validation_status: "PASSED",
        question_format: "CONVERSATION",
      },
    ],
    total_slots_count: 2,
    generated_slots_count: 2,
    reviewed_slots_count: 2,
    conversation_count: 2,
    mcq_count: 0,
    created_at: "2026-09-28T10:00:00Z",
    updated_at: "2026-09-28T10:00:00Z",
  };

  it("Recruiter Release Modal shows mode badge and conversational count for Audio set", async () => {
    const apiFetchMock = vi.mocked(clientModule.apiFetch);
    apiFetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/applications")) {
        return {
          items: [
            {
              applicationId: "app-1",
              jobId: "job-101",
              jobVersionId: "jv-1",
              jobRevision: 1,
              candidateId: "cand-1",
              profileId: "prof-1",
              profileVersion: 1,
              candidateName: "Alice Smith",
              candidateEmail: "alice@example.com",
              status: "SUBMITTED",
              recruiterReviewState: "INTERVIEW_READY",
              submittedAt: "2026-09-28T12:00:00Z",
              matchStatus: "COMPLETED",
              interviewStatus: "NOT_RELEASED",
              interviewEligibility: {
                isEligible: true,
                message: "Eligible for release",
                questionCount: 2,
              },
            },
          ],
          total: 1,
          page: 1,
          limit: 100,
          activeCount: 1,
          withdrawnCount: 0,
        };
      }
      return {};
    });

    render(
      <ReleaseInterviewSetModal
        isOpen={true}
        detail={mockAudioDetail}
        onClose={vi.fn()}
        onSuccessRelease={vi.fn()}
      />
    );

    // Audio-based badge & question count
    expect(screen.getByText("Audio-based interview")).toBeInTheDocument();
    expect(screen.getByText("2 Conversational")).toBeInTheDocument();
    // MCQ should NOT be shown
    expect(screen.queryByText(/MCQ:/i)).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
    });
  });

  it("Candidate Directory displays independent Text-based and Audio-based cards", async () => {
    const apiFetchMock = vi.mocked(clientModule.apiFetch);
    apiFetchMock.mockImplementation(async (url: string) => {
      if (url.includes("/candidate/practice-jobs")) {
        return {
          items: [
            {
              release_id: "rel-text-1",
              job_id: "job-101",
              job_title: "Senior Backend Engineer",
              job_revision: 1,
              interview_mode: "TEXT",
              total_question_count: 3,
              conversation_count: 2,
              mcq_count: 1,
              released_at: "2026-09-28T12:00:00Z",
              status: "RELEASED",
            },
            {
              release_id: "rel-audio-1",
              job_id: "job-101",
              job_title: "Senior Backend Engineer",
              job_revision: 1,
              interview_mode: "AUDIO",
              total_question_count: 4,
              conversation_count: 4,
              conversation_question_count: 4,
              mcq_count: 0,
              released_at: "2026-09-28T12:00:00Z",
              status: "RELEASED",
            },
          ],
        };
      }
      return {};
    });

    render(<CandidatePracticeDirectory />);

    await waitFor(() => {
      expect(screen.getByText("Text-based")).toBeInTheDocument();
      expect(screen.getByText("Audio-based")).toBeInTheDocument();
      expect(screen.getByText(/3 Questions/i)).toBeInTheDocument();
      expect(screen.getByText(/4 Questions/i)).toBeInTheDocument();
    });
  });

  it("Candidate Audio Interview Workspace displays preparation view with disabled start action", async () => {
    (clientModule.apiFetch as any).mockResolvedValueOnce({
      speech_to_text_available: true,
      text_to_speech_available: true,
      microphone_required: true,
      secure_context_required: true,
      max_recording_seconds: 120,
      max_upload_bytes: 10485760,
    });

    render(
      <CandidateInterviewWorkspace
        releaseId="rel-audio-1"
        jobTitle="Senior Backend Engineer"
        initialMode="AUDIO"
        conversationCount={4}
        onExit={vi.fn()}
      />
    );

    // Header & Badge
    expect(screen.getByText("Audio-based interview")).toBeInTheDocument();
    expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();

    // Preparation notices
    expect(screen.getByText(/Zero Audio Retention Policy:/i)).toBeInTheDocument();
    expect(screen.getByText(/Your spoken recording is processed entirely locally/i)).toBeInTheDocument();
    expect(screen.getByText(/Transcript Confirmation:/i)).toBeInTheDocument();

    // Verify start button is disabled until acknowledgement
    const startBtn = screen.getByRole("button", { name: /Start Audio Interview/i });
    expect(startBtn).toBeDisabled();
  });
});
