import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { AiQuestionSetWorkspace } from "../components/ai_interviews/AiQuestionSetWorkspace";
import { CandidateSafePreviewModal } from "../components/ai_interviews/CandidateSafePreviewModal";
import { CandidatePracticeReleaseModal } from "../components/candidate/CandidatePracticeReleaseModal";
import { CandidatePracticeDirectory } from "../components/candidate/CandidatePracticeDirectory";
import * as clientApi from "../api/client";
import * as candidatePracticeApi from "../api/candidatePractice";
import { AiQuestionSetDetail } from "../types/ai_question_set";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

vi.mock("../api/candidatePractice", () => ({
  listEligibleJobsForPracticeRelease: vi.fn(),
  listCandidatePracticeReleases: vi.fn(),
  listCandidatePracticeRequestsForRecruiter: vi.fn(),
  releasePracticeToCandidate: vi.fn(),
  releasePracticeFromRequest: vi.fn(),
  declineCandidatePracticeRequest: vi.fn(),
  revokeCandidatePracticeRelease: vi.fn(),
  getCandidatePracticeDiscovery: vi.fn(),
  getCandidatePracticeCatalog: vi.fn(),
  createCandidatePracticeRequest: vi.fn(),
  withdrawCandidatePracticeRequest: vi.fn(),
  preparePracticeSetForRevision: vi.fn(),
}));

const mockQuestionSetDetail: AiQuestionSetDetail = {
  ai_set_id: "set-101",
  set_version: 1,
  interview_mode: "TEXT",
  template_id: "tpl-101",
  source_template_version: 1,
  source_mode: "JOB_REQUIREMENTS",
  source_content_hash: "hash123",
  job_id: "job-101",
  job_title: "Staff Backend Engineer",
  job_revision: 2,
  lifecycle_status: "DRAFT",
  is_immutable: false,
  total_slots_count: 2,
  generated_slots_count: 2,
  reviewed_slots_count: 1,
  conversation_count: 1,
  mcq_count: 1,
  is_source_stale: false,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  slots: [
    {
      slot_id: "slot-conv-1",
      display_order: 0,
      competency_name: "DISTRIBUTED_SYSTEMS",
      question_type: "SYSTEM_DESIGN",
      estimated_time_minutes: 5,
      permitted_probes: ["How do you handle network partitions?"],
      ai_wording: "Describe how you design an idempotent payment processing workflow.",
      review_state: "KEPT",
      authoring_origin: "AI_GENERATED",
      validation_status: "PASSED",
      question_format: "CONVERSATION",
    },
    {
      slot_id: "slot-mcq-1",
      display_order: 1,
      competency_name: "POSTGRESQL_INDEXING",
      question_type: "KNOWLEDGE_MCQ",
      estimated_time_minutes: 2,
      permitted_probes: [],
      ai_wording: "Which index type in PostgreSQL is best suited for geospatial and multi-dimensional range queries?",
      review_state: "KEPT",
      authoring_origin: "RECRUITER_MANUAL",
      validation_status: "PASSED",
      question_format: "MCQ_SINGLE_ANSWER",
      difficulty: "ADVANCED",
      options: [
        { option_id: "A", text: "B-Tree" },
        { option_id: "B", text: "GiST (Generalized Search Tree)" },
        { option_id: "C", text: "Hash Index" },
        { option_id: "D", text: "BRIN" },
      ],
      correct_option_id: "B",
      answer_explanation: "GiST is specifically engineered to index geometric and range data types effectively.",
    },
  ],
};

describe("Phase 3F.2 MCQ Management & Candidate Practice Release", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("AiQuestionSetWorkspace - MCQ Management", () => {
    it("renders tabs for conversational and MCQ questions", async () => {
      vi.mocked(clientApi.apiFetch).mockResolvedValueOnce(mockQuestionSetDetail);

      render(
        <AiQuestionSetWorkspace
          templateId="tpl-101"
          onNavigateBack={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByRole("tab", { name: /Conversational Questions/i })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: /Multiple Choice Questions/i })).toBeInTheDocument();
        expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
      });
    });

    it("displays MCQ questions, correct option indicator, and explanation for recruiter", async () => {
      vi.mocked(clientApi.apiFetch).mockResolvedValueOnce(mockQuestionSetDetail);

      render(
        <AiQuestionSetWorkspace
          templateId="tpl-101"
          onNavigateBack={vi.fn()}
        />
      );

      const mcqTab = await screen.findByRole("tab", { name: /Multiple Choice Questions/i });
      fireEvent.click(mcqTab);

      await waitFor(() => {
        expect(screen.getByText(/Which index type in PostgreSQL is best suited/i)).toBeInTheDocument();
        expect(screen.getByText("GiST (Generalized Search Tree)")).toBeInTheDocument();
        expect(screen.getByText("Correct")).toBeInTheDocument();
        expect(screen.getByText(/Explanation \(Recruiter\):/i)).toBeInTheDocument();
      });
    });

    it("opens MCQ authoring modal on clicking Add MCQ", async () => {
      vi.mocked(clientApi.apiFetch).mockResolvedValueOnce(mockQuestionSetDetail);

      render(
        <AiQuestionSetWorkspace
          templateId="tpl-101"
          onNavigateBack={vi.fn()}
        />
      );

      const mcqTab = await screen.findByRole("tab", { name: /Multiple Choice Questions/i });
      fireEvent.click(mcqTab);

      const addBtn = await screen.findByRole("button", { name: /Add MCQ/i });
      fireEvent.click(addBtn);

      await waitFor(() => {
        expect(screen.getByText("Add Multiple Choice Question")).toBeInTheDocument();
        expect(screen.getByLabelText(/Competency \/ Topic/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/Question Text/i)).toBeInTheDocument();
      });
    });
  });

  describe("CandidateSafePreviewModal - Privacy Guarantee", () => {
    it("renders candidate safe view without exposing correct answer or explanation", () => {
      render(
        <CandidateSafePreviewModal
          isOpen={true}
          detail={mockQuestionSetDetail}
          onClose={vi.fn()}
        />
      );

      // Conversational question is shown
      expect(screen.getByText(/Describe how you design an idempotent payment processing workflow/i)).toBeInTheDocument();

      // MCQ question is shown with options
      expect(screen.getByText(/Which index type in PostgreSQL is best suited/i)).toBeInTheDocument();
      expect(screen.getByText("GiST (Generalized Search Tree)")).toBeInTheDocument();
      expect(screen.getByText("B-Tree")).toBeInTheDocument();

      // Recruiter secrets MUST NOT appear
      expect(screen.queryByText(/^Correct$/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Explanation \(Recruiter\):/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/GiST is specifically engineered/i)).not.toBeInTheDocument();
    });
  });

  describe("CandidatePracticeReleaseModal - Recruiter Release", () => {
    it("lists eligible jobs and releases practice interview to candidate", async () => {
      vi.mocked(candidatePracticeApi.listEligibleJobsForPracticeRelease).mockResolvedValue({
        eligible: [
          {
            job_id: "job-101",
            job_revision_id: "ver-101",
            job_title: "Staff Backend Engineer",
            job_version: "v2",
            question_set_id: "set-101",
            conversational_question_count: 5,
            mcq_question_count: 5,
            total_question_count: 10,
            status: "READY_TO_RELEASE",
            has_candidate_request: false,
          },
        ],
        ineligible: [],
        items: [],
      });
      vi.mocked(candidatePracticeApi.listCandidatePracticeRequestsForRecruiter).mockResolvedValue([]);
      vi.mocked(candidatePracticeApi.listCandidatePracticeReleases).mockResolvedValue([]);
      vi.mocked(candidatePracticeApi.releasePracticeToCandidate).mockResolvedValue({
        release_id: "rel-1",
        candidate_id: "cand-1",
        job_id: "job-101",
        job_title: "Staff Backend Engineer",
        job_revision: 2,
        ai_set_id: "set-101",
        set_version: 1,
        status: "RELEASED",
        released_at: new Date().toISOString(),
        released_by_user_id: "user-recruiter-1",
        conversation_count: 5,
        mcq_count: 5,
      });

      render(
        <CandidatePracticeReleaseModal
          candidateId="cand-1"
          candidateName="Sarah Connor"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(candidatePracticeApi.listEligibleJobsForPracticeRelease).toHaveBeenCalledWith("cand-1");
        expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
      });

      const releaseBtn = screen.getByRole("button", { name: /Release Practice/i });
      fireEvent.click(releaseBtn);

      await waitFor(() => {
        expect(candidatePracticeApi.releasePracticeToCandidate).toHaveBeenCalledWith("cand-1", "job-101", "ver-101");
      });
    });

    it("allows revoking an active practice release", async () => {
      vi.mocked(candidatePracticeApi.listEligibleJobsForPracticeRelease).mockResolvedValue({ eligible: [], ineligible: [], items: [] });
      vi.mocked(candidatePracticeApi.listCandidatePracticeRequestsForRecruiter).mockResolvedValue([]);
      vi.mocked(candidatePracticeApi.listCandidatePracticeReleases).mockResolvedValue([
        {
          release_id: "rel-1",
          candidate_id: "cand-1",
          job_id: "job-101",
          job_title: "Staff Backend Engineer",
          job_revision: 2,
          ai_set_id: "set-101",
          set_version: 1,
          status: "RELEASED",
          released_at: new Date().toISOString(),
          released_by_user_id: "user-1",
          conversation_count: 5,
          mcq_count: 5,
        },
      ]);
      vi.mocked(candidatePracticeApi.revokeCandidatePracticeRelease).mockResolvedValue({
        success: true,
        release_id: "rel-1",
        status: "REVOKED",
        safe_message: "Practice release revoked successfully.",
      });

      render(
        <CandidatePracticeReleaseModal
          candidateId="cand-1"
          candidateName="Sarah Connor"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      const revokeBtn = await screen.findByRole("button", { name: /Revoke release for Staff Backend Engineer/i });
      fireEvent.click(revokeBtn);

      await waitFor(() => {
        expect(candidatePracticeApi.revokeCandidatePracticeRelease).toHaveBeenCalledWith("rel-1");
      });
    });
  });

  describe("CandidatePracticeDirectory - Candidate Discovery", () => {
    it("fetches and displays released practice jobs with mixed question stats", async () => {
      vi.mocked(candidatePracticeApi.getCandidatePracticeDiscovery).mockResolvedValue({
        items: [
          {
            release_id: "rel-1",
            job_id: "job-101",
            job_title: "Staff Backend Engineer",
            job_revision: 2,
            ai_set_id: "set-101",
            set_version: 1,
            conversation_count: 5,
            mcq_count: 5,
            total_question_count: 10,
            released_at: new Date().toISOString(),
            status: "RELEASED",
          },
        ],
      });
      vi.mocked(candidatePracticeApi.getCandidatePracticeCatalog).mockResolvedValue({ items: [] });

      render(<CandidatePracticeDirectory />);

      await waitFor(() => {
        expect(candidatePracticeApi.getCandidatePracticeDiscovery).toHaveBeenCalled();
        expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
        expect(screen.getByText(/5 conversational · 5 MCQ/i)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Start Practice Interview/i })).toBeInTheDocument();
      });
    });

    it("displays welcome message when no practice jobs are released", async () => {
      vi.mocked(candidatePracticeApi.getCandidatePracticeDiscovery).mockResolvedValue({ items: [] });
      vi.mocked(candidatePracticeApi.getCandidatePracticeCatalog).mockResolvedValue({ items: [] });

      render(<CandidatePracticeDirectory />);

      await waitFor(() => {
        expect(screen.getByText("No assigned practice sessions yet")).toBeInTheDocument();
      });
    });
  });
});
