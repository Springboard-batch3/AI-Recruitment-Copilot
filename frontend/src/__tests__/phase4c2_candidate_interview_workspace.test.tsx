import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { CandidateInterviewWorkspace } from "../components/candidate/CandidateInterviewWorkspace";
import { CandidatePracticeDirectory } from "../components/candidate/CandidatePracticeDirectory";
import * as candidateInterviewsApi from "../api/candidateInterviews";
import * as candidatePracticeApi from "../api/candidatePractice";

vi.mock("../api/candidateInterviews");
vi.mock("../api/candidatePractice");

describe("Phase 4C.2: Candidate Interview Workspace & Persistence", () => {
  const mockSessionInProgress: candidateInterviewsApi.CandidateInterviewSessionResponse = {
    session_id: "sess-101",
    assignment_id: "rel-101",
    job_id: "job-101",
    job_title: "Senior Cloud Engineer",
    job_revision: 2,
    status: "IN_PROGRESS",
    started_at: "2026-09-27T10:00:00Z",
    last_saved_at: "2026-09-27T10:05:00Z",
    submitted_at: null,
    total_questions: 2,
    answered_questions: 1,
    questions: [
      {
        slot_id: "slot-1",
        order: 1,
        question_format: "CONVERSATIONAL",
        question_text: "How do you design for high availability in distributed architectures?",
        options: [],
        is_supplemental: false,
        saved_text_answer: "I use multi-region active-active deployments with automated failover.",
        saved_selected_option_id: null,
        is_answered: true,
        saved_at: "2026-09-27T10:05:00Z",
      },
      {
        slot_id: "slot-2",
        order: 2,
        question_format: "MCQ",
        question_text: "Which protocol operates at the Transport Layer?",
        options: [
          { option_id: "opt-tcp", label: "TCP" },
          { option_id: "opt-http", label: "HTTP" },
          { option_id: "opt-ip", label: "IP" },
          { option_id: "opt-dns", label: "DNS" },
        ],
        is_supplemental: true,
        saved_text_answer: null,
        saved_selected_option_id: null,
        is_answered: false,
        saved_at: null,
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // 1. Landing state and Begin/Resume behavior
  it("renders landing state with job title, question count, and resume action", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);

    expect(await screen.findByText("Senior Cloud Engineer")).toBeInTheDocument();
    expect(screen.getByText("Job Revision #2")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 answered")).toBeInTheDocument();
    expect(screen.getByText("Resume Interview")).toBeInTheDocument();

    // Click Resume Interview to enter questions
    fireEvent.click(screen.getByText("Resume Interview"));
    expect(await screen.findByText(/Question 2 of 2/i)).toBeInTheDocument();
  });

  // 2. Text answer saving and visible save state
  it("persists text answer and displays saved confirmation", async () => {
    const unstartedSession: candidateInterviewsApi.CandidateInterviewSessionResponse = {
      ...mockSessionInProgress,
      answered_questions: 0,
      questions: [
        { ...mockSessionInProgress.questions[0], is_answered: false, saved_text_answer: null },
        mockSessionInProgress.questions[1],
      ],
    };
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(unstartedSession);
    vi.mocked(candidateInterviewsApi.saveInterviewAnswer).mockResolvedValueOnce({
      session_id: "sess-101",
      slot_id: "slot-1",
      saved_at: "2026-09-27T10:10:00Z",
      is_answered: true,
      answered_questions: 1,
      total_questions: 2,
    });

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);

    // Go to workspace
    fireEvent.click(await screen.findByText("Begin Interview"));

    const textarea = await screen.findByPlaceholderText("Type your response here...");
    fireEvent.change(textarea, { target: { value: "Updated distributed design answer." } });

    const saveBtn = screen.getByRole("button", { name: "Save answer" });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(candidateInterviewsApi.saveInterviewAnswer).toHaveBeenCalledWith(
        "sess-101",
        "slot-1",
        expect.objectContaining({
          text_answer: "Updated distributed design answer.",
        })
      );
    });
    expect(await screen.findByText(/Saved/i)).toBeInTheDocument();
  });

  // 3. MCQ selection persistence
  it("persists MCQ selection upon radio option change and continue", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);
    vi.mocked(candidateInterviewsApi.saveInterviewAnswer).mockResolvedValueOnce({
      session_id: "sess-101",
      slot_id: "slot-2",
      saved_at: "2026-09-27T10:12:00Z",
      is_answered: true,
      answered_questions: 2,
      total_questions: 2,
    });

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);
    fireEvent.click(await screen.findByText("Resume Interview"));

    // Question 2 (MCQ) is active because Question 1 is already answered
    expect(await screen.findByText("Which protocol operates at the Transport Layer?")).toBeInTheDocument();
    expect(screen.getByText("Supplemental Question")).toBeInTheDocument();

    const tcpRadio = screen.getByLabelText("TCP");
    fireEvent.click(tcpRadio);

    // Save and review
    const saveReviewBtn = screen.getByRole("button", { name: "Save & Review answers" });
    fireEvent.click(saveReviewBtn);

    expect(candidateInterviewsApi.saveInterviewAnswer).toHaveBeenCalledWith(
      "sess-101",
      "slot-2",
      expect.objectContaining({
        selected_option_id: "opt-tcp",
      })
    );
  });

  // 4. Restoring saved answers on remount / refetch & 5. Progress calculation
  it("restores saved answers and accurate progress from server response", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);

    // Review answers
    fireEvent.click(await screen.findByText("Resume Interview"));
    const reviewBtn = await screen.findByRole("button", { name: /Review all/i });
    fireEvent.click(reviewBtn);

    expect(await screen.findByText("Review Interview Answers")).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 questions remain unanswered/i)).toBeInTheDocument();
  });

  // 6. Review screen and edit navigation
  it("allows navigating back from review screen to edit a specific question", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);
    fireEvent.click(await screen.findByText("Resume Interview"));
    fireEvent.click(await screen.findByRole("button", { name: /Review all/i }));

    const editBtns = await screen.findAllByRole("button", { name: "Edit" });
    expect(editBtns.length).toBe(2);

    // Click edit on Question 1
    fireEvent.click(editBtns[0]);
    expect(await screen.findByText("How do you design for high availability in distributed architectures?")).toBeInTheDocument();
  });

  // 7. Incomplete submission confirmation
  it("prompts for explicit confirmation when attempting incomplete submission", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);
    vi.mocked(candidateInterviewsApi.submitInterview).mockResolvedValueOnce({
      session_id: "sess-101",
      status: "SUBMITTED",
      submitted_at: "2026-09-27T10:15:00Z",
      total_questions: 2,
      answered_questions: 1,
      message: "Submitted incomplete with candidate confirmation.",
    });

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);
    fireEvent.click(await screen.findByText("Resume Interview"));
    fireEvent.click(await screen.findByRole("button", { name: /Review all/i }));

    // Click Submit Interview
    const submitBtn = await screen.findByRole("button", { name: "Submit Interview" });
    fireEvent.click(submitBtn);

    // Expect modal confirmation
    expect(await screen.findByText("Incomplete Interview")).toBeInTheDocument();
    expect(screen.getByText(/unanswered question/i)).toBeInTheDocument();

    // Confirm submission
    const confirmBtn = screen.getByRole("button", { name: "Yes, Submit Incomplete" });
    fireEvent.click(confirmBtn);

    expect(candidateInterviewsApi.submitInterview).toHaveBeenCalledWith("sess-101", {
      confirmed_incomplete: true,
    });
  });

  // 8. Submitted receipt and disabled editing
  it("renders locked submitted receipt after submission", async () => {
    const submittedSession: candidateInterviewsApi.CandidateInterviewSessionResponse = {
      ...mockSessionInProgress,
      status: "SUBMITTED",
      submitted_at: "2026-09-27T10:20:00Z",
      answered_questions: 2,
    };
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(submittedSession);

    const onExitMock = vi.fn();
    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={onExitMock} />);

    expect(await screen.findByText("Official Submission Receipt")).toBeInTheDocument();
    expect(screen.getByText("Interview Submitted Successfully")).toBeInTheDocument();
    expect(screen.getByText("Return to Interviews")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Return to Interviews"));
    expect(onExitMock).toHaveBeenCalled();
  });

  // 9. Candidate-safe rendering without rubrics, correct answers, or internal scores
  it("strictly omits rubrics, correct options, and scores in candidate view", async () => {
    vi.mocked(candidateInterviewsApi.startOrResumeInterview).mockResolvedValueOnce(mockSessionInProgress);

    render(<CandidateInterviewWorkspace releaseId="rel-101" onExit={vi.fn()} />);
    fireEvent.click(await screen.findByText("Resume Interview"));

    const html = document.body.innerHTML;
    expect(html).not.toContain("rubric");
    expect(html).not.toContain("correct_option_id");
    expect(html).not.toContain("score_decomposition");
    expect(html).not.toContain("authoring_origin");
  });
});

