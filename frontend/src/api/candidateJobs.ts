/**
 * Candidate Job Discovery API (Phase 4B.3).
 *
 * Provides typed functions to fetch candidate-safe open jobs and single-job details.
 */
import { apiFetch } from "./client";

export interface CandidateJobSummary {
  jobId: string;
  jobVersionId: string;
  versionNumber: number;
  revision: number;
  title: string;
  department?: string | null;
  location?: string | null;
  employmentType?: string | null;
  workplaceType?: string | null;
  openingsCount?: number | null;
  summary?: string | null;
  publishedAt: string;
  updatedAt: string;
}

export interface CandidateJobDetail {
  jobId: string;
  jobVersionId: string;
  versionNumber: number;
  revision: number;
  title: string;
  department?: string | null;
  location?: string | null;
  employmentType?: string | null;
  workplaceType?: string | null;
  openingsCount?: number | null;
  summary?: string | null;
  responsibilities?: string | null;
  qualifications: string[];
  publishedAt: string;
  updatedAt: string;
  hasActiveApplication?: boolean;
  activeApplicationId?: string | null;
}

export interface CandidateJobListResponse {
  items: CandidateJobSummary[];
  total: number;
  page: number;
  limit: number;
}

export async function fetchCandidateJobs(params?: {
  q?: string;
  page?: number;
  limit?: number;
}): Promise<CandidateJobListResponse> {
  const queryParams = new URLSearchParams();
  if (params?.q) queryParams.set("q", params.q);
  if (params?.page) queryParams.set("page", params.page.toString());
  if (params?.limit) queryParams.set("limit", params.limit.toString());

  const qs = queryParams.toString();
  const endpoint = `/api/v1/candidate/jobs${qs ? `?${qs}` : ""}`;
  return apiFetch<CandidateJobListResponse>(endpoint, {
    method: "GET",
  });
}

export async function fetchCandidateJobDetail(
  jobId: string,
  versionId?: string
): Promise<CandidateJobDetail> {
  const queryParams = new URLSearchParams();
  if (versionId) queryParams.set("version_id", versionId);

  const qs = queryParams.toString();
  const endpoint = `/api/v1/candidate/jobs/${jobId}${qs ? `?${qs}` : ""}`;
  return apiFetch<CandidateJobDetail>(endpoint, {
    method: "GET",
  });
}
