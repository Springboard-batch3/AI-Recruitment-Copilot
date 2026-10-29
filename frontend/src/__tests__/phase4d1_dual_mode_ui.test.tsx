import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AiInterviewsDirectory } from "../components/ai_interviews/AiInterviewsDirectory";
import { AiQuestionSetWorkspace } from "../components/ai_interviews/AiQuestionSetWorkspace";
import type { InterviewMode } from "../types/ai_question_set";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("Phase 4D.1 Frontend: Dual-Mode Interview Foundation and Recruiter Studio", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockSetsText = {
    sets: [
      {
        ai_set_id: "set-text-1",
        template_id: null,
        job_id: "job-101",
        job_title: "Senior Backend Engineer",
        job_revision: 1,
        source_mode: "REQUIREMENT_GROUNDED",
        interview_mode: "TEXT" as InterviewMode,
        set_version: 1,
        lifecycle_status: "VALIDATED" as const,
        is_immutable: false,
        is_source_stale: false,
        generated_question_count: 5,
        total_blueprint_slots: 5,
        conversation_count: 5,
        mcq_count: 0,
        created_at: "2026-09-28T12:00:00Z",
        updated_at: "2026-09-28T12:00:00Z",
      },
    ],
    total_count: 1,
    awaiting_review_count: 0,
    ready_for_approval_count: 1,
    approved_count: 0,
  };

  const mockSetsAudio = {
    sets: [
      {
        ai_set_id: "set-audio-1",
        template_id: null,
        job_id: "job-102",
        job_title: "Staff Infrastructure Engineer",
        job_revision: 1,
        source_mode: "REQUIREMENT_GROUNDED",
        interview_mode: "AUDIO" as InterviewMode,
        set_version: 1,
        lifecycle_status: "DRAFT" as const,
        is_immutable: false,
        is_source_stale: false,
        generated_question_count: 4,
        total_blueprint_slots: 4,
        conversation_count: 4,
        mcq_count: 0,
        created_at: "2026-09-28T14:00:00Z",
        updated_at: "2026-09-28T14:00:00Z",
      },
    ],
    total_count: 1,
    awaiting_review_count: 1,
    ready_for_approval_count: 0,
    approved_count: 0,
  };

  const mockEligibleText = {
    items: [
      {
        job_id: "job-102",
        job_title: "Staff Infrastructure Engineer",
        requirement_count: 4,
        current_ai_set_id: null,
        current_ai_set_status: null,
      },
    ],
  };

  const mockEligibleAudio = {
    items: [
      {
        job_id: "job-101",
        job_title: "Senior Backend Engineer",
        requirement_count: 5,
        current_ai_set_id: null,
        current_ai_set_status: null,
      },
    ],
  };

  it("renders the directory with Text and Audio mode switcher tabs, defaulting to Text-based", async () => {
    (clientModule.apiFetch as any).mockImplementation((path: string) => {
      if (path.includes("/api/v1/ai-interviews/eligible-jobs")) {
        return Promise.resolve(mockEligibleText);
      }
      if (path.includes("/api/v1/ai-interviews")) {
        return Promise.resolve(mockSetsText);
      }
      return Promise.resolve({});
    });

    const handleSelectTemplate = vi.fn();
    render(<AiInterviewsDirectory onSelectTemplate={handleSelectTemplate} />);

    expect(screen.getByText("AI Interviews")).toBeInTheDocument();
    expect(screen.getByText("Choose a job, generate interview question sets, and manage candidate interview practice.")).toBeInTheDocument();

    expect(screen.getByRole("tab", { name: "Text-based" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Audio-based" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
      expect(screen.getByText("Staff Infrastructure Engineer")).toBeInTheDocument();
    });
  });

  it("switches to Audio interviews directory when clicking the Audio-based tab", async () => {
    (clientModule.apiFetch as any).mockImplementation((path: string) => {
      if (path.includes("mode=AUDIO")) {
        if (path.includes("/api/v1/ai-interviews/eligible-jobs")) {
          return Promise.resolve(mockEligibleAudio);
        }
        if (path.includes("/api/v1/ai-interviews")) {
          return Promise.resolve(mockSetsAudio);
        }
      }
      if (path.includes("/api/v1/ai-interviews/eligible-jobs")) {
        return Promise.resolve(mockEligibleText);
      }
      if (path.includes("/api/v1/ai-interviews")) {
        return Promise.resolve(mockSetsText);
      }
      return Promise.resolve({});
    });

    render(<AiInterviewsDirectory onSelectTemplate={vi.fn()} />);

    const audioTab = screen.getByRole("tab", { name: "Audio-based" });
    fireEvent.click(audioTab);

    await waitFor(() => {
      expect(screen.getByText("Staff Infrastructure Engineer")).toBeInTheDocument();
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });
  });

  it("navigates to Audio interviews directory with independent ready jobs and sets", async () => {
    (clientModule.apiFetch as any).mockImplementation((path: string) => {
      if (path.includes("/api/v1/ai-interviews/eligible-jobs")) {
        return Promise.resolve(mockEligibleAudio);
      }
      if (path.includes("/api/v1/ai-interviews")) {
        return Promise.resolve(mockSetsAudio);
      }
      return Promise.resolve({});
    });

    render(<AiInterviewsDirectory onSelectTemplate={vi.fn()} initialMode={"AUDIO" as InterviewMode} />);

    await waitFor(() => {
      expect(screen.getByText("Audio-based")).toBeInTheDocument();
      expect(screen.getByText("Staff Infrastructure Engineer")).toBeInTheDocument();
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });
  });

  it("AUDIO workspace hides MCQ tab and renders neutral Phase 4D.2 release message", async () => {
    const mockAudioDetail = {
      ai_set_id: "set-audio-1",
      job_id: "job-102",
      job_title: "Staff Infrastructure Engineer",
      job_revision: 1,
      source_mode: "REQUIREMENT_GROUNDED",
      interview_mode: "AUDIO" as InterviewMode,
      set_version: 1,
      lifecycle_status: "APPROVED" as const,
      is_immutable: true,
      content_hash: "hash123",
      source_content_hash: "srchash123",
      source_requirement_ids: ["req-1"],
      provider_class: "LOCAL_OPENAI_COMPATIBLE",
      model_identifier: "qwen3-4b",
      prompt_template_version: "2.0.0",
      guardrail_policy_version: "1.0.0",
      schema_version: "1.0.0",
      created_by_user_id: "u1",
      created_at: "2026-09-28T12:00:00Z",
      updated_at: "2026-09-28T12:00:00Z",
      total_slots_count: 1,
      generated_slots_count: 1,
      reviewed_slots_count: 1,
      conversation_count: 1,
      mcq_count: 0,
      is_source_stale: false,
      slots: [
        {
          slot_id: "slot-1",
          display_order: 0,
          source_requirement_ids: ["req-1"],
          competency_name: "Consensus",
          question_type: "TECHNICAL",
          question_format: "CONVERSATION",
          estimated_time_minutes: 3,
          permitted_probes: [],
          rubric: null,
          ai_wording: "Can you verbally explain how leader election works in Raft?",
          review_state: "APPROVED",
          authoring_origin: "LOCAL_LLM_GENERATED",
          validation_status: "PASSED",
          safe_failure_code: null,
          rationale_code: "REQUIREMENT_GROUNDED",
          model_identifier: "qwen3-4b",
          provider_class: "LOCAL_OPENAI_COMPATIBLE",
          updated_at: "2026-09-28T12:00:00Z",
        },
      ],
    };

    (clientModule.apiFetch as any).mockImplementation((path: string) => {
      if (path.includes("/api/v1/ai-interviews/set-audio-1")) {
        return Promise.resolve(mockAudioDetail);
      }
      return Promise.resolve({});
    });

    render(
      <AiQuestionSetWorkspace
        templateId="set-audio-1"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Audio-based")).toBeInTheDocument();
      // MCQ tab should not be present
      expect(screen.queryByRole("tab", { name: /Multiple Choice/i })).not.toBeInTheDocument();
      // Release action is available
      expect(screen.getByRole("button", { name: /Release Interview Set/i })).toBeInTheDocument();
    });
  });
});
