import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { AiQuestionSetWorkspace } from "../components/ai_interviews/AiQuestionSetWorkspace";
import { ReleaseInterviewSetModal } from "../components/ai_interviews/ReleaseInterviewSetModal";
import { JobEditor } from "../components/jobs/JobEditor";
import * as clientApi from "../api/client";
import * as recruiterAppsApi from "../api/recruiterApplications";
import * as jobsApi from "../api/jobs";
import { AiQuestionSetDetail } from "../types/ai_question_set";

vi.mock("../api/client");
vi.mock("../api/recruiterApplications");
vi.mock("../api/jobs");

describe("Phase 4C.3 Corrective UI Additions", () => {
  const mockSealedSetDetail: AiQuestionSetDetail = {
    ai_set_id: "qset-100",
    interview_mode: "TEXT",
    template_id: "tpl-100",
    job_id: "job-100",
    job_title: "Principal Infrastructure Architect",
    job_revision: 3,
    set_version: 1,
    lifecycle_status: "APPROVED",
    is_immutable: true,
    total_slots_count: 3,
    reviewed_slots_count: 3,
    generated_slots_count: 3,
    conversation_count: 2,
    mcq_count: 1,
    is_source_stale: false,
    source_content_hash: "hash-123",
    created_at: "2026-09-27T09:00:00Z",
    updated_at: "2026-09-27T10:00:00Z",
    model_display_name: "Qwen3-4B MLX",
    slots: [
      {
        slot_id: "s-1",
        display_order: 1,
        competency_name: "Distributed Consensus",
        question_type: "BEHAVIORAL",
        authoring_origin: "LOCAL_LLM_GENERATED",
        review_state: "KEPT",
        question_format: "CONVERSATION",
        ai_wording: "Describe your experience debugging Raft consensus failures.",
        estimated_time_minutes: 3,
        permitted_probes: [],
        validation_status: "PASSED",
      },
      {
        slot_id: "s-2",
        display_order: 2,
        competency_name: "Cloud Architecture",
        question_type: "TECHNICAL",
        authoring_origin: "LOCAL_LLM_GENERATED",
        review_state: "KEPT",
        question_format: "CONVERSATION",
        ai_wording: "How do you handle cross-region multi-primary data replication?",
        estimated_time_minutes: 4,
        permitted_probes: [],
        validation_status: "PASSED",
      },
      {
        slot_id: "s-3",
        display_order: 3,
        competency_name: "Kubernetes Networking",
        question_type: "TECHNICAL",
        authoring_origin: "LOCAL_LLM_GENERATED",
        review_state: "KEPT",
        question_format: "MCQ_SINGLE_ANSWER",
        ai_wording: "Which CNI plugin supports native eBPF routing without kube-proxy?",
        options: [
          { option_id: "opt-1", text: "Cilium" },
          { option_id: "opt-2", text: "Flannel" },
        ],
        correct_option_id: "opt-1",
        answer_explanation: "Cilium replaces kube-proxy with eBPF programs.",
        estimated_time_minutes: 2,
        permitted_probes: [],
        validation_status: "PASSED",
      },
    ],
  };

  const mockDraftSetDetail: AiQuestionSetDetail = {
    ...mockSealedSetDetail,
    ai_set_id: "qset-draft",
    lifecycle_status: "DRAFT",
    is_immutable: false,
  };

  const mockApplicants: recruiterAppsApi.RecruiterApplicationSummary[] = [
    {
      applicationId: "app-eligible-1",
      jobId: "job-100",
      jobVersionId: "ver-100",
      jobRevision: 3,
      candidateId: "cand-1",
      profileId: "prof-1",
      profileVersion: 1,
      candidateName: "Elena Vance",
      candidateEmail: "elena@example.com",
      status: "SUBMITTED",
      recruiterReviewState: "INTERVIEW_READY",
      submittedAt: "2026-09-27T10:00:00Z",
      matchStatus: "MATCHED",
      matchScore: 92,
      interviewStatus: "PENDING_RELEASE",
      releaseId: null,
    },
    {
      applicationId: "app-diff-rev",
      jobId: "job-100",
      jobVersionId: "ver-99",
      jobRevision: 2, // Different revision
      candidateId: "cand-2",
      profileId: "prof-2",
      profileVersion: 1,
      candidateName: "Gordon Freeman",
      candidateEmail: "gordon@blackmesa.gov",
      status: "SUBMITTED",
      recruiterReviewState: "INTERVIEW_READY",
      submittedAt: "2026-09-26T10:00:00Z",
      matchStatus: "MATCHED",
      matchScore: 88,
      interviewStatus: "PENDING_RELEASE",
      releaseId: null,
    },
    {
      applicationId: "app-in-review",
      jobId: "job-100",
      jobVersionId: "ver-100",
      jobRevision: 3,
      candidateId: "cand-3",
      profileId: "prof-3",
      profileVersion: 1,
      candidateName: "Alyx Vance",
      candidateEmail: "alyx@example.com",
      status: "SUBMITTED",
      recruiterReviewState: "IN_REVIEW",
      submittedAt: "2026-09-27T11:00:00Z",
      matchStatus: "MATCHED",
      matchScore: 90,
      interviewStatus: "PENDING_RELEASE",
      releaseId: null,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // Section A & C: AI Interview Release Tests
  // =========================================================================
  describe("A. Release Interview Set from Sealed AI Interview Page", () => {
    it("1. Sealed set displays Release Interview Set action", async () => {
      vi.mocked(clientApi.apiFetch).mockResolvedValue(mockSealedSetDetail);
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });

      render(
        <AiQuestionSetWorkspace
          templateId="tpl-100"
          onNavigateBack={vi.fn()}
        />
      );

      expect(await screen.findByRole("heading", { name: "Principal Infrastructure Architect" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Release Interview Set/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Provenance Details/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Candidate Preview/i })).toBeInTheDocument();
    });

    it("2. Draft or unsealed set does not display an enabled release action", async () => {
      vi.mocked(clientApi.apiFetch).mockResolvedValue(mockDraftSetDetail);

      render(
        <AiQuestionSetWorkspace
          templateId="tpl-100"
          onNavigateBack={vi.fn()}
        />
      );

      expect(await screen.findByRole("heading", { name: "Principal Infrastructure Architect" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Release Interview Set/i })).not.toBeInTheDocument();
    });

    it("3. Modal lists backend-confirmed eligible applicants", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      expect(await screen.findByText("Elena Vance")).toBeInTheDocument();
      expect(screen.getByText("Gordon Freeman")).toBeInTheDocument();
      expect(screen.getByText("Alyx Vance")).toBeInTheDocument();

      // Check metadata strip in modal
      expect(screen.getByText("Rev #3")).toBeInTheDocument();
      expect(screen.getByText("Version 1")).toBeInTheDocument();
    });

    it("4. Release calls existing Phase 4C.1 endpoint with selected application and sealed set", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });
      vi.mocked(recruiterAppsApi.releaseApplicantInterview).mockResolvedValue({
        releaseId: "rel-elena-1",
        applicationId: "app-eligible-1",
        candidateId: "cand-1",
        jobId: "job-100",
        jobTitle: "Principal Infrastructure Architect",
        jobRevision: 3,
        aiSetId: "qset-100",
        setVersion: 1,
        status: "RELEASED",
        releasedAt: "2026-09-27T12:00:00Z",
        message: "Interview released successfully.",
      });

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      const releaseBtn = await screen.findByTitle("Release interview set to Elena Vance");
      fireEvent.click(releaseBtn);

      // Sub-dialog confirmation
      expect(await screen.findByText("Confirm Interview Release")).toBeInTheDocument();
      expect(screen.getByText(/You are about to release this official sealed interview set to/)).toBeInTheDocument();

      const confirmBtn = screen.getByRole("button", { name: "Confirm Release" });
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(recruiterAppsApi.releaseApplicantInterview).toHaveBeenCalledWith(
          "job-100",
          "app-eligible-1",
          "qset-100"
        );
      });
    });

    it("5. Successful release updates the row to Released", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });
      vi.mocked(recruiterAppsApi.releaseApplicantInterview).mockResolvedValue({
        releaseId: "rel-elena-1",
        applicationId: "app-eligible-1",
        candidateId: "cand-1",
        jobId: "job-100",
        jobTitle: "Principal Infrastructure Architect",
        jobRevision: 3,
        aiSetId: "qset-100",
        setVersion: 1,
        status: "RELEASED",
        releasedAt: "2026-09-27T12:00:00Z",
        message: "Interview released successfully.",
      });

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      const releaseBtn = await screen.findByTitle("Release interview set to Elena Vance");
      fireEvent.click(releaseBtn);

      const confirmBtn = await screen.findByRole("button", { name: "Confirm Release" });
      fireEvent.click(confirmBtn);

      expect(await screen.findByText("Official interview set released successfully to Elena Vance.")).toBeInTheDocument();
      expect(screen.getByText("Released")).toBeInTheDocument();
    });

    it("6. Idempotent existing release is shown as released", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });
      const conflictErr: any = new Error("Interview already released to this application");
      conflictErr.code = "ALREADY_RELEASED";
      vi.mocked(recruiterAppsApi.releaseApplicantInterview).mockRejectedValue(conflictErr);

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      const releaseBtn = await screen.findByTitle("Release interview set to Elena Vance");
      fireEvent.click(releaseBtn);

      const confirmBtn = await screen.findByRole("button", { name: "Confirm Release" });
      fireEvent.click(confirmBtn);

      expect(await screen.findByText("Interview was already released to Elena Vance.")).toBeInTheDocument();
      expect(screen.getByText("Released")).toBeInTheDocument();
    });

    it("7. Empty eligible list displays useful guidance and View Applicants", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        limit: 100,
        activeCount: 0,
        withdrawnCount: 0,
      });

      const onOpenApplicants = vi.fn();
      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
          onOpenApplicants={onOpenApplicants}
        />
      );

      expect(await screen.findByText("No Applicants Available")).toBeInTheDocument();
      expect(screen.getByText(/No applicants have submitted applications for this job requisition yet/)).toBeInTheDocument();

      const viewApplicantsBtn = screen.getByRole("button", { name: "View Applicants" });
      fireEvent.click(viewApplicantsBtn);
      expect(onOpenApplicants).toHaveBeenCalledWith("job-100", "Principal Infrastructure Architect");
    });

    it("8. Different Job revision cannot be released and is clearly labeled", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      expect(await screen.findByText("Gordon Freeman")).toBeInTheDocument();
      expect(screen.getByText("Different Job revision (Rev #2)")).toBeInTheDocument();
    });

    it("9. Releasing one applicant does not release every applicant", async () => {
      vi.mocked(recruiterAppsApi.fetchJobApplicants).mockResolvedValue({
        items: mockApplicants,
        total: 3,
        page: 1,
        limit: 100,
        activeCount: 3,
        withdrawnCount: 0,
      });
      vi.mocked(recruiterAppsApi.releaseApplicantInterview).mockResolvedValue({
        releaseId: "rel-elena-1",
        applicationId: "app-eligible-1",
        candidateId: "cand-1",
        jobId: "job-100",
        jobTitle: "Principal Infrastructure Architect",
        jobRevision: 3,
        aiSetId: "qset-100",
        setVersion: 1,
        status: "RELEASED",
        releasedAt: "2026-09-27T12:00:00Z",
        message: "Interview released successfully.",
      });

      render(
        <ReleaseInterviewSetModal
          isOpen={true}
          detail={mockSealedSetDetail}
          onClose={vi.fn()}
        />
      );

      const releaseBtn = await screen.findByTitle("Release interview set to Elena Vance");
      fireEvent.click(releaseBtn);

      const confirmBtn = await screen.findByRole("button", { name: "Confirm Release" });
      fireEvent.click(confirmBtn);

      await screen.findByText("Official interview set released successfully to Elena Vance.");

      // Elena is released, but Alyx Vance remains In Review and unreleased
      expect(screen.getByText("Alyx Vance")).toBeInTheDocument();
      expect(screen.getByText("In Review")).toBeInTheDocument();
      expect(screen.getByText("Not released")).toBeInTheDocument();
    });
  });

  // =========================================================================
  // Section B & C: Job Header UI Correction Tests
  // =========================================================================
  describe("B. Job Detail Header UI Correction", () => {
    const mockReadyJobDetail: jobsApi.JobDetail = {
      jobId: "job-hdr-1",
      createdByUserId: "recruiter-1",
      status: "ACTIVE",
      publicationStatus: "RELEASED",
      releasedAt: "2026-09-26T15:00:00Z",
      createdAt: "2026-09-20T10:00:00Z",
      updatedAt: "2026-09-26T15:00:00Z",
      currentVersion: {
        jobVersionId: "ver-4",
        jobId: "job-hdr-1",
        versionNumber: 1,
        versionStatus: "READY_FOR_MATCHING",
        revision: 4,
        title: "Senior Reliability Architect with Distributed Systems Mastery",
        department: "Core Engineering",
        location: "San Francisco, CA",
        employmentType: "FULL_TIME",
        workplaceType: "HYBRID",
        openingsCount: 2,
        createdByUserId: "recruiter-1",
        requirements: [
          {
            requirementId: "req-1",
            displayOrder: 0,
            category: "SKILL",
            label: "Kubernetes",
            necessity: "MUST_HAVE",
            priority: "HIGH",
            acceptedEvidenceTypes: ["EMPLOYMENT"],
            acceptedEquivalents: [],
            rationale: "Core platform requirement.",
            origin: "RECRUITER_ENTERED",
          },
        ],
        reviewWarnings: [],
        createdAt: "2026-09-20T10:00:00Z",
        updatedAt: "2026-09-26T15:00:00Z",
      },
    };

    it("1. Full Job title renders prominently in the two-part header", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJobDetail);

      render(
        <JobEditor
          jobId="job-hdr-1"
          onNavigateBack={vi.fn()}
        />
      );

      expect(await screen.findByRole("heading", { name: "Senior Reliability Architect with Distributed Systems Mastery" })).toBeInTheDocument();
    });

    it("2. Revision, ready status, and released date render as concise metadata", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJobDetail);

      render(
        <JobEditor
          jobId="job-hdr-1"
          onNavigateBack={vi.fn()}
        />
      );

      await screen.findByRole("heading", { name: /Senior Reliability Architect/ });

      expect(screen.getByText("v1 (Ready for matching)")).toBeInTheDocument();
      expect(screen.getByText("Revision 4")).toBeInTheDocument();
      expect(screen.getByText(/Released/)).toBeInTheDocument();
    });

    it("3. Compare candidates remains the only primary filled button", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJobDetail);

      render(
        <JobEditor
          jobId="job-hdr-1"
          onNavigateBack={vi.fn()}
          onOpenApplicants={vi.fn()}
          onOpenAiInterviews={vi.fn()}
          onCompareCandidates={vi.fn()}
        />
      );

      await screen.findByRole("heading", { name: /Senior Reliability Architect/ });

      const compareBtn = screen.getByRole("button", { name: /Compare candidates/i });
      expect(compareBtn).toBeInTheDocument();
      expect(compareBtn).toHaveClass("btn-primary");

      const applicantsBtn = screen.getByRole("button", { name: /Applicants/i });
      expect(applicantsBtn).toHaveClass("btn-secondary");

      const aiInterviewsBtn = screen.getByRole("button", { name: /AI Interviews/i });
      expect(aiInterviewsBtn).toHaveClass("btn-secondary");

      const createRevisionBtn = screen.getByRole("button", { name: /Create new revision/i });
      expect(createRevisionBtn).toHaveClass("btn-ghost");
    });

    it("4. Existing navigation handlers (Back, Applicants, AI Interviews, Compare) remain unchanged", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJobDetail);

      const onNavigateBack = vi.fn();
      const onOpenApplicants = vi.fn();
      const onOpenAiInterviews = vi.fn();
      const onCompareCandidates = vi.fn();

      render(
        <JobEditor
          jobId="job-hdr-1"
          onNavigateBack={onNavigateBack}
          onOpenApplicants={onOpenApplicants}
          onOpenAiInterviews={onOpenAiInterviews}
          onCompareCandidates={onCompareCandidates}
        />
      );

      await screen.findByRole("heading", { name: /Senior Reliability Architect/ });

      // Click Back to Jobs Directory
      fireEvent.click(screen.getByRole("button", { name: /Back to Jobs Directory/i }));
      expect(onNavigateBack).toHaveBeenCalled();

      // Click Applicants
      fireEvent.click(screen.getByRole("button", { name: /Applicants/i }));
      expect(onOpenApplicants).toHaveBeenCalledWith("job-hdr-1", "Senior Reliability Architect with Distributed Systems Mastery");

      // Click AI Interviews
      fireEvent.click(screen.getByRole("button", { name: /AI Interviews/i }));
      expect(onOpenAiInterviews).toHaveBeenCalledWith("job-hdr-1");

      // Click Compare candidates
      fireEvent.click(screen.getByRole("button", { name: /Compare candidates/i }));
      expect(onCompareCandidates).toHaveBeenCalledWith({
        jobId: "job-hdr-1",
        jobTitle: "Senior Reliability Architect with Distributed Systems Mastery",
        jobVersionId: "ver-4",
        revision: 4,
        requirementsCount: 1,
      });
    });

    it("5. Compact locked revision information replaces repetitive full-width green banner", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockReadyJobDetail);

      render(
        <JobEditor
          jobId="job-hdr-1"
          onNavigateBack={vi.fn()}
        />
      );

      await screen.findByRole("heading", { name: /Senior Reliability Architect/ });

      // The compact locked note is present
      const lockedNote = screen.getByText(/This revision \(v1, Rev #4\) is locked and ready for candidate comparison\./);
      expect(lockedNote).toBeInTheDocument();
      expect(lockedNote.closest(".job-locked-note")).toBeInTheDocument();
    });
  });
});
