import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
import { InterviewReviewWorkspace } from "../components/interviews/InterviewReviewWorkspace";
import { SimulationWorkspace } from "../components/interviews/SimulationWorkspace";
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

describe("Phase 3D.4 Final UI Reconstruction, Accessibility, and Evaluation Tests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockContext = {
    job_id: "job-101",
    job_title: "Staff Backend Architect",
    job_version_id: "v-1",
    job_revision: 1,
    candidate_id: "cand-202",
    candidate_name: "Alex Rivera",
    resume_artifact_id: "res-303",
    match_score: 88.5,
    skill_gap_summary: "Distributed consensus and storage internals",
    ai_capability: {
      ai_enabled: true,
      provider_class: "LM_STUDIO_LOCAL",
      availability_label: "AI Active (Local MLX Qwen)",
      egress_class: "LOCAL_ONLY" as const,
      active_model_name: "qwen3-4b-mlx",
    },
  };

  const mockTemplateData = {
    template_id: "tmpl-505",
    job_id: "job-101",
    template_version: 1,
    lifecycle_status: "DRAFT" as const,
    common_core_questions: [
      {
        question_id: "q-1",
        layer: "COMMON_CORE" as const,
        question_type: "PROBLEM_SOLVING" as const,
        competency_name: "Distributed Consensus",
        question_text: "Explain how Raft handles leader elections during network partitioning.",
        display_order: 0,
        estimated_time_minutes: 6,
        authoring_origin: "AI_ASSISTED" as const,
        deterministic_source_text: "Explain how Raft prevents split-brain elections.",
        deterministic_source_available: true,
        ai_suggestion_text: null,
        has_wording_drift: true,
        can_restore: true,
        question_version: 2,
        rubric: {
          rubric_id: "rub-1",
          target_competency: "Distributed Consensus",
          competency_definition: "Consensus safety and quorum guarantees",
          question_intent: "Assess partitioning resilience",
          rating_anchors: {
            "1": "Superficial knowledge",
            "3": "Solid quorum understanding",
            "5": "Mastery of term numbers and log replication",
          },
          positive_signals: ["Mentions quorum"],
          red_flags: ["Ignores split brain"],
          scoring_guide: "Evaluate consensus rigor",
          rubric_version: 1,
        },
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
        question_type: "TECHNICAL" as const,
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
    content_hash: "hashPhase3D4",
    authoritative_snapshot_hash: "snapPhase3D4",
    created_at: "2026-09-18T10:00:00Z",
  };

  it("PART A & B: Renders reconstructed deterministic blueprint header with title, explanation note, and AI action", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateData);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => { }} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Staff Backend Architect")).toBeInTheDocument();
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

    // Deterministic blueprint structure
    expect(screen.getByText("Authoritative Blueprint Structure")).toBeInTheDocument();
  });

  it("PART B: Editorial question list shows flat items without duplicated cards or chevrons", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateData);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => { }} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Staff Backend Architect")).toBeInTheDocument();
    });

    // Question 1 metadata and actions
    expect(screen.getByText("Question 1")).toBeInTheDocument();
    expect(screen.getByText("Distributed Consensus")).toBeInTheDocument();
    expect(screen.getByText("Explain how Raft handles leader elections during network partitioning.")).toBeInTheDocument();

    // Direct actions
    expect(screen.getAllByRole("button", { name: /Edit/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /View rubric/i })).toBeInTheDocument();

    // Question 2 is Deterministic
    expect(screen.getByText("Question 2")).toBeInTheDocument();
    expect(screen.getByText("Describe an incident where observability pipelines stalled during peak load.")).toBeInTheDocument();

    // Question 3 is rendered
    expect(screen.getByText("Question 3")).toBeInTheDocument();
  });

  it("PART B5: Template action section appears in document flow with compact mode notice", async () => {
    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateData);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-101" onNavigateBack={() => { }} />);

    await waitFor(() => {
      expect(screen.getByText("Interview Template: Staff Backend Architect")).toBeInTheDocument();
    });

    // Template actions
    expect(screen.getByRole("button", { name: /Save Draft/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Validate/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve & Lock Baseline/i })).toBeInTheDocument();

    // Compact mode note
    expect(screen.getByText("Standardized Job Template Mode")).toBeInTheDocument();
    expect(
      screen.getByText(/You are managing the common core interview questions and anchored rubrics/i)
    ).toBeInTheDocument();
  });

  it("PART C & D: Review Workspace renders Visual Summaries (progress, rating distribution, timeline, aggregate)", async () => {
    const mockReviewData = {
      attempt_id: "att-888",
      organization_id: "org-test",
      lifecycle_status: "PROVISIONALLY_ASSESSED",
      candidate_id: "cand-202",
      candidate_name: "Alex Rivera",
      plan_id: "plan-303",
      job_id: "job-101",
      job_title: "Staff Backend Architect",
      record_version: 1,
      is_finalized: false,
      eligibility: {
        is_eligible: false,
        total_core_questions: 1,
        reviewed_core_questions: 0,
        pending_core_questions: 1,
        missing_citations_count: 0,
        blocking_reasons: ["Human review decision pending for core question 1 (Distributed Consensus)"],
      },
      aggregate: {
        policy_version: "1.0.0",
        policy_hash: "aggHashPhase3D4",
        aggregate_score: 4.0,
        numerator: 4,
        denominator: 1,
        total_core_questions: 1,
        scored_core_questions: 1,
        excluded_core_questions: 0,
        supplemental_questions_excluded: 0,
        weights_breakdown: { "Distributed Consensus": 1.0 },
        included_items: [
          { question_id: "q-1", competency: "Distributed Consensus", level: 4, weight: 1.0 },
        ],
        excluded_items: [],
      },
      questions: [
        {
          question_id: "q-1",
          layer: "COMMON_CORE",
          display_order: 0,
          competency_name: "Distributed Consensus",
          question_text: "Explain how Raft handles leader elections during network partitioning.",
          submitted_answer: "In Raft, leader elections require majority quorum.",
          answer_segments: [
            {
              segment_id: "seg-1",
              sequence_order: 0,
              start_char: 0,
              end_char: 49,
              segment_text: "In Raft, leader elections require majority quorum.",
            },
          ],
          provisional_assessment: {
            question_id: "q-1",
            assessment_status: "PROVISIONAL",
            provisional_level: 4,
            criterion_observations: [
              {
                criterion_id: "Distributed Consensus",
                observation_text: "Candidate correctly identified quorum requirements.",
                cited_segment_ids: ["seg-1"],
              },
            ],
            cited_segment_ids: ["seg-1"],
            uncertainty_codes: [],
            model_invocation_metadata: {
              model_name: "qwen3-4b-mlx",
              provider_class: "LOCAL_LM_STUDIO",
            },
          },
          rubric: {
            rubric_id: "rub-1",
            target_competency: "Distributed Consensus",
            levels: [],
          },
          latest_review: null,
          review_history: [],
        },
      ],
    };

    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/review/progress") || url.includes("/review-workspace")) {
        return Promise.resolve(mockReviewData);
      }
      return Promise.resolve({});
    });

    render(<InterviewReviewWorkspace attemptId="att-888" onNavigateBack={() => { }} />);

    await waitFor(() => {
      expect(screen.getByText(/Recruiter Evidence Review/i)).toBeInTheDocument();
    });

    // Visual Summaries Grid
    expect(screen.getByText("Review Progress & Core Separation")).toBeInTheDocument();
    expect(screen.getByText("Core Rating Distribution (Human-Reviewed)")).toBeInTheDocument();
    expect(screen.getByText("Assessment Provenance Timeline")).toBeInTheDocument();

    // Timeline steps
    expect(screen.getByText("1. Candidate Answer Submitted")).toBeInTheDocument();
    expect(screen.getByText("2. Provisional Local AI Assessment")).toBeInTheDocument();
    expect(screen.getByText(/3\. Recruiter Human Review/i)).toBeInTheDocument();
    expect(screen.getByText(/4\. Finalized & Immutable Lock/i)).toBeInTheDocument();

    // Provenance disclaimer
    expect(screen.getByText(/AI assistance is provisional until recruiter review/i)).toBeInTheDocument();
    expect(screen.getByText("qwen3-4b-mlx (LOCAL_LM_STUDIO)")).toBeInTheDocument();
  });
});
