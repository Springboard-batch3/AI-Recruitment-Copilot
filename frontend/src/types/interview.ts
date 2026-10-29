export type InterviewLifecycleState =
  | "DRAFT"
  | "VALIDATED"
  | "APPROVED"
  | "ARCHIVED"
  | "SUPERSEDED";

export type QuestionType =
  | "BEHAVIORAL"
  | "TECHNICAL"
  | "SITUATIONAL"
  | "PROBLEM_SOLVING"
  | "DOMAIN_KNOWLEDGE"
  | "SYSTEM_DESIGN"
  | "ROLE_FIT"
  | "EVIDENCE_CLARIFICATION";

export type InterviewLayer = "COMMON_CORE" | "SUPPLEMENTAL_EVIDENCE";

export type AuthoringOrigin = "DETERMINISTIC" | "AI_ASSISTED" | "RECRUITER_EDITED";

export interface RubricLevel {
  level_name: string;
  score_range: [number, number];
  description: string;
  observable_signals: string[];
}

export interface AnchoredRubric {
  rubric_id: string;
  rubric_version?: number;
  target_competency?: string;
  competency_definition?: string;
  question_intent?: string;
  observable_indicators?: string[];
  observable_signals?: string[];
  rating_anchors?: Record<string | number, string>;
  levels?: RubricLevel[];
  sufficient_evidence_guidance?: string;
  insufficient_evidence_guidance?: string;
  permitted_probes?: string[];
  prohibited_inferences?: string[];
}

export interface TemplateQuestion {
  question_id: string;
  question_version?: number;
  layer: InterviewLayer;
  question_type: QuestionType;
  competency_name: string;
  question_text: string;
  display_order: number;
  estimated_time_minutes: number;
  permitted_probes: string[];
  rubric?: AnchoredRubric | null;
  authoring_origin: AuthoringOrigin;
  source_requirement_ids: string[];
  source_gap_ids?: string[];
  source_match_level?: string | null;
  included_in_common_score?: boolean;
  content_hash?: string | null;
  deterministic_source_text?: string | null;
  deterministic_source_hash?: string | null;
  deterministic_source_available?: boolean;
  has_wording_drift?: boolean;
  can_restore?: boolean;
  restore_unavailable_reason?: string | null;
  ai_suggestion_text?: string | null;
  ai_suggestion_available?: boolean;
  ai_suggestion_review_state?: string | null;
  safe_fallback_reason?: string | null;
  unresolved_manual_required?: boolean;
  safe_metadata?: Record<string, any>;
  ai_provenance?: {
    provider_class?: string;
    model_name?: string;
    applied_at?: string;
    prompt_template_version?: string;
    rationale_code?: string;
  } | null;
}


export interface JobInterviewTemplateResponse {
  template_id: string;
  job_id: string;
  job_version_id: string;
  job_revision: number;
  template_version: number;
  parent_template_id?: string | null;
  lifecycle_state: InterviewLifecycleState;
  common_core_questions: TemplateQuestion[];
  target_duration_minutes: number;
  content_hash: string;
  authoritative_snapshot_hash: string;
  created_at: string;
  approved_at?: string | null;
  approved_by_user_id?: string | null;
}

export interface CandidateInterviewPlanResponse {
  plan_id: string;
  template_id: string;
  candidate_id: string;
  resume_id: string;
  match_run_id: string;
  plan_version: number;
  parent_plan_id?: string | null;
  lifecycle_state: InterviewLifecycleState;
  common_core_questions: TemplateQuestion[];
  supplemental_questions: TemplateQuestion[];
  total_duration_minutes: number;
  common_duration_minutes: number;
  supplemental_duration_minutes: number;
  plan_hash: string;
  created_at: string;
  approved_at?: string | null;
  approved_by_user_id?: string | null;
}

export interface ValidationSummaryResponse {
  is_valid: boolean;
  lifecycle_state: InterviewLifecycleState;
  blocking_rule_violations: string[];
  warnings: string[];
  content_hash: string;
  validated_at: string;
}

export interface VersionSummary {
  version: number;
  id: string;
  lifecycle_state: InterviewLifecycleState;
  content_hash: string;
  created_at: string;
  approved_at?: string | null;
  parent_id?: string | null;
}

export interface VersionHistoryResponse {
  target_id: string;
  job_id: string;
  candidate_id?: string | null;
  current_version: number;
  versions: VersionSummary[];
}

export interface QuestionDiffItem {
  question_id: string;
  change_type: "ADDED" | "REMOVED" | "MODIFIED" | "UNCHANGED";
  field_changes: Record<string, { from: any; to: any }>;
}

export interface VersionDiffResponse {
  base_version: number;
  base_hash: string;
  target_version: number;
  target_hash: string;
  lifecycle_state_change: { from: InterviewLifecycleState; to: InterviewLifecycleState };
  question_diffs: QuestionDiffItem[];
  summary: string;
}

// =========================================================================
// Phase 3C: Wording Assistance & Provenance Types
// =========================================================================

export type WordingRationaleCode =
  | "CLARITY_IMPROVEMENT"
  | "CONCISENESS"
  | "PROFESSIONAL_TONE"
  | "STRUCTURE_POLISH"
  | "NEUTRAL_PHRASING";

export interface StructuredWordingVariant {
  variant_text: string;
  rationale_code?: WordingRationaleCode | null;
  referenced_identifiers: string[];
}

export interface CandidateWordingResult {
  source_question_id: string;
  source_question_version: number;
  operation: string;
  request_schema_version: string;
  prompt_template_version: string;
  deterministic_question_text: string;
  proposed_variants: StructuredWordingVariant[];
  allowed_references_checked: boolean;
  is_validated: boolean;
  validation_codes: string[];
  rejection_reasons: string[];
  authoring_origin: AuthoringOrigin;
  provider_fallback_used: boolean;
  provider_error_code?: string | null;
  safe_error_message?: string | null;
}

export interface QuestionActionResponse {
  question_id: string;
  question_version: number;
  question_text: string;
  authoring_origin: AuthoringOrigin;
  validation_state: string;
  deterministic_source_text?: string | null;
  deterministic_source_available?: boolean;
  has_wording_drift?: boolean;
  can_restore?: boolean;
  restore_unavailable_reason?: string | null;
  content_hash?: string | null;
  safe_metadata: Record<string, any>;
  message?: string | null;
}

export interface QuestionBatchWordingResult {
  question_id: string;
  display_order?: number;
  status: "AI_READY" | "DETERMINISTIC_FALLBACK" | "FAILED_RETRYABLE" | string;
  authoring_origin: AuthoringOrigin;
  question_text: string;
  question_version?: number;
  deterministic_question_text?: string;
  safe_message?: string | null;
  error_message?: string | null;
}

export interface GenerateTemplateAiWordingResponse {
  template_id: string;
  template_version: number;
  lifecycle_status: InterviewLifecycleState;
  total_questions: number;
  ai_generated_count: number;
  ai_ready_count?: number;
  fallback_count: number;
  failed_count: number;
  status: "COMPLETED" | "PARTIALLY_COMPLETED" | "FAILED" | "CANCELLED" | string;
  results: QuestionBatchWordingResult[];
  safe_message?: string;
  message?: string;
}

// =========================================================================
// Phase 3D.1: Recruiter Text-Only Simulation Types
// =========================================================================

export type SimulationLifecycleState = "NOT_STARTED" | "IN_PROGRESS" | "SUBMITTED";

export interface SimulationAnswerItem {
  answer_id: string;
  question_id: string;
  question_version: number;
  display_order: number;
  layer: InterviewLayer;
  answer_text: string;
  answer_version: number;
  is_submitted: boolean;
  content_hash: string;
  updated_at: string;
}

export interface SimulationQuestionSnapshot {
  question_id: string;
  question_version: number;
  display_order: number;
  layer: InterviewLayer;
  question_type: QuestionType;
  competency_name: string;
  question_text: string;
  estimated_time_minutes: number;
  permitted_probes: string[];
  rubric?: AnchoredRubric | null;
}

export interface CriterionObservation {
  criterion_id: string;
  observation?: string;
  observation_text?: string;
  cited_segment_ids: string[];
}

export interface ProvisionalAssessmentItem {
  question_id: string;
  assessment_status: "PROVISIONAL" | "INSUFFICIENT_EVIDENCE" | "VALIDATION_REJECTED" | "VALIDATION_FAILED" | "MANUAL_REVIEW_REQUIRED" | string;
  provisional_level?: number | null;
  insufficient_evidence_reason?: string | null;
  failure_category?: string | null;
  criterion_observations: CriterionObservation[];
  cited_segment_ids: string[];
  uncertainty_codes: string[];
  validation_report?: Record<string, any>;
  model_invocation_metadata?: Record<string, any>;
}

export interface SimulationAttemptResponse {
  attempt_id: string;
  organization_id: string;
  recruiter_user_id: string;
  candidate_id: string;
  candidate_name?: string;
  job_id: string;
  job_title?: string;
  job_version_id: string;
  job_revision: number;
  candidate_profile_version: number;
  match_result_id?: string | null;
  plan_id: string;
  template_id: string;
  plan_version: number;
  record_version: number;
  authoritative_content_hash: string;
  lifecycle_status: SimulationLifecycleState | "PROVISIONALLY_ASSESSED" | string;
  guardrail_policy_version: string;
  total_questions_count: number;
  answered_questions_count: number;
  started_at: string;
  submitted_at?: string | null;
  questions: SimulationQuestionSnapshot[];
  answers: Record<string, SimulationAnswerItem>; // keyed by question_id
  assessments?: Record<string, ProvisionalAssessmentItem>; // keyed by question_id
}

export interface SimulationAssessmentRunResponse {
  attempt_id: string;
  lifecycle_status: string;
  total_assessed: number;
  provisional_count: number;
  insufficient_evidence_count: number;
  validation_rejected_count: number;
  manual_review_required_count: number;
  assessments: Record<string, ProvisionalAssessmentItem>;
  safe_message: string;
}

export interface SimulationAnswerSaveRequest {
  expected_answer_version: number;
  answer_text: string;
}

export interface SimulationAnswerSaveResponse {
  answer: SimulationAnswerItem;
  answered_questions_count: number;
  total_questions_count: number;
  attempt_status: SimulationLifecycleState;
}

export interface SimulationSubmitResponse {
  attempt_id: string;
  lifecycle_status: "SUBMITTED";
  submitted_at: string;
  total_questions_count: number;
  answered_questions_count: number;
  authoritative_content_hash: string;
  message: string;
}

export interface SimulationAnswerSegmentItem {
  segment_id: string;
  sequence_order: number;
  start_char: number;
  end_char: number;
  segment_text: string;
}

export type ReviewDecision =
  | "ACCEPTED"
  | "EDITED"
  | "OVERRIDDEN"
  | "REJECTED"
  | "INSUFFICIENT";

export interface AssessmentReviewItem {
  review_id: string;
  assessment_id: string;
  attempt_id: string;
  question_id: string;
  reviewer_user_id: string;
  review_version: number;
  decision: ReviewDecision;
  reviewed_observation: string;
  reviewed_rubric_level?: number | null;
  retained_citations: string[];
  reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeterministicAggregate {
  policy_version: string;
  policy_hash: string;
  aggregate_score?: number | null;
  numerator?: number | null;
  denominator?: number | null;
  total_core_questions: number;
  scored_core_questions: number;
  excluded_core_questions: number;
  supplemental_questions_excluded: number;
  weights_breakdown: Record<string, number>;
  included_items: Array<{ question_id: string; competency: string; level: number; weight: number }>;
  excluded_items: Array<{ question_id: string; competency: string; reason: string }>;
}

export interface FinalizationEligibility {
  is_eligible: boolean;
  total_core_questions: number;
  reviewed_core_questions: number;
  pending_core_questions: number;
  missing_citations_count: number;
  blocking_reasons: string[];
}

export interface InterviewFinalizeResponse {
  interview_assessment_id: string;
  attempt_id: string;
  lifecycle_status: string;
  is_finalized: boolean;
  finalized_at: string;
  finalized_by_user_id: string;
  aggregate_score?: number | null;
  aggregate_summary?: Record<string, any> | null;
  safe_message: string;
}

export interface ReviewWorkspaceQuestionItem {
  question_id: string;
  display_order: number;
  layer: InterviewLayer;
  competency_name: string;
  question_text: string;
  estimated_time_minutes: number;
  rubric?: AnchoredRubric | null;
  submitted_answer?: string | null;
  answer_segments: SimulationAnswerSegmentItem[];
  provisional_assessment?: ProvisionalAssessmentItem | null;
  latest_review?: AssessmentReviewItem | null;
  review_history: AssessmentReviewItem[];
}

export interface ReviewWorkspaceResponse {
  attempt_id: string;
  lifecycle_status: string;
  is_finalized: boolean;
  candidate_id: string;
  candidate_name: string;
  job_id: string;
  job_title: string;
  record_version: number;
  questions: ReviewWorkspaceQuestionItem[];
  aggregate?: DeterministicAggregate | null;
  eligibility: FinalizationEligibility;
  finalized_assessment?: Record<string, any> | null;
}

