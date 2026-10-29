import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";
import { AdminWorkspace } from "../components/admin/AdminWorkspace";
import { InterviewWorkspace } from "../components/interviews/InterviewWorkspace";
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

describe("Phase 3A.4 Provider Administration & Interview Workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Admin AI Providers Workspace", () => {
    const mockProfiles = [
      {
        profileId: "p-local-1",
        displayName: "Local llama.cpp Qwen3",
        providerType: "LOCAL_OPENAI_COMPATIBLE",
        apiMode: "chat_completions",
        baseUrlMasked: "http://127.0.0.1:8080/v1",
        modelIdentifier: "qwen2.5:7b",
        secretStatus: "NOT_REQUIRED",
        supportedOperations: ["REWORD_QUESTION", "CREATE_BOUNDED_VARIANTS"],
        connectTimeoutSeconds: 5.0,
        requestTimeoutSeconds: 30.0,
        maxInputTokens: 4096,
        maxOutputTokens: 600,
        maxResponseBytes: 262144,
        maxConcurrency: 1,
        retryLimit: 0,
        egressClass: "LOCAL_ONLY",
        privacyReviewStatus: "APPROVED",
        enabled: true,
        compatibilityStatus: "NOT_TESTED",
        version: 1,
        createdAt: "2026-09-15T12:00:00Z",
        updatedAt: "2026-09-15T12:00:00Z",
      },
      {
        profileId: "p-remote-2",
        displayName: "Remote OpenAI Production",
        providerType: "REMOTE_OPENAI_COMPATIBLE",
        apiMode: "chat_completions",
        baseUrlMasked: "https://api.openai.com/v1",
        modelIdentifier: "gpt-4o-mini",
        secretStatus: "CONFIGURED",
        supportedOperations: ["REWORD_QUESTION", "CREATE_BOUNDED_VARIANTS"],
        connectTimeoutSeconds: 5.0,
        requestTimeoutSeconds: 30.0,
        maxInputTokens: 4096,
        maxOutputTokens: 600,
        maxResponseBytes: 262144,
        maxConcurrency: 1,
        retryLimit: 0,
        egressClass: "APPROVED_REMOTE",
        privacyReviewStatus: "APPROVED",
        enabled: false,
        compatibilityStatus: "NOT_TESTED",
        version: 1,
        createdAt: "2026-09-15T12:05:00Z",
        updatedAt: "2026-09-15T12:05:00Z",
      },
    ];

    it("renders configured AI provider profiles with egress and secret badges", async () => {
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockProfiles);

      render(<AdminWorkspace activeTab="ai_providers" />);

      await waitFor(() => {
        expect(screen.getByText("Local llama.cpp Qwen3")).toBeInTheDocument();
      });

      // Expand advanced settings accordion to see table and remote profiles
      const advancedHeader = screen.getByText(/Advanced Provider Profiles & History/i);
      fireEvent.click(advancedHeader);

      await waitFor(() => {
        expect(screen.getByText("Remote OpenAI Production")).toBeInTheDocument();
        expect(screen.getByText("Local AI")).toBeInTheDocument();
        expect(screen.getByText("Remote Provider")).toBeInTheDocument();
      });
    });

    it("requires explicit confirmation when activating an approved remote provider", async () => {
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockProfiles);

      render(<AdminWorkspace activeTab="ai_providers" />);

      await waitFor(() => {
        expect(screen.getByText("Local llama.cpp Qwen3")).toBeInTheDocument();
      });

      // Expand advanced settings
      const advancedHeader = screen.getByText(/Advanced Provider Profiles & History/i);
      fireEvent.click(advancedHeader);

      await waitFor(() => {
        expect(screen.getByText("Remote OpenAI Production")).toBeInTheDocument();
      });

      const activateBtn = screen.getByRole("button", { name: "Activate" });
      fireEvent.click(activateBtn);

      // Confirmation modal opens
      expect(screen.getByText("Remote Data Egress Confirmation Required")).toBeInTheDocument();
      const confirmSubmitBtn = screen.getByRole("button", { name: "Confirm Activation" });
      expect(confirmSubmitBtn).toBeDisabled();

      // Checkbox enabling
      const checkbox = screen.getByLabelText("I confirm and authorize remote data egress for this provider profile.");
      fireEvent.click(checkbox);
      expect(confirmSubmitBtn).not.toBeDisabled();

      // Submit activation
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce({
        ...mockProfiles[1],
        enabled: true,
        version: 2,
      });
      // Mock refresh call
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce([
        { ...mockProfiles[0], enabled: false },
        { ...mockProfiles[1], enabled: true, version: 2 },
      ]);

      fireEvent.click(confirmSubmitBtn);

      await waitFor(() => {
        expect(clientModule.apiFetch).toHaveBeenCalledWith(
          "/api/v1/admin/provider-profiles/p-remote-2/activate",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({
              version: 1,
              confirm_remote_egress: true,
            }),
          })
        );
      });
    });
  });

  describe("Recruiter Interview Workspace Shell", () => {
    const mockContext = {
      job_id: "0a2be23d-adc3-4029-ba44-b8937d733d4d",
      job_title: "Senior Backend Engineer",
      job_version_id: "v-1",
      job_revision: 2,
      candidate_id: "cand-12345",
      candidate_name: "Alex Mercer",
      candidate_email: "alex.mercer@example.com",
      resume_artifact_id: "res-98765",
      match_run_id: "run-456",
      match_score: 87.5,
      skill_gap_summary: "Confidence Tier: HIGH_CONFIDENCE (Score: 87.5%)",
      ai_capability: {
        ai_enabled: true,
        provider_class: "LOCAL_OPENAI_COMPATIBLE",
        availability_label: "Local AI Active",
        egress_class: "LOCAL_ONLY" as const,
        active_model_name: "qwen2.5:7b",
        message: null,
      },
      interview_plan_state: "EMPTY_PLAN",
      message: "Interview workspace ready.",
    };

    it("renders authorized requisition and candidate context without fake questions", async () => {
      vi.mocked(clientModule.apiFetch).mockResolvedValueOnce(mockContext);

      const onBack = vi.fn();
      render(
        <InterviewWorkspace
          jobId="0a2be23d-adc3-4029-ba44-b8937d733d4d"
          resumeId="res-98765"
          onNavigateBack={onBack}
        />
      );

      expect(screen.getByText("Loading verified candidate context & interview workspace...")).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.getByText("Interview Plan: Alex Mercer")).toBeInTheDocument();
      });

      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
      expect(screen.getByText("Local AI Active")).toBeInTheDocument();
      expect(screen.getByText("87.5%")).toBeInTheDocument();
      expect(screen.getByText("Standardized Job Interview Template")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Generate Standardized Template/i })).toBeInTheDocument();
    });

    it("displays access restriction message when unauthorized", async () => {
      vi.mocked(clientModule.apiFetch).mockRejectedValueOnce(
        new (clientModule.ApiError as any)(404, "JOB_NOT_FOUND", "Job requisition not found or unauthorized.")
      );

      const onBack = vi.fn();
      render(
        <InterviewWorkspace
          jobId="unauthorized-job"
          resumeId="res-98765"
          onNavigateBack={onBack}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Access Restricted")).toBeInTheDocument();
        expect(screen.getByText("Job requisition not found or unauthorized.")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: /Return to Candidates/i }));
      expect(onBack).toHaveBeenCalled();
    });
  });
});
