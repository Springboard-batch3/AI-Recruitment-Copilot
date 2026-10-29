import { apiFetch } from "./client";
import {
  CandidatePracticeDiscoveryResponse,
  RecruiterPracticeReleaseSummary,
} from "../types/ai_question_set";

export interface PracticeEligibleItem {
  job_id: string;
  job_revision_id: string;
  job_title: string;
  job_version: string;
  question_set_id: string;
  template_id?: string;
  conversational_question_count: number;
  mcq_question_count: number;
  total_question_count: number;
  status: string;
  approved_at?: string;
  has_candidate_request: boolean;
  candidate_request_id?: string;
}

export interface PracticeIneligibleItem {
  job_id: string;
  job_revision_id?: string;
  job_title: string;
  job_version: string;
  reason_code: string;
  message: string;
  question_set_id?: string;
  template_id?: string;
  older_revision?: number;
  action: "OPEN_QUESTION_SET" | "PREPARE_PRACTICE_SET" | "CREATE_PRACTICE_SET" | "NONE" | string;
}

export interface PracticeEligibleJob {
  job_id: string;
  job_version_id?: string;
  job_title: string;
  job_revision: number;
  ai_set_id: string;
  set_version: number;
  approved_at: string;
  conversation_count: number;
  mcq_count: number;
  total_question_count: number;
  already_released: boolean;
  has_candidate_request?: boolean;
  candidate_request_id?: string;
}

export interface PracticeEligibilityDiagnosticResponse {
  eligible: PracticeEligibleItem[];
  ineligible: PracticeIneligibleItem[];
  items?: PracticeEligibleJob[];
}

export interface PracticeEligibleJobsResponse {
  items: PracticeEligibleJob[];
}

export interface CandidatePracticeRequestDto {
  request_id: string;
  organization_id: string;
  candidate_id: string;
  job_id: string;
  job_version_id: string;
  job_title: string;
  job_version: string;
  status: "REQUESTED" | "RELEASED" | "DECLINED" | "WITHDRAWN" | string;
  requested_at: string;
  processed_at?: string;
  processed_by_recruiter_id?: string;
  resulting_release_id?: string;
  decline_reason?: string;
}

export interface CandidatePracticeCatalogItem {
  catalog_id: string;
  job_id: string;
  job_version_id: string;
  safe_title: string;
  safe_summary?: string;
  skill_labels: string[];
  published_at: string;
  request_status: "NOT_REQUESTED" | "REQUESTED" | "RELEASED" | "DECLINED" | string;
  active_request_id?: string;
  release_id?: string;
  decline_reason?: string;
}

export interface CandidatePracticeCatalogResponse {
  items: CandidatePracticeCatalogItem[];
}

export async function listEligibleJobsForPracticeRelease(
  candidateId: string
): Promise<PracticeEligibilityDiagnosticResponse> {
  return apiFetch<PracticeEligibilityDiagnosticResponse>(
    `/api/v1/recruiter/candidate-practice/candidates/${candidateId}/eligible-jobs`
  );
}

export async function listCandidatePracticeRequestsForRecruiter(
  candidateId: string
): Promise<CandidatePracticeRequestDto[]> {
  return apiFetch<CandidatePracticeRequestDto[]>(
    `/api/v1/recruiter/candidate-practice/candidates/${candidateId}/requests`
  );
}

export async function declineCandidatePracticeRequest(
  requestId: string,
  declineReason?: string
): Promise<CandidatePracticeRequestDto> {
  return apiFetch<CandidatePracticeRequestDto>(
    `/api/v1/recruiter/candidate-practice/requests/${requestId}/decline`,
    {
      method: "POST",
      body: JSON.stringify({ decline_reason: declineReason || undefined }),
    }
  );
}

export async function releasePracticeFromRequest(
  requestId: string
): Promise<RecruiterPracticeReleaseSummary> {
  return apiFetch<RecruiterPracticeReleaseSummary>(
    `/api/v1/recruiter/candidate-practice/requests/${requestId}/release`,
    {
      method: "POST",
    }
  );
}

export async function releasePracticeToCandidate(
  candidateId: string,
  jobId: string,
  jobVersionId?: string
): Promise<RecruiterPracticeReleaseSummary> {
  return apiFetch<RecruiterPracticeReleaseSummary>(
    `/api/v1/recruiter/candidate-practice/candidates/${candidateId}/release`,
    {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, job_version_id: jobVersionId }),
    }
  );
}

export async function listCandidatePracticeReleases(
  candidateId: string
): Promise<RecruiterPracticeReleaseSummary[]> {
  return apiFetch<RecruiterPracticeReleaseSummary[]>(
    `/api/v1/recruiter/candidate-practice/candidates/${candidateId}/releases`
  );
}

export async function revokeCandidatePracticeRelease(
  releaseId: string
): Promise<{ success: boolean; release_id: string; status: string; safe_message: string }> {
  return apiFetch<{ success: boolean; release_id: string; status: string; safe_message: string }>(
    `/api/v1/recruiter/candidate-practice/releases/${releaseId}/revoke`,
    {
      method: "POST",
    }
  );
}

export async function preparePracticeSetForRevision(
  jobId: string,
  jobVersionId: string
): Promise<any> {
  return apiFetch<any>(
    `/api/v1/ai-interviews/jobs/${jobId}/prepare-for-revision/${jobVersionId}`,
    {
      method: "POST",
    }
  );
}

export async function publishJobRevisionToCatalog(payload: {
  job_version_id: string;
  safe_title?: string;
  safe_summary?: string;
  skill_labels?: string[];
}): Promise<CandidatePracticeCatalogItem> {
  return apiFetch<CandidatePracticeCatalogItem>(
    `/api/v1/recruiter/candidate-practice/catalog/publish`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export async function unpublishJobRevisionFromCatalog(
  jobVersionId: string
): Promise<{ success: boolean; message: string }> {
  return apiFetch<{ success: boolean; message: string }>(
    `/api/v1/recruiter/candidate-practice/catalog/unpublish/${jobVersionId}`,
    {
      method: "POST",
    }
  );
}

// Candidate Catalog & Request API
export async function getCandidatePracticeCatalog(): Promise<CandidatePracticeCatalogResponse> {
  return apiFetch<CandidatePracticeCatalogResponse>("/api/v1/candidate/practice/catalog");
}

export async function createCandidatePracticeRequest(
  jobVersionId: string
): Promise<CandidatePracticeRequestDto> {
  return apiFetch<CandidatePracticeRequestDto>("/api/v1/candidate/practice/requests", {
    method: "POST",
    body: JSON.stringify({ job_version_id: jobVersionId }),
  });
}

export async function withdrawCandidatePracticeRequest(
  requestId: string
): Promise<CandidatePracticeRequestDto> {
  return apiFetch<CandidatePracticeRequestDto>(
    `/api/v1/candidate/practice/requests/${requestId}/withdraw`,
    {
      method: "POST",
    }
  );
}

export async function listCandidatePracticeRequests(): Promise<CandidatePracticeRequestDto[]> {
  return apiFetch<CandidatePracticeRequestDto[]>("/api/v1/candidate/practice/requests");
}

export async function getCandidatePracticeDiscovery(): Promise<CandidatePracticeDiscoveryResponse> {
  return apiFetch<CandidatePracticeDiscoveryResponse>("/api/v1/candidate/practice-jobs");
}

