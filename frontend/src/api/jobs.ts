import { apiFetch } from "./client";

export type EmploymentType = "FULL_TIME" | "PART_TIME" | "CONTRACT" | "INTERNSHIP" | "TEMPORARY";
export type WorkplaceType = "ON_SITE" | "HYBRID" | "REMOTE";
export type JobLifecycleStatus = "ACTIVE" | "ARCHIVED";
export type JobPublicationStatus = "NOT_RELEASED" | "RELEASED" | "CLOSED";
export type JobVersionStatus = "DRAFT" | "READY_FOR_MATCHING" | "APPROVED";

export type RequirementCategory =
  | "SKILL"
  | "EXPERIENCE"
  | "RESPONSIBILITY"
  | "EDUCATION"
  | "CERTIFICATION"
  | "LANGUAGE"
  | "OTHER";

export type RequirementNecessity = "MUST_HAVE" | "PREFERRED";
export type RequirementPriority = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type RequirementOrigin = "RECRUITER_ENTERED" | "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED";

export type RequirementEvidenceType =
  | "EMPLOYMENT"
  | "PROJECT"
  | "EDUCATION"
  | "CERTIFICATION"
  | "SKILL_LIST"
  | "PUBLICATION"
  | "PATENT"
  | "OTHER";

export interface RequirementItem {
  requirementId?: string;
  displayOrder: number;
  category: RequirementCategory;
  label: string;
  normalizedLabel?: string;
  taxonomySkillConceptId?: string | null;
  necessity: RequirementNecessity;
  priority: RequirementPriority;
  customWeightOverride?: number | null;
  minExperienceMonths?: number | null;
  recencyYears?: number | null;
  acceptedEvidenceTypes: RequirementEvidenceType[];
  acceptedEquivalents: string[];
  rationale?: string | null;
  origin?: RequirementOrigin;
  reviewRequired?: boolean;
  warningMetadata?: Array<{
    code: string;
    message: string;
    severity?: string;
    rule?: string;
    matched_term?: string;
  }>;
  createdAt?: string;
  updatedAt?: string;
}

export interface JobReviewWarning {
  code: string;
  message: string;
  severity: string;
  requirementId?: string | null;
  rule?: string | null;
  matchedTerm?: string | null;
}

export interface JobVersionSummary {
  jobVersionId: string;
  versionNumber: number;
  versionStatus: JobVersionStatus;
  revision: number;
  title: string;
  department?: string | null;
  location?: string | null;
  employmentType?: EmploymentType | null;
  workplaceType?: WorkplaceType | null;
  openingsCount?: number | null;
  internalReference?: string | null;
  requirementsCount: number;
  readyByUserId?: string | null;
  readyAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface JobVersionDetail {
  jobVersionId: string;
  jobId: string;
  versionNumber: number;
  versionStatus: JobVersionStatus;
  revision: number;
  title: string;
  department?: string | null;
  location?: string | null;
  employmentType?: EmploymentType | null;
  workplaceType?: WorkplaceType | null;
  openingsCount?: number | null;
  internalReference?: string | null;
  summary?: string | null;
  responsibilities?: string | null;
  jobDescriptionText?: string | null;
  occupationConceptId?: string | null;
  occupationTitle?: string | null;
  taxonomyVersion?: string | null;
  parserVersion?: string | null;
  createdByUserId: string;
  readyByUserId?: string | null;
  readyAt?: string | null;
  approvedByUserId?: string | null;
  approvedAt?: string | null;
  requirements: RequirementItem[];
  reviewWarnings: JobReviewWarning[];
  createdAt: string;
  updatedAt: string;
}

export type JobReadinessSeverity = "ERROR" | "WARNING" | "INFO";

export interface JobReadinessFinding {
  code: string;
  severity: JobReadinessSeverity;
  message: string;
  requirementId?: string | null;
  field?: string | null;
  rule?: string | null;
  blocksReadiness: boolean;
}

export interface JobReadinessResponse {
  jobId: string;
  jobVersionId: string;
  versionNumber: number;
  revision: number;
  versionStatus: JobVersionStatus;
  ready: boolean;
  errors: JobReadinessFinding[];
  warnings: JobReadinessFinding[];
  info: JobReadinessFinding[];
  checkedAt: string;
}

export interface JobSummary {
  jobId: string;
  createdByUserId: string;
  status: JobLifecycleStatus;
  publicationStatus?: JobPublicationStatus;
  releasedAt?: string | null;
  releasedByUserId?: string | null;
  releasedJobVersionId?: string | null;
  createdAt: string;
  updatedAt: string;
  currentVersion: JobVersionSummary;
}

export interface JobDetail {
  jobId: string;
  createdByUserId: string;
  status: JobLifecycleStatus;
  publicationStatus?: JobPublicationStatus;
  releasedAt?: string | null;
  releasedByUserId?: string | null;
  releasedJobVersionId?: string | null;
  createdAt: string;
  updatedAt: string;
  currentVersion: JobVersionDetail;
}

export interface JobCreatePayload {
  title: string;
  department?: string | null;
  location?: string | null;
  employmentType?: EmploymentType | null;
  workplaceType?: WorkplaceType | null;
  openingsCount?: number | null;
  internalReference?: string | null;
  summary?: string | null;
  responsibilities?: string | null;
  jobDescriptionText?: string | null;
  occupationConceptId?: string | null;
}

export interface JobDraftUpdatePayload {
  expectedRevision: number;
  title?: string;
  department?: string | null;
  location?: string | null;
  employmentType?: EmploymentType | null;
  workplaceType?: WorkplaceType | null;
  openingsCount?: number | null;
  internalReference?: string | null;
  summary?: string | null;
  responsibilities?: string | null;
  jobDescriptionText?: string | null;
  occupationConceptId?: string | null;
}

export interface JobRequirementsSavePayload {
  expectedRevision: number;
  requirements: Array<{
    displayOrder: number;
    category: RequirementCategory;
    label: string;
    taxonomySkillConceptId?: string | null;
    necessity: RequirementNecessity;
    priority: RequirementPriority;
    customWeightOverride?: number | null;
    minExperienceMonths?: number | null;
    recencyYears?: number | null;
    acceptedEvidenceTypes?: RequirementEvidenceType[];
    acceptedEquivalents?: string[];
    rationale?: string | null;
  }>;
}

export async function fetchJobs(): Promise<JobSummary[]> {
  return apiFetch<JobSummary[]>("/api/v1/jobs");
}

export async function fetchJobDetail(jobId: string): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}`);
}

export async function createJobRequisition(payload: JobCreatePayload, idempotencyKey?: string): Promise<JobDetail> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }
  return apiFetch<JobDetail>("/api/v1/jobs", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

export async function updateJobDraft(jobId: string, payload: JobDraftUpdatePayload): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}/draft`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function saveDraftRequirements(jobId: string, payload: JobRequirementsSavePayload): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}/draft/requirements`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function checkJobReadiness(
  jobId: string,
  expectedRevision?: number
): Promise<JobReadinessResponse> {
  const qs = expectedRevision !== undefined ? `?expected_revision=${expectedRevision}` : "";
  return apiFetch<JobReadinessResponse>(`/api/v1/jobs/${jobId}/readiness${qs}`);
}

export async function markJobReady(
  jobId: string,
  expectedRevision: number
): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}/ready`, {
    method: "POST",
    body: JSON.stringify({ expected_revision: expectedRevision }),
  });
}

export async function releaseJob(jobId: string): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}/release`, {
    method: "POST",
  });
}

export async function createSuccessorVersion(
  jobId: string,
  fromVersionNumber?: number
): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/api/v1/jobs/${jobId}/versions`, {
    method: "POST",
    body: JSON.stringify({ from_version_number: fromVersionNumber }),
  });
}

export async function fetchJobVersions(jobId: string): Promise<JobVersionSummary[]> {
  return apiFetch<JobVersionSummary[]>(`/api/v1/jobs/${jobId}/versions`);
}

export async function deleteJob(jobId: string): Promise<{ success: boolean; message: string }> {
  return apiFetch<{ success: boolean; message: string }>(`/api/v1/jobs/${jobId}`, {
    method: "DELETE",
  });
}

// =========================================================================
// Match Run & Candidate Comparison API Helpers
// =========================================================================

export interface CandidateSelectorItem {
  profile_id: string;
  profile_version: number;
}

export interface MatchRunCreatePayload {
  jobId: string;
  expectedRevision: number;
  candidates: CandidateSelectorItem[];
  evaluationDate?: string | null;
  idempotencyKey?: string | null;
}

export interface MatchRunSummary {
  runId: string;
  lifecycleStatus: string;
  jobId: string;
  jobVersionId: string;
  revision: number;
  candidateCount: number;
  matcherAlgorithmVersion: string;
  matchingRuleVersion: string;
  scoringPolicyVersion: string;
  matchInputSchemaVersion: string;
  inputFingerprint: string;
  candidateInputFingerprint: string;
  reused: boolean;
  createdAt: string;
  resultsUrl: string;
}

export interface EvidenceSummary {
  total_criteria: number;
  required_total: number;
  required_evidence_found: number;
  required_partial_evidence: number;
  required_not_found: number;
  required_needs_verification: number;
  preferred_total: number;
  preferred_evidence_found: number;
  overall_evidence_found: number;
  overall_partial_evidence: number;
  overall_not_found: number;
  extraction_confidence: string;
}

export interface MatchResultRankingItem {
  resultId: string;
  runId: string;
  profileId: string;
  profileVersion: number;
  rank: number;
  isTied: boolean;
  tieGroupId?: string | null;
  rawCompatibilityScore: number;
  displayCompatibilityScore: number;
  confidenceTier: string;
  warningCount: number;
  mustHaveWarningCount: number;
  mustHaveTotalCount?: number;
  mustHaveEvidencedCount?: number;
  notEvidencedCount?: number;
  needsConfirmationCount?: number;
  evidence_summary?: EvidenceSummary | null;
  componentScores: Record<string, number>;
  confidenceDiagnostics: Record<string, any>;
  mustHaveWarnings: Array<{
    rule_code: string;
    requirement_id: string;
    requirement_name: string;
    safe_explanation: string;
  }>;
  generalWarnings: Array<{
    rule_code: string;
    safe_explanation: string;
  }>;
  requirement_contributions?: any[];
  requirementContributions?: any[];
  detailUrl?: string;
  detail_url?: string;
}

export interface PaginatedMatchResults {
  items: MatchResultRankingItem[];
  total: number;
  limit: number;
  offset: number;
  runId: string;
  jobId: string;
  revision: number;
}

export async function createMatchRun(payload: MatchRunCreatePayload): Promise<MatchRunSummary> {
  const headers: Record<string, string> = {};
  if (payload.idempotencyKey) {
    headers["Idempotency-Key"] = payload.idempotencyKey;
  }
  return apiFetch<MatchRunSummary>("/api/v1/matches/runs", {
    method: "POST",
    headers,
    body: JSON.stringify({
      job_id: payload.jobId,
      expected_revision: payload.expectedRevision,
      candidates: payload.candidates,
      evaluation_date: payload.evaluationDate,
      idempotency_key: payload.idempotencyKey,
    }),
  });
}

export async function fetchMatchResults(runId: string, limit = 50, offset = 0): Promise<PaginatedMatchResults> {
  return apiFetch<PaginatedMatchResults>(`/api/v1/matches/runs/${runId}/results?limit=${limit}&offset=${offset}`);
}

export async function fetchMatchRun(runId: string): Promise<MatchRunSummary> {
  return apiFetch<MatchRunSummary>(`/api/v1/matches/runs/${runId}`);
}

// =========================================================================
// Phase 2D Gap Report API Helpers
// =========================================================================

export type RequirementGapState =
  | "COVERED"
  | "PARTIALLY_COVERED"
  | "NOT_EVIDENCED"
  | "UNVERIFIED"
  | "CONFLICTING_REVIEW_REQUIRED"
  | "NOT_APPLICABLE";

export type GapReviewActionCode =
  | "NONE"
  | "PORTFOLIO_VERIFICATION"
  | "INTERVIEW_EXPLORATION"
  | "DOCUMENTATION_REQUEST"
  | "REFERENCE_CHECK";

export interface GapReportSummary {
  reportId: string;
  matchRunId: string;
  matchResultId: string;
  jobId: string;
  jobVersionId: string;
  revision: number;
  profileId: string;
  profileVersion: number;
  evaluationDate: string;
  matcherAlgorithmVersion: string;
  matchingRuleVersion: string;
  scoringPolicyVersion: string;
  matchInputSchemaVersion: string;
  taxonomyVersion?: string | null;
  gapSchemaVersion: string;
  gapPolicyVersion: string;
  wordingTemplateVersion: string;
  sourceFingerprint: string;
  reportFingerprint: string;
  totalRequirements: number;
  coveredCount: number;
  partiallyCoveredCount: number;
  notEvidencedCount: number;
  unverifiedCount: number;
  conflictingCount: number;
  notApplicableCount: number;
  mustHaveWarningCount: number;
  reused?: boolean;
  createdAt: string;
  reportUrl: string;
}

export interface GapReportItem {
  itemId: string;
  reportId: string;
  requirementId: string;
  requirementName?: string;
  displayOrder: number;
  category: RequirementCategory;
  necessity: RequirementNecessity;
  priority: RequirementPriority;
  sourceMatchState: string;
  sourceMatchValue: number;
  gapState: RequirementGapState;
  requiredValue?: string | null;
  documentedValue?: string | null;
  measurableShortfallValue?: number | null;
  shortfallUnit?: string | null;
  shortfallReasonCode?: string | null;
  evidenceReferences: string[];
  reviewRequired: boolean;
  reviewActionCode: GapReviewActionCode;
  mustHaveWarningReference?: string | null;
  recruiterWording: string;
  createdAt: string;
}

export interface GapReportDetail extends GapReportSummary {
  mustHaveTotalCount?: number;
  mustHaveEvidencedCount?: number;
  evidence_summary?: EvidenceSummary | null;
  compatibilityScore?: number | null;
  confidenceTier?: string | null;
  legacyReportNotice?: string | null;
  items: GapReportItem[];
}

export interface CanonicalGapReport {
  reportId: string;
  matchRunId: string;
  matchResultId: string;
  jobId: string;
  jobVersionId: string;
  revision: number;
  profileId: string;
  profileVersion: number;
  evaluationDate: string;
  matcherAlgorithmVersion: string;
  matchingRuleVersion: string;
  scoringPolicyVersion: string;
  matchInputSchemaVersion: string;
  taxonomyVersion?: string | null;
  gapSchemaVersion: string;
  gapPolicyVersion: string;
  wordingTemplateVersion: string;
  sourceFingerprint: string;
  reportFingerprint: string;
  totalRequirements: number;
  summaryCounts: Record<string, number>;
  mustHaveWarningCount: number;
  mustHaveTotalCount?: number;
  mustHaveEvidencedCount?: number;
  evidence_summary?: EvidenceSummary | null;
  compatibilityScore?: number | null;
  confidenceTier?: string | null;
  legacyReportNotice?: string | null;
  gaps: GapReportItem[];
  allEvidenceReferences: string[];
  disclaimer: string;
  createdAt: string;
}

export async function fetchGapReportsForResult(resultId: string): Promise<GapReportSummary[]> {
  return apiFetch<GapReportSummary[]>(`/api/v1/matches/results/${resultId}/gap-reports`);
}

export async function createGapReport(matchResultId: string, idempotencyKey: string): Promise<GapReportSummary> {
  return apiFetch<GapReportSummary>("/api/v1/gap-reports", {
    method: "POST",
    headers: {
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      match_result_id: matchResultId,
      idempotency_key: idempotencyKey,
    }),
  });
}

export async function fetchGapReportDetail(reportId: string): Promise<GapReportDetail> {
  return apiFetch<GapReportDetail>(`/api/v1/gap-reports/${reportId}`);
}

export async function fetchCanonicalGapReportJson(reportId: string): Promise<CanonicalGapReport> {
  return apiFetch<CanonicalGapReport>(`/api/v1/gap-reports/${reportId}/json`);
}

