/**
 * Candidate Applications API (Phase 4B.4 & Milestone-1 Resume Application Flow).
 *
 * Provides typed functions to create drafts, upload resumes, check resume processing status,
 * submit applications, fetch applications, fetch single application details, and withdraw applications.
 */
import { apiFetch } from "./client";
import { CandidateJobSummary } from "./candidateJobs";

export type ApplicationStatus =
  | "DRAFT"
  | "RESUME_PROCESSING"
  | "READY_TO_SUBMIT"
  | "SUBMITTED"
  | "PROCESSING_FAILED"
  | "WITHDRAWN";

export interface CandidateApplication {
  applicationId: string;
  jobId: string;
  jobVersionId?: string | null;
  jobRevision?: number | null;
  artifactId?: string | null;
  profileId?: string | null;
  profileVersion?: number | null;
  status: ApplicationStatus;
  submittedAt?: string | null;
  withdrawnAt?: string | null;
  canWithdraw: boolean;
  canSubmit: boolean;
  job: CandidateJobSummary;
  createdAt: string;
  updatedAt: string;
}

export interface CandidateApplicationListResponse {
  items: CandidateApplication[];
  total: number;
  page: number;
  limit: number;
}

export interface CandidateResumeStatus {
  applicationId: string;
  status: ApplicationStatus;
  artifactId?: string | null;
  filename?: string | null;
  fileSizeBytes?: number | null;
  profileId?: string | null;
  profileVersion?: number | null;
  candidateName?: string | null;
  candidateEmail?: string | null;
  skillsFound: string[];
  extractionStage: string;
  errorMessage?: string | null;
  canSubmit: boolean;
}

export interface CreateDraftPayload {
  jobId: string;
  expectedRevision?: number;
}

export interface SubmitApplicationPayload {
  idempotencyKey?: string;
  expectedRevision?: number;
}

export async function createOrGetApplicationDraft(
  payload: CreateDraftPayload
): Promise<CandidateApplication> {
  return apiFetch<CandidateApplication>("/api/v1/candidate/applications/drafts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function uploadApplicationResume(
  applicationId: string,
  file: File
): Promise<CandidateResumeStatus> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`/api/v1/candidate/applications/${applicationId}/resume`, {
    method: "POST",
    body: formData,
    credentials: "include",
  });

  if (!response.ok) {
    let errorDetail = "Failed to upload resume";
    try {
      const errJson = await response.json();
      errorDetail = errJson.detail || errorDetail;
    } catch {
      // fallback to status text
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export async function fetchApplicationResumeStatus(
  applicationId: string
): Promise<CandidateResumeStatus> {
  return apiFetch<CandidateResumeStatus>(
    `/api/v1/candidate/applications/${applicationId}/resume/status`,
    {
      method: "GET",
    }
  );
}

export async function submitApplicationDraft(
  applicationId: string
): Promise<CandidateApplication> {
  return apiFetch<CandidateApplication>(
    `/api/v1/candidate/applications/${applicationId}/submit`,
    {
      method: "POST",
    }
  );
}

export async function submitApplication(
  jobId: string,
  payload?: SubmitApplicationPayload
): Promise<CandidateApplication> {
  return apiFetch<CandidateApplication>(`/api/v1/candidate/applications/jobs/${jobId}`, {
    method: "POST",
    body: JSON.stringify(payload || {}),
  });
}

export async function fetchMyApplications(params?: {
  page?: number;
  limit?: number;
}): Promise<CandidateApplicationListResponse> {
  const queryParams = new URLSearchParams();
  if (params?.page) queryParams.set("page", params.page.toString());
  if (params?.limit) queryParams.set("limit", params.limit.toString());

  const qs = queryParams.toString();
  const endpoint = `/api/v1/candidate/applications${qs ? `?${qs}` : ""}`;
  return apiFetch<CandidateApplicationListResponse>(endpoint, {
    method: "GET",
  });
}

export async function fetchApplicationDetail(
  applicationId: string
): Promise<CandidateApplication> {
  return apiFetch<CandidateApplication>(`/api/v1/candidate/applications/${applicationId}`, {
    method: "GET",
  });
}

export async function withdrawApplication(
  applicationId: string
): Promise<CandidateApplication> {
  return apiFetch<CandidateApplication>(
    `/api/v1/candidate/applications/${applicationId}/withdraw`,
    {
      method: "POST",
    }
  );
}

