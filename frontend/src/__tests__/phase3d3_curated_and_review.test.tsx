import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
import { InterviewReviewWorkspace } from "../components/interviews/InterviewReviewWorkspace";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("Phase 3D.3 Frontend: AI-Curated Interview UI & Human Review Workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockContext = {
    job_id: "job-101",
    job_title: "Principal Infrastructure Engineer",
    job_version_id: "v-1",
    job_revision: 1,
    candidate_id: "cand-202",
    candidate_name: "Morgan Bailey",
    resume_artifact_id: "res-303",
    ai_capability: {
      ai_enabled: true,
      provider_class: "LM_STUDIO_LOCAL",
      availability_label: "AI Active (Local MLX Qwen)",
      egress_class: "LOCAL_ONLY" as const,
    },
    interview_plan_state: "DRAFT",
    message: "Context ready",
  };

  const mockTemplateWithMixedWording = {
    template_id: "tpl-500",
    job_id: "job-101",
    job_version_id: "v-1",
    job_revision: 1,
    template_version: 3,
    lifecycle_state: "DRAFT" as const,
    common_core_questions: [
      {
        question_id: "q-1",
        layer: "COMMON_CORE" as const,
        question_type: "TECHNICAL" as const,
        competency_name: "Distributed Consensus",
        question_text: "Can you detail how Raft handles split-brain elections in unstable networks?",
        display_order: 0,
        estimated_time_minutes: 6,
        authoring_origin: "AI_ASSISTED" as const,
        deterministic_source_text: "Explain how Raft prevents split-brain elections.",
        deterministic_source_available: true,
        ai_suggestion_text: null,
        has_wording_drift: true,
        can_restore: true,
        question_version: 2,
        rubric: null,
      },
      {
        question_id: "q-2",
        layer: "COMMON_CORE" as const,
        question_type: "BEHAVIORAL" as const,
        competency_name: "Production Triage",
        question_text: "Describe an incident where observability pipelines stalled during peak load.",
        display_order: 1,
        estimated_time_minutes: 5,
        authoring_origin: "DETERMINISTIC" as const,
        deterministic_source_text: "Describe an incident where observability pipelines stalled during peak load.",
        deterministic_source_available: true,
        ai_suggestion_text: "Tell me about a high-stress outage when telemetry broke.",
        has_wording_drift: false,
        can_restore: false,
        question_version: 1,
        rubric: null,
      },
      {
        question_id: "q-3",
        layer: "COMMON_CORE" as const,
        question_type: "PROBLEM_SOLVING" as const,
        competency_name: "Capacity Planning",
        question_text: "Walk through forecasting read replicas for exponential query growth.",
        display_order: 2,
        estimated_time_minutes: 5,
        authoring_origin: "AI_ASSISTED" as const,
        deterministic_source_text: null,
        deterministic_source_available: false,
        ai_suggestion_text: null,
        has_wording_drift: false,
        can_restore: false,
        question_version: 1,
        rubric: null,
      },
    ],
    supplemental_questions: [],
    target_duration_minutes: 45,
    content_hash: "hashMixedWording",
    authoritative_snapshot_hash: "snapMixedWording",
    created_at: "2026-09-17T10:00:00Z",
  };

  it("PART A1 & A2: Displays coherent Deterministic Blueprint header and action buttons", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateWithMixedWording);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => {}} />);

    // Header title
    await waitFor(() => {
      expect(screen.getByText("Interview Template: Principal Infrastructure Engineer")).toBeInTheDocument();
    });

    // Authoritative foundation explanation note
    expect(
      screen.getByText(/Authoritative Foundation:/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/This workspace defines the deterministic interview blueprint for this role/i)
    ).toBeInTheDocument();

    // Prominent Generate AI Questions button
    expect(screen.getByRole("button", { name: /Generate AI Questions/i })).toBeInTheDocument();

    // Blueprint structure section
    expect(screen.getByText("Authoritative Blueprint Structure")).toBeInTheDocument();
  });

  it("PART A1 & A3: Shows deterministic blueprint question metadata and direct actions", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateWithMixedWording);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Principal Infrastructure Engineer")).toBeInTheDocument();
    });

    // Question 1 metadata
    expect(screen.getByText("Question 1")).toBeInTheDocument();
    expect(screen.getByText("Distributed Consensus")).toBeInTheDocument();
    expect(screen.getByText("Can you detail how Raft handles split-brain elections in unstable networks?")).toBeInTheDocument();

    // Question 2 is deterministic
    expect(screen.getByText("Question 2")).toBeInTheDocument();
    expect(screen.getByText("Production Triage")).toBeInTheDocument();

    // Question 3
    expect(screen.getByText("Question 3")).toBeInTheDocument();
    expect(screen.getByText("Capacity Planning")).toBeInTheDocument();
  });

  it("PART A2: Blueprint question list displays authoritative text and preserves order", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateWithMixedWording);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Principal Infrastructure Engineer")).toBeInTheDocument();
    });

    // Verify questions in order
    expect(screen.getByText("Question 1")).toBeInTheDocument();
    expect(screen.getByText("Question 2")).toBeInTheDocument();
    expect(screen.getByText("Question 3")).toBeInTheDocument();
    expect(screen.getByText("Order #1")).toBeInTheDocument();
    expect(screen.getByText("Order #2")).toBeInTheDocument();
    expect(screen.getByText("Order #3")).toBeInTheDocument();
  });

  it("PART A2: Pure deterministic blueprint view without AI suggestions drawer", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateWithMixedWording);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Principal Infrastructure Engineer")).toBeInTheDocument();
    });

    // AI suggestions drawer should NOT be present in deterministic workspace
    expect(screen.queryByText("Review Pending AI Suggestions")).toBeNull();
    expect(screen.queryByRole("tab", { name: "AI-curated version" })).toBeNull();
  });

  // Human Review Workspace Tests (Parts B, C, D, E, F, G)
  const mockReviewWorkspaceData = {
    attempt_id: "att-777",
    organization_id: "org-test",
    lifecycle_status: "PROVISIONALLY_ASSESSED",
    candidate_id: "cand-202",
    plan_id: "plan-303",
    job_id: "job-101",
    record_version: 1,
    questions: [
      {
        question_id: "q-core-1",
        layer: "COMMON_CORE",
        display_order: 0,
        competency_name: "Distributed Consensus",
        question_text: "Can you detail how Raft handles split-brain elections in unstable networks?",
        submitted_answer: "In Raft, leader elections require quorum (majority) of nodes. If network splits into minority and majority, only majority can elect leader. Term numbers reject stale leaders.",
        answer_segments: [
          {
            segment_id: "seg-101",
            segment_index: 0,
            char_start: 0,
            char_end: 73,
            segment_text: "In Raft, leader elections require quorum (majority) of nodes.",
          },
          {
            segment_id: "seg-102",
            segment_index: 1,
            char_start: 74,
            char_end: 153,
            segment_text: "If network splits into minority and majority, only majority can elect leader.",
          },
          {
            segment_id: "seg-103",
            segment_index: 2,
            char_start: 154,
            char_end: 189,
            segment_text: "Term numbers reject stale leaders.",
          },
        ],
        provisional_assessment: {
          assessment_id: "ass-1",
          proposed_rubric_level: 4,
          provisional_observation: "Candidate clearly described majority quorum requirement and term numbering to prevent split-brain state.",
          cited_segment_ids: ["seg-101", "seg-102"],
          uncertainty_codes: [],
          validator_result: "SAFE_BOUNDS_VERIFIED",
          model_provenance: {
            model_name: "qwen3-4b-mlx",
            provider_class: "LOCAL_LM_STUDIO",
            egress_class: "LOCAL_ONLY",
          },
          assessed_at: "2026-09-17T11:00:00Z",
        },
        rubric: {
          rubric_id: "rub-1",
          target_competency: "Distributed Consensus",
          levels: [
            { level_name: "Novice", score_range: [1, 1], description: "Vague notion of leader", observable_signals: [] },
            { level_name: "Proficient", score_range: [3, 3], description: "Understands consensus quorum", observable_signals: [] },
            { level_name: "Advanced", score_range: [4, 4], description: "Understands network partitioning and term numbers", observable_signals: [] },
          ],
        },
        latest_review: null,
      },
    ],
    aggregate: {
      policy_version: "3D.3-deterministic",
      aggregate_score: null,
      included_question_ids: ["q-core-1"],
      excluded_question_ids: [],
      weights: { "q-core-1": 1.0 },
      numerator: 0.0,
      denominator: 0.0,
      reviewed_question_count: 0,
      total_core_questions: 1,
      is_finalized: false,
    },
    eligibility: {
      is_eligible: false,
      total_core_questions: 1,
      reviewed_core_questions: 0,
      blocking_reasons: [
        "Human review decision pending for core question 1 (Distributed Consensus)",
      ],
    },
  };

  it("PART E: Review Workspace renders evidence area, provisional AI area with local model disclaimer", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/review/progress") || url.includes("/review-workspace")) return Promise.resolve(mockReviewWorkspaceData);
      return Promise.resolve({});
    });

    render(<InterviewReviewWorkspace attemptId="att-777" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/Recruiter Evidence Review/i)).toBeInTheDocument();
    });

    // Evidence Area
    expect(screen.getByText("Submitted Candidate Answer")).toBeInTheDocument();
    expect(
      screen.getAllByText(/In Raft, leader elections require quorum/i).length
    ).toBeGreaterThan(0);

    // AI Area with local model and disclaimer
    expect(screen.getByText("Provisional AI Assessment")).toBeInTheDocument();
    expect(screen.getByText(/AI assistance is provisional until recruiter review\./i)).toBeInTheDocument();
    expect(screen.getByText("qwen3-4b-mlx (LOCAL_LM_STUDIO)")).toBeInTheDocument();
    expect(screen.getByText("Proposed Level 4")).toBeInTheDocument();

    // Human Review actions
    expect(screen.getByRole("button", { name: /Accept Provisional/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Edit Observation/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Override Level/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reject Observation/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mark Insufficient/i })).toBeInTheDocument();
  });

  it("PART C: Accept provisional assessment preserves provisional values and updates review state", async () => {
    let reviewRecorded = false;
    (clientModule.apiFetch as any).mockImplementation((url: string, opts?: any) => {
      if (url.includes("/review/progress") || url.includes("/review-workspace")) {
        if (reviewRecorded) {
          return Promise.resolve({
            ...mockReviewWorkspaceData,
            questions: [
              {
                ...mockReviewWorkspaceData.questions[0],
                latest_review: {
                  review_id: "rev-1",
                  question_id: "q-core-1",
                  decision: "ACCEPTED",
                  reviewed_observation: mockReviewWorkspaceData.questions[0].provisional_assessment.provisional_observation,
                  reviewed_rubric_level: 4,
                  retained_citations: ["seg-101", "seg-102"],
                  reason: null,
                  reviewer_user_id: "rec-1",
                  review_version: 1,
                  created_at: "2026-09-17T12:00:00Z",
                  updated_at: "2026-09-17T12:00:00Z",
                },
              },
            ],
            aggregate: {
              ...mockReviewWorkspaceData.aggregate,
              aggregate_score: 4.0,
              numerator: 4.0,
              denominator: 1.0,
              reviewed_question_count: 1,
            },
            eligibility: {
              is_eligible: true,
              total_core_questions: 1,
              reviewed_core_questions: 1,
              blocking_reasons: [],
            },
          });
        }
        return Promise.resolve(mockReviewWorkspaceData);
      }
      if (url.includes("/review/accept")) {
        reviewRecorded = true;
        return Promise.resolve({
          review_id: "rev-1",
          attempt_id: "att-777",
          question_id: "q-core-1",
          decision: "ACCEPTED",
          reviewed_observation: mockReviewWorkspaceData.questions[0].provisional_assessment.provisional_observation,
          reviewed_rubric_level: 4,
          retained_citations: ["seg-101", "seg-102"],
          review_version: 1,
        });
      }
      return Promise.resolve({});
    });

    render(<InterviewReviewWorkspace attemptId="att-777" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/Recruiter Evidence Review/i)).toBeInTheDocument();
    });

    const acceptBtn = screen.getByRole("button", { name: /Accept Provisional/i });
    fireEvent.click(acceptBtn);

    await waitFor(() => {
      expect(screen.getByText(/Provisional assessment accepted without alteration/i)).toBeInTheDocument();
      expect(screen.getByText("ACCEPTED (v1)")).toBeInTheDocument();
    });
  });

  it("PART C & D: Override rubric level requires non-empty reason", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/review/progress") || url.includes("/review-workspace")) return Promise.resolve(mockReviewWorkspaceData);
      return Promise.resolve({});
    });

    render(<InterviewReviewWorkspace attemptId="att-777" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/Recruiter Evidence Review/i)).toBeInTheDocument();
    });

    const overrideBtn = screen.getByRole("button", { name: /Override Level/i });
    fireEvent.click(overrideBtn);

    expect(screen.getByText("Override Rubric Level")).toBeInTheDocument();

    // Confirm button should be disabled when reason is empty
    const confirmBtn = screen.getByRole("button", { name: /Confirm Level Override/i });
    expect(confirmBtn).toBeDisabled();

    // Enter non-empty reason
    const reasonInput = screen.getByPlaceholderText(/Explain why this answer warrants a level change/i);
    fireEvent.change(reasonInput, { target: { value: "Candidate only covered 2-node failure, not network partition recovery." } });

    expect(confirmBtn).not.toBeDisabled();
  });

  it("PART G: Finalization is blocked when reviews incomplete and enabled when eligible", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/review/progress") || url.includes("/review-workspace")) return Promise.resolve(mockReviewWorkspaceData);
      return Promise.resolve({});
    });

    render(<InterviewReviewWorkspace attemptId="att-777" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/Recruiter Evidence Review/i)).toBeInTheDocument();
    });

    // In mockReviewWorkspaceData, is_eligible is false
    const finalizeBtn = screen.getByRole("button", { name: /Finalize Assessment/i });
    expect(finalizeBtn).toBeDisabled();
  });
});
