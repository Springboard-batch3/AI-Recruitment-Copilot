import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AdminWorkspace } from "../components/admin/AdminWorkspace";

describe("Admin Candidates Workspace & Management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders Candidate Directory and does not show Reset Password button for candidates", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/admin/candidates") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                id: "cand-uuid-1",
                identityId: "cand-uuid-1",
                fullName: "Alice Walker",
                email: "alice@example.com",
                headline: "Frontend Engineer",
                isActive: true,
                hasAccount: true,
                createdAt: "2026-10-01T10:00:00Z",
                applicationsCount: 2,
                interviewsCount: 1,
              },
              {
                id: "cand-uuid-2",
                identityId: "cand-uuid-2",
                fullName: "Bob Smith",
                email: "bob@example.com",
                headline: "Backend Engineer",
                isActive: false,
                hasAccount: false,
                createdAt: "2026-10-02T10:00:00Z",
                applicationsCount: 1,
                interviewsCount: 0,
              },
            ]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<AdminWorkspace activeTab="candidates" />);

    await waitFor(() => {
      expect(screen.getByText("Candidate Directory & Accounts")).toBeInTheDocument();
      expect(screen.getByText("Alice Walker")).toBeInTheDocument();
      expect(screen.getByText("alice@example.com")).toBeInTheDocument();
      expect(screen.getByText("Bob Smith")).toBeInTheDocument();
      expect(screen.getByText("bob@example.com")).toBeInTheDocument();
    });

    // Reset Password button must NOT be present on candidate accounts
    expect(screen.queryByText("Reset Password")).not.toBeInTheDocument();

    // Active candidate should have Deactivate button
    expect(screen.getByText("Deactivate")).toBeInTheDocument();

    // Deactivated candidate (Bob Smith) should have Activate and Delete buttons
    expect(screen.getByText("Activate")).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();
  });

  it("handles candidate search filtering", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/admin/candidates") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                id: "cand-uuid-1",
                fullName: "Charlie Brown",
                email: "charlie@example.com",
                isActive: true,
                hasAccount: true,
                createdAt: "2026-10-01T10:00:00Z",
                applicationsCount: 1,
                interviewsCount: 0,
              },
              {
                id: "cand-uuid-2",
                fullName: "Diana Prince",
                email: "diana@example.com",
                isActive: true,
                hasAccount: true,
                createdAt: "2026-10-02T10:00:00Z",
                applicationsCount: 0,
                interviewsCount: 0,
              },
            ]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<AdminWorkspace activeTab="candidates" />);

    await waitFor(() => {
      expect(screen.getByText("Charlie Brown")).toBeInTheDocument();
      expect(screen.getByText("Diana Prince")).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/Search candidates/i);
    fireEvent.change(searchInput, { target: { value: "Diana" } });

    expect(screen.queryByText("Charlie Brown")).not.toBeInTheDocument();
    expect(screen.getByText("Diana Prince")).toBeInTheDocument();
  });

  it("calls DELETE endpoint when Delete Candidate is clicked and confirmed", async () => {
    let deleteCalled = false;
    let deleteUrl = "";

    vi.spyOn(window, "confirm").mockReturnValue(true);

    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/v1/admin/candidates") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                id: "cand-uuid-to-delete",
                fullName: "Candidate To Delete",
                email: "dropme@example.com",
                isActive: false,
                hasAccount: true,
                createdAt: "2026-10-01T10:00:00Z",
                applicationsCount: 0,
                interviewsCount: 0,
              },
            ]),
        });
      }
      if (url === "/api/v1/admin/candidates/cand-uuid-to-delete" && opts?.method === "DELETE") {
        deleteCalled = true;
        deleteUrl = url;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ message: "Candidate dropped successfully" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<AdminWorkspace activeTab="candidates" />);

    await waitFor(() => {
      expect(screen.getByText("Candidate To Delete")).toBeInTheDocument();
    });

    const deleteBtn = screen.getByText("Delete");
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
      expect(deleteUrl).toBe("/api/v1/admin/candidates/cand-uuid-to-delete");
    });
  });

  it("renders Delete button for deactivated platform user and triggers DELETE user endpoint", async () => {
    let deleteUserCalled = false;
    let deleteUserUrl = "";

    vi.spyOn(window, "confirm").mockReturnValue(true);

    globalThis.fetch = vi.fn().mockImplementation((url, opts) => {
      if (url === "/api/v1/admin/users") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                userId: "user-uuid-inactive",
                email: "recruiter.diag@company.test",
                displayName: "Diag Recruiter",
                role: "RECRUITER",
                isActive: false,
                mustChangePassword: false,
                createdAt: "2026-09-04T10:00:00Z",
              },
            ]),
        });
      }
      if (url === "/api/v1/admin/users/user-uuid-inactive" && opts?.method === "DELETE") {
        deleteUserCalled = true;
        deleteUserUrl = url;
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ message: "User deleted successfully" }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<AdminWorkspace activeTab="users" />);

    await waitFor(() => {
      expect(screen.getByText("Diag Recruiter")).toBeInTheDocument();
      expect(screen.getByText("Inactive")).toBeInTheDocument();
    });

    // Both Activate and Delete buttons should be present for deactivated user
    expect(screen.getByText("Activate")).toBeInTheDocument();
    const deleteBtn = screen.getByText("Delete");
    expect(deleteBtn).toBeInTheDocument();

    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(deleteUserCalled).toBe(true);
      expect(deleteUserUrl).toBe("/api/v1/admin/users/user-uuid-inactive");
    });
  });

  it("displays candidate exact account email ID and matches search on account email and resume email", async () => {
    globalThis.fetch = vi.fn().mockImplementation((url) => {
      if (url === "/api/v1/admin/candidates") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () =>
            Promise.resolve([
              {
                id: "cand-zayaan-id",
                identityId: "cand-zayaan-id",
                fullName: "Zayaan Contractor",
                email: "zc@1.com",
                accountEmail: "zc@1.com",
                resumeEmail: "zayaan.contractor@example.com",
                headline: "Frontend Developer",
                isActive: true,
                hasAccount: true,
                createdAt: "2026-10-03T10:00:00Z",
                applicationsCount: 1,
                interviewsCount: 0,
              },
            ]),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
    });

    render(<AdminWorkspace activeTab="candidates" />);

    await waitFor(() => {
      expect(screen.getByText("Zayaan Contractor")).toBeInTheDocument();
      // Exact account email ID must be shown
      expect(screen.getByText("zc@1.com")).toBeInTheDocument();
      // Distinct resume email notice must be shown
      expect(screen.getByText(/Resume email: zayaan\.contractor@example\.com/i)).toBeInTheDocument();
    });

    // Test search by exact account email ID
    const searchInput = screen.getByPlaceholderText(/Search candidates/i);
    fireEvent.change(searchInput, { target: { value: "zc@1.com" } });
    expect(screen.getByText("Zayaan Contractor")).toBeInTheDocument();

    // Test search by resume email
    fireEvent.change(searchInput, { target: { value: "zayaan.contractor" } });
    expect(screen.getByText("Zayaan Contractor")).toBeInTheDocument();
  });
});
