import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { CandidateActivationPage } from "../components/candidate/CandidateActivationPage";
import { CandidateLoginPage } from "../components/candidate/CandidateLoginPage";
import { CandidateShell } from "../components/candidate/CandidateShell";
import { CandidateInvitationModal } from "../components/candidate/CandidateInvitationModal";
import { CandidateAuthProvider } from "../context/CandidateAuthContext";
import * as candidateAuthApi from "../api/candidateAuth";
import { getPortalContext, getPortalOrigin, getLoginPortalSubtitle } from "../utils/portal";

vi.mock("../api/candidateAuth", () => ({
  registerCandidate: vi.fn(),
  candidateLogin: vi.fn(),
  candidateLogout: vi.fn(),
  getCandidateMe: vi.fn(),
  activateCandidateAccount: vi.fn(),
  createCandidateInvitation: vi.fn(),
  listCandidateInvitations: vi.fn(),
  revokeCandidateInvitation: vi.fn(),
  revokeCandidateAccess: vi.fn(),
  getCandidateAccessStatus: vi.fn(),
}));

describe("Candidate Portal Authentication & Lifecycle", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("Portal Context Utilities", () => {
    it("recognizes candidate.localhost as candidate portal", () => {
      expect(getPortalContext("candidate.localhost")).toBe("candidate");
      expect(getPortalContext("candidate.company.test")).toBe("candidate");
      expect(getLoginPortalSubtitle("candidate")).toBe("Sign in to your Candidate portal");
    });

    it("generates correct candidate portal origin", () => {
      const mockWin = {
        location: {
          protocol: "http:",
          port: "5173",
          hostname: "localhost",
        },
      };
      expect(getPortalOrigin("candidate", mockWin)).toBe("http://candidate.localhost:5173");
    });
  });

  describe("CandidateActivationPage", () => {
    it("extracts token from URL fragment and strips it from history", async () => {
      window.history.pushState(null, "", "/candidate/activate#token=test-activation-token-12345");

      const replaceStateSpy = vi.spyOn(window.history, "replaceState");

      render(
        <CandidateAuthProvider>
          <CandidateActivationPage />
        </CandidateAuthProvider>
      );

      // Verify replaceState was called to strip the token from URL
      expect(replaceStateSpy).toHaveBeenCalledWith(null, "", expect.stringMatching(/\/candidate\/activate/));
    });

    it("enforces password matching and minimum length", async () => {
      window.location.hash = "#token=valid-secret-token";

      render(
        <CandidateAuthProvider>
          <CandidateActivationPage />
        </CandidateAuthProvider>
      );

      const passwordInput = screen.getByLabelText(/^New Password/i);
      const confirmInput = screen.getByLabelText(/^Confirm Password/i);
      const submitBtn = screen.getByRole("button", { name: /Activate Account/i });

      // Short password
      fireEvent.change(passwordInput, { target: { value: "short" } });
      fireEvent.change(confirmInput, { target: { value: "short" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.getByText(/Password must be at least 8 characters long/i)).toBeInTheDocument();
      });

      // Mismatched confirmation
      fireEvent.change(passwordInput, { target: { value: "correctPassword123" } });
      fireEvent.change(confirmInput, { target: { value: "differentPassword123" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.getByText(/Passwords do not match/i)).toBeInTheDocument();
      });
    });

    it("successfully activates account and invokes onSuccess callback", async () => {
      window.location.hash = "#token=active-token-xyz";

      vi.mocked(candidateAuthApi.activateCandidateAccount).mockResolvedValueOnce({
        userId: "cand-user-1",
        candidateId: "profile-1",
        organizationId: "org-1",
        email: "candidate@example.com",
        displayName: "John Doe",
        role: "CANDIDATE",
        bindingStatus: "ACTIVE",
        csrfToken: "fresh-csrf-token",
      });

      const onSuccess = vi.fn();

      render(
        <CandidateAuthProvider>
          <CandidateActivationPage onSuccess={onSuccess} />
        </CandidateAuthProvider>
      );

      const passwordInput = screen.getByLabelText(/^New Password/i);
      const confirmInput = screen.getByLabelText(/^Confirm Password/i);
      const submitBtn = screen.getByRole("button", { name: /Activate Account/i });

      fireEvent.change(passwordInput, { target: { value: "ValidPass123" } });
      fireEvent.change(confirmInput, { target: { value: "ValidPass123" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(candidateAuthApi.activateCandidateAccount).toHaveBeenCalledWith(
          "active-token-xyz",
          "ValidPass123",
          "ValidPass123"
        );
        expect(onSuccess).toHaveBeenCalled();
      });
    });
  });

  describe("CandidateRegisterPage", () => {
    it("validates form fields and calls register on submit", async () => {
      const { CandidateRegisterPage } = await import("../components/candidate/CandidateRegisterPage");
      vi.mocked(candidateAuthApi.registerCandidate).mockResolvedValueOnce({
        userId: "cand-user-reg",
        candidateId: undefined,
        organizationId: undefined,
        email: "newcand@example.com",
        displayName: "New Candidate",
        role: "CANDIDATE",
        bindingStatus: undefined,
        csrfToken: "fresh-reg-csrf",
      });

      const onSuccess = vi.fn();

      render(
        <CandidateAuthProvider>
          <CandidateRegisterPage onSuccess={onSuccess} />
        </CandidateAuthProvider>
      );

      const nameInput = screen.getByLabelText(/Full Name/i);
      const emailInput = screen.getByLabelText(/Email Address/i);
      const passInput = screen.getByLabelText(/^Password/i);
      const confirmInput = screen.getByLabelText(/Confirm Password/i);
      const submitBtn = screen.getByRole("button", { name: /Create Account/i });

      fireEvent.change(nameInput, { target: { value: "New Candidate" } });
      fireEvent.change(emailInput, { target: { value: "newcand@example.com" } });
      fireEvent.change(passInput, { target: { value: "Password123!" } });
      fireEvent.change(confirmInput, { target: { value: "Password123!" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(candidateAuthApi.registerCandidate).toHaveBeenCalledWith(
          "New Candidate",
          "newcand@example.com",
          "Password123!"
        );
        expect(onSuccess).toHaveBeenCalled();
      });
    });
  });

  describe("CandidateLoginPage", () => {
    it("submits valid credentials and triggers onSuccess callback", async () => {
      vi.mocked(candidateAuthApi.candidateLogin).mockResolvedValueOnce({
        userId: "cand-user-1",
        candidateId: "profile-1",
        organizationId: "org-1",
        email: "candidate@example.com",
        displayName: "Jane Doe",
        role: "CANDIDATE",
        bindingStatus: "ACTIVE",
        csrfToken: "candidate-csrf-login",
      });

      const onSuccess = vi.fn();

      render(
        <CandidateAuthProvider>
          <CandidateLoginPage onSuccess={onSuccess} />
        </CandidateAuthProvider>
      );

      const emailInput = screen.getByLabelText(/Email Address/i);
      const passwordInput = screen.getByLabelText(/^Password/i);
      const submitBtn = screen.getByRole("button", { name: /Sign In to Candidate Portal/i });

      fireEvent.change(emailInput, { target: { value: "candidate@example.com" } });
      fireEvent.change(passwordInput, { target: { value: "SecretPass123" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(candidateAuthApi.candidateLogin).toHaveBeenCalledWith(
          "candidate@example.com",
          "SecretPass123"
        );
        expect(onSuccess).toHaveBeenCalled();
      });
    });

    it("displays error message on invalid credentials", async () => {
      vi.mocked(candidateAuthApi.candidateLogin).mockRejectedValueOnce(
        new Error("Invalid email or password.")
      );

      render(
        <CandidateAuthProvider>
          <CandidateLoginPage />
        </CandidateAuthProvider>
      );

      const emailInput = screen.getByLabelText(/Email Address/i);
      const passwordInput = screen.getByLabelText(/^Password/i);
      const submitBtn = screen.getByRole("button", { name: /Sign In to Candidate Portal/i });

      fireEvent.change(emailInput, { target: { value: "candidate@example.com" } });
      fireEvent.change(passwordInput, { target: { value: "WrongPass" } });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.getByText(/Invalid email or password/i)).toBeInTheDocument();
      });
    });
  });

  describe("CandidateShell", () => {
    it("renders candidate portal branding and navigation", async () => {
      render(
        <CandidateAuthProvider>
          <CandidateShell />
        </CandidateAuthProvider>
      );

      await waitFor(() => {
        expect(screen.getByText("Open Jobs")).toBeInTheDocument();
      });
      expect(screen.getByText("Candidate Portal")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Sign out of Candidate Portal/i })).toBeInTheDocument();

      // Ensure no staff navigation elements appear
      expect(screen.queryByText(/Candidates Directory/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/ATS Integrations/i)).not.toBeInTheDocument();
    });

    it("triggers logout upon clicking sign out button", async () => {
      vi.mocked(candidateAuthApi.candidateLogout).mockResolvedValueOnce(undefined);

      const onLogout = vi.fn();

      render(
        <CandidateAuthProvider>
          <CandidateShell onLogout={onLogout} />
        </CandidateAuthProvider>
      );

      const logoutBtn = screen.getByRole("button", { name: /Sign out of Candidate Portal/i });
      fireEvent.click(logoutBtn);

      await waitFor(() => {
        expect(candidateAuthApi.candidateLogout).toHaveBeenCalled();
        expect(onLogout).toHaveBeenCalled();
      });
    });
  });

  describe("CandidateInvitationModal (Recruiter UI)", () => {
    it("loads and displays existing candidate invitations", async () => {
      vi.mocked(candidateAuthApi.getCandidateAccessStatus).mockResolvedValueOnce({
        candidateId: "cand-1",
        accountStatus: "INVITED",
        hasActiveAccount: false,
        email: "alex@example.com",
        invitation: {
          invitationId: "inv-1",
          status: "ACTIVE",
          expiresAt: new Date(Date.now() + 86400000).toISOString(),
          createdAt: new Date().toISOString(),
        },
      });
      vi.mocked(candidateAuthApi.listCandidateInvitations).mockResolvedValueOnce([
        {
          invitationId: "inv-1",
          candidateId: "cand-1",
          organizationId: "org-1",
          purpose: "PRACTICE_INTERVIEW_ACCESS",
          status: "ACTIVE",
          createdAt: new Date().toISOString(),
          expiresAt: new Date(Date.now() + 86400000).toISOString(),
          consumedAt: null,
          revokedAt: null,
          createdByUserId: "user-1",
        },
      ]);

      render(
        <CandidateInvitationModal
          candidateId="cand-1"
          candidateName="Alex Smith"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(candidateAuthApi.listCandidateInvitations).toHaveBeenCalledWith("cand-1");
        expect(screen.getByText("INVITED")).toBeInTheDocument();
      });
    });

    it("generates a new 24h invitation link and displays raw URL", async () => {
      vi.mocked(candidateAuthApi.getCandidateAccessStatus).mockResolvedValueOnce({
        candidateId: "cand-1",
        accountStatus: "NOT_INVITED",
        hasActiveAccount: false,
        invitation: null,
      });
      vi.mocked(candidateAuthApi.listCandidateInvitations).mockResolvedValueOnce([]);
      vi.mocked(candidateAuthApi.createCandidateInvitation).mockResolvedValueOnce({
        invitationId: "inv-2",
        candidateId: "cand-1",
        organizationId: "org-1",
        rawToken: "raw-secret-token",
        activationUrl: "http://candidate.localhost:5173/candidate/activate#token=raw-secret-token",
        expiresAt: new Date(Date.now() + 86400000).toISOString(),
        createdAt: new Date().toISOString(),
      });

      render(
        <CandidateInvitationModal
          candidateId="cand-1"
          candidateName="Alex Smith"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      const generateBtn = await screen.findByRole("button", { name: /Create access link/i });
      fireEvent.click(generateBtn);

      await waitFor(() => {
        expect(candidateAuthApi.createCandidateInvitation).toHaveBeenCalledWith("cand-1", 24);
        expect(screen.getByDisplayValue(/raw-secret-token/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Copy Link/i })).toBeInTheDocument();
      });
    });

    it("revokes an active invitation", async () => {
      vi.mocked(candidateAuthApi.getCandidateAccessStatus).mockResolvedValue({
        candidateId: "cand-1",
        accountStatus: "INVITED",
        hasActiveAccount: false,
        invitation: {
          invitationId: "inv-1",
          status: "ACTIVE",
          expiresAt: new Date(Date.now() + 86400000).toISOString(),
          createdAt: new Date().toISOString(),
        },
      });
      vi.mocked(candidateAuthApi.listCandidateInvitations).mockResolvedValue([
        {
          invitationId: "inv-1",
          candidateId: "cand-1",
          organizationId: "org-1",
          purpose: "PRACTICE_INTERVIEW_ACCESS",
          status: "ACTIVE",
          createdAt: new Date().toISOString(),
          expiresAt: new Date(Date.now() + 86400000).toISOString(),
          consumedAt: null,
          revokedAt: null,
          createdByUserId: "user-1",
        },
      ]);
      vi.mocked(candidateAuthApi.revokeCandidateInvitation).mockResolvedValueOnce({
        invitationId: "inv-1",
        candidateId: "cand-1",
        organizationId: "org-1",
        purpose: "PRACTICE_INTERVIEW_ACCESS",
        status: "REVOKED",
        createdAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 86400000).toISOString(),
        consumedAt: null,
        revokedAt: new Date().toISOString(),
        createdByUserId: "user-1",
      });

      render(
        <CandidateInvitationModal
          candidateId="cand-1"
          candidateName="Alex Smith"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      const revokeBtns = await screen.findAllByRole("button", { name: /Revoke/i });
      fireEvent.click(revokeBtns[0]);

      await waitFor(() => {
        expect(candidateAuthApi.revokeCandidateInvitation).toHaveBeenCalledWith("inv-1");
      });
    });

    it("displays candidate portal access link when account is active", async () => {
      vi.mocked(candidateAuthApi.getCandidateAccessStatus).mockResolvedValue({
        candidateId: "cand-active-1",
        accountStatus: "ACTIVE",
        hasActiveAccount: true,
        email: "alex.smith@example.com",
        displayName: "Alex Smith",
      });
      vi.mocked(candidateAuthApi.listCandidateInvitations).mockResolvedValue([]);

      render(
        <CandidateInvitationModal
          candidateId="cand-active-1"
          candidateName="Alex Smith"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/Candidate Practice Portal Link/i)).toBeInTheDocument();
        expect(screen.getByDisplayValue(/\/candidate\/login/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Copy Portal Link/i })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Revoke Access/i })).toBeInTheDocument();
      });
    });

    it("handles full revoke flow for active candidate access", async () => {
      vi.mocked(candidateAuthApi.getCandidateAccessStatus).mockResolvedValue({
        candidateId: "cand-active-1",
        accountStatus: "ACTIVE",
        hasActiveAccount: true,
        email: "alex.smith@example.com",
        displayName: "Alex Smith",
      });
      vi.mocked(candidateAuthApi.listCandidateInvitations).mockResolvedValue([]);
      vi.mocked(candidateAuthApi.revokeCandidateAccess).mockResolvedValue({
        candidateId: "cand-active-1",
        accountStatus: "SUSPENDED",
        hasActiveAccount: false,
        email: "alex.smith@example.com",
      });

      render(
        <CandidateInvitationModal
          candidateId="cand-active-1"
          candidateName="Alex Smith"
          isOpen={true}
          onClose={vi.fn()}
        />
      );

      const revokeBtn = await screen.findByRole("button", { name: /Revoke Access/i });
      fireEvent.click(revokeBtn);

      const confirmBtn = await screen.findByRole("button", { name: /Confirm Revoke/i });
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(candidateAuthApi.revokeCandidateAccess).toHaveBeenCalledWith("cand-active-1");
      });
    });
  });
});
