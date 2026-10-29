import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { AuthenticatedShell } from "../components/AuthenticatedShell";
import { AnalyticsLauncher } from "../components/analytics/AnalyticsLauncher";
import * as clientApi from "../api/client";
import { AuthContext } from "../context/AuthContext";

describe("Phase 4E.2 — Analytics Launcher & Navigation Access Control", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  const renderWithAuth = (ui: React.ReactNode, role: "RECRUITER" | "ADMIN" | "REVIEWER" | "CANDIDATE" = "RECRUITER") => {
    const mockAuthValue: any = {
      user: {
        userId: "00000000-0000-0000-0000-000000000001",
        email: `${role.toLowerCase()}@example.com`,
        displayName: `${role} User`,
        role: role,
        isActive: true,
        mustChangePassword: false,
      },
      status: "AUTHENTICATED",
      login: vi.fn(),
      logout: vi.fn(),
      changePassword: vi.fn(),
    };

    return render(
      <AuthContext.Provider value={mockAuthValue}>
        {ui}
      </AuthContext.Provider>
    );
  };

  it("renders Analytics tab for Recruiter role", () => {
    const handleNav = vi.fn();
    renderWithAuth(
      <AuthenticatedShell activeNavTab="candidates" onNavigateTab={handleNav}>
        <div>Main Content</div>
      </AuthenticatedShell>,
      "RECRUITER"
    );

    const analyticsBtn = screen.getByRole("button", { name: /analytics/i });
    expect(analyticsBtn).toBeDefined();
    fireEvent.click(analyticsBtn);
    expect(handleNav).toHaveBeenCalledWith("analytics");
  });

  it("renders Analytics tab for Admin role", () => {
    const handleNav = vi.fn();
    renderWithAuth(
      <AuthenticatedShell activeNavTab="users" onNavigateTab={handleNav}>
        <div>Admin Content</div>
      </AuthenticatedShell>,
      "ADMIN"
    );

    const analyticsBtn = screen.getByRole("button", { name: /analytics/i });
    expect(analyticsBtn).toBeDefined();
  });

  it("does NOT render Analytics tab for Reviewer or Candidate roles", () => {
    renderWithAuth(
      <AuthenticatedShell activeNavTab="reviews">
        <div>Reviewer Content</div>
      </AuthenticatedShell>,
      "REVIEWER"
    );

    const analyticsBtn = screen.queryByRole("button", { name: /analytics/i });
    expect(analyticsBtn).toBeNull();
  });

  it("AnalyticsLauncher requests single-use ticket and opens launch URL", async () => {
    const mockCreateSession = vi.spyOn(clientApi, "createDashboardSession").mockResolvedValue({
      ticket: "mock-secret-ticket-abc",
      launch_url: "http://localhost:8501/?ticket=mock-secret-ticket-abc",
      expires_in_seconds: 120,
    });

    const windowOpenSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    render(<AnalyticsLauncher />);

    expect(screen.getByText(/Recruitment Analytics Dashboard/i)).toBeDefined();
    const launchBtn = screen.getByRole("button", { name: /Open Analytics Dashboard/i });

    fireEvent.click(launchBtn);

    await waitFor(() => {
      expect(mockCreateSession).toHaveBeenCalled();
      expect(windowOpenSpy).toHaveBeenCalledWith(
        "http://localhost:8501/?ticket=mock-secret-ticket-abc",
        "_blank",
        "noopener,noreferrer"
      );
    });

    // Never renders the raw ticket in visible DOM text
    expect(screen.queryByText("mock-secret-ticket-abc")).toBeNull();
  });

  it("AnalyticsLauncher renders safe error on failure with Retry button", async () => {
    vi.spyOn(clientApi, "createDashboardSession").mockRejectedValue(
      new Error("Backend service unreachable")
    );

    render(<AnalyticsLauncher />);
    const launchBtn = screen.getByRole("button", { name: /Open Analytics Dashboard/i });

    fireEvent.click(launchBtn);

    await waitFor(() => {
      expect(screen.getByText(/Backend service unreachable/i)).toBeDefined();
      expect(screen.getByRole("button", { name: /Retry Launch/i })).toBeDefined();
    });
  });
});
