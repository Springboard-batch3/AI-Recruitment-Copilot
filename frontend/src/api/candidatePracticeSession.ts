import { apiFetch } from "./client";
import { QuestionFormat } from "../types/ai_question_set";

export type PracticeSessionLifecycle =
  | "INITIALIZED"
  | "IN_PROGRESS"
  | "QUESTION_GENERATING"
  | "AWAITING_ANSWER"
  | "ANSWER_SAVING"
  | "QUESTION_GENERATION_FAILED_RETRYABLE"
  | "FINAL_ANSWER_SAVING"
  | "FINAL_REVIEW_GENERATING"
  | "FINAL_REVIEW_FAILED_RETRYABLE"
  | "ANSWERS_COMPLETE"
  | "COMPLETED"
  | "ABANDONED"
  | "EXPIRED";

export type PracticeSection = "CONVERSATION" | "MCQ" | "COMPLETE";

export type PracticeMessageRole = "CANDIDATE" | "INTERVIEWER" | "SYSTEM";

export type PracticeMessageKind =
  | "GREETING"
  | "PRIMARY_QUESTION"
  | "ANSWER"
  | "FOLLOW_UP"
  | "ACKNOWLEDGEMENT"
  | "SYSTEM_NOTICE";

export interface PracticeMessageDto {
  message_id: string;
  sequence_number: number;
  role: PracticeMessageRole;
  message_kind: PracticeMessageKind;
  text: string;
  source_question_id?: string | null;
  created_at: string;
}

export interface PracticeActiveQuestionOptionDto {
  option_id: string;
  text: string;
  display_order: number;
}

export interface PracticeActiveQuestionDto {
  question_id: string;
  question_format: QuestionFormat;
  wording: string;
  competency_name: string;
  difficulty?: string | null;
  options?: PracticeActiveQuestionOptionDto[] | null;
  turn_count_for_question: number;
  follow_up_available: boolean;
}

export type AnswerStatus = "ASSESSABLE" | "INSUFFICIENT_RESPONSE" | "REVIEW_UNAVAILABLE";

export type DimensionLevel =
  | "STRONG"
  | "DEVELOPING"
  | "NEEDS_DETAIL"
  | "NOT_DEMONSTRATED"
  | "NOT_APPLICABLE";

export type ReviewLifecycleStatus =
  | "SUBMITTED"
  | "REVIEW_GENERATING"
  | "REVIEW_READY"
  | "REVIEW_FAILED_RETRYABLE"
  | "REVIEW_FAILED_FINAL";

export interface AnswerStrengthItem {
  dimension: string;
  observation: string;
  answer_segment_ids: string[];
}

export interface ImprovementAreaItem {
  dimension: string;
  observation: string;
  answer_segment_ids: string[];
}

export interface FollowUpItem {
  needed: boolean;
  question?: string | null;
}

export interface PracticeAnswerReviewDto {
  review_id: string;
  answer_id: string;
  question_id: string;
  schema_version: string;
  lifecycle_status: ReviewLifecycleStatus;
  answer_status: AnswerStatus;
  acknowledgement: string;
  strengths: AnswerStrengthItem[];
  improvement_areas: ImprovementAreaItem[];
  next_steps: string[];
  stronger_structure: string;
  dimension_levels: Record<string, DimensionLevel>;
  follow_up?: FollowUpItem | null;
  uncertainty_codes: string[];
  model_identifier?: string | null;
  retry_count: number;
  created_at: string;
  completed_at?: string | null;
}

export interface PracticeMcqReviewDto {
  schema_version: string;
  source_question_id: string;
  selected_option_id: string;
  correct_option_id: string;
  is_correct: boolean;
  explanation: string;
  selected_option_review?: string | null;
  learning_point?: string | null;
  competency?: string | null;
}

export interface PracticeSessionDetailResponse {
  session_id: string;
  release_id: string;
  job_id: string;
  lifecycle_status: PracticeSessionLifecycle;
  current_section: PracticeSection;
  current_conv_index: number;
  total_conv_count: number;
  current_mcq_index: number;
  total_mcq_count: number;
  total_response_count: number;
  turn_count_for_question: number;
  active_question?: PracticeActiveQuestionDto | null;
  messages: PracticeMessageDto[];
  latest_review?: PracticeAnswerReviewDto | null;
  latest_mcq_review?: PracticeMcqReviewDto | null;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at: string;
  time_remaining_seconds: number;
  safe_fallback_notice?: string | null;
}

export interface PracticeSessionAbandonResponse {
  session_id: string;
  lifecycle_status: PracticeSessionLifecycle;
  message: string;
}

export async function startOrResumePracticeSession(
  releaseId: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    "/api/v1/candidate/practice-sessions/start",
    {
      method: "POST",
      body: JSON.stringify({ release_id: releaseId }),
    }
  );
}

export async function getPracticeSessionState(
  sessionId: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}`
  );
}

export async function submitConversationalAnswer(
  sessionId: string,
  questionId: string,
  answerText: string,
  idempotencyKey?: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/answer-conversation`,
    {
      method: "POST",
      body: JSON.stringify({
        question_id: questionId,
        answer_text: answerText,
        idempotency_key: idempotencyKey || crypto.randomUUID(),
      }),
    }
  );
}

export async function retryQuestionGeneration(
  sessionId: string,
  idempotencyKey?: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/retry-question`,
    {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: idempotencyKey || crypto.randomUUID(),
      }),
    }
  );
}

export async function retryFinalReview(
  sessionId: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/retry-final-review`,
    {
      method: "POST",
    }
  );
}

export async function retryAnswerReview(
  sessionId: string,
  answerId: string,
  idempotencyKey?: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/retry-review`,
    {
      method: "POST",
      body: JSON.stringify({
        answer_id: answerId,
        idempotency_key: idempotencyKey || crypto.randomUUID(),
      }),
    }
  );
}

export async function advancePracticeQuestion(
  sessionId: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/advance-question`,
    {
      method: "POST",
    }
  );
}

export async function submitMcqAnswer(
  sessionId: string,
  questionId: string,
  selectedOptionId: string,
  idempotencyKey?: string
): Promise<PracticeSessionDetailResponse> {
  return apiFetch<PracticeSessionDetailResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/answer-mcq`,
    {
      method: "POST",
      body: JSON.stringify({
        question_id: questionId,
        selected_option_id: selectedOptionId,
        idempotency_key: idempotencyKey || crypto.randomUUID(),
      }),
    }
  );
}

export interface StrengthAreaItem {
  title: string;
  observation: string;
  source_review_ids: string[];
}

export interface PriorityImprovementItem {
  title: string;
  observation: string;
  source_review_ids: string[];
}

export interface CompetencyCommentaryItem {
  competency_id: string;
  commentary: string;
  source_review_ids: string[];
}

export interface ResponsePatternItem {
  observation: string;
  source_review_ids: string[];
}

export interface CompetencyAggregateDto {
  competency_name: string;
  numeric_score?: number | null;
  anchored_level: string;
  assessable_count: number;
  total_questions: number;
  excluded_count: number;
  display_band: string;
  description: string;
}

export interface DimensionAggregateDto {
  dimension_key: string;
  dimension_label: string;
  anchored_level: string;
  numeric_score?: number | null;
  assessable_count: number;
  excluded_count: number;
  display_label: string;
}

export interface QuestionTrendItemDto {
  sequence_number: number;
  question_id: string;
  question_format: string;
  wording: string;
  candidate_answer?: string | null;
  competency_name: string;
  assessability_status: string;
  answer_summary?: string | null;
  what_was_done_well?: string[];
  what_was_missing?: string[];
  how_to_improve?: string[];
  stronger_answer_structure?: string | null;
  evidence_excerpt?: string | null;
  dimension_levels: Record<string, string>;
  follow_up_completed: boolean;
  is_mcq_correct?: boolean | null;
  review_id?: string | null;
}

export interface McqTopicResultDto {
  topic_name: string;
  attempted_count: number;
  correct_count: number;
  learning_points: string[];
}

export interface McqBreakdownDto {
  total_count: number;
  attempted_count: number;
  correct_count: number;
  skipped_count: number;
  topics: McqTopicResultDto[];
}

export interface CompletionCountsDto {
  total_primary_questions: number;
  total_conversational_questions: number;
  total_mcq_questions: number;
  assessable_answers_count: number;
  insufficient_answers_count: number;
  skipped_count: number;
  unavailable_count: number;
}

export interface CandidatePracticeReportDto {
  report_id: string;
  session_id: string;
  candidate_id: string;
  job_id: string;
  job_title: string;
  job_revision: number;
  report_version: number;
  schema_version: string;
  lifecycle_status: string;
  aggregation_policy_version: string;
  source_review_hash: string;
  summary_text: string;
  strongest_areas: StrengthAreaItem[];
  priority_improvements: PriorityImprovementItem[];
  competency_commentary: CompetencyCommentaryItem[];
  response_patterns: ResponsePatternItem[];
  next_practice_plan: string[];
  competency_aggregates: CompetencyAggregateDto[];
  radar_eligible: boolean;
  dimension_aggregates: DimensionAggregateDto[];
  question_trend: QuestionTrendItemDto[];
  trend_classification: string;
  mcq_breakdown?: McqBreakdownDto | null;
  completion_counts: CompletionCountsDto;
  disclaimer: string;
  validation_status: string;
  retry_count: number;
  is_current: boolean;
  created_at: string;
  finalized_at?: string | null;
}

export async function abandonPracticeSession(
  sessionId: string
): Promise<PracticeSessionAbandonResponse> {
  return apiFetch<PracticeSessionAbandonResponse>(
    `/api/v1/candidate/practice-sessions/${sessionId}/abandon`,
    {
      method: "POST",
    }
  );
}

export async function getPracticeReport(
  sessionId: string
): Promise<CandidatePracticeReportDto> {
  return apiFetch<CandidatePracticeReportDto>(
    `/api/v1/candidate/practice-sessions/${sessionId}/report`
  );
}

export async function retryPracticeReport(
  sessionId: string
): Promise<CandidatePracticeReportDto> {
  return apiFetch<CandidatePracticeReportDto>(
    `/api/v1/candidate/practice-sessions/${sessionId}/retry-report`,
    {
      method: "POST",
    }
  );
}


