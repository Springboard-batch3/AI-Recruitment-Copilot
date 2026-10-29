export type InterviewMode = "TEXT" | "AUDIO";

export type AiSetLifecycle = "DRAFT" | "VALIDATED" | "APPROVED" | "STALE";

export type SlotReviewState = "UNREVIEWED" | "KEPT" | "EDITED" | "REVISED" | "RECRUITER_AUTHORED";

export type QuestionFormat = "CONVERSATION" | "MCQ_SINGLE_ANSWER";

export type QuestionDifficulty = "FOUNDATIONAL" | "INTERMEDIATE" | "ADVANCED";

export interface McqOptionDto {
  option_id: string;
  text: string;
}

export interface SafeRubricDto {
  rubric_id: string;
  rubric_version: number;
  competency_definition: string;
  question_intent: string;
  observable_indicators: string[];
  rating_anchors: Record<string, string>;
  sufficient_evidence_guidance?: string | null;
  insufficient_evidence_guidance?: string | null;
  permitted_probes: string[];
}

export interface QuestionProvenanceDto {
  authoring_origin: string;
  provider_class?: string | null;
  model_identifier?: string | null;
  invocation_id?: string | null;
  prompt_template_version?: string | null;
  guardrail_policy_version?: string | null;
  schema_version?: string | null;
  generated_at?: string | null;
  validation_status?: string | null;
  validation_result?: string | null;
  job_version_id?: string | null;
  job_revision?: number | null;
  source_requirement_ids?: string[];
  set_version?: number | null;
  is_edited?: boolean;
  reviewer_user_id?: string | null;
  reviewed_at?: string | null;
}

export interface ReleaseEligibilitySummaryDto {
  is_eligible: boolean;
  reason_code?: string | null;
  safe_message?: string | null;
  blocking_items?: Array<{
    slot_number?: number;
    slot_id?: string;
    reason_code?: string;
    message?: string;
  }>;
}

export interface AiQuestionSlotItem {
  slot_id: string;
  display_order: number;
  competency_name: string;
  question_type: string;
  estimated_time_minutes: number;
  permitted_probes: string[];
  rubric?: SafeRubricDto | null;
  ai_wording?: string | null;
  question_text?: string | null;
  review_state: SlotReviewState;
  authoring_origin: string;
  validation_status: "PASSED" | "FAILED" | "PENDING";
  safe_failure_code?: string | null;
  rationale_code?: string | null;
  provenance?: QuestionProvenanceDto | null;
  updated_at?: string | null;
  question_format: QuestionFormat;
  options?: McqOptionDto[] | null;
  correct_option_id?: string | null;
  answer_explanation?: string | null;
  difficulty?: QuestionDifficulty | null;
}

export interface AiQuestionSetDetail {
  ai_set_id: string;
  set_version: number;
  interview_mode: InterviewMode;
  template_id?: string | null;
  source_template_version?: number | null;
  source_mode?: string;
  source_content_hash: string;
  source_requirement_ids?: string[];
  job_id: string;
  job_title: string;
  job_revision: number;
  lifecycle_status: AiSetLifecycle;
  is_immutable: boolean;
  content_hash?: string | null;
  action?: string | null;
  provider_class?: string | null;
  model_identifier?: string | null;
  model_display_name?: string | null;
  prompt_template_version?: string | null;
  guardrail_policy_version?: string | null;
  schema_version?: string | null;
  predecessor_set_id?: string | null;
  slots: AiQuestionSlotItem[];
  total_slots_count: number;
  generated_slots_count: number;
  reviewed_slots_count: number;
  conversation_count: number;
  mcq_count: number;
  is_source_stale: boolean;
  stale_reason?: string | null;
  release_eligibility?: ReleaseEligibilitySummaryDto | null;
  is_legacy?: boolean;
  created_at: string;
  updated_at: string;
  approved_at?: string | null;
}

export interface AiQuestionSetSummary {
  ai_set_id: string;
  set_version: number;
  interview_mode: InterviewMode;
  template_id?: string | null;
  source_mode?: string;
  job_id: string;
  job_title: string;
  lifecycle_status: AiSetLifecycle;
  is_immutable: boolean;
  is_source_stale: boolean;
  is_legacy?: boolean;
  model_display_name?: string | null;
  generated_question_count: number;
  total_blueprint_slots: number;
  conversation_count: number;
  mcq_count: number;
  applied_candidate_count?: number;
  released_candidate_count?: number;
  created_at: string;
  updated_at: string;
}

export interface AiQuestionSetDirectory {
  sets: AiQuestionSetSummary[];
  total_count: number;
  awaiting_review_count: number;
  ready_for_approval_count: number;
  approved_count: number;
}

export interface AiQuestionSetValidateResult {
  is_valid: boolean;
  lifecycle_status: string;
  blocking_errors: string[];
  safe_message: string;
  release_eligibility?: ReleaseEligibilitySummaryDto | null;
}

export interface AiQuestionSetApproveResult {
  ai_set_id: string;
  set_version: number;
  lifecycle_status: string;
  is_immutable: boolean;
  content_hash: string;
  approved_at: string;
  safe_message: string;
}

export interface EligibleJobItem {
  job_id: string;
  job_title: string;
  requirement_count: number;
  current_ai_set_id?: string | null;
  current_ai_set_status?: string | null;
  applied_candidate_count?: number;
  released_candidate_count?: number;
}

export interface EligibleJobsResponse {
  items: EligibleJobItem[];
}

export interface CandidatePracticeJobSummary {
  release_id: string;
  job_id: string;
  job_title: string;
  job_revision?: number;
  ai_set_id?: string;
  set_version?: number;
  interview_mode?: InterviewMode;
  conversation_count?: number;
  conversation_question_count?: number;
  mcq_count: number;
  total_question_count?: number;
  released_at: string;
  status?: "RELEASED" | "REVOKED" | string;
  status_label?: string;
  session_status?: "IN_PROGRESS" | "SUBMITTED" | string;
  is_submitted?: boolean;
}

export interface CandidatePracticeDiscoveryResponse {
  items: CandidatePracticeJobSummary[];
}

export interface RecruiterPracticeReleaseSummary {
  release_id: string;
  candidate_id: string;
  job_id: string;
  job_title: string;
  job_revision: number;
  ai_set_id: string;
  set_version: number;
  status: "RELEASED" | "REVOKED";
  released_at: string;
  revoked_at?: string | null;
  released_by_user_id: string;
  conversation_count: number;
  mcq_count: number;
}

export interface CandidatePracticeQuestionSafe {
  question_id: string;
  slot_id: string;
  display_order: number;
  question_format: QuestionFormat;
  question_text: string;
  options?: McqOptionDto[] | null;
  difficulty?: QuestionDifficulty | null;
  estimated_time_minutes: number;
  competency_name: string;
}
