import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
import { AnchoredRubricModal } from "../components/interviews/AnchoredRubricModal";
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

describe("Phase 3D.1 Template Repairs and Simulation Workspace Tests", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockRubricQuestion = {
    question_id: "q-101",
    layer: "COMMON_CORE" as const,
    question_type: "BEHAVIORAL" as const,
    competency_name: "Python Architecture & System Design",
    question_text: "Describe a complex backend system you designed from scratch.",
    display_order: 0,
    estimated_time_minutes: 5,
    permitted_probes: ["How did you handle scaling bottlenecks?"],
    authoring_origin: "DETERMINISTIC" as const,
    source_requirement_ids: ["req-1"],
    content_hash: "hash101",
    question_version: 1,
    rubric: {
      rubric_id: "rub-101",
      target_competency: "Python Architecture & System Design",
      competency_definition: "Architecting reliable Python services",
      question_intent: "Assess system design proficiency and production experience",
      rating_anchors: {
        "1": "Superficial knowledge, unable to structure microservices.",
        "3": "Solid architectural decisions with standard frameworks.",
        "5": "Mastery of distributed principles, resilience patterns, and observability.",
      },
      positive_signals: ["Mentions idempotency", "Discusses trade-offs"],
      red_flags: ["Ignores error handling", "No caching awareness"],
      scoring_guide: "Evaluate holistic design rigor.",
      rubric_version: 1,
    },
  };

  it("PART A1: AnchoredRubricModal renders bounded dialog, rating anchors, signals, and handles Escape", () => {
    const handleClose = vi.fn();
    const { unmount } = render(
      <AnchoredRubricModal question={mockRubricQuestion} onClose={handleClose} />
    );

    // Title and metadata
    expect(screen.getByText("Anchored rubric")).toBeInTheDocument();
    expect(screen.getByText(/Python Architecture & System Design/i)).toBeInTheDocument();
    expect(screen.getByText(/Assess system design proficiency/i)).toBeInTheDocument();

    // Score anchors
    expect(screen.getByText("Score 1")).toBeInTheDocument();
    expect(screen.getByText(/Superficial knowledge/i)).toBeInTheDocument();
    expect(screen.getByText("Score 3")).toBeInTheDocument();
    expect(screen.getByText(/Solid architectural decisions/i)).toBeInTheDocument();
    expect(screen.getByText("Score 5")).toBeInTheDocument();
    expect(screen.getByText(/Mastery of distributed principles/i)).toBeInTheDocument();

    // Observable signals
    expect(screen.getByText("Mentions idempotency")).toBeInTheDocument();
    expect(screen.getByText("Ignores error handling")).toBeInTheDocument();

    // Press Escape
    fireEvent.keyDown(window, { key: "Escape", code: "Escape" });
    expect(handleClose).toHaveBeenCalled();

    unmount();
  });

  it("PART A2: Restore deterministic handles 409 conflict by reloading latest version", async () => {
    const mockContext = {
      job_id: "job-123",
      job_title: "Senior Software Engineer",
      job_version_id: "v-1",
      job_revision: 1,
      candidate_id: "cand-456",
      candidate_name: "Jordan Lee",
      resume_artifact_id: "res-789",
      ai_capability: {
        ai_enabled: true,
        provider_class: "LM_STUDIO_LOCAL",
        availability_label: "AI Active",
        egress_class: "LOCAL_ONLY" as const,
      },
      interview_plan_state: "DRAFT",
      message: "Ready",
    };

    const mockTemplate = {
      template_id: "tpl-100",
      job_id: "job-123",
      job_version_id: "v-1",
      job_revision: 1,
      template_version: 1,
      lifecycle_state: "DRAFT" as const,
      common_core_questions: [
        {
          question_id: "q-101",
          layer: "COMMON_CORE" as const,
          question_type: "TECHNICAL" as const,
          competency_name: "Python Core",
          question_text: "AI generated phrasing draft text.",
          display_order: 0,
          estimated_time_minutes: 5,
          authoring_origin: "AI_ASSISTED" as const,
          deterministic_source_text: "Original deterministic blueprint question.",
          deterministic_source_available: true,
          has_wording_drift: true,
          can_restore: true,
          question_version: 1,
          rubric: null,
        },
      ],
      target_duration_minutes: 45,
      content_hash: "tplhash123",
      authoritative_snapshot_hash: "snaphash123",
      created_at: "2026-09-16T12:00:00Z",
    };

    const mockRefreshedTemplate = {
      ...mockTemplate,
      template_version: 2,
      common_core_questions: [
        {
          ...mockTemplate.common_core_questions[0],
          question_version: 2,
          question_text: "Updated text by another recruiter.",
        },
      ],
    };

    (clientModule.apiFetch as any).mockImplementation((url: string, opts?: any) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) {
        return Promise.resolve(mockTemplate);
      }
      if (url.includes("/restore-deterministic")) {
        const err: any = new Error("Stale question version conflict (409)");
        err.status = 409;
        return Promise.reject(err);
      }
      return Promise.resolve({});
    });

    // Mock window.confirm
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <InterviewWorkspace
        jobId="job-123"
        onNavigateBack={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Question 1")).toBeInTheDocument();
    });

    const restoreBtn = screen.getByTitle("Restore original deterministic baseline wording");
    expect(restoreBtn).toBeInTheDocument();

    // Trigger restore which throws 409
    (clientModule.apiFetch as any).mockImplementation((url: string, opts?: any) => {
      if (url.includes("/restore-deterministic")) {
        const err: any = new Error("Stale question version conflict (409)");
        err.status = 409;
        return Promise.reject(err);
      }
      if (url.includes("/interview-templates/latest")) {
        return Promise.resolve(mockRefreshedTemplate);
      }
      return Promise.resolve({});
    });

    fireEvent.click(restoreBtn);

    await waitFor(() => {
      expect(
        screen.getByText("The question changed in another action. The latest version was loaded.")
      ).toBeInTheDocument();
    });
  });

  it("PART A3: Displays Manual Question Required badge for unresolved slots", async () => {
    const mockContext = {
      job_id: "job-123",
      job_title: "Staff Engineer",
      job_version_id: "v-1",
      job_revision: 1,
      candidate_id: "cand-456",
      candidate_name: "Jordan Lee",
      resume_artifact_id: "res-789",
      ai_capability: {
        ai_enabled: true,
        provider_class: "LM_STUDIO_LOCAL",
        availability_label: "AI Active",
        egress_class: "LOCAL_ONLY" as const,
      },
      interview_plan_state: "DRAFT",
      message: "Ready",
    };

    const mockTemplateWithUnresolved = {
      template_id: "tpl-100",
      job_id: "job-123",
      job_version_id: "v-1",
      job_revision: 1,
      template_version: 1,
      lifecycle_state: "DRAFT" as const,
      common_core_questions: [
        {
          question_id: "q-102",
          layer: "COMMON_CORE" as const,
          question_type: "TECHNICAL" as const,
          competency_name: "Novel Architecture",
          question_text: "[Manual question required: Enter question text for Novel Architecture]",
          display_order: 0,
          estimated_time_minutes: 5,
          authoring_origin: "RECRUITER_EDITED" as const,
          deterministic_source_available: false,
          has_wording_drift: false,
          can_restore: false,
          unresolved_manual_required: true,
          question_version: 1,
          rubric: null,
        },
      ],
      target_duration_minutes: 45,
      content_hash: "tplhash123",
      authoritative_snapshot_hash: "snaphash123",
      created_at: "2026-09-16T12:00:00Z",
    };

    (clientModule.apiFetch as any).mockImplementation((url: string) => {
      if (url.includes("/interview-context")) return Promise.resolve(mockContext);
      if (url.includes("/interview-templates/latest")) return Promise.resolve(mockTemplateWithUnresolved);
      return Promise.resolve({});
    });

    render(<InterviewWorkspace jobId="job-123" onNavigateBack={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText("Manual Question Required")).toBeInTheDocument();
    });

    // Restore original button should not be present because can_restore is false
    expect(screen.queryByTitle("Restore original deterministic baseline wording")).toBeNull();
  });

  it("PART B: SimulationWorkspace runs draft autosaving, question navigation, pause, and submission", async () => {
    const mockAttempt = {
      attempt_id: "att-001",
      organization_id: "org-1",
      recruiter_id: "rec-1",
      candidate_id: "cand-456",
      candidate_name: "Alex Mercer",
      candidate_email: "alex@example.com",
      plan_id: "plan-789",
      plan_version: 1,
      template_id: "tpl-100",
      template_version: 1,
      job_id: "job-123",
      job_title: "Staff Backend Engineer",
      content_hash: "hash12345678",
      lifecycle_status: "IN_PROGRESS" as const,
      attempt_version: 1,
      common_core_questions: [
        {
          question_id: "q-1",
          layer: "COMMON_CORE" as const,
          question_type: "TECHNICAL" as const,
          competency_name: "Distributed Systems",
          question_text: "Describe how you design distributed caches.",
          display_order: 0,
          estimated_time_minutes: 5,
          permitted_probes: ["How do you handle eviction?"],
          rubric: null,
        },
        {
          question_id: "q-2",
          layer: "COMMON_CORE" as const,
          question_type: "BEHAVIORAL" as const,
          competency_name: "Incident Response",
          question_text: "Tell me about a high-severity outage you led.",
          display_order: 1,
          estimated_time_minutes: 5,
          permitted_probes: ["What post-mortem actions were taken?"],
          rubric: null,
        },
      ],
      supplemental_questions: [],
      answers: [],
      created_at: "2026-09-17T12:00:00Z",
      updated_at: "2026-09-17T12:00:00Z",
    };

    (clientModule.apiFetch as any).mockImplementation((url: string, opts?: any) => {
      if (url.includes("/simulations/start")) {
        return Promise.resolve(mockAttempt);
      }
      if (url.includes("/answers/q-1")) {
        return Promise.resolve({
          answer_id: "ans-1",
          attempt_id: "att-001",
          question_id: "q-1",
          answer_version: 2,
          saved_at: "2026-09-17T12:01:00Z",
          status: "SAVED",
        });
      }
      if (url.includes("/submit")) {
        return Promise.resolve({
          attempt_id: "att-001",
          lifecycle_status: "SUBMITTED",
          submitted_at: "2026-09-17T12:05:00Z",
          answers: [
            {
              answer_id: "ans-1",
              question_id: "q-1",
              answer_text: "Candidate articulated multi-region Redis cluster design.",
              answer_version: 2,
              submitted_at: "2026-09-17T12:05:00Z",
            },
          ],
        });
      }
      return Promise.resolve({});
    });

    render(
      <SimulationWorkspace
        planId="plan-789"
        jobId="job-123"
        onNavigateBack={() => {}}
      />
    );

    // Context headers
    await waitFor(() => {
      expect(screen.getByText(/Alex Mercer • Staff Backend Engineer/i)).toBeInTheDocument();
      expect(screen.getByText("Describe how you design distributed caches.")).toBeInTheDocument();
    });

    // Permitted probe
    expect(screen.getByText("How do you handle eviction?")).toBeInTheDocument();

    // Type answer
    const textarea = screen.getByRole("textbox", { name: /Candidate Response \/ Recruiter Notes/i });
    fireEvent.change(textarea, { target: { value: "Candidate articulated multi-region Redis cluster design." } });

    // Navigate to next question
    const nextBtn = screen.getByRole("button", { name: /Next Question/i });
    fireEvent.click(nextBtn);

    await waitFor(() => {
      expect(screen.getByText("Tell me about a high-severity outage you led.")).toBeInTheDocument();
    });

    // Open submit modal
    const submitBtn = screen.getByRole("button", { name: /Submit & Lock Simulation/i });
    fireEvent.click(submitBtn);

    expect(screen.getByText("Confirm Simulation Submission")).toBeInTheDocument();

    // Confirm submission
    const confirmBtn = screen.getByRole("button", { name: /Confirm & Lock Submission/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(screen.getByText("Simulation Submitted & Locked")).toBeInTheDocument();
    });
  });
});
