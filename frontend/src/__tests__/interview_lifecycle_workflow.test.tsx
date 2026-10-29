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

describe("Phase 3B.4 Interview Workspace Lifecycle & Review Workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockContext = {
    job_id: "job-12345",
    job_title: "Staff Software Engineer",
    job_version_id: "v-1",
    job_revision: 1,
    candidate_id: "cand-67890",
    candidate_name: "Jordan Lee",
    candidate_email: "jordan.lee@example.com",
    resume_artifact_id: "res-11111",
    match_run_id: "run-999",
    match_score: 92.0,
    skill_gap_summary: "High confidence match",
    ai_capability: {
      ai_enabled: false,
      provider_class: "DISABLED",
      availability_label: "AI Disabled (Deterministic Engine)",
      egress_class: "LOCAL_ONLY" as const,
      active_model_name: null,
      message: null,
    },
    interview_plan_state: "DRAFT",
    message: "Interview workspace ready.",
  };

  const mockTemplate = {
    template_id: "tpl-100",
    job_id: "job-12345",
    job_version_id: "v-1",
    job_revision: 1,
    template_version: 1,
    parent_template_id: null,
    lifecycle_state: "DRAFT" as const,
    common_core_questions: [
      {
        question_id: "q-1",
        layer: "COMMON_CORE" as const,
        question_type: "TECHNICAL" as const,
        competency_name: "Distributed Systems",
        question_text: "Describe how you design distributed state synchronization.",
        display_order: 0,
        estimated_time_minutes: 10,
        permitted_probes: ["How do you handle split-brain?"],
        authoring_origin: "DETERMINISTIC" as const,
        source_requirement_ids: ["req-1"],
        rubric: {
          rubric_id: "rub-1",
          target_competency: "Distributed Systems",
          levels: [
            {
              level_name: "Basic",
              score_range: [1, 2] as [number, number],
              description: "Understands single node locks.",
              observable_signals: ["Mentions Redis"],
            },
          ],
        },
      },
    ],
    target_duration_minutes: 45,
    content_hash: "a".repeat(64),
    authoritative_snapshot_hash: "b".repeat(64),
    created_at: "2026-09-16T12:00:00Z",
  };

  it("loads existing template draft, allows safe edit, validate and approval flow", async () => {
    // 1. Context call
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockContext);
    // 2. Latest template call
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockTemplate);
    // 3. Candidate plan call (none initially)
    vi.mocked(clientModule.apiFetch).mockRejectedValueOnce(new Error("No plan"));

    const onBack = vi.fn();
    render(
      <InterviewWorkspace
        jobId="job-12345"
        resumeId="res-11111"
        onNavigateBack={onBack}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Interview Plan: Jordan Lee")).toBeInTheDocument();
    });

    expect(screen.getByText("Distributed Systems")).toBeInTheDocument();

    // Click Edit to enter inline edit mode
    const editBtn = screen.getByRole("button", { name: /^Edit$/i });
    fireEvent.click(editBtn);

    // Edit textarea
    const textarea = screen.getByDisplayValue("Describe how you design distributed state synchronization.");
    fireEvent.change(textarea, { target: { value: "Updated question text by recruiter." } });

    expect(screen.getByText("Unsaved Edits")).toBeInTheDocument();

    // Save Draft
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce({
      ...mockTemplate,
      common_core_questions: [
        {
          ...mockTemplate.common_core_questions[0],
          question_text: "Updated question text by recruiter.",
          authoring_origin: "RECRUITER_EDITED",
        },
      ],
    });

    const saveBtn = screen.getByRole("button", { name: /Save Draft/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText("Changes saved successfully.")).toBeInTheDocument();
    });

    // Validate Draft
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce({
      is_valid: true,
      lifecycle_state: "VALIDATED",
      blocking_rule_violations: [],
      warnings: [],
      content_hash: "updatedhash",
      validated_at: "2026-09-16T12:05:00Z",
    });

    const validateBtn = screen.getByRole("button", { name: /Validate/i });
    fireEvent.click(validateBtn);

    await waitFor(() => {
      expect(screen.getByText("Validation passed! Template is ready for approval.")).toBeInTheDocument();
    });

    // Approve
    vi.mocked(clientModule.apiFetch).mockResolvedValueOnce({
      ...mockTemplate,
      lifecycle_state: "APPROVED",
      approved_at: "2026-09-16T12:10:00Z",
      approved_by_user_id: "recruiter-1",
    });

    const approveBtn = screen.getByRole("button", { name: /Approve & Lock/i });
    fireEvent.click(approveBtn);

    await waitFor(() => {
      expect(screen.getByText("Template approved and locked as immutable authoritative baseline.")).toBeInTheDocument();
    });
  });
});
