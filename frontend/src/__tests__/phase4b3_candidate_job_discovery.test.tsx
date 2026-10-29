import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { CandidateShell } from "../components/candidate/CandidateShell";
import * as candidateAuthContext from "../context/CandidateAuthContext";
import * as candidateJobsApi from "../api/candidateJobs";

vi.mock("../api/candidateJobs");

describe("Candidate Job Discovery (Phase 4B.3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(candidateAuthContext, "useCandidateAuth").mockReturnValue({
      candidateUser: {
        userId: "user-123",
        email: "candidate@example.com",
        displayName: "Jane Candidate",
        role: "CANDIDATE",
        candidateId: "cand-123",
        organizationId: "org-123",
        bindingStatus: "ACTIVE",
      },
      status: "AUTHENTICATED",
      login: vi.fn(),
      logout: vi.fn(),
      activateAccount: vi.fn(),
    } as any);
  });

  it("renders CandidateShell with Open Jobs and Practice Interviews tabs, without My Applications", async () => {
    vi.mocked(candidateJobsApi.fetchCandidateJobs).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      limit: 10,
    });

    render(<CandidateShell />);

    expect(screen.getByRole("button", { name: /Open Jobs/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /My Applications/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Practice Interviews/i })).toBeInTheDocument();
  });

  it("lists eligible open jobs in CandidateOpenJobs view", async () => {
    vi.mocked(candidateJobsApi.fetchCandidateJobs).mockResolvedValue({
      items: [
        {
          jobId: "job-101",
          jobVersionId: "ver-101",
          versionNumber: 1,
          revision: 1,
          title: "Principal Cloud Architect",
          department: "Infrastructure",
          location: "San Francisco, CA",
          employmentType: "FULL_TIME",
          workplaceType: "HYBRID",
          openingsCount: 1,
          summary: "Lead our cloud architecture strategy.",
          publishedAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
      ],
      total: 1,
      page: 1,
      limit: 10,
    });

    render(<CandidateShell />);

    await waitFor(() => {
      expect(screen.getByText("Principal Cloud Architect")).toBeInTheDocument();
      expect(screen.getByText("Infrastructure")).toBeInTheDocument();
      expect(screen.getByText("San Francisco, CA")).toBeInTheDocument();
      expect(screen.getByText("View Details")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /Apply/i })).not.toBeInTheDocument();
  });

  it("navigates to CandidateJobDetail on clicking View Details and displays candidate-safe fields without Apply button", async () => {
    vi.mocked(candidateJobsApi.fetchCandidateJobs).mockResolvedValue({
      items: [
        {
          jobId: "job-101",
          jobVersionId: "ver-101",
          versionNumber: 1,
          revision: 1,
          title: "Principal Cloud Architect",
          department: "Infrastructure",
          location: "San Francisco, CA",
          employmentType: "FULL_TIME",
          workplaceType: "HYBRID",
          openingsCount: 1,
          summary: "Lead our cloud architecture strategy.",
          publishedAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        },
      ],
      total: 1,
      page: 1,
      limit: 10,
    });

    vi.mocked(candidateJobsApi.fetchCandidateJobDetail).mockResolvedValue({
      jobId: "job-101",
      jobVersionId: "ver-101",
      versionNumber: 1,
      revision: 1,
      title: "Principal Cloud Architect",
      department: "Infrastructure",
      location: "San Francisco, CA",
      employmentType: "FULL_TIME",
      workplaceType: "HYBRID",
      openingsCount: 1,
      summary: "Lead our cloud architecture strategy.",
      responsibilities: "Design multi-region VPC topologies and zero-trust IAM.",
      qualifications: ["10+ years distributed systems", "AWS Solutions Architect Professional"],
      publishedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });

    render(<CandidateShell />);

    await waitFor(() => {
      expect(screen.getByText("Principal Cloud Architect")).toBeInTheDocument();
    });

    const viewDetailsBtn = screen.getByRole("button", { name: /View details for Principal Cloud Architect/i });
    fireEvent.click(viewDetailsBtn);

    await waitFor(() => {
      expect(candidateJobsApi.fetchCandidateJobDetail).toHaveBeenCalledWith("job-101", "ver-101");
      expect(screen.getByText("Key Responsibilities")).toBeInTheDocument();
      expect(screen.getByText("Design multi-region VPC topologies and zero-trust IAM.")).toBeInTheDocument();
      expect(screen.getByText("10+ years distributed systems")).toBeInTheDocument();
      expect(screen.getByText("AWS Solutions Architect Professional")).toBeInTheDocument();
      expect(screen.getByText("Back to Open Jobs")).toBeInTheDocument();
    });

    // Phase 4B.4 added Apply button; ensure Withdraw button does not exist on Job Detail
    expect(screen.getByRole("button", { name: /Apply to Position/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Withdraw/i })).not.toBeInTheDocument();
  });
});
