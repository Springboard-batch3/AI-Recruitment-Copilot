import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { CandidateLoginPage } from "../components/candidate/CandidateLoginPage";
import { JobEditor } from "../components/jobs/JobEditor";
import { CandidateAuthProvider } from "../context/CandidateAuthContext";
import * as jobsApi from "../api/jobs";
import * as candidateAuthApi from "../api/candidateAuth";

vi.mock("../api/jobs");
vi.mock("../api/candidateAuth");

describe("UI, Privacy, and Audio Interview Corrections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("1. Job Presentation Corrections", () => {
    const mockLockedJob: jobsApi.JobDetail = {
      jobId: "job-locked-1",
      createdByUserId: "user-1",
      status: "ACTIVE",
      publicationStatus: "RELEASED",
      releasedAt: "2026-09-28T10:00:00Z",
      createdAt: "2026-09-28T09:00:00Z",
      updatedAt: "2026-09-28T10:00:00Z",
      currentVersion: {
        jobVersionId: "ver-1",
        jobId: "job-123",
        createdByUserId: "recruiter-1",
        versionNumber: 1,
        versionStatus: "READY_FOR_MATCHING",
        revision: 1,
        title: "Junior Python API Developer",
        department: "Engineering",
        location: "Gandhinagar, Gujarat, India",
        employmentType: "FULL_TIME",
        workplaceType: "HYBRID",
        openingsCount: 1,
        internalReference: "ENG-PY-01",
        summary: "Build scalable API services.",
        responsibilities: "Maintain high performance REST and GraphQL endpoints.",
        requirements: [
          {
            requirementId: "req-1",
            displayOrder: 0,
            category: "SKILL",
            label: "Python FastAPI",
            necessity: "MUST_HAVE",
            priority: "CRITICAL",
            acceptedEvidenceTypes: ["EMPLOYMENT"],
            acceptedEquivalents: [],
            origin: "RECRUITER_ENTERED",
          },
        ],
        reviewWarnings: [],
        createdAt: "2026-09-28T09:00:00Z",
        updatedAt: "2026-09-28T10:00:00Z",
      },
    };

    it("renders semantic definition grid (<dl>, <dt>, <dd>) without disabled inputs for locked revisions", async () => {
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockLockedJob);

      render(
        <JobEditor
          jobId="job-locked-1"
          onNavigateBack={vi.fn()}
        />
      );

      await screen.findByRole("heading", { name: /Junior Python API Developer/ });

      // Check section titles have no decorative numbering
      expect(screen.getByRole("heading", { name: "Job details" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "About the role" })).toBeInTheDocument();

      // Check values appear in semantic definition elements
      expect(screen.getByText("Engineering")).toBeInTheDocument();
      expect(screen.getByText("Gandhinagar, Gujarat, India")).toBeInTheDocument();
      expect(screen.getByText("Full-time")).toBeInTheDocument();
      expect(screen.getByText("Hybrid")).toBeInTheDocument();
      expect(screen.getByText("ENG-PY-01")).toBeInTheDocument();

      // Confirm there are NO disabled text inputs or textareas for job details
      expect(screen.queryByPlaceholderText(/e.g. Senior Backend Developer/i)).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/e.g. Frontend developer/i)).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/e.g. Engineering/i)).not.toBeInTheDocument();
    });

    it("renders editable inputs and brief example placeholders when editing a draft", async () => {
      const mockDraftJob: jobsApi.JobDetail = {
        ...mockLockedJob,
        currentVersion: {
          ...mockLockedJob.currentVersion,
          versionStatus: "DRAFT",
          revision: 2,
          title: "Junior Python API Developer",
        },
      };
      vi.mocked(jobsApi.fetchJobDetail).mockResolvedValue(mockDraftJob);

      render(
        <JobEditor
          jobId="job-draft-1"
          onNavigateBack={vi.fn()}
        />
      );

      await screen.findByDisplayValue("Junior Python API Developer");

      // Verify editable inputs
      const titleInput = screen.getByLabelText(/Job title/i);
      expect(titleInput).not.toBeDisabled();
      expect(titleInput).toHaveAttribute("placeholder", "e.g. Frontend developer");

      const deptInput = screen.getByLabelText(/Department/i);
      expect(deptInput).toHaveAttribute("placeholder", "e.g. Product engineering");

      const locInput = screen.getByLabelText(/Location/i);
      expect(locInput).toHaveAttribute("placeholder", "e.g. Ahmedabad, Gujarat");

      const refInput = screen.getByLabelText(/Reference code \(optional\)/i);
      expect(refInput).toHaveAttribute("placeholder", "e.g. ENG-104");
    });
  });

  describe("2. Candidate Login & Navigation Separation", () => {
    it("renders compact candidate login card without Staff sign in link", () => {
      render(
        <CandidateAuthProvider>
          <CandidateLoginPage />
        </CandidateAuthProvider>
      );

      expect(screen.getByText("Smart Hiring")).toBeInTheDocument();
      expect(screen.getByText("Candidate portal")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
      expect(screen.getByText(/Sign in to manage applications and complete released interviews/i)).toBeInTheDocument();

      // Inputs and buttons
      expect(screen.getByLabelText("Email address")).toBeInTheDocument();
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Sign In to Candidate Portal/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Create candidate account/i })).toBeInTheDocument();

      // Ensure NO Staff or Admin links exist on candidate page
      expect(screen.queryByText(/Staff sign in/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Are you a recruiter/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Admin login/i)).not.toBeInTheDocument();
    });
  });
});
