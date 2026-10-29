import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { JobApplicationDetailView } from "../components/jobs/JobApplicationDetailView";
import { CandidateApplicationDetailView } from "../components/candidate/CandidateApplicationDetail";
import * as recruiterApi from "../api/recruiterApplications";
import * as candidateApi from "../api/candidateApplications";

vi.mock("../api/recruiterApplications");
vi.mock("../api/candidateApplications");

describe("Phase 4E.3 Frontend Workflow Integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders recruiter interview mode selector and switches between TEXT and AUDIO modes", async () => {
    const mockDetail: recruiterApi.RecruiterApplicationDetail = {
      applicationId: "app-123",
      jobId: "job-123",
      jobTitle: "Senior Backend Engineer",
      jobVersionId: "ver-123",
      jobRevision: 1,
      candidateId: "cand-123",
      profileId: "prof-123",
      profileVersion: 1,
      candidateName: "Jane Doe",
      candidateEmail: "jane@example.com",
      candidatePhone: "555-123-4567",
      currentRole: "Backend Dev",
      status: "SUBMITTED",
      recruiterReviewState: "INTERVIEW_READY",
      submittedAt: "2026-09-29T12:05:00Z",
      profileSkills: ["Python", "FastAPI"],
      profileHeadline: "Senior Engineer",
      matchEvidence: {
        runId: "run-1",
        rank: 1,
        rawScore: 88,
        displayScore: 88,
        confidenceTier: "STRONG",
        warningCount: 0,
        mustHaveWarningCount: 0,
        isTied: false,
        scoreDecomposition: {},
        computedAt: "2026-09-29T12:06:00Z",
      },
      interviewEligibility: {
        isEligible: true,
        message: "Ready for release",
        interviewMode: "TEXT",
        questionSetId: "set-text-1",
        setVersion: 1,
        questionCount: 5,
        existingReleaseId: null,
      },
    };

    vi.mocked(recruiterApi.fetchJobApplicantDetail).mockImplementation((jobId, appId, mode) => {
      return Promise.resolve({
        ...mockDetail,
        interviewEligibility: {
          ...mockDetail.interviewEligibility,
          interviewMode: mode || "TEXT",
          questionCount: mode === "AUDIO" ? 4 : 5,
          questionSetId: mode === "AUDIO" ? "set-audio-1" : "set-text-1",
        },
      });
    });

    render(
      <JobApplicationDetailView
        jobId="job-123"
        applicationId="app-123"
        onNavigateBack={vi.fn()}
      />
    );

    // Initial load calls with "TEXT"
    await waitFor(() => {
      expect(recruiterApi.fetchJobApplicantDetail).toHaveBeenCalledWith("job-123", "app-123", "TEXT");
    });

    // Check tabs exist
    const textTab = screen.getByRole("button", { name: "Text Interview" });
    const audioTab = screen.getByRole("button", { name: "Audio Interview" });
    expect(textTab).toBeInTheDocument();
    expect(audioTab).toBeInTheDocument();

    // Switch to AUDIO mode
    fireEvent.click(audioTab);

    await waitFor(() => {
      expect(recruiterApi.fetchJobApplicantDetail).toHaveBeenCalledWith("job-123", "app-123", "AUDIO");
    });
  });

  it("provides direct navigation from CandidateApplicationDetail to Assigned Interviews", async () => {
    const mockAppDetail: candidateApi.CandidateApplication = {
      applicationId: "app-789-xyz",
      jobId: "job-789",
      jobVersionId: "ver-789",
      jobRevision: 1,
      artifactId: "art-789",
      profileId: "prof-789",
      profileVersion: 1,
      status: "SUBMITTED",
      submittedAt: "2026-09-29T10:05:00Z",
      canWithdraw: true,
      canSubmit: false,
      createdAt: "2026-09-29T10:00:00Z",
      updatedAt: "2026-09-29T10:05:00Z",
      job: {
        jobId: "job-789",
        jobVersionId: "ver-789",
        versionNumber: 1,
        revision: 1,
        title: "Full Stack Engineer",
        department: "Engineering",
        location: "Remote",
        publishedAt: "2026-09-29T09:00:00Z",
        updatedAt: "2026-09-29T09:00:00Z",
      },
    };

    vi.mocked(candidateApi.fetchApplicationDetail).mockResolvedValue(mockAppDetail);

    const mockNavigateInterviews = vi.fn();

    render(
      <CandidateApplicationDetailView
        applicationId="app-789-xyz"
        onBack={vi.fn()}
        onNavigateJobs={vi.fn()}
        onNavigateInterviews={mockNavigateInterviews}
      />
    );

    await waitFor(() => {
      expect(candidateApi.fetchApplicationDetail).toHaveBeenCalledWith("app-789-xyz");
    });

    // Check that the Go to Interviews button is displayed and works
    const interviewBtn = screen.getByRole("button", { name: /Go to Interviews/i });
    expect(interviewBtn).toBeInTheDocument();
    fireEvent.click(interviewBtn);
    expect(mockNavigateInterviews).toHaveBeenCalledTimes(1);
  });
});
