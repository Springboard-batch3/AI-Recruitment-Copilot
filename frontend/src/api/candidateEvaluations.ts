import { apiFetch } from "./client";

export interface EvaluationJobSummary {
  job_id: string;
  job_version_id: string;
  job_revision: number;
  job_title: string;
  department?: string | null;
  total_releases_count: number;
  in_progress_count: number;
  submitted_count: number;
  analysis_pending_count: number;
  ready_for_review_count: number;
  finalized_count: number;
  last_submission_at?: string | null;
}

export interface EvaluationCandidateSummary {
  release_id: string;
  session_id?: string | null;
  candidate_id: string;
  candidate_name: string;
  candidate_email?: string | null;
  application_source: string;
  submitted_at?: string | null;
  answers_recorded_display: string;
  ai_analysis_status: string;
  recruiter_evaluation_status: string;
  interview_mode?: "TEXT" | "AUDIO";
  resume_match_score?: number | null;
  required_skills_coverage?: string | null;
}

export interface EvidenceCitation {
  segment_id?: string;
  citation_id?: string;
  text?: string;
  snippet_text?: string;
  source?: string;
  start_char?: number | null;
  end_char?: number | null;
}

export interface AiProvisionalAnalysis {
  analysis_status?: string;
  status?: string;
  provisional_rating?: number | null;
  addressed_well?: string[];
  missing_or_unclear?: string[];
  suggested_improvement?: string | null;
  suggested_improvements?: string[];
  dimension_scores?: Record<string, number>;
  dimension_observations?: Record<string, string>;
  evidence_citations?: EvidenceCitation[];
  is_insufficient_evidence?: boolean;
  uncertainty_notes?: string | null;
  analysis_model_version?: string | null;
  evaluated_at?: string | null;
}

export interface QuestionRubricAnchor {
  score_1?: string | null;
  score_2?: string | null;
  score_3?: string | null;
  score_4?: string | null;
  score_5?: string | null;
  observable_indicators?: string[];
  sufficient_evidence_guidance?: string | null;
  insufficient_evidence_guidance?: string | null;
}

export interface RecruiterQuestionReview {
  question_evaluation_id?: string | null;
  review_state: string; // ACCEPTED, EDITED, OVERRIDDEN, INSUFFICIENT, UNREVIEWED
  reviewed_rating?: number | null;
  reviewed_observations?: Record<string, any>;
  override_reason?: string | null;
  recruiter_note?: string | null;
  reviewer_name?: string | null;
  reviewed_at?: string | null;
}

export interface EvaluationQuestionDetail {
  slot_id: string;
  question_order: number;
  is_supplemental: boolean;
  question_type: string;
  question_format: string; // CONVERSATIONAL, MCQ
  competency?: string | null;
  competency_name?: string | null;
  question_text: string;
  submitted_answer_text?: string | null;
  candidate_text_answer?: string | null;
  selected_option_id?: string | null;
  candidate_selected_option_id?: string | null;
  selected_option_text?: string | null;
  candidate_selected_option_text?: string | null;
  correct_option_id?: string | null;
  correct_option_text?: string | null;
  is_correct?: boolean | null;
  stored_explanation?: string | null;
  explanation?: string | null;
  is_skipped: boolean;
  ai_provisional_analysis?: AiProvisionalAnalysis | null;
  ai_provisional?: AiProvisionalAnalysis | null;
  rubric?: QuestionRubricAnchor | null;
  recruiter_review?: RecruiterQuestionReview | null;
}

export interface ResumeMatchReference {
  has_match_reference?: boolean;
  overall_score?: number | null;
  overall_compatibility_score?: number | null;
  matched_required_count?: number | null;
  required_skills_matched_count?: number | null;
  total_required_count?: number | null;
  total_required_skills_count?: number | null;
  category_breakdown?: Record<string, any>;
  category_scores?: Record<string, number>;
  profile_version?: number | null;
  match_run_id?: string | null;
  match_rule_version?: string | null;
  notes?: string | null;
}

export interface EvaluationOverviewSummary {
  questions_reviewed: number;
  total_questions: number;
  core_questions_count: number;
  supplemental_questions_count: number;
  ai_coverage_count?: number;
  insufficient_evidence_count: number;
  aggregate_score?: number | null;
  scoring_policy_version?: string;
  dimension_averages: Record<string, number>;
  key_strengths: string[];
  priority_review_areas: string[];
  is_aggregate_complete?: boolean;
  is_ready_to_finalize?: boolean;
  status?: string;
}

export interface EvaluationAuditActivity {
  event_type: string;
  timestamp: string;
  actor_name?: string | null;
  summary: string;
}

export interface CandidateInterviewEvaluationDetail {
  session_id: string;
  evaluation_id?: string | null;
  release_id: string;
  candidate_id: string;
  candidate_name: string;
  candidate_email?: string | null;
  job_id: string;
  job_title: string;
  job_revision: number;
  question_set_version: number;
  interview_mode?: "TEXT" | "AUDIO";
  submitted_at?: string | null;
  status: string;
  candidate_feedback_status: string;
  candidate_feedback_published_at?: string | null;
  finalized_by_name?: string | null;
  finalized_at?: string | null;
  resume_match_reference?: ResumeMatchReference | null;
  overview: EvaluationOverviewSummary;
  questions: EvaluationQuestionDetail[];
  activity_log: EvaluationAuditActivity[];
}

export interface CandidateEvaluationExport {
  session_id: string;
  evaluation_id?: string | null;
  release_id: string;
  candidate_id: string;
  candidate_name: string;
  candidate_email?: string | null;
  job_id: string;
  job_title: string;
  job_revision: number;
  question_set_version: number;
  interview_mode: "TEXT" | "AUDIO";
  submitted_at?: string | null;
  status: string;
  candidate_feedback_status: string;
  candidate_feedback_published_at?: string | null;
  finalized_by_name?: string | null;
  finalized_at?: string | null;
  resume_match_reference?: ResumeMatchReference | null;
  overview: EvaluationOverviewSummary;
  questions: EvaluationQuestionDetail[];
  activity_log: EvaluationAuditActivity[];
  exported_at: string;
}

export interface SaveQuestionReviewPayload {
  review_state: string;
  reviewed_rating?: number | null;
  override_reason?: string | null;
  recruiter_note?: string | null;
  reviewed_observations?: Record<string, any>;
}

export interface SaveQuestionReviewResult {
  question_evaluation_id: string;
  session_id: string;
  slot_id: string;
  review_state: string;
  reviewed_rating?: number | null;
  override_reason?: string | null;
  recruiter_notes?: string | null;
  reviewed_by_user_id: string;
  reviewed_at: string;
  questions_reviewed_count: number;
  total_questions_count: number;
  deterministic_aggregate_score?: number | null;
  is_ready_to_finalize: boolean;
}

export interface FinalizeEvaluationPayload {
  confirmation_notes?: string | null;
}

export interface FinalizeEvaluationResult {
  evaluation_id: string;
  session_id: string;
  status: string;
  aggregate_score?: number | null;
  finalized_at: string;
  message: string;
}

export interface PublishFeedbackResult {
  evaluation_id: string;
  session_id: string;
  candidate_feedback_status: string;
  published_at: string;
  message: string;
}

export interface QuestionSafeFeedback {
  slot_id: string;
  question_order: number;
  competency_name?: string | null;
  question_text: string;
  is_mcq: boolean;
  performance_indicator: string;
  reviewed_strength?: string | null;
  suggested_growth_area?: string | null;
}

export interface CandidateSafeFeedbackReport {
  session_id: string;
  job_title: string;
  job_revision?: number;
  interview_mode?: "TEXT" | "AUDIO";
  completed_at: string;
  published_at: string;
  top_strengths: string[];
  priority_development_areas: string[];
  dimension_summary: Record<string, number>;
  questions_feedback: QuestionSafeFeedback[];
}

export async function listEvaluationJobs(): Promise<EvaluationJobSummary[]> {
  return apiFetch<EvaluationJobSummary[]>("/api/v1/recruiter/evaluations/jobs");
}

export async function listJobCandidatesForEvaluation(
  jobId: string,
  jobRevision: number,
  mode?: string
): Promise<EvaluationCandidateSummary[]> {
  const query = mode && mode !== "ALL" ? `?mode=${encodeURIComponent(mode)}` : "";
  return apiFetch<EvaluationCandidateSummary[]>(
    `/api/v1/recruiter/evaluations/jobs/${jobId}/revisions/${jobRevision}/candidates${query}`
  );
}

export async function getCandidateEvaluationDetail(
  sessionId: string
): Promise<CandidateInterviewEvaluationDetail> {
  return apiFetch<CandidateInterviewEvaluationDetail>(
    `/api/v1/recruiter/evaluations/sessions/${sessionId}`
  );
}

export async function exportCandidateEvaluationJson(
  sessionId: string
): Promise<CandidateEvaluationExport> {
  return apiFetch<CandidateEvaluationExport>(
    `/api/v1/recruiter/evaluations/sessions/${sessionId}/export`
  );
}

export async function saveQuestionEvaluationReview(
  sessionId: string,
  slotId: string,
  payload: SaveQuestionReviewPayload
): Promise<SaveQuestionReviewResult> {
  return apiFetch<SaveQuestionReviewResult>(
    `/api/v1/recruiter/evaluations/sessions/${sessionId}/questions/${slotId}/review`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}

export async function finalizeInterviewEvaluation(
  sessionId: string,
  payload: FinalizeEvaluationPayload = {}
): Promise<FinalizeEvaluationResult> {
  return apiFetch<FinalizeEvaluationResult>(
    `/api/v1/recruiter/evaluations/sessions/${sessionId}/finalize`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

export async function publishCandidateFeedbackReport(
  sessionId: string
): Promise<PublishFeedbackResult> {
  return apiFetch<PublishFeedbackResult>(
    `/api/v1/recruiter/evaluations/sessions/${sessionId}/publish-feedback`,
    {
      method: "POST",
    }
  );
}

export async function triggerAiInterviewAnalysis(
  sessionId: string,
  reanalyzeFailed: boolean = false
): Promise<CandidateInterviewEvaluationDetail> {
  const url = reanalyzeFailed
    ? `/api/v1/recruiter/evaluations/sessions/${sessionId}/analyze?reanalyze_failed=true`
    : `/api/v1/recruiter/evaluations/sessions/${sessionId}/analyze`;
  return apiFetch<CandidateInterviewEvaluationDetail>(
    url,
    {
      method: "POST",
    }
  );
}

export async function getCandidateFeedbackReport(
  sessionId: string
): Promise<CandidateSafeFeedbackReport> {
  return apiFetch<CandidateSafeFeedbackReport>(
    `/api/v1/candidate/interviews/sessions/${sessionId}/feedback`
  );
}


