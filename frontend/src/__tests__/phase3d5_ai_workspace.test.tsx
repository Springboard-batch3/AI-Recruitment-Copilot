import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AuthenticatedShell } from "../components/AuthenticatedShell";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
import { AiInterviewsDirectory } from "../components/ai_interviews/AiInterviewsDirectory";
import { AiQuestionSetWorkspace } from "../components/ai_interviews/AiQuestionSetWorkspace";
import { AuthContext } from "../context/AuthContext";
import * as clientModule from "../api/client";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

describe("Phase 3D.5 Frontend: Dedicated AI Interview Questions Workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockRecruiterAuth = {
    user: {
      user_id: "u-recruiter-1",
      userId: "u-recruiter-1",
      email: "recruiter@example.com",
      display_name: "Morgan Recruiter",
      displayName: "Morgan Recruiter",
      role: "RECRUITER" as const,
      is_active: true,
      isActive: true,
      must_change_password: false,
      mustChangePassword: false,
    },
    status: "AUTHENTICATED" as const,
    csrfToken: "mock-csrf",
    sessionError: null,
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshSession: vi.fn(),
    clearSessionError: vi.fn(),
  };

  describe("PART A: Top-Level AI Questions Navigation Item", () => {
    it("renders AI Questions tab with accessible label for recruiters", () => {
      const handleNavigate = vi.fn();
      render(
        <AuthContext.Provider value={mockRecruiterAuth}>
          <AuthenticatedShell
            activeNavTab="ai_interviews"
            onNavigateTab={handleNavigate}
            aiQuestionsCount={3}
          >
            <div>Workspace content</div>
          </AuthenticatedShell>
        </AuthContext.Provider>
      );

      const aiTab = screen.getByRole("button", { name: /AI-generated interview questions/i });
      expect(aiTab).toBeInTheDocument();
      expect(aiTab).toHaveClass("tab-active");
      expect(screen.getByText("Interviews")).toBeInTheDocument();

      fireEvent.click(aiTab);
      expect(handleNavigate).toHaveBeenCalledWith("ai_interviews");
    });
  });

  describe("PART B: Deterministic-Only Interview Template Workspace", () => {
    const mockContext = {
      job_id: "job-101",
      job_title: "Staff Distributed Systems Engineer",
      job_version_id: "v-1",
      job_revision: 2,
      candidate_id: "",
      candidate_name: "",
      resume_artifact_id: "",
      ai_capability: {
        ai_enabled: true,
        provider_class: "LM_STUDIO_LOCAL",
        availability_label: "AI Active",
        egress_class: "LOCAL_ONLY" as const,
      },
      interview_plan_state: "DRAFT",
      message: "Context ready",
    };

    const mockTemplate = {
      template_id: "tpl-101",
      job_id: "job-101",
      job_version_id: "v-1",
      job_revision: 2,
      template_version: 1,
      lifecycle_status: "DRAFT",
      common_core_questions: [
        {
          question_id: "slot-1",
          question_type: "TECHNICAL",
          competency_name: "DISTRIBUTED_CONSENSUS",
          question_text: "Deterministic blueprint question: explain Paxos state machine replication.",
          display_order: 0,
          estimated_time_minutes: 10,
          rubric: {
            rubric_id: "rub-1",
            rubric_version: 1,
            competency_definition: "Understands consensus quorum algorithms",
            question_intent: "Assess distributed consistency comprehension",
            observable_indicators: ["Mentions quorum reads", "Explains log replication"],
            rating_anchors: { "1": "Basic", "3": "Solid", "5": "Expert" },
            permitted_probes: ["What happens on partition?"],
          },
        },
      ],
    };

    it("displays 'Interview Template' heading and authoritative blueprint explanation note", async () => {
      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string) => {
        if (url.includes("/interview-context")) return mockContext;
        if (url.includes("/interview-templates/latest")) return mockTemplate;
        if (url.includes("/ai-question-set")) return { ai_set_id: "ai-set-1" };
        return {};
      });

      render(
        <InterviewWorkspace
          jobId="job-101"
          onNavigateBack={vi.fn()}
          onNavigateToAiInterview={vi.fn()}
        />
      );

      // Verify heading says Interview Template
      expect(await screen.findByRole("heading", { name: /Interview Template: Staff Distributed Systems Engineer/i })).toBeInTheDocument();

      // Verify explanation note
      expect(screen.getByText(/This workspace defines the deterministic interview blueprint for this role/i)).toBeInTheDocument();

      // Verify Generate AI Questions button is present
      expect(screen.getByRole("button", { name: /Generate AI Questions/i })).toBeInTheDocument();

      // Verify Open AI Question Set button is present when set exists
      expect(await screen.findByRole("button", { name: /Open AI Question Set/i })).toBeInTheDocument();
    });

    it("triggers AI question generation and navigates to AI workspace", async () => {
      const handleNavigateAi = vi.fn();
      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string, opts?: any) => {
        if (url.includes("/interview-context")) return mockContext;
        if (url.includes("/interview-templates/latest")) return mockTemplate;
        if (url.includes("/ai-question-sets/generate")) {
          return { ai_set_id: "ai-set-new" };
        }
        throw new Error("404");
      });

      render(
        <InterviewWorkspace
          jobId="job-101"
          onNavigateBack={vi.fn()}
          onNavigateToAiInterview={handleNavigateAi}
        />
      );

      const generateBtn = await screen.findByRole("button", { name: /Generate AI Questions/i });
      fireEvent.click(generateBtn);

      await waitFor(() => {
        expect(handleNavigateAi).toHaveBeenCalledWith("tpl-101");
      });
    });
  });

  describe("PART E: Job-First AI Interview Questions Workspace", () => {
    const mockEligibleJobs = {
      items: [
        {
          job_id: "job-101",
          job_title: "Staff Distributed Systems Engineer",
          requirement_count: 5,
        },
        {
          job_id: "job-102",
          job_title: "Junior Python API Developer",
          requirement_count: 3,
        },
      ],
    };

    const mockDirectoryData = {
      sets: [
        {
          ai_set_id: "set-1",
          set_version: 1,
          template_id: "tpl-101",
          job_id: "job-101",
          job_title: "Staff Distributed Systems Engineer",
          lifecycle_status: "DRAFT" as const,
          is_immutable: false,
          is_source_stale: false,
          generated_question_count: 3,
          total_blueprint_slots: 3,
          created_at: "2026-09-18T10:00:00Z",
          updated_at: "2026-09-18T10:30:00Z",
        },
      ],
      total_count: 1,
      awaiting_review_count: 1,
      ready_for_approval_count: 0,
      approved_count: 0,
    };

    it("renders job-first creation screen, selects job, generates questions and handles recent sets", async () => {
      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string, opts?: any) => {
        if (url.includes("/eligible-jobs")) return mockEligibleJobs;
        if (url.includes("/ai-interviews/generate")) {
          return { ai_set_id: "set-new", template_id: "tpl-102" };
        }
        if (url.includes("/ai-interviews") || url.includes("/ai-question-sets")) return mockDirectoryData;
        return {};
      });

      const handleSelect = vi.fn();
      render(<AiInterviewsDirectory onSelectTemplate={handleSelect} />);

      // Verify heading and supporting text
      expect(await screen.findByRole("heading", { name: /AI Interviews|AI interview questions/i })).toBeInTheDocument();
      expect(screen.getByText(/Choose a job, generate/i)).toBeInTheDocument();

      // Verify available count: job-101 is excluded because it already has a set in mockDirectoryData.sets, so only job-102 is available
      expect(screen.getByText(/1 job (available|ready)/i)).toBeInTheDocument();

      // job-101 only appears once in existing work / Continue reviewing, not in Choose a job
      expect(screen.getAllByText("Staff Distributed Systems Engineer").length).toBe(1);
      // job-102 appears in Choose a job
      expect(screen.getByText("Junior Python API Developer")).toBeInTheDocument();
      expect(screen.getByText("3 requirements")).toBeInTheDocument();

      // Verify generate button initial state
      const generateBtn = screen.getByRole("button", { name: /Generate AI questions|Open AI questions/i });
      expect(generateBtn).toBeDisabled();

      // Select job-102
      const radioInput = screen.getByRole("radio", { name: /Junior Python API Developer/i });
      fireEvent.click(radioInput);

      // Now generate button is enabled
      const activeGenerateBtn = screen.getByRole("button", { name: "Generate AI questions" });
      expect(activeGenerateBtn).toBeEnabled();

      // Click Generate questions
      fireEvent.click(activeGenerateBtn);

      await waitFor(() => {
        expect(handleSelect).toHaveBeenCalledWith("set-new");
      });

      // Verify recent AI question sets section
      expect(screen.getByText("Continue reviewing")).toBeInTheDocument();
      const openBtn = screen.getByRole("button", { name: /Continue review for Staff Distributed Systems Engineer/i });
      fireEvent.click(openBtn);
      expect(handleSelect).toHaveBeenCalledWith("set-1");
    });

    it("renders compact empty state when all eligible jobs already have AI question sets", async () => {
      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string) => {
        if (url.includes("/eligible-jobs")) {
          return {
            items: [
              {
                job_id: "job-101",
                job_title: "Staff Distributed Systems Engineer",
                requirement_count: 5,
              },
            ],
          };
        }
        if (url.includes("/ai-interviews") || url.includes("/ai-question-sets")) return mockDirectoryData;
        return {};
      });

      render(<AiInterviewsDirectory onSelectTemplate={vi.fn()} />);

      // job-101 has an existing set, so selectable jobs = 0, triggering compact empty state for job selection
      expect(await screen.findByText(/All ready jobs have (AI|text) question sets/i)).toBeInTheDocument();
      // But Continue reviewing list is still rendered
      expect(screen.getByText("Continue reviewing")).toBeInTheDocument();
      expect(screen.getByText("Staff Distributed Systems Engineer")).toBeInTheDocument();
    });

    it("renders compact empty state when no jobs are ready", async () => {
      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string) => {
        if (url.includes("/eligible-jobs")) return { items: [] };
        if (url.endsWith("/ai-interviews") || url.endsWith("/ai-question-sets")) return { sets: [], total_count: 0, awaiting_review_count: 0, ready_for_approval_count: 0, approved_count: 0 };
        return {};
      });

      const handleGoToJobs = vi.fn();
      render(<AiInterviewsDirectory onSelectTemplate={vi.fn()} onNavigateToJobs={handleGoToJobs} />);

      expect(await screen.findByText("No jobs are ready yet")).toBeInTheDocument();
      expect(screen.getByText(/Complete a job and add approved requirements before generating/i)).toBeInTheDocument();
      const goBtn = screen.getByRole("button", { name: "Go to Jobs" });
      fireEvent.click(goBtn);
      expect(handleGoToJobs).toHaveBeenCalled();
    });
  });

  describe("PART F, G & D: AI Question Set Workspace & Safe Rubric Modal", () => {
    const mockDetail = {
      ai_set_id: "set-1",
      set_version: 1,
      template_id: "tpl-101",
      source_template_version: 1,
      source_content_hash: "hash-12345",
      job_id: "job-101",
      job_title: "Staff Distributed Systems Engineer",
      job_revision: 2,
      lifecycle_status: "DRAFT" as const,
      is_immutable: false,
      total_slots_count: 2,
      generated_slots_count: 2,
      reviewed_slots_count: 0,
      is_source_stale: false,
      created_at: "2026-09-18T10:00:00Z",
      updated_at: "2026-09-18T10:30:00Z",
      slots: [
        {
          slot_id: "slot-1",
          display_order: 0,
          competency_name: "DISTRIBUTED_CONSENSUS",
          question_type: "TECHNICAL",
          estimated_time_minutes: 8,
          permitted_probes: ["How is leader failure handled?"],
          ai_wording: "Can you describe your experience implementing leader election in a distributed cluster?",
          review_state: "UNREVIEWED" as const,
          authoring_origin: "AI_GENERATED",
          validation_status: "PASSED" as const,
          rubric: {
            rubric_id: "rub-1",
            rubric_version: 1,
            competency_definition: "Evaluates leader lease management and heartbeat handling.",
            question_intent: "Assess quorum state transitions.",
            observable_indicators: ["Mentions term numbers", "Heartbeat timers"],
            rating_anchors: { "1": "Novice", "3": "Proficient", "5": "Master" },
            permitted_probes: ["How is leader failure handled?"],
          },
        },
        {
          slot_id: "slot-2",
          display_order: 1,
          competency_name: "INCIDENT_TRIAGE",
          question_type: "SITUATIONAL",
          estimated_time_minutes: 7,
          permitted_probes: ["What if logs are corrupted?"],
          ai_wording: "Tell me about a time when network partition caused inconsistency and how you triaged it.",
          review_state: "UNREVIEWED" as const,
          authoring_origin: "AI_GENERATED",
          validation_status: "PASSED" as const,
          rubric: null,
        },
      ],
    };

    it("renders slots in navigator, opens SafeRubricModal with zero leakage, and keeps slot", async () => {
      vi.mocked(clientModule.apiFetch).mockResolvedValue(mockDetail);

      render(<AiQuestionSetWorkspace templateId="tpl-101" onNavigateBack={vi.fn()} />);

      expect(await screen.findByText("Staff Distributed Systems Engineer")).toBeInTheDocument();
      expect(screen.getAllByText("Distributed Consensus").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("Incident Triage")).toBeInTheDocument();

      // Open rubric modal
      const rubricBtn = screen.getByRole("button", { name: /view rubric/i });
      fireEvent.click(rubricBtn);

      expect(await screen.findByRole("heading", { name: /evaluation rubric: distributed consensus/i })).toBeInTheDocument();
      expect(screen.getByText("Evaluates leader lease management and heartbeat handling.")).toBeInTheDocument();
      // Verify zero leakage: no baseline question text
      expect(screen.queryByText(/Deterministic blueprint question/i)).not.toBeInTheDocument();

      // Close modal
      const closeBtn = screen.getByRole("button", { name: /Close Rubric/i });
      fireEvent.click(closeBtn);

      // Keep slot
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce({
        ...mockDetail,
        reviewed_slots_count: 1,
        slots: [
          { ...mockDetail.slots[0], review_state: "KEPT" as const },
          mockDetail.slots[1],
        ],
      });

      const markReviewedBtn = screen.getByRole("button", { name: /mark as reviewed/i });
      fireEvent.click(markReviewedBtn);

      await waitFor(() => {
        expect(screen.getAllByText("Question wording marked as reviewed.").length).toBeGreaterThanOrEqual(1);
      });
    });

    it("validates, approves and seals the AI question set", async () => {
      const allReviewedDetail = {
        ...mockDetail,
        reviewed_slots_count: 2,
        slots: [
          { ...mockDetail.slots[0], review_state: "KEPT" as const },
          { ...mockDetail.slots[1], review_state: "KEPT" as const },
        ],
      };

      let isApprovedMock = false;

      vi.mocked(clientModule.apiFetch).mockImplementation(async (url: string) => {
        if (url.includes("/ai-interviews/tpl-101/validate") || url.includes("/ai-question-set/validate")) {
          return {
            is_valid: true,
            lifecycle_status: "VALIDATED",
            blocking_errors: [],
            safe_message: "All 2 question slots pass readiness checks.",
          };
        }
        if (url.includes("/approve")) {
          isApprovedMock = true;
          return {
            ai_set_id: "set-1",
            set_version: 1,
            lifecycle_status: "APPROVED",
            is_immutable: true,
            content_hash: "a9f1b2c3d4e5f600112233445566778899aabbccddeeff001122334455667788",
            approved_at: "2026-09-18T11:00:00Z",
            safe_message: "AI question set approved and sealed.",
          };
        }
        return {
          ...allReviewedDetail,
          lifecycle_status: isApprovedMock ? ("APPROVED" as const) : ("DRAFT" as const),
          is_immutable: isApprovedMock,
        };
      });

      render(<AiQuestionSetWorkspace templateId="tpl-101" onNavigateBack={vi.fn()} />);

      // Validate questions
      const validateBtn = await screen.findByRole("button", { name: /Validate questions/i });
      fireEvent.click(validateBtn);

      // Approve & Seal
      const approveBtn = await screen.findByRole("button", { name: /Approve & Seal/i });
      fireEvent.click(approveBtn);

      await waitFor(() => {
        expect(screen.getAllByText(/AI question set approved and sealed/i).length).toBeGreaterThanOrEqual(1);
      });
    });
  });
});
