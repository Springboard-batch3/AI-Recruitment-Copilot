import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { CandidateJobDetailView } from "../components/candidate/CandidateJobDetail";
import { CandidateMyApplications } from "../components/candidate/CandidateMyApplications";
import { CandidateApplicationDetailView } from "../components/candidate/CandidateApplicationDetail";
import * as candidateAuthContext from "../context/CandidateAuthContext";
import * as candidateJobsApi from "../api/candidateJobs";
import * as candidateAppsApi from "../api/candidateApplications";

vi.mock("../api/candidateJobs");
vi.mock("../api/candidateApplications");

describe("Candidate Applications Lifecycle (Phase 4B.4)", () => {
  const mockCandidateUser = {
    userId: "user-123",
    email: "candidate@example.com",
    displayName: "Jane Candidate",
    role: "CANDIDATE",
    candidateId: "cand-123",
    organizationId: "org-123",
    bindingStatus: "ACTIVE",
  };

  const sampleJobSummary1 = {
    jobId: "job-1",
    jobVersionId: "ver-1",
    versionNumber: 1,
    revision: 1,
    title: "Staff Backend Engineer",
    department: "Engineering",
    location: "Remote",
    employmentType: "FULL_TIME",
    workplaceType: "REMOTE",
    openingsCount: 1,
    summary: "Build scalable distributed systems.",
    status: "PUBLISHED",
    publishedAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };

  const sampleJobSummary2 = {
    jobId: "job-2",
    jobVersionId: "ver-2",
    versionNumber: 2,
    revision: 2,
    title: "Frontend Architect",
    department: "Product",
    location: "New York, NY",
    employmentType: "FULL_TIME",
    workplaceType: "HYBRID",
    openingsCount: 1,
    summary: "Design modern candidate web apps.",
    status: "PUBLISHED",
    publishedAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(candidateAuthContext, "useCandidateAuth").mockReturnValue({
      candidateUser: mockCandidateUser,
      status: "AUTHENTICATED",
      login: vi.fn(),
      logout: vi.fn(),
      activateAccount: vi.fn(),
    } as any);
  });

  it("renders Apply button on Job Detail for unapplied job and navigates into application form", async () => {
    vi.mocked(candidateJobsApi.fetchCandidateJobDetail).mockResolvedValue({
      ...sampleJobSummary1,
      responsibilities: ["Build backend APIs"],
      qualifications: ["Python", "FastAPI"],
      hasActiveApplication: false,
      activeApplicationId: null,
    } as any);

    vi.mocked(candidateAppsApi.createOrGetApplicationDraft).mockResolvedValue({
      applicationId: "app-201",
      jobId: "job-1",
      jobVersionId: "ver-1",
      jobRevision: 1,
      profileVersion: null,
      status: "DRAFT",
      submittedAt: null,
      withdrawnAt: null,
      canWithdraw: false,
      canSubmit: false,
      job: sampleJobSummary1,
      createdAt: "2026-09-26T12:00:00Z",
      updatedAt: "2026-09-26T12:00:00Z",
    });

    const onBack = vi.fn();
    const onNavigateApp = vi.fn();

    render(
      <CandidateJobDetailView
        jobId="job-1"
        onBack={onBack}
        onNavigateApplication={onNavigateApp}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
    });

    const applyButton = screen.getByRole("button", { name: /Apply to Position/i });
    expect(applyButton).toBeInTheDocument();

    fireEvent.click(applyButton);

    await waitFor(() => {
      expect(candidateAppsApi.createOrGetApplicationDraft).toHaveBeenCalledWith(
        expect.objectContaining({
          jobId: "job-1",
          expectedRevision: 1,
        })
      );
      expect(screen.getByText("1. Upload Resume")).toBeInTheDocument();
    });
  });

  it("renders Applied badge and View Application button when job already has active application", async () => {
    vi.mocked(candidateJobsApi.fetchCandidateJobDetail).mockResolvedValue({
      ...sampleJobSummary1,
      responsibilities: [],
      qualifications: [],
      hasActiveApplication: true,
      activeApplicationId: "app-201",
    } as any);

    const onBack = vi.fn();
    const onNavigateApp = vi.fn();

    render(
      <CandidateJobDetailView
        jobId="job-1"
        onBack={onBack}
        onNavigateApplication={onNavigateApp}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /View Application/i })).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /Apply to Position/i })).not.toBeInTheDocument();
    const viewAppBtn = screen.getByRole("button", { name: /View Application/i });
    fireEvent.click(viewAppBtn);
    expect(onNavigateApp).toHaveBeenCalledWith("app-201");
  });

  it("lists submitted and withdrawn applications in My Applications", async () => {
    vi.mocked(candidateAppsApi.fetchMyApplications).mockResolvedValue({
      items: [
        {
          applicationId: "app-1",
          jobId: "job-1",
          jobVersionId: "ver-1",
          jobRevision: 1,
          profileVersion: 1,
          status: "SUBMITTED",
          submittedAt: "2026-09-26T10:00:00Z",
          withdrawnAt: null,
          canWithdraw: true,
          canSubmit: false,
          createdAt: "2026-09-26T09:00:00Z",
          updatedAt: "2026-09-26T10:00:00Z",
          job: sampleJobSummary1,
        },
        {
          applicationId: "app-2",
          jobId: "job-2",
          jobVersionId: "ver-2",
          jobRevision: 2,
          profileVersion: 1,
          status: "WITHDRAWN",
          submittedAt: "2026-09-20T10:00:00Z",
          withdrawnAt: "2026-09-22T10:00:00Z",
          canWithdraw: false,
          canSubmit: false,
          createdAt: "2026-09-20T09:00:00Z",
          updatedAt: "2026-09-22T10:00:00Z",
          job: sampleJobSummary2,
        },
      ],
      total: 2,
      page: 1,
      limit: 10,
    });

    const onSelectApp = vi.fn();
    const onNavJobs = vi.fn();

    render(
      <CandidateMyApplications
        onSelectApplication={onSelectApp}
        onNavigateJobs={onNavJobs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
      expect(screen.getByText("Frontend Architect")).toBeInTheDocument();
      expect(screen.getByText("Submitted")).toBeInTheDocument();
      expect(screen.getByText("Withdrawn")).toBeInTheDocument();
    });
  });

  it("handles withdrawal flow in Application Detail", async () => {
    vi.mocked(candidateAppsApi.fetchApplicationDetail).mockResolvedValue({
      applicationId: "app-1",
      jobId: "job-1",
      jobVersionId: "ver-1",
      jobRevision: 1,
      profileVersion: 1,
      status: "SUBMITTED",
      submittedAt: "2026-09-26T10:00:00Z",
      withdrawnAt: null,
      canWithdraw: true,
      canSubmit: false,
      createdAt: "2026-09-26T09:00:00Z",
      updatedAt: "2026-09-26T10:00:00Z",
      job: sampleJobSummary1,
    });

    vi.mocked(candidateAppsApi.withdrawApplication).mockResolvedValue({
      applicationId: "app-1",
      jobId: "job-1",
      jobVersionId: "ver-1",
      jobRevision: 1,
      profileVersion: 1,
      status: "WITHDRAWN",
      submittedAt: "2026-09-26T10:00:00Z",
      withdrawnAt: "2026-09-26T12:00:00Z",
      canWithdraw: false,
      canSubmit: false,
      createdAt: "2026-09-26T09:00:00Z",
      updatedAt: "2026-09-26T12:00:00Z",
      job: sampleJobSummary1,
    });

    const onBack = vi.fn();
    const onNavigateJobs = vi.fn();

    render(
      <CandidateApplicationDetailView
        applicationId="app-1"
        onBack={onBack}
        onNavigateJobs={onNavigateJobs}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Staff Backend Engineer")).toBeInTheDocument();
    });

    const withdrawBtn = screen.getByRole("button", { name: /Withdraw Application/i });
    expect(withdrawBtn).toBeInTheDocument();

    // Click withdraw to open confirmation dialog
    fireEvent.click(withdrawBtn);

    expect(screen.getByText(/Withdraw Application\?/i)).toBeInTheDocument();

    // Confirm withdrawal
    const confirmBtn = screen.getByRole("button", { name: /Confirm Withdrawal/i });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(candidateAppsApi.withdrawApplication).toHaveBeenCalledWith("app-1");
      expect(screen.getByText("Withdrawn")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Withdraw Application/i })).not.toBeInTheDocument();
    });
  });
});
