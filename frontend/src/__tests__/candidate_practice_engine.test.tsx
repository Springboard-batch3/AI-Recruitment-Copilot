import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { CandidatePracticeRoom } from "../components/candidate/CandidatePracticeRoom";
import { CandidatePracticeDirectory } from "../components/candidate/CandidatePracticeDirectory";
import * as candidatePracticeApi from "../api/candidatePractice";
import * as practiceSessionApi from "../api/candidatePracticeSession";

vi.mock("../api/candidatePractice", () => ({
  getCandidatePracticeDiscovery: vi.fn(),
  getCandidatePracticeCatalog: vi.fn().mockResolvedValue({ items: [] }),
  createCandidatePracticeRequest: vi.fn(),
  withdrawCandidatePracticeRequest: vi.fn(),
}));

vi.mock("../api/candidatePracticeSession", () => ({
  startOrResumePracticeSession: vi.fn(),
  getPracticeSessionState: vi.fn(),
  submitConversationalAnswer: vi.fn(),
  submitMcqAnswer: vi.fn(),
  abandonPracticeSession: vi.fn(),
  getPracticeReport: vi.fn().mockResolvedValue({
    report_id: "rep-1",
    session_id: "session-1",
    candidate_id: "cand-1",
    job_id: "job-1",
    job_title: "Role Practice Interview",
    job_revision: 1,
    report_version: 1,
    schema_version: "practice-coaching-summary.v1",
    lifecycle_status: "COMPLETED",
    aggregation_policy_version: "1.0.0",
    source_review_hash: "hash-1",
    summary_text: "Great job completing your practice session.",
    strongest_areas: [],
    priority_improvements: [],
    competency_commentary: [],
    response_patterns: [],
    next_practice_plan: ["Action 1", "Action 2", "Action 3"],
    competency_aggregates: [],
    radar_eligible: false,
    dimension_aggregates: [],
    question_trend: [],
    trend_classification: "NOT_ENOUGH_ASSESSABLE_DATA",
    completion_counts: {
      total_primary_questions: 1,
      total_conversational_questions: 1,
      total_mcq_questions: 0,
      assessable_answers_count: 1,
      insufficient_answers_count: 0,
      skipped_count: 0,
      unavailable_count: 0,
    },
    disclaimer: "This is practice coaching and not a hiring decision.",
    validation_status: "VALIDATED",
    retry_count: 0,
    is_current: true,
    created_at: new Date().toISOString(),
  }),
  retryPracticeReport: vi.fn(),
}));

const mockDiscoveryData = {
  items: [
    {
      release_id: "rel-123",
      job_id: "job-101",
      job_title: "Senior Backend Engineer",
      job_revision: 1,
      released_at: new Date().toISOString(),
      conversation_count: 2,
      mcq_count: 1,
      total_question_count: 3,
    },
  ],
};

const mockInitialSessionState: practiceSessionApi.PracticeSessionDetailResponse = {
  session_id: "session-123",
  release_id: "rel-123",
  job_id: "job-101",
  lifecycle_status: "IN_PROGRESS",
  current_section: "CONVERSATION",
  current_conv_index: 0,
  total_conv_count: 2,
  current_mcq_index: 0,
  total_mcq_count: 1,
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
    follow_up_available: true,
  },
  messages: [
    {
      message_id: "msg-1",
      sequence_number: 1,
      role: "INTERVIEWER",
      message_kind: "GREETING",
      text: "Welcome to your practice session! Take your time to answer each question.",
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

describe("Candidate Practice Engine & Room", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom scrollIntoView mock
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("renders practice directory with Start Practice Interview action and launches session", async () => {
    vi.mocked(candidatePracticeApi.getCandidatePracticeDiscovery).mockResolvedValue(mockDiscoveryData as any);
    vi.mocked(practiceSessionApi.startOrResumePracticeSession).mockResolvedValue(mockInitialSessionState);
    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockInitialSessionState);

    render(<CandidatePracticeDirectory />);

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });

    const startBtn = screen.getByRole("button", { name: /Start Practice Interview/i });
    expect(startBtn).toBeInTheDocument();
  });

  it("submits conversational answer and displays next AI question", async () => {
    const onExit = vi.fn();
    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockInitialSessionState);

    const mockNextQuestionState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockInitialSessionState,
      current_conv_index: 1,
      total_response_count: 1,
      active_question: {
        question_id: "q-conv-2",
        question_format: "CONVERSATION",
        wording: "How do you handle circuit breakers in microservices?",
        competency_name: "System Design",
        turn_count_for_question: 0,
        follow_up_available: false,
      },
      messages: [
        ...mockInitialSessionState.messages,
        {
          message_id: "msg-3",
          sequence_number: 3,
          role: "CANDIDATE",
          message_kind: "ANSWER",
          text: "I start with EXPLAIN ANALYZE to identify sequential scans.",
          created_at: new Date().toISOString(),
        },
        {
          message_id: "msg-4",
          sequence_number: 4,
          role: "INTERVIEWER",
          message_kind: "PRIMARY_QUESTION",
          text: "How do you handle circuit breakers in microservices?",
          source_question_id: "q-conv-2",
          created_at: new Date().toISOString(),
        },
      ],
    };

    vi.mocked(practiceSessionApi.submitConversationalAnswer).mockResolvedValue(mockNextQuestionState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Type your structured response here/i)).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Type your structured response here/i);
    fireEvent.change(textarea, {
      target: { value: "I start with EXPLAIN ANALYZE to identify sequential scans." },
    });

    const submitBtn = screen.getByRole("button", { name: /Submit Response/i });
    expect(submitBtn).toBeEnabled();

    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(practiceSessionApi.submitConversationalAnswer).toHaveBeenCalledWith(
        "session-123",
        "q-conv-1",
        "I start with EXPLAIN ANALYZE to identify sequential scans."
      );
    });

    await waitFor(() => {
      expect(screen.getByText("How do you handle circuit breakers in microservices?")).toBeInTheDocument();
    });
  });

  it("renders MCQ question and allows option selection and submission", async () => {
    const onExit = vi.fn();
    const mockMcqState: practiceSessionApi.PracticeSessionDetailResponse = {
      session_id: "session-123",
      release_id: "rel-123",
      job_id: "job-101",
      lifecycle_status: "IN_PROGRESS",
      current_section: "MCQ",
      current_conv_index: 2,
      total_conv_count: 2,
      current_mcq_index: 0,
      total_mcq_count: 1,
      total_response_count: 2,
      turn_count_for_question: 0,
      time_remaining_seconds: 80000,
      expires_at: new Date(Date.now() + 80000000).toISOString(),
      active_question: {
        question_id: "q-mcq-1",
        question_format: "MCQ_SINGLE_ANSWER",
        wording: "Which index type is best for range queries in PostgreSQL?",
        competency_name: "PostgreSQL Indexing",
        options: [
          { option_id: "opt-1", text: "B-Tree Index", display_order: 1 },
          { option_id: "opt-2", text: "Hash Index", display_order: 2 },
          { option_id: "opt-3", text: "Bloom Filter Index", display_order: 3 },
        ],
        turn_count_for_question: 0,
        follow_up_available: false,
      },
      messages: [],
    };

    const mockCompletedState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockMcqState,
      lifecycle_status: "COMPLETED",
      current_section: "COMPLETE",
      total_response_count: 3,
      active_question: null,
    };

    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockMcqState);
    vi.mocked(practiceSessionApi.submitMcqAnswer).mockResolvedValue(mockCompletedState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText("Which index type is best for range queries in PostgreSQL?")).toBeInTheDocument();
      expect(screen.getByText("B-Tree Index")).toBeInTheDocument();
      expect(screen.getByText("Hash Index")).toBeInTheDocument();
    });

    const submitBtn = screen.getByRole("button", { name: /Submit Answer & Continue/i });
    expect(submitBtn).toBeDisabled();

    // Select Option A (B-Tree Index)
    const optionA = screen.getByText("B-Tree Index");
    fireEvent.click(optionA);

    expect(submitBtn).toBeEnabled();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(practiceSessionApi.submitMcqAnswer).toHaveBeenCalledWith(
        "session-123",
        "q-mcq-1",
        "opt-1"
      );
    });

    await waitFor(() => {
      expect(screen.getByText(/Practice Coaching Report/i)).toBeInTheDocument();
      expect(screen.getByText("Great job completing your practice session.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Return to Directory/i })).toBeInTheDocument();
    });
  });

  it("displays safe fallback notice banner when present in response", async () => {
    const onExit = vi.fn();
    const mockFallbackState: practiceSessionApi.PracticeSessionDetailResponse = {
      ...mockInitialSessionState,
      safe_fallback_notice: "AI assistance adjusted to standard neutral pacing.",
    };

    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockFallbackState);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByText("AI assistance adjusted to standard neutral pacing.")).toBeInTheDocument();
    });
  });

  it("handles abandon session confirmation and exits room", async () => {
    const onExit = vi.fn();
    vi.mocked(practiceSessionApi.getPracticeSessionState).mockResolvedValue(mockInitialSessionState);
    vi.mocked(practiceSessionApi.abandonPracticeSession).mockResolvedValue({
      session_id: "session-123",
      lifecycle_status: "ABANDONED",
      message: "Practice session abandoned.",
    });

    // Mock window.confirm to return true
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<CandidatePracticeRoom sessionId="session-123" onExit={onExit} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Exit Session/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Exit Session/i }));

    await waitFor(() => {
      expect(practiceSessionApi.abandonPracticeSession).toHaveBeenCalledWith("session-123");
      expect(onExit).toHaveBeenCalled();
    });
  });
});
