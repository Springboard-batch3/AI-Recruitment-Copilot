import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { JobsDirectory } from "../components/jobs/JobsDirectory";
import { AtsConnectionsAdmin } from "../components/admin/AtsConnectionsAdmin";
import { AtsImportModal } from "../components/ats/AtsImportModal";
import { AtsExportModal } from "../components/ats/AtsExportModal";
import { JobSummary } from "../api/jobs";
import * as atsApi from "../api/ats";

// Mock API functions
vi.mock("../api/ats", () => ({
  listAdminATSConnections: vi.fn(),
  createATSConnection: vi.fn(),
  testATSConnection: vi.fn(),
  toggleATSConnection: vi.fn(),
  toggleATSKillSwitch: vi.fn(),
  listActiveATSConnections: vi.fn(),
  triggerATSImport: vi.fn(),
  previewATSExport: vi.fn(),
  confirmATSExport: vi.fn(),
}));

describe("Phase 3E.3: Jobs Directory Polish & ATS Integration", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockJobs: JobSummary[] = [
    {
      jobId: "job-1",
      createdByUserId: "user-1",
      status: "ACTIVE",
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-15T00:00:00Z",
      currentVersion: {
        jobVersionId: "jv-1",
        versionNumber: 2,
        revision: 3,
        versionStatus: "APPROVED",
        title: "Senior Software Engineer",
        internalReference: "SWE-2026-01",
        department: "Engineering",
        location: "San Francisco, CA",
        workplaceType: "HYBRID",
        requirementsCount: 5,
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-15T00:00:00Z",
      },
    },
    {
      jobId: "job-2",
      createdByUserId: "user-1",
      status: "ACTIVE",
      createdAt: "2026-02-01T00:00:00Z",
      updatedAt: "2026-02-05T00:00:00Z",
      currentVersion: {
        jobVersionId: "jv-2",
        versionNumber: 1,
        revision: 1,
        versionStatus: "DRAFT",
        title: "Product Manager",
        internalReference: "PM-2026-02",
        department: "Product",
        location: "Remote",
        workplaceType: "REMOTE",
        requirementsCount: 0,
        createdAt: "2026-02-01T00:00:00Z",
        updatedAt: "2026-02-05T00:00:00Z",
      },
    },
  ];

  describe("Jobs Directory Polish", () => {
    it("renders page header with sentence case 'Create job' button and toolbar", () => {
      const handleCreate = vi.fn();
      const { container } = render(
        <JobsDirectory
          jobs={mockJobs}
          isLoading={false}
          error={null}
          onSelectJob={vi.fn()}
          onCreateJob={handleCreate}
          onRefresh={vi.fn()}
        />
      );

      expect(screen.getByRole("heading", { name: /Job requisitions/i })).toBeInTheDocument();
      const createBtn = screen.getByRole("button", { name: /^Create job$/i });
      expect(createBtn).toBeInTheDocument();
      fireEvent.click(createBtn);
      expect(handleCreate).toHaveBeenCalledTimes(1);

      // Search input with visible/accessible placeholder
      const searchInput = screen.getByPlaceholderText("Title, department, location or reference");
      expect(searchInput).toBeInTheDocument();

      // Polite live region for count
      const liveRegion = container.querySelector(".directory-count-badge");
      expect(liveRegion).toHaveAttribute("aria-live", "polite");
      expect(liveRegion).toHaveTextContent("2 jobs");
    });

    it("displays 'View' for approved jobs and 'Add requirements' for draft jobs without requirements", () => {
      const handleSelect = vi.fn();
      render(
        <JobsDirectory
          jobs={mockJobs}
          isLoading={false}
          error={null}
          onSelectJob={handleSelect}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      const viewBtn = screen.getByRole("button", { name: /View (job )?Senior Software Engineer/i });
      const editBtn = screen.getByRole("button", { name: /^Add requirements Product Manager$/i });

      expect(viewBtn).toBeInTheDocument();
      expect(editBtn).toBeInTheDocument();

      fireEvent.click(viewBtn);
      expect(handleSelect).toHaveBeenCalledWith("job-1");

      fireEvent.click(editBtn);
      expect(handleSelect).toHaveBeenCalledWith("job-2");
    });

    it("displays 'Version N · Revision N' and 'Needs requirements' for zero requirements", () => {
      render(
        <JobsDirectory
          jobs={mockJobs}
          isLoading={false}
          error={null}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      expect(screen.getByText("Version 2 · Revision 3")).toBeInTheDocument();
      expect(screen.getByText("Version 1 · Revision 1")).toBeInTheDocument();

      // Zero requirements shows 'Needs requirements' in badge
      expect(screen.getAllByText("Needs requirements").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText(/5 requirements/i)).toBeInTheDocument();
    });

    it("updates live filtered count on search and filtering", () => {
      const { container } = render(
        <JobsDirectory
          jobs={mockJobs}
          isLoading={false}
          error={null}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      const searchInput = screen.getByPlaceholderText("Title, department, location or reference");
      fireEvent.change(searchInput, { target: { value: "Product" } });

      const liveRegion = container.querySelector(".directory-count-badge");
      expect(liveRegion).toHaveTextContent("1 job");
      expect(screen.getByText("Product Manager")).toBeInTheDocument();
      expect(screen.queryByText("Senior Software Engineer")).not.toBeInTheDocument();
    });

    it("does NOT contain any interview question generation or template controls", () => {
      render(
        <JobsDirectory
          jobs={mockJobs}
          isLoading={false}
          error={null}
          onSelectJob={vi.fn()}
          onCreateJob={vi.fn()}
          onRefresh={vi.fn()}
        />
      );

      expect(screen.queryByText(/Generate AI Questions/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/AI Questions/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Choose a job/i)).not.toBeInTheDocument();
    });
  });

  describe("ATS Admin Integration", () => {
    it("renders connection list and masks secrets without revealing saved credentials", async () => {
      const mockConnections = [
        {
          connection_id: "conn-1",
          organization_id: "org-1",
          connection_name: "Mock Greenhouse Ingestion",
          adapter_type: "MOCK",
          tenant_label: "test-tenant-1",
          sync_scopes: ["JOBS", "CANDIDATES", "ATTACHMENTS"],
          is_enabled: true,
          kill_switch_active: false,
          health_status: "HEALTHY",
          secret_ref: "vault:secret_key_12345",
          last_sync_at: "2026-03-01T12:00:00Z",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ];

      vi.mocked(atsApi.listAdminATSConnections).mockResolvedValue({
        items: mockConnections as any,
        total_count: 1,
      });

      render(<AtsConnectionsAdmin />);

      await waitFor(() => {
        expect(screen.getByText("Mock Greenhouse Ingestion")).toBeInTheDocument();
      });

      expect(screen.getByText("MOCK")).toBeInTheDocument();
      expect(screen.getByText("HEALTHY")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Test" })).toBeInTheDocument();

      // Ensure secret ref or plaintext is not exposed in list UI
      expect(screen.queryByText("vault:secret_key_12345")).not.toBeInTheDocument();
    });
  });

  describe("ATS Recruiter Import Workflow", () => {
    it("shows dry run preview with valid, invalid, duplicate and changed counts", async () => {
      const mockActiveConnections = [
        {
          connection_id: "conn-mock",
          connection_name: "Mock HR Adapter",
          adapter_type: "MOCK",
          tenant_label: "staging",
        },
      ];

      const mockDryRunResult = {
        import_run_id: "run-dry-1",
        connection_id: "conn-mock",
        status: "COMPLETED",
        is_dry_run: true,
        successful_items_count: 7,
        failed_items_count: 1,
        duplicate_items_count: 2,
        changed_items_count: 0,
        safe_error_summary: ["Item ext-err: Unsupported file format (rejected at Phase 1 boundary)."],
      };

      vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
        items: mockActiveConnections as any,
        total_count: 1,
      });
      vi.mocked(atsApi.triggerATSImport).mockResolvedValue(mockDryRunResult as any);

      render(<AtsImportModal isOpen={true} onClose={vi.fn()} />);

      await waitFor(() => {
        expect(screen.getByText("Mock HR Adapter (MOCK)")).toBeInTheDocument();
      });

      const previewBtn = screen.getByRole("button", { name: /Dry Run Preview/i });
      fireEvent.click(previewBtn);

      await waitFor(() => {
        expect(screen.getByText("Dry Run Preview Results")).toBeInTheDocument();
      });

      expect(screen.getByText("7 ready · 2 already imported · 1 rejected")).toBeInTheDocument();
    });

    it("displays empty state when no active connections are returned", async () => {
      vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
        items: [],
        total_count: 0,
      });

      render(<AtsImportModal isOpen={true} onClose={vi.fn()} />);

      await waitFor(() => {
        expect(
          screen.getByText("No ATS connections are available for your workspace.")
        ).toBeInTheDocument();
      });

      expect(
        screen.getByText("Please contact an Administrator to configure an integration.")
      ).toBeInTheDocument();
    });

    it("displays retryable error state on API failure without claiming empty state", async () => {
      vi.mocked(atsApi.listActiveATSConnections).mockRejectedValue(
        new Error("Forbidden or network connection failure.")
      );

      render(<AtsImportModal isOpen={true} onClose={vi.fn()} />);

      await waitFor(() => {
        expect(
          screen.getByText("We couldn’t load ATS connections.")
        ).toBeInTheDocument();
      });

      // Must NOT show empty state text
      expect(
        screen.queryByText("No ATS connections are available for your workspace.")
      ).not.toBeInTheDocument();

      // Retry button is available and triggers reload
      const retryBtn = screen.getByRole("button", { name: "Retry" });
      expect(retryBtn).toBeInTheDocument();

      vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
        items: [
          {
            connection_id: "conn-1",
            connection_name: "Recovered ATS",
            adapter_type: "MOCK",
          } as any,
        ],
        total_count: 1,
      });

      fireEvent.click(retryBtn);

      await waitFor(() => {
        expect(screen.getByText("Recovered ATS (MOCK)")).toBeInTheDocument();
      });
    });
  });

  describe("ATS Recruiter Export Workflow", () => {
    it("previews exact destination, note content and requires confirmation with preview hash", async () => {
      const mockActiveConnections = [
        {
          connection_id: "conn-mock",
          connection_name: "Mock HR Adapter",
          adapter_type: "MOCK",
          tenant_label: "staging",
        },
      ];

      const mockPreview = {
        candidate_id: "cand-123",
        connection_id: "conn-mock",
        destination: "ATS Candidate Notes - Alex Rivera",
        note_text: "Recruiter reviewed candidate portfolio and verified qualifications.",
        preview_hash: "sha256:abc123456789deadbeef",
      };

      vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
        items: mockActiveConnections as any,
        total_count: 1,
      });
      vi.mocked(atsApi.previewATSExport).mockResolvedValue(mockPreview as any);
      vi.mocked(atsApi.confirmATSExport).mockResolvedValue({
        receipt_id: "rcpt-1",
        status: "SUCCESS",
        external_note_id: "ext-note-999",
      } as any);

      render(
        <AtsExportModal
          isOpen={true}
          candidateId="cand-123"
          candidateName="Alex Rivera"
          onClose={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Mock HR Adapter (MOCK)")).toBeInTheDocument();
      });

      const noteTextarea = screen.getByPlaceholderText(/Author or paste human-reviewed candidate assessment notes/i);
      fireEvent.change(noteTextarea, {
        target: { value: "Recruiter reviewed candidate portfolio and verified qualifications." },
      });

      const previewBtn = screen.getByRole("button", { name: /Preview Export/i });
      fireEvent.click(previewBtn);

      await waitFor(() => {
        expect(screen.getByText("Confirm & Push to ATS")).toBeInTheDocument();
      });

      expect(screen.getByText("ATS Candidate Notes - Alex Rivera")).toBeInTheDocument();
      expect(screen.getByText("sha256:abc123456789deadbeef")).toBeInTheDocument();

      const confirmBtn = screen.getByRole("button", { name: /Confirm & Push to ATS/i });
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(atsApi.confirmATSExport).toHaveBeenCalledWith({
          candidate_id: "cand-123",
          connection_id: "conn-mock",
          destination: "ATS Candidate Notes - Alex Rivera",
          note_text: "Recruiter reviewed candidate portfolio and verified qualifications.",
          preview_hash: "sha256:abc123456789deadbeef",
        });
      });
    });
  });
});
