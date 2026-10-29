import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { CandidatePracticeReport } from "../components/candidate/CandidatePracticeReport";
import * as practiceSessionApi from "../api/candidatePracticeSession";

vi.mock("../api/candidatePracticeSession", () => ({
  getPracticeReport: vi.fn(),
  retryPracticeReport: vi.fn(),
}));

const mockCompletedReport: practiceSessionApi.CandidatePracticeReportDto = {
  report_id: "rep-123",
  session_id: "session-123",
  candidate_id: "cand-456",
  job_id: "job-101",
  job_title: "Senior Backend Engineer",
  job_revision: 1,
  report_version: 1,
  schema_version: "practice-coaching-summary.v1",
  lifecycle_status: "COMPLETED",
  aggregation_policy_version: "1.0.0",
  source_review_hash: "hash-123",
  summary_text: "You demonstrated solid knowledge across distributed storage and indexing. Focus on clarifying database connection pool bounds.",
  strongest_areas: [
    {
      title: "Indexing & Query Plans",
      observation: "Accurately highlighted composite indexes and EXPLAIN ANALYZE execution traces.",
      source_review_ids: ["rev-1"],
    },
    {
      title: "Cache Invalidation",
      observation: "Explained write-through cache consistency and TTL eviction trade-offs.",
      source_review_ids: ["rev-2"],
    },
  ],
  priority_improvements: [
    {
      title: "Connection Pooling Tuning",
      observation: "Specify exact sizing formulas based on core count and thread contention.",
      source_review_ids: ["rev-1"],
    },
  ],
  competency_commentary: [
    {
      competency_id: "comp-1",
      commentary: "Deep familiarity with PostgreSQL storage engines and query optimization.",
      source_review_ids: ["rev-1"],
    },
    {
      competency_id: "comp-2",
      commentary: "Good architectural grasp of distributed cache consistency protocols.",
      source_review_ids: ["rev-2"],
    },
    {
      competency_id: "comp-3",
      commentary: "Clear structured explanation of microservices observability and tracing.",
      source_review_ids: ["rev-3"],
    },
  ],
  response_patterns: [
    {
      observation: "Consistently structured answers with problem identification, solution, and outcome.",
      source_review_ids: ["rev-1", "rev-2"],
    },
  ],
  next_practice_plan: [
    "Use a structured problem-action-result format for your answers.",
    "Explain specific verification steps you performed in your technical work.",
    "Practice explaining edge cases and trade-offs for core competencies.",
  ],
  competency_aggregates: [
    {
      competency_name: "PostgreSQL Optimization",
      numeric_score: 4.0,
      anchored_level: "STRONG",
      assessable_count: 2,
      total_questions: 2,
      excluded_count: 0,
      display_band: "Strong (Level 4)",
      description: "Strong demonstrated understanding across all assessed questions.",
    },
    {
      competency_name: "Distributed Caching",
      numeric_score: 3.5,
      anchored_level: "DEVELOPING",
      assessable_count: 2,
      total_questions: 2,
      excluded_count: 0,
      display_band: "Developing (Level 3)",
      description: "Solid conceptual grasp with some minor details to clarify.",
    },
    {
      competency_name: "System Observability",
      numeric_score: 3.0,
      anchored_level: "DEVELOPING",
      assessable_count: 1,
      total_questions: 1,
      excluded_count: 0,
      display_band: "Developing (Level 3)",
      description: "Good foundation demonstrated with room for deeper examples.",
    },
  ],
  radar_eligible: true,
  dimension_aggregates: [
    {
      dimension_key: "relevance",
      dimension_label: "Relevance & Directness",
      numeric_score: 4.0,
      anchored_level: "STRONG",
      assessable_count: 3,
      excluded_count: 0,
      display_label: "Directly addresses core questions with zero deviation.",
    },
    {
      dimension_key: "technical_depth",
      dimension_label: "Technical Depth",
      numeric_score: 3.5,
      anchored_level: "DEVELOPING",
      assessable_count: 3,
      excluded_count: 0,
      display_label: "Strong architectural grasp with minor edge-case gaps.",
    },
  ],
  question_trend: [
    {
      sequence_number: 1,
      question_id: "q-1",
      question_format: "CONVERSATION",
      wording: "How do you optimize slow database queries in PostgreSQL?",
      competency_name: "PostgreSQL Optimization",
      assessability_status: "ASSESSABLE",
      dimension_levels: {
        relevance: "STRONG",
        clarity: "STRONG",
        technical_depth: "STRONG",
      },
      follow_up_completed: true,
      review_id: "rev-1",
    },
    {
      sequence_number: 2,
      question_id: "q-2",
      question_format: "MCQ",
      wording: "Which isolation level prevents phantom reads in standard SQL?",
      competency_name: "PostgreSQL Optimization",
      assessability_status: "ASSESSABLE",
      dimension_levels: {},
      is_mcq_correct: true,
      follow_up_completed: false,
    },
  ],
  trend_classification: "STABLE",
  mcq_breakdown: {
    total_count: 1,
    attempted_count: 1,
    correct_count: 1,
    skipped_count: 0,
    topics: [
      {
        topic_name: "Transaction Isolation",
        attempted_count: 1,
        correct_count: 1,
        learning_points: ["Serializable isolation prevents phantom reads."],
      },
    ],
  },
  completion_counts: {
    total_primary_questions: 2,
    total_conversational_questions: 1,
    total_mcq_questions: 1,
    assessable_answers_count: 2,
    insufficient_answers_count: 0,
    skipped_count: 0,
    unavailable_count: 0,
  },
  disclaimer: "This is practice coaching and not a hiring decision.",
  validation_status: "VALIDATED",
  retry_count: 0,
  is_current: true,
  created_at: new Date().toISOString(),
};

describe("CandidatePracticeReport (Phase 3G.3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads and renders coaching summary narrative and disclaimer", async () => {
    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValue(mockCompletedReport);

    render(
      <CandidatePracticeReport
        sessionId="session-123"
        onExit={() => {}}
      />
    );

    expect(screen.getByText(/Answers complete — preparing your coaching report/i)).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText(/Practice Coaching Report/i)).toBeInTheDocument();
    });

    expect(
      screen.getByText(/You demonstrated solid knowledge across distributed storage and indexing/i)
    ).toBeInTheDocument();
    expect(
      screen.getByText(/This is practice coaching and not a hiring decision\./i)
    ).toBeInTheDocument();
  });

  it("renders demonstrated strengths and priority improvement cards", async () => {
    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValue(mockCompletedReport);

    render(
      <CandidatePracticeReport
        sessionId="session-strengths-123"
        onExit={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Indexing & Query Plans/i)).toBeInTheDocument();
      expect(screen.getByText(/Connection Pooling Tuning/i)).toBeInTheDocument();
    });
  });

  it("renders the SVG radar chart when 3 or more competencies are assessable", async () => {
    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValue(mockCompletedReport);

    render(
      <CandidatePracticeReport
        sessionId="session-radar-123"
        onExit={() => {}}
      />
    );

    const radar = await screen.findByTestId("competency-radar-svg");
    expect(radar).toBeInTheDocument();
    const matches = screen.getAllByText(/PostgreSQL Optimization/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it("renders question trend strip and expands question review details on click", async () => {
    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValue(mockCompletedReport);

    render(
      <CandidatePracticeReport
        sessionId="session-123"
        onExit={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Question 1/i)).toBeInTheDocument();
      expect(screen.getByText(/Question 2/i)).toBeInTheDocument();
    });

    // Expand Q1 review details
    const question1Item = screen.getByText(/How do you optimize slow database queries in PostgreSQL/i);
    fireEvent.click(question1Item);

    await waitFor(() => {
      expect(screen.getByText(/Evaluated Dimensions/i)).toBeInTheDocument();
      expect(screen.getByText(/relevance: STRONG/i)).toBeInTheDocument();
    });
  });

  it("renders MCQ topic performance breakdown", async () => {
    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValue(mockCompletedReport);

    render(
      <CandidatePracticeReport
        sessionId="session-123"
        onExit={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Multiple-Choice Knowledge Checks/i)).toBeInTheDocument();
      expect(screen.getByText("Transaction Isolation")).toBeInTheDocument();
      expect(screen.getByText("1 / 1 correct")).toBeInTheDocument();
    });
  });

  it("shows retry button on summary generation failure while preserving deterministic data", async () => {
    const failedReport: practiceSessionApi.CandidatePracticeReportDto = {
      ...mockCompletedReport,
      lifecycle_status: "SUMMARY_FAILED_RETRYABLE",
      summary_text: "",
    };

    vi.mocked(practiceSessionApi.getPracticeReport).mockResolvedValueOnce(failedReport);
    vi.mocked(practiceSessionApi.retryPracticeReport).mockResolvedValueOnce({
      ...mockCompletedReport,
      lifecycle_status: "COMPLETED",
    });

    render(
      <CandidatePracticeReport
        sessionId="session-123"
        onExit={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Summary generation temporarily unavailable/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Retry Summary/i })).toBeInTheDocument();
    });

    // Click retry
    fireEvent.click(screen.getByRole("button", { name: /Retry Summary/i }));

    await waitFor(() => {
      expect(practiceSessionApi.retryPracticeReport).toHaveBeenCalledWith("session-123");
    });
  });
});
