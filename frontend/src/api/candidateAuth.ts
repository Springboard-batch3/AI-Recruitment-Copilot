import { apiFetch } from "./client";

export interface CreateInvitationResponse {
  invitationId: string;
  candidateId: string;
  organizationId: string;
  rawToken: string;
  activationUrl: string;
  expiresAt: string;
  createdAt: string;
}

export interface CandidateInvitationSummary {
  invitationId: string;
  candidateId: string;
  organizationId: string;
  purpose: string;
  status: "ACTIVE" | "CONSUMED" | "EXPIRED" | "REVOKED";
  expiresAt: string;
  consumedAt?: string | null;
  revokedAt?: string | null;
  createdByUserId: string;
  createdAt: string;
}

export interface CandidateAuthResponse {
  userId: string;
  email: string;
  displayName: string;
  role: string;
  candidateId?: string | null;
  organizationId?: string | null;
  bindingStatus?: string | null;
  csrfToken: string;
  idleExpiresAt?: string;
  absoluteExpiresAt?: string;
}

export interface CandidateAccessInvitationInfo {
  invitationId: string;
  status: "ACTIVE" | "EXPIRED" | "CONSUMED" | "REVOKED";
  expiresAt: string;
  createdAt: string;
}

export interface CandidateAccessStatusResponse {
  candidateId: string;
  accountStatus: "NOT_INVITED" | "INVITED" | "ACTIVE" | "SUSPENDED";
  hasActiveAccount: boolean;
  email?: string | null;
  displayName?: string | null;
  invitation?: CandidateAccessInvitationInfo | null;
}

export async function getCandidateAccessStatus(
  candidateId: string
): Promise<CandidateAccessStatusResponse> {
  return apiFetch<CandidateAccessStatusResponse>(
    `/api/v1/recruiter/candidate-access/candidates/${candidateId}`
  );
}

export async function createCandidateInvitation(
  candidateId: string,
  validHours: number = 24
): Promise<CreateInvitationResponse> {
  return apiFetch<CreateInvitationResponse>(
    `/api/v1/recruiter/candidate-access/candidates/${candidateId}/invitations`,
    {
      method: "POST",
      body: JSON.stringify({
        candidate_id: candidateId,
        valid_hours: validHours,
      }),
    }
  );
}

export async function listCandidateInvitations(
  candidateId: string
): Promise<CandidateInvitationSummary[]> {
  return apiFetch<CandidateInvitationSummary[]>(
    `/api/v1/recruiter/candidate-invitations/${candidateId}`
  );
}

export async function revokeCandidateInvitation(
  invitationId: string
): Promise<CandidateInvitationSummary> {
  return apiFetch<CandidateInvitationSummary>(
    `/api/v1/recruiter/candidate-access/invitations/${invitationId}/revoke`,
    {
      method: "POST",
    }
  );
}

export async function revokeCandidateAccess(
  candidateId: string
): Promise<CandidateAccessStatusResponse> {
  return apiFetch<CandidateAccessStatusResponse>(
    `/api/v1/recruiter/candidate-access/candidates/${candidateId}/revoke`,
    {
      method: "POST",
    }
  );
}

export async function activateCandidateAccount(
  token: string,
  password: string,
  passwordConfirmation: string
): Promise<CandidateAuthResponse> {
  return apiFetch<CandidateAuthResponse>("/api/v1/candidate/auth/activate", {
    method: "POST",
    body: JSON.stringify({
      token,
      password,
      password_confirmation: passwordConfirmation,
    }),
  });
}

export async function registerCandidate(
  fullName: string,
  email: string,
  password: string
): Promise<CandidateAuthResponse> {
  return apiFetch<CandidateAuthResponse>("/api/v1/candidate/auth/register", {
    method: "POST",
    body: JSON.stringify({
      full_name: fullName,
      email,
      password,
    }),
  });
}

export async function candidateLogin(
  email: string,
  password: string
): Promise<CandidateAuthResponse> {
  return apiFetch<CandidateAuthResponse>("/api/v1/candidate/auth/login", {
    method: "POST",
    body: JSON.stringify({
      email,
      password,
    }),
  });
}

export async function getCandidateMe(): Promise<CandidateAuthResponse> {
  return apiFetch<CandidateAuthResponse>("/api/v1/candidate/auth/me");
}

export async function candidateLogout(): Promise<void> {
  return apiFetch("/api/v1/candidate/auth/logout", {
    method: "POST",
  });
}

