import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { JobApplicationDetailView } from "../components/jobs/JobApplicationDetailView";
import { CandidatePracticeDirectory } from "../components/candidate/CandidatePracticeDirectory";
import * as recruiterApplicationsApi from "../api/recruiterApplications";
import * as candidatePracticeApi from "../api/candidatePractice";

vi.mock("../api/recruiterApplications");
vi.mock("../api/candidatePractice");

describe("Phase 4C.1: Interview Eligibility and Controlled Release UI", () => {
  const sampleDetailEligible: recruiterApplicationsApi.RecruiterApplicationDetail = {
    applicationId: "app-1",
    jobId: "job-1",
    jobTitle: "Senior Backend Engineer",
    jobVersionId: "ver-1",
    jobRevision: 1,
    candidateId: "cand-1",
    profileId: "prof-1",
    profileVersion: 1,
    candidateName: "Alice Smith",
    candidateEmail: "alice@example.com",
    status: "SUBMITTED",
    recruiterReviewState: "INTERVIEW_READY",
    submittedAt: "2026-09-27T10:00:00Z",
    profileSkills: ["Python", "FastAPI"],
    profileHeadline: "Senior Python Developer",
    interviewEligibility: {
      isEligible: true,
      reasonCode: null,
      message: "Ready to release approved interview to candidate.",
      questionSetId: "qset-1",
      setVersion: 1,
      questionCount: 4,
      existingReleaseId: null,
      existingReleaseStatus: null,
    },
  };

  const sampleDetailBlocked: recruiterApplicationsApi.RecruiterApplicationDetail = {
    applicationId: "app-2",
    jobId: "job-1",
    jobTitle: "Senior Backend Engineer",
    jobVersionId: "ver-1",
    jobRevision: 1,
    candidateId: "cand-2",
    profileId: "prof-2",
    profileVersion: 1,
    candidateName: "Bob Jones",
    status: "SUBMITTED",
    recruiterReviewState: "IN_REVIEW",
    submittedAt: "2026-09-27T11:00:00Z",
    profileSkills: ["Node.js"],
    interviewEligibility: {
      isEligible: false,
      reasonCode: "NOT_INTERVIEW_READY",
      message: "Application review state is currently 'IN_REVIEW'. Move to 'INTERVIEW_READY' to enable release.",
      questionSetId: null,
      setVersion: null,
      questionCount: 0,
      existingReleaseId: null,
      existingReleaseStatus: null,
    },
  };

  const sampleDetailReleased: recruiterApplicationsApi.RecruiterApplicationDetail = {
    ...sampleDetailEligible,
    interviewEligibility: {
      isEligible: false,
      reasonCode: "ALREADY_RELEASED",
      message: "An interview for this job revision is already released to this candidate.",
      questionSetId: "qset-1",
      setVersion: 1,
      questionCount: 4,
      existingReleaseId: "rel-1",
      existingReleaseStatus: "RELEASED",
    },
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("1. Eligible application displays Release Interview button and opens confirmation dialog", async () => {
    vi.mocked(recruiterApplicationsApi.fetchJobApplicantDetail).mockResolvedValue(sampleDetailEligible);
    vi.mocked(recruiterApplicationsApi.releaseApplicantInterview).mockResolvedValue({
      releaseId: "rel-1",
      applicationId: "app-1",
      candidateId: "cand-1",
      jobId: "job-1",
      jobTitle: "Senior Backend Engineer",
      jobRevision: 1,
      aiSetId: "qset-1",
      setVersion: 1,
      status: "RELEASED",
      releasedAt: "2026-09-27T12:00:00Z",
      message: "Interview released successfully.",
    });

    render(
      <JobApplicationDetailView
        jobId="job-1"
        applicationId="app-1"
        onNavigateBack={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
    });

    const releaseBtn = screen.getByRole("button", { name: /release interview/i });
    expect(releaseBtn).toBeInTheDocument();

    // Click Release Interview to open confirmation modal
    fireEvent.click(releaseBtn);
    expect(screen.getByText("Release Official Interview")).toBeInTheDocument();

    // Confirm release
    const confirmBtn = screen.getByRole("button", { name: /confirm release/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(recruiterApplicationsApi.releaseApplicantInterview).toHaveBeenCalledWith(
        "job-1",
        "app-1",
        "qset-1"
      );
    });
  });

  it("2. Blocked application displays the correct reason message and no release button", async () => {
    vi.mocked(recruiterApplicationsApi.fetchJobApplicantDetail).mockResolvedValue(sampleDetailBlocked);

    render(
      <JobApplicationDetailView
        jobId="job-1"
        applicationId="app-2"
        onNavigateBack={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Bob Jones")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /release interview/i })).not.toBeInTheDocument();
    const reasonElements = screen.getAllByText(/Application review state is currently 'IN_REVIEW'/i);
    expect(reasonElements.length).toBeGreaterThan(0);
  });

  it("3. Released state replaces the release button with Interview Released badge", async () => {
    vi.mocked(recruiterApplicationsApi.fetchJobApplicantDetail).mockResolvedValue(sampleDetailReleased);

    render(
      <JobApplicationDetailView
        jobId="job-1"
        applicationId="app-1"
        onNavigateBack={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Alice Smith")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /release interview/i })).not.toBeInTheDocument();
    expect(screen.getByText("Interview Released")).toBeInTheDocument();
    expect(screen.getByText(/Interview released and active for candidate on Interviews portal/i)).toBeInTheDocument();
  });

  it("4. Candidate Interviews directory displays official released assignment with Available badge", async () => {
    vi.mocked(candidatePracticeApi.getCandidatePracticeDiscovery).mockResolvedValue({
      items: [
        {
          release_id: "rel-1",
          job_id: "job-1",
          job_version_id: "ver-1",
          job_title: "Senior Backend Engineer",
          job_revision: 1,
          conversation_question_count: 2,
          conversation_count: 2,
          mcq_count: 2,
          total_question_count: 4,
          released_at: "2026-09-27T12:00:00Z",
          status_label: "Available",
        } as any,
      ],
    });

    render(<CandidatePracticeDirectory />);

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });

    expect(screen.getByText("Available")).toBeInTheDocument();
    expect(screen.getByText("4 Questions")).toBeInTheDocument();
    expect(screen.getByText(/Released for your submitted application/i)).toBeInTheDocument();
  });
});
