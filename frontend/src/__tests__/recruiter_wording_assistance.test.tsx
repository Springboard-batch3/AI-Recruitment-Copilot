import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    code: string;
    constructor(status: number, code: string, message: string) {
      super(message);
      this.status = status;
      this.code = code;
    }
  },
}));

describe("Phase 3C.4 Recruiter Wording Assistance & Comparison Modal Tests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockContextWithAI = {
    job_id: "job-123",
    job_title: "Staff Backend Engineer",
    job_version_id: "v-1",
    job_revision: 1,
    candidate_id: "cand-456",
    candidate_name: "Alex Mercer",
    candidate_email: "alex@example.com",
    resume_artifact_id: "res-789",
    match_run_id: "run-001",
    match_score: 95.0,
    skill_gap_summary: "Strong core skills",
    ai_capability: {
      ai_enabled: true,
      provider_class: "LM_STUDIO_LOCAL",
      availability_label: "AI Active (Local LM Studio)",
      egress_class: "LOCAL_ONLY" as const,
      active_model_name: "mistral-7b-instruct",
      message: null,
    },
    interview_plan_state: "DRAFT",
    message: "Interview workspace ready.",
  };

  const mockTemplate = {
    template_id: "tpl-100",
    job_id: "job-123",
    job_version_id: "v-1",
    job_revision: 1,
    template_version: 1,
    parent_template_id: null,
    lifecycle_state: "DRAFT" as const,
    common_core_questions: [
      {
        question_id: "q-101",
        layer: "COMMON_CORE" as const,
        question_type: "TECHNICAL" as const,
        competency_name: "Distributed Architecture",
        question_text: "Explain your experience designing and tuning distributed caching mechanisms.",
        display_order: 0,
        estimated_time_minutes: 5,
        permitted_probes: ["What eviction policy did you choose?"],
        authoring_origin: "DETERMINISTIC" as const,
        source_requirement_ids: ["req-1"],
        content_hash: "hash101",
        question_version: 1,
        rubric: null,
      },
    ],
    target_duration_minutes: 45,
    content_hash: "tplhash123",
    authoritative_snapshot_hash: "snaphash123",
    created_at: "2026-09-16T12:00:00Z",
  };

  it("renders Improve Wording button for eligible drafts and handles comparison modal interactions", async () => {
    // 1. Initial context and template calls
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockContextWithAI);
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockTemplate);
    vi.mocked(clientModule.apiFetch).mockRejectedValueOnce(new Error("No plan"));

    render(
      <InterviewWorkspace
        jobId="job-123"
        resumeId="res-789"
        onNavigateBack={vi.fn()}
      />
    );

    // Wait for questions to load
    await waitFor(() => {
      expect(screen.getByText("Explain your experience designing and tuning distributed caching mechanisms.")).toBeInTheDocument();
    });

    // Check origin source caption
    expect(screen.getByText(/Deterministic blueprint baseline/i)).toBeInTheDocument();

    // Check Improve wording button exists and is clickable
    const improveBtn = screen.getByRole("button", { name: /improve wording for question 1/i });
    expect(improveBtn).toBeInTheDocument();

    // Mock wording assistance response
    const mockWordingResult = {
      source_question_id: "q-101",
      source_question_version: 1,
      operation: "REWORD_QUESTION",
      request_schema_version: "1.0.0",
      prompt_template_version: "1.0.0",
      deterministic_question_text: "Explain your experience designing and tuning distributed caching mechanisms.",
      proposed_variants: [
        {
          variant_text: "Could you walk me through how you design, implement, and tune distributed caching layers in production systems?",
          rationale_code: "CLARITY_IMPROVEMENT" as const,
          referenced_identifiers: ["req-1"],
        },
      ],
      allowed_references_checked: true,
      is_validated: true,
      validation_codes: [],
      rejection_reasons: [],
      authoring_origin: "AI_ASSISTED" as const,
      provider_fallback_used: false,
      provider_error_code: null,
      safe_error_message: null,
    };

    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockWordingResult);

    fireEvent.click(improveBtn);

    // Wait for comparison modal to open
    await waitFor(() => {
      expect(screen.getByText("Review Improved Question Wording")).toBeInTheDocument();
    });

    // Verify comparison modal displays both versions
    expect(screen.getByText("Original Deterministic")).toBeInTheDocument();
    expect(screen.getByText("AI-Assisted Suggestion")).toBeInTheDocument();
    expect(screen.getByText("Could you walk me through how you design, implement, and tune distributed caching layers in production systems?")).toBeInTheDocument();

    // Mock apply suggestion response
    const mockApplyResponse = {
      question_id: "q-101",
      question_version: 2,
      question_text: "Could you walk me through how you design, implement, and tune distributed caching layers in production systems?",
      authoring_origin: "AI_ASSISTED" as const,
      validation_state: "PENDING",
      deterministic_source_text: "Explain your experience designing and tuning distributed caching mechanisms.",
      safe_metadata: {
        deterministic_source_text: "Explain your experience designing and tuning distributed caching mechanisms.",
        ai_provenance: {
          provider_class: "LOCAL",
          model_name: "mistral-7b-instruct",
          applied_at: "2026-09-16T21:00:00Z",
          prompt_template_version: "1.0.0",
        },
      },
    };

    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockApplyResponse);

    // Click "Use Suggestion"
    const useSuggestionBtn = screen.getByRole("button", { name: /use suggestion/i });
    fireEvent.click(useSuggestionBtn);

    // Wait for modal to close and question card to update
    await waitFor(() => {
      expect(screen.queryByText("Review Improved Question Wording")).not.toBeInTheDocument();
    });

    // Check origin updated to AI wording suggestion
    expect(screen.getByText(/Source: AI wording suggestion/i)).toBeInTheDocument();
    // Check Restore button appears
    expect(screen.getByRole("button", { name: /restore original deterministic baseline wording/i })).toBeInTheDocument();
  });
});
