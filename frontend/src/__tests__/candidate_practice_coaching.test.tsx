import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { CandidatePracticeRoom } from "../components/candidate/CandidatePracticeRoom";
import * as practiceSessionApi from "../api/candidatePracticeSession";

vi.mock("../api/candidatePracticeSession", () => ({
  getPracticeSessionState: vi.fn(),
  submitConversationalAnswer: vi.fn(),
  submitMcqAnswer: vi.fn(),
  retryQuestionGeneration: vi.fn(),
  retryFinalReview: vi.fn(),
  abandonPracticeSession: vi.fn(),
  getPracticeReport: vi.fn().mockResolvedValue({
    report_id: "rep-1",
    session_id: "session-123",
    candidate_id: "cand-1",
    job_id: "job-101",
    job_title: "Role Practice Interview",
    job_revision: 1,
    report_version: 1,
    schema_version: "live-interview-final-review.v1",
    lifecycle_status: "SUMMARY_READY",
    aggregation_policy_version: "1.0.0",
    source_review_hash: "hash-1",
    summary_text: "Solid demonstration of Python and database performance concepts.",
    strongest_areas: [
      {
        title: "Question 1",
        observation: "Detailed explanation of EXPLAIN ANALYZE.",
        source_review_ids: ["q-conv-1"],
      },
    ],
    priority_improvements: [
      {
        title: "Question 2",
        observation: "Lacked connection pool eviction details.",
        source_review_ids: ["q-conv-2"],
      },
    ],
    competency_commentary: [],
    response_patterns: [],
    next_practice_plan: [
      "Review PostgreSQL vacuum configurations.",
      "Practice distributed tracing scenarios.",
      "Deep-dive async database transaction rollbacks.",
    ],
    competency_aggregates: [],
    radar_eligible: false,
    dimension_aggregates: [],
    question_trend: [],
    trend_classification: "STABLE",
    completion_counts: {
      total_primary_questions: 2,
      total_conversational_questions: 2,
      total_mcq_questions: 0,
      assessable_answers_count: 2,
      insufficient_answers_count: 0,
      skipped_count: 0,
      unavailable_count: 0,
    },
    disclaimer: "This is practice coaching and not a hiring decision.",
    validation_status: "PASSED",
    retry_count: 0,
    is_current: true,
    created_at: new Date().toISOString(),
  }),
}));

const mockInitialSessionState: practiceSessionApi.PracticeSessionDetailResponse = {
  session_id: "session-123",
  release_id: "rel-123",
  job_id: "job-101",
  lifecycle_status: "AWAITING_ANSWER",
  current_section: "CONVERSATION",
  current_conv_index: 0,
  total_conv_count: 2,
  current_mcq_index: 0,
  total_mcq_count: 0,
  total_response_count: 0,
  turn_count_for_question: 0,
  time_remaining_seconds: 86400,
  expires_at: new Date(Date.now() + 86400000).toISOString(),
  active_question: {
    question_id: "q-conv-1",
    question_format: "CONVERSATION",
    wording: "How do you optimize slow database queries in PostgreSQL?",
    competency_name: "PostgreSQL Optimization",
    difficulty: "INTERMEDIATE",
    turn_count_for_question: 0,
    follow_up_available: false,
  },
  messages: [
    {
      message_id: "msg-1",
      sequence_number: 1,
      role: "INTERVIEWER",
      message_kind: "GREETING",
      text: "Welcome to your practice interview session.",
      created_at: new Date().toISOString(),
    },
    {
      message_id: "msg-2",
      sequence_number: 2,
      role: "INTERVIEWER",
      message_kind: "PRIMARY_QUESTION",
      text: "How do you optimize slow database queries in PostgreSQL?",
      source_question_id: "q-conv-1",
      created_at: new Date().toISOString(),
    },
  ],
};

describe("Candidate Live Sequential AI Interview Room (Phase 3G Architecture Correction)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("removes in-session coaching review panel, review badges, and follow-up controls during active interview", async () => {
    const onExit = vi.fn();
    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockInitialSessionState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText("How do you optimize slow database queries in PostgreSQL?")).toBeInTheDocument();
    });

    // Verify removed elements from Section L and V:
    expect(screen.queryByText(/AI Coaching Review/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Review Unavailable/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Retry Review/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Follow-up Question/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Final turn on this question/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/PRIMARY PROMPT/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Continue to Next Question/i })).not.toBeInTheDocument();
  });

  it("displays Question 1 only after successful generation and shows composer only when awaiting answer", async () => {
    const onExit = vi.fn();
    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockInitialSessionState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText("AI-generated question")).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/Type your structured response here/i)).toBeInTheDocument();
    });
  });

  it("shows retry banner with 'Retry Question Generation' on QUESTION_GENERATION_FAILED_RETRYABLE", async () => {
    const onExit = vi.fn();
    const failedState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockInitialSessionState,
      lifecycle_status: "QUESTION_GENERATION_FAILED_RETRYABLE",
      active_question: null,
      messages: [mockInitialSessionState.messages[0]], // only greeting
    };

    const recoveredState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockInitialSessionState,
      lifecycle_status: "AWAITING_ANSWER",
    };

    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(failedState);
    vi.mocked(practiceSessionApi.retryQuestionGeneration).mockResolvedValue(recoveredState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText("The next question could not be generated.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Retry Question Generation/i })).toBeInTheDocument();
    });

    // Candidate clicks retry
    fireEvent.click(screen.getByRole("button", { name: /Retry Question Generation/i }));

    await waitFor(() => {
      expect(practiceSessionApi.retryQuestionGeneration).toHaveBeenCalledWith("session-123");
    });
  });

  it("renders final comprehensive coaching report once session reaches COMPLETED", async () => {
    const onExit = vi.fn();
    const completedState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockInitialSessionState,
      lifecycle_status: "COMPLETED",
      active_question: null,
    };

    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(completedState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText(/Practice Coaching Report/i)).toBeInTheDocument();
      expect(screen.getByText("Solid demonstration of Python and database performance concepts.")).toBeInTheDocument();
      expect(screen.getByText("Review PostgreSQL vacuum configurations.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Return to Directory/i })).toBeInTheDocument();
    });
  });
});
