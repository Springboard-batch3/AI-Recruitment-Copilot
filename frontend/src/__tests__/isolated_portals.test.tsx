import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { getPortalContext, getPortalOrigin, getCrossPortalUrl, getLoginPortalSubtitle } from "../utils/portal";
import { LoginPage } from "../components/auth/LoginPage";
import { AuthenticatedShell } from "../components/AuthenticatedShell";
import { AtsConnectionsAdmin } from "../components/admin/AtsConnectionsAdmin";
import { AtsImportModal } from "../components/ats/AtsImportModal";
import App from "../App";
import * as authContext from "../context/AuthContext";
import * as atsApi from "../api/ats";

vi.mock("../api/ats", () => ({
  listAdminATSConnections: vi.fn(),
  createATSConnection: vi.fn(),
  testATSConnection: vi.fn(),
  updateATSConnection: vi.fn(),
  listActiveATSConnections: vi.fn(),
  triggerATSImport: vi.fn(),
}));

describe("Isolated Simultaneous Portals & Navigation", () => {
  const originalLocation = window.location;

  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("Portal Utility Functions", () => {
    it("correctly identifies portal context from hostname", () => {
      expect(getPortalContext("admin.localhost")).toBe("admin");
      expect(getPortalContext("admin.company.test")).toBe("admin");
      expect(getPortalContext("recruiter.localhost")).toBe("recruiter");
      expect(getPortalContext("recruiter.company.test")).toBe("recruiter");
      expect(getPortalContext("localhost")).toBe("default");
      expect(getPortalContext("127.0.0.1")).toBe("default");
      expect(getPortalContext("")).toBe("default");
    });

    it("generates correct cross-portal origins and URLs", () => {
      const mockWin = {
        location: {
          protocol: "http:",
          port: "5173",
          hostname: "admin.localhost",
        },
      };

      expect(getPortalOrigin("recruiter", mockWin)).toBe("http://recruiter.localhost:5173");
      expect(getPortalOrigin("admin", mockWin)).toBe("http://admin.localhost:5173");

      expect(getCrossPortalUrl("recruiter", "?tab=upload", mockWin)).toBe(
        "http://recruiter.localhost:5173/?tab=upload"
      );
      expect(getCrossPortalUrl("admin", "/?tab=ats_integrations", mockWin)).toBe(
        "http://admin.localhost:5173/?tab=ats_integrations"
      );
    });

    it("provides contextual login subtitles", () => {
      expect(getLoginPortalSubtitle("admin")).toBe("Sign in to Admin workspace");
      expect(getLoginPortalSubtitle("recruiter")).toBe("Sign in to Recruiter workspace");
      expect(getLoginPortalSubtitle("default")).toBe("Sign in to continue to the recruitment workspace.");
    });
  });

  describe("LoginPage Presentation", () => {
    it("displays Admin workspace title when on admin.localhost", () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, hostname: "admin.localhost", search: "" },
      });

      render(
        <authContext.AuthProvider>
          <LoginPage />
        </authContext.AuthProvider>
      );

      expect(screen.getByText("Sign in to Admin workspace")).toBeInTheDocument();
      expect(screen.getByText("Admin Workspace")).toBeInTheDocument();
    });

    it("displays Recruiter workspace title when on recruiter.localhost", () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, hostname: "recruiter.localhost", search: "" },
      });

      render(
        <authContext.AuthProvider>
          <LoginPage />
        </authContext.AuthProvider>
      );

      expect(screen.getByText("Sign in to Recruiter workspace")).toBeInTheDocument();
      expect(screen.getByText("Recruiter Workspace")).toBeInTheDocument();
    });
  });

  describe("AuthenticatedShell & Cross-Portal Links", () => {
    it("renders Admin workspace badge and Open Recruiter workspace link for Admin", () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, protocol: "http:", hostname: "admin.localhost", port: "5173" },
      });

      vi.spyOn(authContext, "useAuth").mockReturnValue({
        status: "AUTHENTICATED",
        user: {
          userId: "11111111-1111-1111-1111-111111111111",
          email: "admin@company.test",
          displayName: "Platform Admin",
          role: "ADMIN",
          isActive: true,
          mustChangePassword: false,
        },
        csrfToken: "dummy-csrf",
        sessionError: null,
        login: vi.fn(),
        logout: vi.fn(),
        changePassword: vi.fn(),
        refreshSession: vi.fn(),
        clearSessionError: vi.fn(),
      });

      render(
        <AuthenticatedShell>
          <div>Admin Content</div>
        </AuthenticatedShell>
      );

      expect(screen.getByText("Admin workspace")).toBeInTheDocument();

      // Open user profile dropdown
      const profileBtn = screen.getByRole("button", { name: /User profile and account settings/i });
      fireEvent.click(profileBtn);

      const crossLink = screen.getByRole("menuitem", { name: /Open Recruiter workspace/i });
      expect(crossLink).toBeInTheDocument();
      expect(crossLink).toHaveAttribute("target", "_blank");
      expect(crossLink).toHaveAttribute("rel", "noopener noreferrer");
      expect(crossLink.getAttribute("href")).toContain("recruiter.localhost:5173");
    });

    it("renders Recruiter workspace badge and Open Admin workspace link for Recruiter", () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, protocol: "http:", hostname: "recruiter.localhost", port: "5173" },
      });

      vi.spyOn(authContext, "useAuth").mockReturnValue({
        status: "AUTHENTICATED",
        user: {
          userId: "22222222-2222-2222-2222-222222222222",
          email: "recruiter@company.test",
          displayName: "Lead Recruiter",
          role: "RECRUITER",
          isActive: true,
          mustChangePassword: false,
        },
        csrfToken: "dummy-csrf",
        sessionError: null,
        login: vi.fn(),
        logout: vi.fn(),
        changePassword: vi.fn(),
        refreshSession: vi.fn(),
        clearSessionError: vi.fn(),
      });

      render(
        <AuthenticatedShell>
          <div>Recruiter Content</div>
        </AuthenticatedShell>
      );

      expect(screen.getByText("Recruiter workspace")).toBeInTheDocument();

      // Open user profile dropdown
      const profileBtn = screen.getByRole("button", { name: /User profile and account settings/i });
      fireEvent.click(profileBtn);

      const crossLink = screen.getByRole("menuitem", { name: /Open Admin workspace/i });
      expect(crossLink).toBeInTheDocument();
      expect(crossLink).toHaveAttribute("target", "_blank");
      expect(crossLink).toHaveAttribute("rel", "noopener noreferrer");
      expect(crossLink.getAttribute("href")).toContain("admin.localhost:5173");
    });
  });

  describe("ATS Integrations Verification Links", () => {
    it("renders Open recruiter import button in Admin ATS Connections view", async () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, protocol: "http:", hostname: "admin.localhost", port: "5173" },
      });

      vi.mocked(atsApi.listAdminATSConnections).mockResolvedValue({
        items: [],
        total: 0,
      } as any);

      render(<AtsConnectionsAdmin />);

      await waitFor(() => {
        expect(screen.getByText("ATS Integrations")).toBeInTheDocument();
      });

      const recruiterImportLink = screen.getByRole("link", { name: /Open recruiter import/i });
      expect(recruiterImportLink).toBeInTheDocument();
      expect(recruiterImportLink).toHaveAttribute("target", "_blank");
      expect(recruiterImportLink).toHaveAttribute("rel", "noopener noreferrer");
      expect(recruiterImportLink.getAttribute("href")).toContain("recruiter.localhost:5173/?tab=upload");
    });

    it("renders Open ATS connections in AtsImportModal when user has ADMIN role", async () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: { ...originalLocation, protocol: "http:", hostname: "recruiter.localhost", port: "5173" },
      });

      vi.mocked(atsApi.listActiveATSConnections).mockResolvedValue({
        items: [
          {
            connection_id: "conn-123",
            connection_name: "Workday Mock",
            adapter_type: "MOCK",
            is_enabled: true,
            kill_switch_active: false,
            created_at: new Date().toISOString(),
          } as any,
        ],
        total: 1,
      } as any);

      vi.spyOn(authContext, "useOptionalAuth").mockReturnValue({
        status: "AUTHENTICATED",
        user: {
          userId: "11111111-1111-1111-1111-111111111111",
          email: "admin@company.test",
          displayName: "Admin Recruiter",
          role: "ADMIN",
          isActive: true,
          mustChangePassword: false,
        },
        csrfToken: "dummy-csrf",
        sessionError: null,
        login: vi.fn(),
        logout: vi.fn(),
        changePassword: vi.fn(),
        refreshSession: vi.fn(),
        clearSessionError: vi.fn(),
      });

      render(
        <AtsImportModal
          isOpen={true}
          onClose={vi.fn()}
          onImportComplete={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText("Import from ATS / File")).toBeInTheDocument();
      });

      const atsAdminLink = screen.getByRole("link", { name: /Open ATS connections/i });
      expect(atsAdminLink).toBeInTheDocument();
      expect(atsAdminLink).toHaveAttribute("target", "_blank");
      expect(atsAdminLink.getAttribute("href")).toContain("admin.localhost:5173/?tab=ats_integrations");
    });
  });

  describe("Deep Linking & Tab Restoration via URL", () => {
    it("opens Upload view directly when ?tab=upload query param is present", async () => {
      Object.defineProperty(window, "location", {
        writable: true,
        value: {
          ...originalLocation,
          protocol: "http:",
          hostname: "recruiter.localhost",
          port: "5173",
          search: "?tab=upload",
          pathname: "/",
        },
      });

      globalThis.fetch = vi.fn().mockImplementation((url) => {
        if (url === "/api/v1/auth/me") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                userId: "22222222-2222-2222-2222-222222222222",
                email: "recruiter@company.test",
                displayName: "Recruiter Staff",
                role: "RECRUITER",
                isActive: true,
                mustChangePassword: false,
                csrfToken: "valid-csrf",
              }),
          });
        }
        if (url === "/api/v1/health") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ status: "healthy", database: "connected" }),
          });
        }
        if (url === "/api/v1/resumes") {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve([]),
          });
        }
        if (url === "/api/v1/jobs") {
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
        expect(screen.getByText("Upload Candidate Resume")).toBeInTheDocument();
      });
    });
  });
});
