import { apiFetch } from "./client";

export type CandidateApplicationStatus = "SUBMITTED" | "WITHDRAWN";
export type RecruiterReviewState = "RECEIVED" | "IN_REVIEW" | "INTERVIEW_READY";

export interface RecruiterApplicationSummary {
  applicationId: string;
  jobId: string;
  jobVersionId: string;
  jobRevision: number;
  candidateId: string;
  profileId: string;
  profileVersion: number;
  candidateName: string;
  candidateEmail?: string | null;
  candidatePhone?: string | null;
  currentRole?: string | null;
  status: CandidateApplicationStatus;
  recruiterReviewState: RecruiterReviewState;
  submittedAt: string;
  withdrawnAt?: string | null;
  matchStatus: string;
  matchScore?: number | null;
  confidenceTier?: string | null;
  interviewStatus: string;
  releaseId?: string | null;
  interviewMode?: string | null;
}

export interface RecruiterApplicationListResponse {
  items: RecruiterApplicationSummary[];
  total: number;
  page: number;
  limit: number;
  activeCount: number;
  withdrawnCount: number;
}

export interface RecruiterMatchEvidence {
  runId: string;
  rank: number;
  rawScore: number;
  displayScore: number;
  confidenceTier: string;
  warningCount: number;
  mustHaveWarningCount: number;
  isTied: boolean;
  scoreDecomposition: Record<string, any>;
  computedAt: string;
}

export interface RecruiterInterviewReleaseEligibility {
  isEligible: boolean;
  reasonCode?: string | null;
  message: string;
  interviewMode?: string | null;
  questionSetId?: string | null;
  setVersion?: number | null;
  questionCount: number;
  existingReleaseId?: string | null;
  existingReleaseStatus?: string | null;
}

export interface RecruiterApplicationDetail {
  applicationId: string;
  jobId: string;
  jobTitle: string;
  jobVersionId: string;
  jobRevision: number;
  candidateId: string;
  profileId: string;
  profileVersion: number;
  candidateName: string;
  candidateEmail?: string | null;
  candidatePhone?: string | null;
  currentRole?: string | null;
  status: CandidateApplicationStatus;
  recruiterReviewState: RecruiterReviewState;
  submittedAt: string;
  withdrawnAt?: string | null;
  profileSkills: string[];
  profileHeadline?: string | null;
  matchEvidence?: RecruiterMatchEvidence | null;
  interviewEligibility: RecruiterInterviewReleaseEligibility;
}

export interface RecruiterReleaseInterviewResponse {
  releaseId: string;
  applicationId: string;
  candidateId: string;
  jobId: string;
  jobTitle: string;
  jobRevision: number;
  interviewMode?: string;
  aiSetId: string;
  setVersion: number;
  status: string;
  releasedAt: string;
  message: string;
}

export async function fetchJobApplicants(
  jobId: string,
  params?: {
    status?: string;
    reviewState?: string;
    mode?: string;
    revision?: number;
    page?: number;
    limit?: number;
  }
): Promise<RecruiterApplicationListResponse> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.reviewState) query.set("review_state", params.reviewState);
  if (params?.mode) query.set("mode", params.mode);
  if (params?.revision !== undefined && params?.revision !== null) query.set("revision", String(params.revision));
  if (params?.page) query.set("page", String(params.page));
  if (params?.limit) query.set("limit", String(params.limit));

  const qs = query.toString() ? `?${query.toString()}` : "";
  return apiFetch<RecruiterApplicationListResponse>(`/api/v1/jobs/${jobId}/applications${qs}`);
}

export async function fetchJobApplicantDetail(
  jobId: string,
  applicationId: string,
  mode?: string
): Promise<RecruiterApplicationDetail> {
  const query = mode ? `?mode=${encodeURIComponent(mode)}` : "";
  return apiFetch<RecruiterApplicationDetail>(`/api/v1/jobs/${jobId}/applications/${applicationId}${query}`);
}

export async function updateApplicantReviewState(
  jobId: string,
  applicationId: string,
  newState: RecruiterReviewState,
  expectedState?: RecruiterReviewState
): Promise<RecruiterApplicationDetail> {
  return apiFetch<RecruiterApplicationDetail>(`/api/v1/jobs/${jobId}/applications/${applicationId}/review-state`, {
    method: "PATCH",
    body: JSON.stringify({
      newState,
      expectedState,
    }),
  });
}

export async function releaseApplicantInterview(
  jobId: string,
  applicationId: string,
  aiSetId?: string,
  idempotencyKey?: string
): Promise<RecruiterReleaseInterviewResponse> {
  return apiFetch<RecruiterReleaseInterviewResponse>(`/api/v1/jobs/${jobId}/applications/${applicationId}/release-interview`, {
    method: "POST",
    body: JSON.stringify({
      aiSetId,
      idempotencyKey,
    }),
  });
}
