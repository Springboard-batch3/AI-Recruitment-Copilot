import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { JobApplicantsView } from "../components/jobs/JobApplicantsView";
import { JobApplicationDetailView } from "../components/jobs/JobApplicationDetailView";
import * as recruiterApi from "../api/recruiterApplications";

vi.mock("../api/recruiterApplications", () => ({
  fetchJobApplicants: vi.fn(),
  fetchJobApplicantDetail: vi.fn(),
  updateApplicantReviewState: vi.fn(),
  releaseApplicantInterview: vi.fn(),
}));

describe("Phase 4B.5 Frontend Recruiter Applicant Workflow", () => {
  const mockApplicantsResponse: recruiterApi.RecruiterApplicationListResponse = {
    items: [
      {
        applicationId: "app-111",
        jobId: "job-123",
        jobVersionId: "jver-123",
        jobRevision: 2,
        candidateId: "cand-111",
        profileId: "prof-111",
        profileVersion: 1,
        candidateName: "Alice Smith",
        candidateEmail: "alice@example.com",
        candidatePhone: "555-0101",
        currentRole: null,
        status: "SUBMITTED",
        recruiterReviewState: "RECEIVED",
        submittedAt: new Date().toISOString(),
        withdrawnAt: null,
        matchStatus: "Not evaluated",
        matchScore: null,
        confidenceTier: null,
        interviewStatus: "NOT_RELEASED",
        releaseId: null,
      },
      {
        applicationId: "app-222",
        jobId: "job-123",
        jobVersionId: "jver-123",
        jobRevision: 2,
        candidateId: "cand-222",
        profileId: "prof-222",
        profileVersion: 1,
        candidateName: "Bob Jones",
        candidateEmail: "bob@example.com",
        candidatePhone: "555-0202",
        currentRole: null,
        status: "WITHDRAWN",
        recruiterReviewState: "RECEIVED",
        submittedAt: new Date().toISOString(),
        withdrawnAt: new Date().toISOString(),
        matchStatus: "Evaluated",
        matchScore: 88.5,
        confidenceTier: "HIGH",
        interviewStatus: "NOT_RELEASED",
        releaseId: null,
      },
    ],
    total: 2,
    page: 1,
    limit: 20,
    activeCount: 1,
    withdrawnCount: 1,
  };

  const mockDetailResponse: recruiterApi.RecruiterApplicationDetail = {
    applicationId: "app-111",
    jobId: "job-123",
    jobTitle: "Senior Backend Engineer",
    jobVersionId: "jver-123",
    jobRevision: 2,
    candidateId: "cand-111",
    profileId: "prof-111",
    profileVersion: 1,
    candidateName: "Alice Smith",
    candidateEmail: "alice@example.com",
    candidatePhone: "555-0101",
    currentRole: null,
    status: "SUBMITTED",
    recruiterReviewState: "RECEIVED",
    submittedAt: new Date().toISOString(),
    withdrawnAt: null,
    profileSkills: [],
    profileHeadline: null,
    matchEvidence: null,
    interviewEligibility: {
      isEligible: false,
      reasonCode: "NOT_INTERVIEW_READY",
      message: "Application review state is currently 'RECEIVED'. Move to 'INTERVIEW_READY' to enable release.",
      questionCount: 0,
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders applicant list and status filters", async () => {
    vi.mocked(recruiterApi.fetchJobApplicants).mockResolvedValue(mockApplicantsResponse);
    const onSelect = vi.fn();

    render(
      <JobApplicantsView
        jobId="job-123"
        jobTitle="Senior Backend Engineer"
        onSelectApplication={onSelect}
      />
    );

    expect(screen.getByText(/Applicants for Senior Backend Engineer/i)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
      expect(screen.getByText("Bob Jones")).toBeInTheDocument();
      expect(screen.getByText("Active: 1")).toBeInTheDocument();
      expect(screen.getByText("Withdrawn: 1")).toBeInTheDocument();
    });

    const viewButtons = screen.getAllByText("View Application");
    fireEvent.click(viewButtons[0]);
    expect(onSelect).toHaveBeenCalledWith("app-111");
  });

  it("handles recruiter review state transitions in application detail", async () => {
    vi.mocked(recruiterApi.fetchJobApplicantDetail).mockResolvedValue(mockDetailResponse);
    vi.mocked(recruiterApi.updateApplicantReviewState).mockResolvedValue({
      ...mockDetailResponse,
      recruiterReviewState: "IN_REVIEW",
    });

    render(
      <JobApplicationDetailView
        jobId="job-123"
        applicationId="app-111"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
      expect(screen.getByText(/Start Review/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Start Review/i));
    await waitFor(() => {
      expect(recruiterApi.updateApplicantReviewState).toHaveBeenCalledWith(
        "job-123",
        "app-111",
        "IN_REVIEW",
        "RECEIVED"
      );
    });
  });

  it("shows interview release button only when eligible and triggers release", async () => {
    const eligibleDetail: recruiterApi.RecruiterApplicationDetail = {
      ...mockDetailResponse,
      recruiterReviewState: "INTERVIEW_READY",
      interviewEligibility: {
        isEligible: true,
        reasonCode: null,
        message: "Ready to release approved interview to candidate.",
        questionSetId: "qset-123",
        setVersion: 1,
        questionCount: 3,
      },
    };

    vi.mocked(recruiterApi.fetchJobApplicantDetail).mockResolvedValue(eligibleDetail);
    vi.mocked(recruiterApi.releaseApplicantInterview).mockResolvedValue({
      releaseId: "rel-123",
      applicationId: "app-111",
      candidateId: "cand-111",
      jobId: "job-123",
      jobTitle: "Senior Backend Engineer",
      jobRevision: 2,
      aiSetId: "qset-123",
      setVersion: 1,
      status: "RELEASED",
      releasedAt: new Date().toISOString(),
      message: "Approved interview successfully released to candidate.",
    });

    render(
      <JobApplicationDetailView
        jobId="job-123"
        applicationId="app-111"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Release Interview")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Release Interview"));
    const confirmBtn = screen.queryByRole("button", { name: /confirm release/i });
    if (confirmBtn) {
      fireEvent.click(confirmBtn);
    }
    await waitFor(() => {
      expect(recruiterApi.releaseApplicantInterview).toHaveBeenCalledWith(
        "job-123",
        "app-111",
        "qset-123"
      );
    });
  });
});
