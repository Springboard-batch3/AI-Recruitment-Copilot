import React, { useRef, useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { AuthenticatedShell } from "../components/AuthenticatedShell";
import { AuthContext } from "../context/AuthContext";
import { PageHeader } from "../components/common/PageHeader";
import { Button } from "../components/common/Button";
import { FormField } from "../components/common/FormField";
import { StatusBadge, humanizeStatus } from "../components/common/StatusBadge";
import { InlineNotice } from "../components/common/InlineNotice";
import { EmptyState } from "../components/common/EmptyState";
import { Dialog } from "../components/common/Dialog";

describe("Phase 3E.1: UI Foundation, Navigation & Shared Primitives", () => {
  const mockRecruiterAuth = {
    user: {
      user_id: "u-recruiter-1",
      userId: "u-recruiter-1",
      email: "recruiter@example.com",
      display_name: "Recruiter User",
      displayName: "Recruiter User",
      role: "RECRUITER" as const,
      is_active: true,
      isActive: true,
      must_change_password: false,
      mustChangePassword: false,
    },
    status: "AUTHENTICATED" as const,
    csrfToken: "mock-csrf",
    sessionError: null,
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshSession: vi.fn(),
    clearSessionError: vi.fn(),
  };

  describe("Section A: Authenticated Shell & Navigation", () => {
    it("renders the skip link targeting #main-content", () => {
      render(
        <AuthContext.Provider value={mockRecruiterAuth}>
          <AuthenticatedShell activeNavTab="jobs">
            <div>Main Content Area</div>
          </AuthenticatedShell>
        </AuthContext.Provider>
      );

      const skipLink = screen.getByRole("link", { name: /skip to main content/i });
      expect(skipLink).toBeInTheDocument();
      expect(skipLink).toHaveAttribute("href", "#main-content");

      const mainElement = screen.getByRole("main");
      expect(mainElement).toHaveAttribute("id", "main-content");
    });

    it("renders the 4 approved recruiter navigation items in exact order", () => {
      render(
        <AuthContext.Provider value={mockRecruiterAuth}>
          <AuthenticatedShell activeNavTab="jobs">
            <div>Content</div>
          </AuthenticatedShell>
        </AuthContext.Provider>
      );

      const navElement = screen.getByRole("navigation", { name: /main navigation|role navigation/i });
      const navButtons = navElement.querySelectorAll("button");
      expect(navButtons.length).toBe(5);

      expect(navButtons[0]).toHaveTextContent("Jobs");
      expect(navButtons[1]).toHaveTextContent("Candidates");
      expect(navButtons[2]).toHaveTextContent("Interviews");
      expect(navButtons[3]).toHaveTextContent("Evaluations");
      expect(navButtons[4]).toHaveTextContent("Analytics");
    });

    it("marks the active navigation tab with aria-current='page'", () => {
      const { rerender } = render(
        <AuthContext.Provider value={mockRecruiterAuth}>
          <AuthenticatedShell activeNavTab="jobs">
            <div>Jobs View</div>
          </AuthenticatedShell>
        </AuthContext.Provider>
      );

      const jobsBtn = screen.getByRole("button", { name: /^Jobs/i });
      expect(jobsBtn).toHaveAttribute("aria-current", "page");

      // Switch to AI Questions
      rerender(
        <AuthContext.Provider value={mockRecruiterAuth}>
          <AuthenticatedShell activeNavTab="ai_interviews">
            <div>AI Questions View</div>
          </AuthenticatedShell>
        </AuthContext.Provider>
      );

      const aiBtn = screen.getByRole("button", { name: /AI-generated interview questions/i });
      expect(aiBtn).toHaveAttribute("aria-current", "page");
      expect(screen.getByRole("button", { name: /^Jobs/i })).not.toHaveAttribute("aria-current");
    });
  });

  describe("Section B: PageHeader Shared Primitive", () => {
    it("renders title, subtitle, back button, and actions correctly", () => {
      const handleBack = vi.fn();
      render(
        <PageHeader
          title="Job Requisitions"
          subtitle="Manage active job requisition details"
          backAction={{ label: "Back to List", onClick: handleBack }}
          actions={<button type="button">Create Job</button>}
        />
      );

      expect(screen.getByRole("heading", { level: 1, name: "Job Requisitions" })).toBeInTheDocument();
      expect(screen.getByText("Manage active job requisition details")).toBeInTheDocument();

      const backBtn = screen.getByRole("button", { name: "Back to List" });
      fireEvent.click(backBtn);
      expect(handleBack).toHaveBeenCalledTimes(1);

      expect(screen.getByRole("button", { name: "Create Job" })).toBeInTheDocument();
    });
  });

  describe("Section C: Button Shared Primitive", () => {
    it("renders primary and secondary button variants with icons", () => {
      render(
        <>
          <Button variant="primary" icon="plus">
            Create Requisition
          </Button>
          <Button variant="secondary" icon="arrow-left">
            Back
          </Button>
          <Button variant="quiet">
            Cancel
          </Button>
          <Button variant="destructive">
            Delete Draft
          </Button>
        </>
      );

      const primaryBtn = screen.getByRole("button", { name: /Create Requisition/i });
      expect(primaryBtn).toHaveClass("btn-primary");

      const secBtn = screen.getByRole("button", { name: /Back/i });
      expect(secBtn).toHaveClass("btn-secondary");

      const quietBtn = screen.getByRole("button", { name: /Cancel/i });
      expect(quietBtn).toHaveClass("btn-quiet");

      const destBtn = screen.getByRole("button", { name: /Delete Draft/i });
      expect(destBtn).toHaveClass("btn-destructive");
    });

    it("renders loading state with descriptive text and disables button", () => {
      render(
        <Button variant="primary" isLoading loadingText="Saving changes...">
          Save
        </Button>
      );

      const btn = screen.getByRole("button");
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute("aria-busy", "true");
      expect(screen.getByText("Saving changes...")).toBeInTheDocument();
    });
  });

  describe("Section D: FormField Shared Primitive", () => {
    it("renders accessible label, hint, and connects aria attributes", () => {
      render(
        <FormField id="job-title" label="Job Title" hint="Enter official requisition title" required>
          <input id="job-title" type="text" />
        </FormField>
      );

      expect(screen.getByLabelText(/Job Title/i)).toBeInTheDocument();
      expect(screen.getByText("Enter official requisition title")).toHaveAttribute("id", "job-title-hint");
    });

    it("renders accessible error state with role='alert'", () => {
      render(
        <FormField id="job-title" label="Job Title" error="Job title is required.">
          <input id="job-title" type="text" />
        </FormField>
      );

      const errorMsg = screen.getByRole("alert");
      expect(errorMsg).toHaveTextContent("Job title is required.");
      expect(errorMsg).toHaveAttribute("id", "job-title-error");
    });
  });

  describe("Section E: StatusBadge and Humanize Function", () => {
    it("converts all raw enums into human-readable labels", () => {
      expect(humanizeStatus("APPROVED")).toBe("Approved");
      expect(humanizeStatus("VALIDATED")).toBe("Ready for Review");
      expect(humanizeStatus("DRAFT")).toBe("Draft");
      expect(humanizeStatus("STALE")).toBe("Stale");
      expect(humanizeStatus("KEPT")).toBe("Reviewed");
      expect(humanizeStatus("EDITED")).toBe("Edited");
      expect(humanizeStatus("REVISED")).toBe("Regenerated");
      expect(humanizeStatus("RECRUITER_AUTHORED")).toBe("Written by Recruiter");
      expect(humanizeStatus("UNREVIEWED")).toBe("To Review");
    });

    it("renders humanized badge without raw enum wording", () => {
      render(<StatusBadge status="RECRUITER_AUTHORED" />);
      expect(screen.getByText("Written by Recruiter")).toBeInTheDocument();
      expect(screen.queryByText("RECRUITER_AUTHORED")).not.toBeInTheDocument();
    });
  });

  describe("Section F: InlineNotice and EmptyState Primitives", () => {
    it("renders info, warning, and error notice variants with correct accessibility roles", () => {
      const { rerender } = render(
        <InlineNotice variant="info" title="Information">
          System is operating normally.
        </InlineNotice>
      );

      expect(screen.getByRole("status")).toHaveTextContent("System is operating normally.");

      rerender(
        <InlineNotice variant="error" title="Generation Failed">
          Local model is unavailable.
        </InlineNotice>
      );

      expect(screen.getByRole("alert")).toHaveTextContent("Local model is unavailable.");
    });

    it("renders EmptyState with title, description, and actions", () => {
      const handleAction = vi.fn();
      render(
        <EmptyState
          icon="briefcase"
          title="No Job Requisitions"
          description="Create your first job requisition to get started."
          primaryAction={{ label: "Create Job", onClick: handleAction }}
        />
      );

      expect(screen.getByRole("region", { name: "No Job Requisitions" })).toBeInTheDocument();
      expect(screen.getByText("Create your first job requisition to get started.")).toBeInTheDocument();

      const btn = screen.getByRole("button", { name: "Create Job" });
      fireEvent.click(btn);
      expect(handleAction).toHaveBeenCalledTimes(1);
    });
  });

  describe("Section G: Dialog Accessible Modal Pattern", () => {
    function DialogHarness() {
      const [isOpen, setIsOpen] = useState(false);
      const openBtnRef = useRef<HTMLButtonElement>(null);

      return (
        <div>
          <button
            ref={openBtnRef}
            type="button"
            onClick={() => setIsOpen(true)}
          >
            Open Evaluation Rubric
          </button>

          <Dialog
            isOpen={isOpen}
            onClose={() => setIsOpen(false)}
            title="Evaluation Rubric: System Architecture"
            triggerRef={openBtnRef}
            footer={
              <button type="button" onClick={() => setIsOpen(false)}>
                Done
              </button>
            }
          >
            <p>Rubric details and scoring anchors</p>
            <input type="text" placeholder="Notes" />
          </Dialog>
        </div>
      );
    }

    it("opens dialog with role='dialog', aria-modal='true', and traps Escape key to close", () => {
      render(<DialogHarness />);

      const triggerBtn = screen.getByRole("button", { name: "Open Evaluation Rubric" });
      fireEvent.click(triggerBtn);

      const dialog = screen.getByRole("dialog");
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute("aria-modal", "true");
      expect(screen.getByRole("heading", { name: /Evaluation Rubric: System Architecture/i })).toBeInTheDocument();

      // Press Escape key
      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });
});
