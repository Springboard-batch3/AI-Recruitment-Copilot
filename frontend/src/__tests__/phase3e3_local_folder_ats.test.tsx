import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AtsImportModal } from "../components/ats/AtsImportModal";
import * as atsApi from "../api/ats";
import * as jobsApi from "../api/jobs";

vi.mock("../api/ats");
vi.mock("../api/jobs");
vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: {
      userId: "u-recruiter-1",
      email: "recruiter@example.com",
      displayName: "Recruiter User",
      role: "RECRUITER",
    },
  }),
}));

describe("Phase 3E.3: Local Folder ATS Demonstration Adapter Frontend Tests", () => {
  const mockLocalConnection = {
    connection_id: "conn-local-1",
    organization_id: "org-1",
    connection_name: "Local Candidate Drop Folder",
    adapter_type: "LOCAL_FOLDER_DEMO",
    tenant_label: "local_ats_demo/inbox",
    scopes: ["candidates.read"],
    is_enabled: true,
    health_status: "HEALTHY",
  };

  const mockJobs = [
    {
      jobId: "job-101",
      currentVersion: {
        title: "Senior Backend Engineer",
        department: "Engineering",
      },
    },
    {
      jobId: "job-102",
      currentVersion: {
        title: "Product Designer",
        department: "Design",
      },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
      items: [mockLocalConnection as any],
      total_count: 1,
    });
    vi.mocked(jobsApi.fetchJobs).mockResolvedValue(mockJobs as any);
  });

  it("loads Local Folder Demo connection and requires destination job selection", async () => {
    render(<AtsImportModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Local Candidate Drop Folder/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Senior Backend Engineer \(Engineering\)/i)).toBeInTheDocument();

    const scanBtn = screen.getByRole("button", { name: /Scan Folder/i });
    expect(scanBtn).toBeInTheDocument();
    expect(scanBtn).not.toBeDisabled();
  });

  it("executes folder dry-run scan and renders itemized preview table with metrics", async () => {
    const mockScanResponse = {
      import_run_id: "run-local-dry-1",
      connection_id: "conn-local-1",
      status: "COMPLETED",
      is_dry_run: true,
      successful_items_count: 2,
      failed_items_count: 1,
      duplicate_items_count: 1,
      changed_items_count: 0,
      total_candidates_count: 4,
      preview_token: "tok_12345",
      preview_hash: "hash_abcde",
      summary_json: {
        scan_items: [
          {
            filename: "alice_smith.pdf",
            format: "PDF",
            file_size_bytes: 45000,
            status: "READY",
          },
          {
            filename: "bob_jones.docx",
            format: "DOCX",
            file_size_bytes: 32000,
            status: "READY",
          },
          {
            filename: "charlie_dup.pdf",
            format: "PDF",
            file_size_bytes: 51000,
            status: "DUPLICATE",
            safe_error_message: "Candidate already imported.",
          },
          {
            filename: "corrupt.pdf",
            format: "PDF",
            file_size_bytes: 1200,
            status: "REJECTED",
            safe_error_message: "PDF parsing failed.",
          },
        ],
      },
    };

    vi.mocked(atsApi.triggerATSImport).mockResolvedValue(mockScanResponse as any);

    render(<AtsImportModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/Local Candidate Drop Folder/i)).toBeInTheDocument();
    });

    const scanBtn = screen.getByRole("button", { name: /Scan Folder/i });
    fireEvent.click(scanBtn);

    await waitFor(() => {
      expect(screen.getByText("Dry-Run Scan Preview")).toBeInTheDocument();
    });

    // Verify summary metrics
    expect(screen.getByText("2 ready · 1 already imported · 1 rejected")).toBeInTheDocument();

    // Verify item rows
    expect(screen.getByText("alice_smith.pdf")).toBeInTheDocument();
    expect(screen.getByText("bob_jones.docx")).toBeInTheDocument();
    expect(screen.getByText("charlie_dup.pdf")).toBeInTheDocument();
    expect(screen.getByText("corrupt.pdf")).toBeInTheDocument();

    // Status badges
    expect(screen.getAllByText("Ready to Import").length).toBe(2);
    expect(screen.getByText("Duplicate (Skip)")).toBeInTheDocument();
    expect(screen.getByText("REJECTED")).toBeInTheDocument();

    // Verify import confirmation button label
    const importBtn = screen.getByRole("button", { name: /Import 2 candidates/i });
    expect(importBtn).toBeInTheDocument();
    expect(importBtn).not.toBeDisabled();
  });

  it("confirms import with selected job and displays completed receipt view", async () => {
    const mockScanResponse = {
      import_run_id: "run-local-dry-2",
      connection_id: "conn-local-1",
      status: "COMPLETED",
      is_dry_run: true,
      successful_items_count: 1,
      failed_items_count: 0,
      duplicate_items_count: 0,
      changed_items_count: 0,
      total_candidates_count: 1,
      preview_token: "tok_confirm_123",
      preview_hash: "hash_confirm_abc",
      summary_json: {
        scan_items: [
          {
            filename: "john_doe.pdf",
            format: "PDF",
            file_size_bytes: 40000,
            status: "READY",
          },
        ],
      },
    };

    const mockConfirmResponse = {
      import_run_id: "run-local-live-2",
      connection_id: "conn-local-1",
      status: "COMPLETED",
      is_dry_run: false,
      successful_items_count: 1,
      failed_items_count: 0,
      duplicate_items_count: 0,
      changed_items_count: 0,
      total_candidates_count: 1,
      summary_json: {
        source_provenance: "Local ATS Demo",
        destination_job_title: "Senior Backend Engineer",
      },
    };

    vi.mocked(atsApi.triggerATSImport)
      .mockResolvedValueOnce(mockScanResponse as any)
      .mockResolvedValueOnce(mockConfirmResponse as any);

    const onImportComplete = vi.fn();
    const onClose = vi.fn();

    render(<AtsImportModal isOpen={true} onClose={onClose} onImportComplete={onImportComplete} />);

    await waitFor(() => {
      expect(screen.getByText(/Local Candidate Drop Folder/i)).toBeInTheDocument();
    });

    // Trigger Scan
    const scanBtn = screen.getByRole("button", { name: /Scan Folder/i });
    fireEvent.click(scanBtn);

    await waitFor(() => {
      expect(screen.getByText("Import 1 candidate")).toBeInTheDocument();
    });

    // Trigger Import
    const importBtn = screen.getByRole("button", { name: /Import 1 candidate/i });
    fireEvent.click(importBtn);

    await waitFor(() => {
      expect(screen.getByText(/Import Completed \(COMPLETED\)/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Local ATS Demo/i)).toBeInTheDocument();
    expect(atsApi.triggerATSImport).toHaveBeenCalledWith("conn-local-1", {
      is_dry_run: false,
      destination_job_id: "job-101",
      preview_token: "tok_confirm_123",
      preview_hash: "hash_confirm_abc",
    });

    // Close button triggers onImportComplete
    const doneBtn = screen.getByRole("button", { name: /Go to Candidates/i });
    fireEvent.click(doneBtn);

    expect(onClose).toHaveBeenCalled();
    expect(onImportComplete).toHaveBeenCalled();
  });
});
