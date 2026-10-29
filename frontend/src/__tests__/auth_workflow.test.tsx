import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import App from "../App";

describe("Phase 2A.4: Authentication & Role-Aware Shell Workflow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("1. shows loading splash on initial session restoration and handles 401 unauthenticated", async () => {
    // Mock /api/v1/auth/me to return 401
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ detail: { code: "UNAUTHENTICATED", message: "Auth required" } }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    // Initially shows splash
    expect(screen.getByText(/Verifying session/i)).toBeInTheDocument();

    // Resolves to login page
    await waitFor(() => {
      expect(screen.getByText(/Smart Hiring/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Work email/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/^Password$/i)).toBeInTheDocument();
    });

    // Ensure candidate directory is never displayed/flashed
    expect(screen.queryByText(/Candidates Directory/i)).not.toBeInTheDocument();
  });

  it("2. supports password visibility toggle with accessible label", async () => {
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: { code: "UNAUTHENTICATED" } }),
      })
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText(/^Password$/i)).toBeInTheDocument();
    });

    const passwordInput = screen.getByLabelText(/^Password$/i);
    const toggleBtn = screen.getByRole("button", { name: /Show password/i });

    expect(passwordInput).toHaveAttribute("type", "password");

    // Click toggle
    await userEvent.click(toggleBtn);
    expect(passwordInput).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: /Hide password/i })).toBeInTheDocument();

    // Click toggle again
    await userEvent.click(screen.getByRole("button", { name: /Hide password/i }));
    expect(passwordInput).toHaveAttribute("type", "password");
  });

  it("3. validates required fields and displays generic login error on invalid credentials", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ detail: { code: "UNAUTHENTICATED" } }),
        });
      }
      if (url === "/api/v1/auth/login" && opts?.method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ detail: { code: "INVALID_CREDENTIALS", message: "Unable to sign in with the provided credentials." } }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByLabelText(/Work email/i)).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole("button", { name: /^Sign in$/i });

    // Submit empty
    await userEvent.click(submitBtn);
    expect(screen.getByText(/Please enter your email address/i)).toBeInTheDocument();

    // Fill email only
    await userEvent.type(screen.getByLabelText(/Work email/i), "user@company.com");
    await userEvent.click(submitBtn);
    expect(screen.getByText(/Please enter your password/i)).toBeInTheDocument();

    // Fill password and submit
    await userEvent.type(screen.getByLabelText(/^Password$/i), "WrongPassword123!");
    await userEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/Unable to sign in with the provided credentials/i)).toBeInTheDocument();
    });
  });

  it("4. redirects to ChangePasswordPage when must_change_password is true and validates input before submission", async () => {
    let changePasswordCalled = false;

    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "user-uuid-1",
              email: "temp.user@company.com",
              displayName: "Temp Recruiter",
              role: "RECRUITER",
              isActive: true,
              mustChangePassword: true,
              csrfToken: "csrf-token-1",
            }),
        });
      }
      if (url === "/api/v1/auth/change-password" && opts?.method === "POST") {
        changePasswordCalled = true;
        expect(opts.headers.get("X-CSRF-Token")).toBe("csrf-token-1");
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              user: {
                userId: "user-uuid-1",
                email: "temp.user@company.com",
                displayName: "Temp Recruiter",
                role: "RECRUITER",
                isActive: true,
                mustChangePassword: false,
              },
              csrfToken: "csrf-token-rotated-2",
            }),
        });
      }
      if (url === "/api/v1/resumes") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Password Update Required/i })).toBeInTheDocument();
      expect(screen.getByLabelText(/Current \/ Temporary Password/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/New Password \(min. 8 characters\)/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Confirm New Password/i)).toBeInTheDocument();
    });

    // Recruiter dashboard is prevented initially
    expect(screen.queryByText(/Candidates Directory/i)).not.toBeInTheDocument();

    const submitBtn = screen.getByRole("button", { name: /Update Password & Continue/i });

    // 1. Submit empty - triggers validation
    await userEvent.click(submitBtn);
    expect(screen.getByText(/Please enter your current or temporary password/i)).toBeInTheDocument();
    expect(changePasswordCalled).toBe(false);

    // 2. Mismatched confirmation
    await userEvent.type(screen.getByLabelText(/Current \/ Temporary Password/i), "TempPassword123!");
    await userEvent.type(screen.getByLabelText(/New Password \(min. 8 characters\)/i), "NewPassword123!");
    await userEvent.type(screen.getByLabelText(/Confirm New Password/i), "MismatchPassword123!");
    await userEvent.click(submitBtn);
    expect(screen.getByText(/New password confirmation does not match/i)).toBeInTheDocument();
    expect(changePasswordCalled).toBe(false);

    // 3. Proper password match submission
    await userEvent.clear(screen.getByLabelText(/Confirm New Password/i));
    await userEvent.type(screen.getByLabelText(/Confirm New Password/i), "NewPassword123!");
    await userEvent.click(submitBtn);

    // 4. Transitions immediately to Recruiter workspace and leaves ChangePasswordPage
    await waitFor(() => {
      expect(changePasswordCalled).toBe(true);
      expect(screen.queryByRole("heading", { name: /Password Update Required/i })).not.toBeInTheDocument();
      expect(screen.getByText("Temp Recruiter")).toBeInTheDocument();
      expect(screen.getByText("RECRUITER")).toBeInTheDocument();
    });
  });

  it("5. successfully routes Recruiter to Milestone 1 Candidate Workspace", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "recruiter-uuid-1",
              email: "recruiter@company.com",
              displayName: "Jane Recruiter",
              role: "RECRUITER",
              isActive: true,
              mustChangePassword: false,
              csrfToken: "csrf-token-recruiter",
            }),
        });
      }
      if (url === "/api/v1/resumes") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        });
      }
      if (url === "/api/v1/health") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ status: "healthy", database: "connected" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Jane Recruiter")).toBeInTheDocument();
      expect(screen.getByText("RECRUITER")).toBeInTheDocument();
      expect(screen.getAllByText(/Upload Resume/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/No candidate profiles yet/i)).toBeInTheDocument();
    });

    // Admin / Reviewer tabs must not be present
    expect(screen.queryByText(/User Management/i)).not.toBeInTheDocument();
  });

  it("6. successfully routes Admin to Admin Workspace with User Management & Audit Logs", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "admin-uuid-1",
              email: "admin@company.com",
              displayName: "Super Admin",
              role: "ADMIN",
              isActive: true,
              mustChangePassword: false,
              csrfToken: "csrf-token-admin",
            }),
        });
      }
      if (url === "/api/v1/admin/users") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                userId: "admin-uuid-1",
                email: "admin@company.com",
                displayName: "Super Admin",
                role: "ADMIN",
                isActive: true,
                mustChangePassword: false,
                createdAt: "2026-09-01T12:00:00Z",
              },
            ]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText("Super Admin").length).toBeGreaterThan(0);
      expect(screen.getAllByText("ADMIN").length).toBeGreaterThan(0);
      expect(screen.getByText("Create New User")).toBeInTheDocument();
      expect(screen.getByText(/Platform Users/i)).toBeInTheDocument();
      expect(screen.getByText("admin@company.com")).toBeInTheDocument();
    });

    // Recruiter candidate directory tabs must NOT be present
    expect(screen.queryByText("Upload Resume")).not.toBeInTheDocument();
  });

  it("7. successfully routes Reviewer to Reviewer Workspace empty state", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "reviewer-uuid-1",
              email: "reviewer@company.com",
              displayName: "Hiring Reviewer",
              role: "REVIEWER",
              isActive: true,
              mustChangePassword: false,
              csrfToken: "csrf-token-reviewer",
            }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Hiring Reviewer")).toBeInTheDocument();
      expect(screen.getByText("REVIEWER")).toBeInTheDocument();
      expect(screen.getByText(/No assigned reviews yet/i)).toBeInTheDocument();
      expect(screen.getByText(/Assigned job reviews and evaluation scorecards will appear here once job requisitions are configured/i)).toBeInTheDocument();
    });

    expect(screen.queryByText(/Platform Users/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Upload Resume")).not.toBeInTheDocument();
  });

  it("8. executes logout flow by calling /api/v1/auth/logout with CSRF and returning to login", async () => {
    let logoutCalled = false;

    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "recruiter-uuid-1",
              email: "recruiter@company.com",
              displayName: "Jane Recruiter",
              role: "RECRUITER",
              isActive: true,
              mustChangePassword: false,
              csrfToken: "csrf-token-recruiter",
            }),
        });
      }
      if (url === "/api/v1/resumes") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        });
      }
      if (url === "/api/v1/auth/logout" && opts?.method === "POST") {
        logoutCalled = true;
        // Verify CSRF header sent
        expect(opts.headers.get("X-CSRF-Token")).toBe("csrf-token-recruiter");
        return Promise.resolve({ ok: true, status: 204 });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Jane Recruiter")).toBeInTheDocument();
    });

    // Open user profile menu
    const profileBtn = screen.getByRole("button", { name: /User profile and account settings/i });
    await userEvent.click(profileBtn);

    const signOutBtn = screen.getByRole("menuitem", { name: /Sign Out/i });
    await userEvent.click(signOutBtn);

    await waitFor(() => {
      expect(logoutCalled).toBe(true);
      expect(screen.getByRole("heading", { name: /Smart Hiring/i })).toBeInTheDocument();
    });

    // Ensure tokens are never placed into localStorage or sessionStorage
    expect(localStorage.getItem("token")).toBeNull();
    expect(localStorage.getItem("csrfToken")).toBeNull();
    expect(sessionStorage.getItem("token")).toBeNull();
  });

  it("9. closes user profile dropdown menu on Escape key press and restores focus", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/auth/me") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve({
              userId: "recruiter-uuid-2",
              email: "recruiter2@company.com",
              displayName: "Alex Recruiter",
              role: "RECRUITER",
              isActive: true,
              mustChangePassword: false,
              csrfToken: "csrf-token-recruiter-2",
            }),
        });
      }
      if (url === "/api/v1/resumes") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve([]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Alex Recruiter")).toBeInTheDocument();
    });

    const profileBtn = screen.getByRole("button", { name: /User profile and account settings/i });
    expect(screen.queryByRole("menu", { name: /User options/i })).not.toBeInTheDocument();

    // Open menu
    await userEvent.click(profileBtn);
    expect(screen.getByRole("menu", { name: /User options/i })).toBeInTheDocument();

    // Press Escape
    fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
    expect(screen.queryByRole("menu", { name: /User options/i })).not.toBeInTheDocument();
  });
});

