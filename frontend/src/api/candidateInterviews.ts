import { apiFetch } from "./client";

export interface CandidateInterviewMcqOptionDto {
  id?: string;
  option_id?: string;
  text?: string;
  label?: string;
}

export interface CandidateInterviewQuestionDto {
  slot_id: string;
  question_order?: number;
  order?: number;
  question_format: "CONVERSATIONAL" | "CONVERSATION" | "MCQ" | "MCQ_SINGLE_ANSWER";
  question_text: string;
  competency?: string | null;
  options: CandidateInterviewMcqOptionDto[];
  is_supplemental: boolean;
  saved_text_answer?: string | null;
  saved_selected_option_id?: string | null;
  is_answered: boolean;
  saved_at?: string | null;
}

export interface CandidateInterviewSessionResponse {
  session_id: string;
  assignment_id: string;
  job_id: string;
  job_title: string;
  job_revision: number;
  interview_mode?: "TEXT" | "AUDIO";
  status: "IN_PROGRESS" | "SUBMITTED";
  started_at: string;
  last_saved_at?: string | null;
  submitted_at?: string | null;
  total_questions: number;
  answered_questions: number;
  questions: CandidateInterviewQuestionDto[];
}

export interface CandidateSaveAnswerRequest {
  text_answer?: string | null;
  selected_option_id?: string | null;
  is_skipped?: boolean;
}

export interface CandidateSaveAnswerResponse {
  session_id: string;
  slot_id: string;
  saved_at: string;
  is_answered: boolean;
  answered_questions: number;
  total_questions: number;
}

export interface CandidateSubmitInterviewRequest {
  confirmed_incomplete?: boolean;
}

export interface CandidateSubmitInterviewResponse {
  session_id: string;
  status: "SUBMITTED";
  submitted_at: string;
  total_questions: number;
  answered_questions: number;
  message: string;
}

/**
 * Start or resume an official interview session for a released assignment.
 */
export async function startOrResumeInterview(
  releaseId: string
): Promise<CandidateInterviewSessionResponse> {
  return apiFetch<CandidateInterviewSessionResponse>(
    `/api/v1/candidate/interviews/${releaseId}/start`,
    {
      method: "POST",
    }
  );
}

/**
 * Get an official interview session state and saved answers.
 */
export async function getInterviewSession(
  sessionId: string
): Promise<CandidateInterviewSessionResponse> {
  return apiFetch<CandidateInterviewSessionResponse>(
    `/api/v1/candidate/interviews/sessions/${sessionId}`
  );
}

/**
 * Save or update one answer for an in-progress official interview session.
 */
export async function saveInterviewAnswer(
  sessionId: string,
  slotId: string,
  payload: CandidateSaveAnswerRequest
): Promise<CandidateSaveAnswerResponse> {
  return apiFetch<CandidateSaveAnswerResponse>(
    `/api/v1/candidate/interviews/sessions/${sessionId}/answers/${slotId}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}

export interface CandidateInterviewCapabilityResponse {
  speech_to_text_available: boolean;
  text_to_speech_available: boolean;
  microphone_required: boolean;
  secure_context_required: boolean;
  max_recording_seconds: number;
  max_upload_bytes: number;
  unavailable_reason?: string | null;
  configured_model_label?: string | null;
  model_recommendation_warning?: string | null;
}

export interface CandidateTranscriptionResponse {
  session_id: string;
  slot_id: string;
  transcript: string;
  duration_seconds?: number;
  is_empty: boolean;
}

/**
 * Fetch candidate-safe speech capabilities for audio interviews.
 */
export async function fetchInterviewCapabilities(): Promise<CandidateInterviewCapabilityResponse> {
  return apiFetch<CandidateInterviewCapabilityResponse>(
    `/api/v1/candidate/interviews/capabilities`
  );
}

/**
 * Fetch Piper TTS question audio blob.
 */
export async function fetchQuestionAudioBlob(
  sessionId: string,
  slotId: string
): Promise<Blob> {
  const response = await fetch(
    `/api/v1/candidate/interviews/sessions/${sessionId}/questions/${slotId}/audio`,
    {
      credentials: "include",
    }
  );
  if (!response.ok) {
    let errorMsg = `Audio fetch failed (${response.status})`;
    try {
      const data = await response.json();
      if (data?.detail?.message) errorMsg = data.detail.message;
      else if (typeof data?.detail === "string") errorMsg = data.detail;
    } catch {
      // fallback
    }
    throw new Error(errorMsg);
  }
  return response.blob();
}

/**
 * Upload candidate recording for ephemeral transcription.
 */
export async function transcribeQuestionAudio(
  sessionId: string,
  slotId: string,
  audioBlob: Blob,
  signal?: AbortSignal
): Promise<CandidateTranscriptionResponse> {
  const formData = new FormData();
  const fileExt = audioBlob.type.includes("wav") ? "wav" : audioBlob.type.includes("ogg") ? "ogg" : "webm";
  formData.append("file", audioBlob, `recording.${fileExt}`);

  return apiFetch<CandidateTranscriptionResponse>(
    `/api/v1/candidate/interviews/sessions/${sessionId}/questions/${slotId}/transcribe`,
    {
      method: "POST",
      body: formData,
      signal,
    }
  );
}

/**
 * Submit an official interview session.
 */
export async function submitInterview(
  sessionId: string,
  payload: CandidateSubmitInterviewRequest = {}
): Promise<CandidateSubmitInterviewResponse> {
  return apiFetch<CandidateSubmitInterviewResponse>(
    `/api/v1/candidate/interviews/sessions/${sessionId}/submit`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );
}

