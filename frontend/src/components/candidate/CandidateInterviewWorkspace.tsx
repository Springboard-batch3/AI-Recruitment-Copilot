import React, { useEffect, useState, useRef } from "react";
import {
  CandidateInterviewCapabilityResponse,
  CandidateInterviewQuestionDto,
  CandidateInterviewSessionResponse,
  fetchInterviewCapabilities,
  fetchQuestionAudioBlob,
  getInterviewSession,
  saveInterviewAnswer,
  startOrResumeInterview,
  submitInterview,
  transcribeQuestionAudio,
} from "../../api/candidateInterviews";
import { Icon } from "../Icon";
import { VoiceAnalogDisplay } from "./VoiceAnalogDisplay";

interface CandidateInterviewWorkspaceProps {
  releaseId: string;
  initialMode?: "TEXT" | "AUDIO";
  jobTitle?: string;
  jobRevision?: number;
  conversationCount?: number;
  onExit: () => void;
}

type WorkspaceView = "landing" | "question" | "review" | "submitted" | "audio_prep";
type SaveState = "idle" | "saving" | "saved" | "error";

export function CandidateInterviewWorkspace({
  releaseId,
  initialMode,
  jobTitle,
  jobRevision,
  conversationCount,
  onExit,
}: CandidateInterviewWorkspaceProps) {
  const [session, setSession] = useState<CandidateInterviewSessionResponse | null>(null);
  const [currentView, setCurrentView] = useState<WorkspaceView>(initialMode === "AUDIO" ? "audio_prep" : "landing");
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(initialMode !== "AUDIO");
  const [error, setError] = useState<string | null>(null);

  // Edit buffer state for Text mode questions
  const [textDraft, setTextDraft] = useState<string>("");
  const [mcqDraft, setMcqDraft] = useState<string>("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [lastSavedTimestamp, setLastSavedTimestamp] = useState<string | null>(null);

  // Incomplete confirmation dialog state
  const [showIncompleteModal, setShowIncompleteModal] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitReceipt, setSubmitReceipt] = useState<{
    submittedAt: string;
    message: string;
  } | null>(null);

  // -------------------------------------------------------------
  // Phase 4D.3: Audio Interview Execution State
  // -------------------------------------------------------------
  const [capabilities, setCapabilities] = useState<CandidateInterviewCapabilityResponse | null>(null);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState<boolean>(false);
  const [micAcknowledged, setMicAcknowledged] = useState<boolean>(false);
  const [isStartingAudio, setIsStartingAudio] = useState<boolean>(false);

  // Audio Question Playback
  const [isPlayingQuestion, setIsPlayingQuestion] = useState<boolean>(false);
  const [questionAudioUrl, setQuestionAudioUrl] = useState<string | null>(null);
  const [isFetchingQuestionAudio, setIsFetchingQuestionAudio] = useState<boolean>(false);
  const [questionAudioError, setQuestionAudioError] = useState<string | null>(null);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1.0);
  const audioPlayerRef = useRef<HTMLAudioElement | null>(null);

  // Candidate Recording & Ephemeral STT
  // States: 'READY' | 'RECORDING' | 'PROCESSING' | 'REVIEW' | 'ACCEPTED' | 'ERROR'
  const [audioState, setAudioState] = useState<"READY" | "RECORDING" | "PROCESSING" | "REVIEW" | "ACCEPTED" | "ERROR">("READY");
  const [recordingSeconds, setRecordingSeconds] = useState<number>(0);
  const [transcriptDraft, setTranscriptDraft] = useState<string | null>(null);
  const [audioRecordingError, setAudioRecordingError] = useState<string | null>(null);
  const [activeMediaStream, setActiveMediaStream] = useState<MediaStream | null>(null);

  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<any>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  const activeRequestIdRef = useRef<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const activeQuestion: CandidateInterviewQuestionDto | undefined =
    session?.questions?.[currentQuestionIndex];

  const isAudioSession = (session?.interview_mode || initialMode || "TEXT").toUpperCase() === "AUDIO";

  useEffect(() => {
    const rolePrefix = jobTitle ? `${jobTitle} — ` : "";
    document.title = `${rolePrefix}AI Interview — Candidate Portal | Smart Hiring`;
  }, [jobTitle]);

  // Cleanup helper for media stream tracks and object URLs
  const stopAllRecordingTracks = () => {
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }
    setActiveMediaStream(null);
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  };

  const stopQuestionAudio = () => {
    if (audioPlayerRef.current) {
      audioPlayerRef.current.pause();
      audioPlayerRef.current.currentTime = 0;
    }
    setIsPlayingQuestion(false);
  };

  // Sync draft buffers whenever currentQuestion changes
  useEffect(() => {
    if (activeQuestion) {
      setTextDraft(activeQuestion.saved_text_answer || "");
      setMcqDraft(activeQuestion.saved_selected_option_id || "");
      setTranscriptDraft(null);
      setAudioRecordingError(null);
      setQuestionAudioError(null);
      setSaveState("idle");
      stopQuestionAudio();
      stopAllRecordingTracks();
      setRecordingSeconds(0);
      recordedChunksRef.current = [];

      // Cancel any ongoing transcription request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      activeRequestIdRef.current = null;

      // Initialize audioState based on whether question is already answered
      if (activeQuestion.is_answered && activeQuestion.saved_text_answer) {
        setAudioState("ACCEPTED");
      } else {
        setAudioState("READY");
      }

      if (questionAudioUrl) {
        URL.revokeObjectURL(questionAudioUrl);
        setQuestionAudioUrl(null);
      }
    }
  }, [currentQuestionIndex, activeQuestion?.slot_id]);

  // Teardown on unmount
  useEffect(() => {
    return () => {
      stopAllRecordingTracks();
      stopQuestionAudio();
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  // Initial load / fetch
  useEffect(() => {
    let isMounted = true;
    async function init() {
      if (initialMode === "AUDIO") {
        setCapabilitiesLoading(true);
        try {
          const caps = await fetchInterviewCapabilities();
          if (isMounted) setCapabilities(caps);
        } catch {
          // ignore or fallback
        } finally {
          if (isMounted) {
            setCapabilitiesLoading(false);
            setIsLoading(false);
          }
        }
        return;
      }

      try {
        setIsLoading(true);
        setError(null);
        const data = await startOrResumeInterview(releaseId);
        if (!isMounted) return;
        setSession(data);
        if (data.interview_mode === "AUDIO") {
          setCurrentView("audio_prep");
          try {
            const caps = await fetchInterviewCapabilities();
            if (isMounted) setCapabilities(caps);
          } catch {
            // ignore
          }
        } else if (data.status === "SUBMITTED") {
          setCurrentView("submitted");
          setSubmitReceipt({
            submittedAt: data.submitted_at || new Date().toISOString(),
            message: "Your interview responses have been submitted.",
          });
        } else {
          const firstUnansweredIndex = data.questions.findIndex((q) => !q.is_answered);
          if (firstUnansweredIndex >= 0) {
            setCurrentQuestionIndex(firstUnansweredIndex);
          } else {
            setCurrentQuestionIndex(0);
          }
        }
      } catch (err: any) {
        if (!isMounted) return;
        if (err?.code === "AUDIO_INTERVIEW_NOT_READY" || err?.message?.includes("Audio interview setup")) {
          setCurrentView("audio_prep");
        } else {
          setError(err?.message || "Failed to start or resume interview.");
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }
    init();
    return () => {
      isMounted = false;
    };
  }, [releaseId, initialMode]);

  // Handle modal keyboard trap / escape
  useEffect(() => {
    if (showIncompleteModal && modalRef.current) {
      modalRef.current.focus();
    }
  }, [showIncompleteModal]);

  const handleKeyDownModal = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setShowIncompleteModal(false);
    }
  };

  const isConversationalQuestion = (q?: CandidateInterviewQuestionDto): boolean => {
    if (!q) return true;
    const fmt = (q.question_format || "").toUpperCase();
    if (fmt === "MCQ" || fmt === "MCQ_SINGLE_ANSWER") return false;
    if (q.options && q.options.length > 0) return false;
    return true;
  };

  const handleSaveAnswer = async (
    customText?: string,
    customMcq?: string
  ): Promise<boolean> => {
    if (!session || !activeQuestion || session.status === "SUBMITTED") return false;

    const t = customText !== undefined ? customText : textDraft;
    const m = customMcq !== undefined ? customMcq : mcqDraft;
    const isConv = isConversationalQuestion(activeQuestion);

    const payload = {
      text_answer: isConv ? t : undefined,
      selected_option_id: !isConv ? m : undefined,
    };

    try {
      setSaveState("saving");
      const res = await saveInterviewAnswer(session.session_id, activeQuestion.slot_id, payload);
      setSaveState("saved");
      setLastSavedTimestamp(new Date(res.saved_at).toLocaleTimeString());

      setSession((prev) => {
        if (!prev) return null;
        const updatedQuestions = prev.questions.map((q) => {
          if (q.slot_id === activeQuestion.slot_id) {
            return {
              ...q,
              saved_text_answer: payload.text_answer ?? null,
              saved_selected_option_id: payload.selected_option_id ?? null,
              is_answered: res.is_answered,
              saved_at: res.saved_at,
            };
          }
          return q;
        });
        return {
          ...prev,
          answered_questions: res.answered_questions,
          last_saved_at: res.saved_at,
          questions: updatedQuestions,
        };
      });
      return true;
    } catch (err: any) {
      setSaveState("error");
      setError(err?.message || "Could not save answer. Please retry.");
      return false;
    }
  };

  const handleSaveAndContinue = async () => {
    if (!session) return;
    if (!isAudioSession) {
      await handleSaveAnswer();
    }
    if (currentQuestionIndex < session.questions.length - 1) {
      setCurrentQuestionIndex((prev) => prev + 1);
    } else {
      setCurrentView("review");
    }
  };

  const handlePrevious = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex((prev) => prev - 1);
    }
  };

  const handleGoToQuestion = (index: number) => {
    setCurrentQuestionIndex(index);
    setCurrentView("question");
  };

  const handleReviewClick = async () => {
    if (activeQuestion && !isAudioSession) {
      const isConv = isConversationalQuestion(activeQuestion);
      if (
        (isConv && textDraft !== (activeQuestion.saved_text_answer || "")) ||
        (!isConv && mcqDraft !== (activeQuestion.saved_selected_option_id || ""))
      ) {
        await handleSaveAnswer();
      }
    }
    setCurrentView("review");
  };

  const executeFinalSubmit = async (confirmedIncomplete: boolean = false) => {
    if (!session) return;
    try {
      setIsSubmitting(true);
      setError(null);
      const res = await submitInterview(session.session_id, {
        confirmed_incomplete: confirmedIncomplete,
      });
      setSubmitReceipt({
        submittedAt: res.submitted_at,
        message: res.message,
      });
      setSession((prev) => (prev ? { ...prev, status: "SUBMITTED", submitted_at: res.submitted_at } : null));
      setShowIncompleteModal(false);
      setCurrentView("submitted");
    } catch (err: any) {
      setError(err?.message || "Failed to submit interview.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSubmitAttempt = () => {
    if (!session) return;
    const unansweredCount = session.total_questions - session.answered_questions;
    if (unansweredCount > 0) {
      setShowIncompleteModal(true);
    } else {
      executeFinalSubmit(false);
    }
  };

  // -------------------------------------------------------------
  // Audio Playback & Recording Handlers
  // -------------------------------------------------------------
  const handleToggleQuestionAudio = async () => {
    if (!session || !activeQuestion) return;

    if (isPlayingQuestion) {
      stopQuestionAudio();
      return;
    }

    try {
      setIsFetchingQuestionAudio(true);
      setQuestionAudioError(null);

      let url = questionAudioUrl;
      if (!url) {
        const blob = await fetchQuestionAudioBlob(session.session_id, activeQuestion.slot_id);
        url = URL.createObjectURL(blob);
        setQuestionAudioUrl(url);
      }

      let player = audioPlayerRef.current;
      if (!player) {
        player = new Audio(url);
        player.playbackRate = playbackSpeed;
        audioPlayerRef.current = player;
      } else {
        player.pause();
        player.src = url;
        player.playbackRate = playbackSpeed;
        player.currentTime = 0;
      }

      player.playbackRate = playbackSpeed;

      player.onended = () => {
        setIsPlayingQuestion(false);
      };
      player.onerror = () => {
        setIsPlayingQuestion(false);
        if (player && player.error) {
          setQuestionAudioError("Unable to play question audio. Please read the question text.");
        }
      };

      setIsPlayingQuestion(true);
      const playPromise = player.play();
      if (playPromise !== undefined) {
        await playPromise;
      }
    } catch (err: any) {
      setIsPlayingQuestion(false);
      if (err?.name === "AbortError" || err?.message?.includes("aborted")) {
        return;
      }
      setQuestionAudioError(err?.message || "Failed to load question speech.");
    } finally {
      setIsFetchingQuestionAudio(false);
    }
  };

  const handleStartRecording = async () => {
    if (!session || !activeQuestion) return;

    // 1. Stop any question playback first to prevent question speech leaking into candidate mic
    stopQuestionAudio();
    setAudioRecordingError(null);
    setTranscriptDraft(null);

    // Abort any prior in-flight transcription request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }

    // 2. Check secure context / mediaDevices support
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setAudioRecordingError("Browser microphone recording is not supported in this browser environment.");
      setAudioState("ERROR");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      setActiveMediaStream(stream);

      // Generate a unique client recording/request ID for this attempt
      const clientRequestId = `${activeQuestion.slot_id}_${Date.now()}`;
      activeRequestIdRef.current = clientRequestId;

      // Determine supported MIME type
      const mimeTypes = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/wav"];
      let selectedMime = "";
      if (typeof MediaRecorder !== "undefined") {
        for (const mt of mimeTypes) {
          if (MediaRecorder.isTypeSupported(mt)) {
            selectedMime = mt;
            break;
          }
        }
      }

      const options = selectedMime ? { mimeType: selectedMime } : undefined;
      const mediaRecorder = new MediaRecorder(stream, options);
      mediaRecorderRef.current = mediaRecorder;
      recordedChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          recordedChunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stopAllRecordingTracks();

        const chunks = recordedChunksRef.current;
        if (chunks.length === 0) {
          setAudioRecordingError("No audio data was captured. Please try recording again.");
          setAudioState("ERROR");
          return;
        }

        const mime = selectedMime || "audio/webm";
        const audioBlob = new Blob(chunks, { type: mime });

        // Ephemeral transcription with client request ID guard
        const abortCtrl = new AbortController();
        abortControllerRef.current = abortCtrl;
        setAudioState("PROCESSING");
        setAudioRecordingError(null);

        try {
          const transRes = await transcribeQuestionAudio(
            session.session_id,
            activeQuestion.slot_id,
            audioBlob,
            abortCtrl.signal
          );

          // Guard against stale response
          if (activeRequestIdRef.current !== clientRequestId) {
            return;
          }

          if (transRes.is_empty || !transRes.transcript.trim()) {
            setAudioRecordingError("Speech could not be clearly transcribed. Please speak closer to the microphone and try recording again.");
            setTranscriptDraft(null);
            setAudioState("ERROR");
          } else {
            setTranscriptDraft(transRes.transcript.trim());
            setAudioState("REVIEW");
          }
        } catch (transErr: any) {
          if (activeRequestIdRef.current !== clientRequestId) {
            return;
          }
          if (transErr?.name === "AbortError" || transErr?.message?.includes("aborted")) {
            return;
          }
          setAudioRecordingError(transErr?.message || "Transcription failed. Please try recording again.");
          setAudioState("ERROR");
        }
      };

      mediaRecorder.start(250); // collect in chunks
      setAudioState("RECORDING");
      setRecordingSeconds(0);

      const maxSecs = capabilities?.max_recording_seconds || 120;
      recordingTimerRef.current = setInterval(() => {
        setRecordingSeconds((prev) => {
          const next = prev + 1;
          if (next >= maxSecs) {
            handleStopRecording();
          }
          return next;
        });
      }, 1000);
    } catch (micErr: any) {
      if (micErr?.name === "NotAllowedError" || micErr?.name === "PermissionDeniedError") {
        setAudioRecordingError("Microphone permission was denied. Please allow microphone access in your browser settings to continue.");
      } else if (micErr?.name === "NotFoundError" || micErr?.name === "DevicesNotFoundError") {
        setAudioRecordingError("No microphone hardware was found on your device.");
      } else {
        setAudioRecordingError(micErr?.message || "Could not access microphone.");
      }
      stopAllRecordingTracks();
      setAudioState("ERROR");
    }
  };

  const handleStopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  };

  const handleCancelRecording = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    activeRequestIdRef.current = null;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.ondataavailable = null;
      mediaRecorderRef.current.onstop = null;
      mediaRecorderRef.current.stop();
    }
    stopAllRecordingTracks();
    setRecordingSeconds(0);
    recordedChunksRef.current = [];
    setAudioRecordingError(null);
    setAudioState(activeQuestion?.is_answered ? "ACCEPTED" : "READY");
  };

  const handleAcceptTranscript = async () => {
    if (!transcriptDraft) return;
    const ok = await handleSaveAnswer(transcriptDraft);
    if (ok) {
      setAudioState("ACCEPTED");
    }
  };

  const handleDiscardAndRecordAgain = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    activeRequestIdRef.current = null;
    setTranscriptDraft(null);
    setAudioRecordingError(null);
    setAudioState("READY");
  };

  const handleStartAudioInterview = async () => {
    try {
      setIsStartingAudio(true);
      setError(null);
      const data = await startOrResumeInterview(releaseId);
      setSession(data);
      setCurrentView("question");
      const firstUnansweredIndex = data.questions.findIndex((q) => !q.is_answered);
      if (firstUnansweredIndex >= 0) {
        setCurrentQuestionIndex(firstUnansweredIndex);
      } else {
        setCurrentQuestionIndex(0);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to start audio interview session.");
    } finally {
      setIsStartingAudio(false);
    }
  };

  if (isLoading) {
    return (
      <div className="candidate-home-container" style={{ maxWidth: "800px", margin: "40px auto", padding: "20px", textAlign: "center" }}>
        <div className="loading-spinner" style={{ margin: "0 auto 16px auto" }} />
        <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "15px" }}>
          Loading official interview session...
        </p>
      </div>
    );
  }

  // -------------------------------------------------------------
  // 1. AUDIO INTERVIEW PREPARATION VIEW
  // -------------------------------------------------------------
  if (currentView === "audio_prep") {
    const title = jobTitle || session?.job_title || "Audio Interview";
    const revNum = jobRevision || session?.job_revision || 1;
    const questionsCount = conversationCount || session?.total_questions || 0;

    const sttReady = Boolean(capabilities?.speech_to_text_available);
    const ttsReady = Boolean(capabilities?.text_to_speech_available);
    const browserSupportsMedia = typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
    const isLocalOrSecure = typeof window !== "undefined" && (window.isSecureContext || window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1");

    const allRuntimesReady = sttReady && ttsReady;
    const canStart = allRuntimesReady && micAcknowledged && !isStartingAudio;

    return (
      <div
        className="candidate-home-container"
        style={{ maxWidth: "760px", margin: "32px auto", padding: "20px" }}
      >
        <div
          style={{
            backgroundColor: "var(--surface-base, #ffffff)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
            borderRadius: "12px",
            padding: "36px 32px",
            boxShadow: "0 1px 4px rgba(0, 0, 0, 0.05)",
          }}
        >
          {/* Header */}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                color: "#6d28d9",
                backgroundColor: "#f5f3ff",
                padding: "2px 8px",
                borderRadius: "4px",
                border: "1px solid #ddd6fe",
              }}
            >
              Audio-based interview
            </span>
            <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)" }}>
              Job Revision #{revNum}
            </span>
          </div>

          <h1
            style={{
              fontSize: "24px",
              fontWeight: 700,
              color: "var(--text-primary, #0f172a)",
              margin: "0 0 16px 0",
            }}
          >
            {title}
          </h1>

          <p
            style={{
              fontSize: "14px",
              color: "var(--text-secondary, #475569)",
              lineHeight: 1.6,
              marginBottom: "24px",
            }}
          >
            Welcome to your local audio interview. Review the conversational format, system readiness, and privacy assurances below before beginning.
          </p>

          {/* Key Details Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "14px",
              marginBottom: "24px",
            }}
          >
            <div
              style={{
                backgroundColor: "var(--surface-muted, #f8fafc)",
                padding: "14px 16px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Format
              </span>
              <strong style={{ fontSize: "16px", color: "var(--text-primary, #0f172a)" }}>
                Conversational Audio
              </strong>
              <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", display: "block", marginTop: "2px" }}>
                {questionsCount > 0 ? `${questionsCount} conversational questions` : "Conversational questions only"}
              </span>
            </div>

            <div
              style={{
                backgroundColor: "var(--surface-muted, #f8fafc)",
                padding: "14px 16px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Audio Limits
              </span>
              <strong style={{ fontSize: "16px", color: "var(--text-primary, #0f172a)" }}>
                {capabilities?.max_recording_seconds || 120}s Max per Question
              </strong>
              <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", display: "block", marginTop: "2px" }}>
                Record, review, and confirm transcript
              </span>
            </div>
          </div>

          {/* System Readiness & Capabilities */}
          <div
            style={{
              backgroundColor: "#f8fafc",
              border: "1px solid #e2e8f0",
              borderRadius: "8px",
              padding: "16px 20px",
              marginBottom: "24px",
            }}
          >
            <span style={{ fontSize: "12px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px", color: "#475569", display: "block", marginBottom: "12px" }}>
              Local Speech System Readiness
            </span>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", fontSize: "13px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon
                  name={sttReady ? "check-circle" : "alert-triangle"}
                  size={16}
                  style={{ color: sttReady ? "#059669" : "#d97706" }}
                  aria-hidden="true"
                />
                <span>
                  Transcription: <strong>{sttReady ? "Ready" : "Unavailable"}</strong>
                </span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon
                  name={ttsReady ? "check-circle" : "alert-triangle"}
                  size={16}
                  style={{ color: ttsReady ? "#059669" : "#d97706" }}
                  aria-hidden="true"
                />
                <span>
                  Question playback: <strong>{ttsReady ? "Ready" : "Unavailable"}</strong>
                </span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon
                  name={browserSupportsMedia ? "check-circle" : "alert-triangle"}
                  size={16}
                  style={{ color: browserSupportsMedia ? "#059669" : "#d97706" }}
                  aria-hidden="true"
                />
                <span>
                  Browser Audio: <strong>{browserSupportsMedia ? "Supported" : "Unsupported"}</strong>
                </span>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon
                  name={isLocalOrSecure ? "check-circle" : "alert-triangle"}
                  size={16}
                  style={{ color: isLocalOrSecure ? "#059669" : "#d97706" }}
                  aria-hidden="true"
                />
                <span>
                  Context: <strong>{isLocalOrSecure ? "Secure / Localhost" : "Insecure Context"}</strong>
                </span>
              </div>
            </div>

            {!allRuntimesReady && capabilities?.unavailable_reason && (
              <div style={{ marginTop: "12px", fontSize: "14px", color: "var(--status-warn, #76551f)", backgroundColor: "var(--bg-surface-raised, #ffffff)", padding: "10px 14px", borderRadius: "6px", border: "1px solid var(--border-subtle, #d6dcd7)" }}>
                <Icon name="info" size={14} style={{ marginRight: "6px", verticalAlign: "middle" }} aria-hidden="true" />
                {capabilities.unavailable_reason}
              </div>
            )}
          </div>

          {/* Privacy & Zero-Retention Notice */}
          <div
            style={{
              backgroundColor: "var(--bg-surface-raised, #ffffff)",
              border: "1px solid var(--border-subtle, #d6dcd7)",
              borderRadius: "6px",
              padding: "18px 22px",
              marginBottom: "24px",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
              fontSize: "15px",
              color: "var(--text-primary, #202824)",
              lineHeight: 1.5,
            }}
          >
            <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
              <Icon name="shield" size={18} style={{ color: "var(--accent-primary, #174f4a)", flexShrink: 0, marginTop: "2px" }} aria-hidden="true" />
              <div>
                <strong>Zero Audio Retention Policy:</strong> Your spoken recording is processed entirely locally on the server for transcription and immediately deleted. Raw audio files, waveforms, or acoustic pitch/emotion metrics are never saved or sent to any cloud service.
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
              <Icon name="file-text" size={18} style={{ color: "var(--accent-primary, #174f4a)", flexShrink: 0, marginTop: "2px" }} aria-hidden="true" />
              <div>
                <strong>Transcript Confirmation:</strong> You will review the generated transcript before deciding to accept it as your official answer or record again. Only the confirmed transcript text is evaluated.
              </div>
            </div>
          </div>

          {/* Explicit Acknowledgement Checkbox */}
          <div style={{ marginBottom: "28px" }}>
            <label
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "12px",
                cursor: "pointer",
                padding: "14px 16px",
                borderRadius: "6px",
                backgroundColor: "var(--bg-surface-raised, #ffffff)",
                border: micAcknowledged ? "1px solid var(--border-medium, #7b857f)" : "1px solid var(--border-subtle, #d6dcd7)",
                transition: "border-color 0.15s ease",
              }}
            >
              <input
                type="checkbox"
                checked={micAcknowledged}
                onChange={(e) => setMicAcknowledged(e.target.checked)}
                style={{ marginTop: "3px", accentColor: "var(--accent-primary, #174f4a)" }}
              />
              <span style={{ fontSize: "15px", color: "var(--text-primary, #202824)", lineHeight: 1.5 }}>
                I understand that my microphone will be used solely for local transcription of my responses, and raw voice recordings are not retained.
              </span>
            </label>
          </div>

          {error && (
            <div className="feedback-box error" role="alert" style={{ marginBottom: "20px" }}>
              <Icon name="alert-triangle" size={16} aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          {/* Actions */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "16px", paddingTop: "24px", borderTop: "1px solid var(--border-subtle, #d6dcd7)", marginTop: "24px" }}>
            <button
              type="button"
              className="btn btn-secondary btn-md"
              onClick={onExit}
              disabled={isStartingAudio}
            >
              Return to Interviews
            </button>

            <button
              type="button"
              className="btn btn-primary btn-md"
              disabled={!canStart}
              onClick={handleStartAudioInterview}
              style={{ minWidth: "220px" }}
            >
              {isStartingAudio ? "Starting Interview..." : "Start Audio Interview"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (error && !session) {
    return (
      <div className="candidate-home-container" style={{ maxWidth: "800px", margin: "40px auto", padding: "20px" }}>
        <div className="feedback-box error" role="alert" style={{ marginBottom: "20px" }}>
          <Icon name="alert-triangle" size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
        <div style={{ marginTop: "24px" }}>
          <button type="button" className="btn btn-secondary btn-md" onClick={onExit}>
            Return to Interviews
          </button>
        </div>
      </div>
    );
  }

  if (!session) return null;

  // -------------------------------------------------------------
  // 2. SUBMITTED STATE
  // -------------------------------------------------------------
  if (currentView === "submitted" || session.status === "SUBMITTED") {
    const formattedDate = session.submitted_at
      ? new Date(session.submitted_at).toLocaleString()
      : submitReceipt?.submittedAt
      ? new Date(submitReceipt.submittedAt).toLocaleString()
      : new Date().toLocaleString();

    return (
      <div
        className="candidate-home-container"
        style={{ maxWidth: "760px", margin: "32px auto", padding: "20px" }}
      >
        <div
          tabIndex={-1}
          role="region"
          aria-label="Interview Submitted Receipt"
          style={{
            backgroundColor: "var(--surface-base, #ffffff)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
            borderRadius: "12px",
            padding: "36px 32px",
            textAlign: "center",
            boxShadow: "0 1px 4px rgba(0, 0, 0, 0.05)",
          }}
        >
          <div
            style={{
              width: "56px",
              height: "56px",
              borderRadius: "50%",
              backgroundColor: "#f0fdf4",
              border: "1px solid #bbf7d0",
              color: "#166534",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              marginBottom: "20px",
            }}
          >
            <Icon name="check-circle" size={30} aria-hidden="true" />
          </div>

          <span
            style={{
              fontSize: "12px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              color: "#059669",
              display: "block",
              marginBottom: "8px",
            }}
          >
            Official Submission Receipt
          </span>

          <h1
            style={{
              fontSize: "24px",
              fontWeight: 700,
              color: "var(--text-primary, #0f172a)",
              margin: "0 0 12px 0",
            }}
          >
            Interview Submitted Successfully
          </h1>

          <p
            style={{
              fontSize: "15px",
              color: "var(--text-secondary, #475569)",
              maxWidth: "540px",
              margin: "0 auto 24px auto",
              lineHeight: 1.6,
            }}
          >
            Your answers for <strong>{session.job_title}</strong> (Rev #{session.job_revision}) have been locked and submitted to the hiring team.
          </p>

          <div
            style={{
              backgroundColor: "var(--surface-muted, #f8fafc)",
              border: "1px solid var(--border-subtle, #e2e8f0)",
              borderRadius: "8px",
              padding: "16px 20px",
              maxWidth: "480px",
              margin: "0 auto 28px auto",
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gap: "12px",
              textAlign: "left",
            }}
          >
            <div>
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Submission Time
              </span>
              <strong style={{ fontSize: "13px", color: "var(--text-primary, #0f172a)" }}>
                {formattedDate}
              </strong>
            </div>
            <div>
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Answers Recorded
              </span>
              <strong style={{ fontSize: "13px", color: "var(--text-primary, #0f172a)" }}>
                {session.answered_questions} of {session.total_questions} Questions
              </strong>
            </div>
          </div>

          <p
            style={{
              fontSize: "13px",
              color: "var(--text-secondary, #64748b)",
              marginBottom: "28px",
            }}
          >
            The hiring team will review your application and interview submission.
          </p>

          <div style={{ paddingTop: "20px", borderTop: "1px solid var(--border-subtle, #d6dcd7)", marginTop: "24px" }}>
            <button
              type="button"
              className="btn btn-primary btn-md"
              onClick={onExit}
              style={{ minWidth: "180px" }}
            >
              Return to Interviews
            </button>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------
  // 3. LANDING STATE (Text Mode)
  // -------------------------------------------------------------
  if (currentView === "landing") {
    const isResuming = session.answered_questions > 0;

    return (
      <div
        className="candidate-home-container"
        style={{ maxWidth: "760px", margin: "32px auto", padding: "20px" }}
      >
        <div
          style={{
            backgroundColor: "var(--surface-base, #ffffff)",
            border: "1px solid var(--border-subtle, #e2e8f0)",
            borderRadius: "12px",
            padding: "32px",
            boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                color: "#2563eb",
                backgroundColor: "#eff6ff",
                padding: "2px 8px",
                borderRadius: "4px",
                border: "1px solid #bfdbfe",
              }}
            >
              Official Interview
            </span>
            <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)" }}>
              Job Revision #{session.job_revision}
            </span>
          </div>

          <h1
            style={{
              fontSize: "24px",
              fontWeight: 700,
              color: "var(--text-primary, #0f172a)",
              margin: "0 0 16px 0",
            }}
          >
            {session.job_title}
          </h1>

          <p
            style={{
              fontSize: "14px",
              color: "var(--text-secondary, #475569)",
              lineHeight: 1.6,
              marginBottom: "24px",
            }}
          >
            Welcome to your official interview workspace. Your responses will be saved directly to the system as you progress. You can review and revise any answer before final submission.
          </p>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: "14px",
              marginBottom: "28px",
            }}
          >
            <div
              style={{
                backgroundColor: "var(--surface-muted, #f8fafc)",
                padding: "14px 16px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Total Questions
              </span>
              <strong style={{ fontSize: "18px", color: "var(--text-primary, #0f172a)" }}>
                {session.total_questions}
              </strong>
            </div>

            <div
              style={{
                backgroundColor: "var(--surface-muted, #f8fafc)",
                padding: "14px 16px",
                borderRadius: "8px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>
                Saved Progress
              </span>
              <strong style={{ fontSize: "18px", color: "var(--text-primary, #0f172a)" }}>
                {session.answered_questions} of {session.total_questions} answered
              </strong>
            </div>
          </div>

          <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setCurrentView("question")}
              style={{ minWidth: "160px" }}
            >
              {isResuming ? "Resume Interview" : "Begin Interview"}
            </button>

            <button type="button" className="btn btn-secondary" onClick={onExit}>
              Back
            </button>
          </div>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------
  // 4. REVIEW STATE
  // -------------------------------------------------------------
  if (currentView === "review") {
    const unansweredCount = session.total_questions - session.answered_questions;

    return (
      <div
        className="candidate-home-container"
        style={{ maxWidth: "840px", margin: "28px auto", padding: "20px" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
          <div>
            <h1 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: "0 0 4px 0" }}>
              Review Interview Answers
            </h1>
            <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
              {session.job_title} · Revision #{session.job_revision} {isAudioSession && "· Audio Interview"}
            </p>
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setCurrentView("question")}
          >
            Back to Workspace
          </button>
        </div>

        {/* Status Summary Banner */}
        <div
          style={{
            backgroundColor: unansweredCount === 0 ? "#f0fdf4" : "#fffbeb",
            border: unansweredCount === 0 ? "1px solid #bbf7d0" : "1px solid #fde68a",
            borderRadius: "8px",
            padding: "16px 20px",
            marginBottom: "24px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <Icon
              name={unansweredCount === 0 ? "check-circle" : "alert-triangle"}
              size={20}
              style={{ color: unansweredCount === 0 ? "#166534" : "#b45309" }}
              aria-hidden="true"
            />
            <div>
              <strong style={{ fontSize: "14px", color: unansweredCount === 0 ? "#166534" : "#92400e" }}>
                {unansweredCount === 0
                  ? "All questions answered! Ready for final submission."
                  : `${unansweredCount} of ${session.total_questions} questions remain unanswered.`}
              </strong>
              <div style={{ fontSize: "12px", color: unansweredCount === 0 ? "#15803d" : "#b45309" }}>
                {unansweredCount === 0
                  ? "Once submitted, your answers will be locked and sent to recruiters."
                  : "You may return to any question to complete your response."}
              </div>
            </div>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSubmitAttempt}
            disabled={isSubmitting}
            style={{ minWidth: "160px" }}
          >
            {isSubmitting ? "Submitting..." : "Submit Interview"}
          </button>
        </div>

        {/* Question Review Grid */}
        <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginBottom: "28px" }}>
          {session.questions.map((q, idx) => {
            const hasAnswer = q.is_answered;
            const isConv = isConversationalQuestion(q);
            let previewText = "No answer recorded yet.";
            if (hasAnswer) {
              if (isConv) {
                previewText = q.saved_text_answer || "(Empty answer)";
              } else {
                const opt = q.options?.find((o) => (o.option_id || o.id) === q.saved_selected_option_id);
                previewText = opt ? (opt.label || opt.text || opt.option_id || "") : "Option selected";
              }
            }

            return (
              <div
                key={q.slot_id}
                style={{
                  backgroundColor: "var(--surface-base, #ffffff)",
                  border: "1px solid var(--border-subtle, #e2e8f0)",
                  borderRadius: "8px",
                  padding: "16px 20px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "16px",
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                    <span style={{ fontSize: "12px", fontWeight: 700, color: "var(--text-primary, #0f172a)" }}>
                      Question {idx + 1}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        padding: "2px 6px",
                        borderRadius: "4px",
                        backgroundColor: hasAnswer ? "#f0fdf4" : "#fef3c7",
                        color: hasAnswer ? "#166534" : "#92400e",
                        fontWeight: 600,
                      }}
                    >
                      {hasAnswer ? "Answered" : "Unanswered"}
                    </span>
                    {q.is_supplemental && (
                      <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)" }}>
                        (Supplemental)
                      </span>
                    )}
                  </div>
                  <p
                    style={{
                      fontSize: "13px",
                      color: "var(--text-secondary, #475569)",
                      margin: "0 0 6px 0",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {q.question_text}
                  </p>
                  <p
                    style={{
                      fontSize: "12px",
                      color: hasAnswer ? "var(--text-primary, #0f172a)" : "var(--text-secondary, #94a3b8)",
                      margin: 0,
                      fontStyle: hasAnswer ? "normal" : "italic",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {previewText}
                  </p>
                </div>

                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => handleGoToQuestion(idx)}
                >
                  {isAudioSession ? "Review" : "Edit"}
                </button>
              </div>
            );
          })}
        </div>

        {/* Incomplete Submission Confirmation Modal */}
        {showIncompleteModal && (
          <div
            className="modal-backdrop"
            style={{
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: "rgba(15, 23, 42, 0.6)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1000,
              padding: "16px",
            }}
            onKeyDown={handleKeyDownModal}
          >
            <div
              ref={modalRef}
              tabIndex={-1}
              role="dialog"
              aria-modal="true"
              aria-labelledby="incomplete-dialog-title"
              style={{
                backgroundColor: "var(--surface-base, #ffffff)",
                borderRadius: "12px",
                maxWidth: "480px",
                width: "100%",
                padding: "28px",
                boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
                <Icon name="alert-triangle" size={22} style={{ color: "#d97706" }} aria-hidden="true" />
                <h2
                  id="incomplete-dialog-title"
                  style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: 0 }}
                >
                  Incomplete Interview
                </h2>
              </div>

              <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", lineHeight: 1.5, margin: "0 0 20px 0" }}>
                You have <strong>{unansweredCount} unanswered question{unansweredCount === 1 ? "" : "s"}</strong>. Once submitted, you will not be able to return or change your answers. Are you sure you want to submit now?
              </p>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowIncompleteModal(false)}
                  disabled={isSubmitting}
                >
                  Continue Answering
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => executeFinalSubmit(true)}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Submitting..." : "Yes, Submit Incomplete"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // -------------------------------------------------------------
  // 5. QUESTION WORKSPACE STATE
  // -------------------------------------------------------------
  if (!activeQuestion) return null;

  const progressPercentage = Math.round(
    (session.answered_questions / session.total_questions) * 100
  );

  const maxRecordingSecs = capabilities?.max_recording_seconds || 120;
  const remainingSeconds = Math.max(0, maxRecordingSecs - recordingSeconds);
  const isQuestionAnswered = activeQuestion.is_answered;

  return (
    <div
      className="candidate-home-container"
      style={{
        maxWidth: isAudioSession ? "1100px" : "800px",
        margin: "24px auto",
        padding: "16px 20px",
      }}
    >
      {/* Top Bar with Header, Progress & Review Nav */}
      <div style={{ marginBottom: "20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "10px" }}>
          <div>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: isAudioSession ? "#0f766e" : "#2563eb",
                backgroundColor: isAudioSession ? "#f0fdfa" : "#eff6ff",
                border: isAudioSession ? "1px solid #99f6e4" : "1px solid #bfdbfe",
                padding: "3px 10px",
                borderRadius: "6px",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                display: "inline-block",
                marginBottom: "6px",
              }}
            >
              {isAudioSession ? "Audio Interview" : "Official Interview"} · {session.job_title}
            </span>
            <div style={{ fontSize: "15px", fontWeight: 700, color: "#0f172a" }}>
              Question {currentQuestionIndex + 1} of {session.total_questions}
              <span style={{ fontSize: "13px", fontWeight: 400, color: "#64748b", marginLeft: "8px" }}>
                ({session.answered_questions} of {session.total_questions} answered)
              </span>
            </div>
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleReviewClick}
            style={{ whiteSpace: "nowrap" }}
          >
            Review all ({session.answered_questions}/{session.total_questions})
          </button>
        </div>

        {/* Accessible Progress Bar */}
        <div
          role="progressbar"
          aria-valuenow={session.answered_questions}
          aria-valuemin={0}
          aria-valuemax={session.total_questions}
          aria-label={`Interview progress: ${session.answered_questions} of ${session.total_questions} questions answered`}
          style={{
            height: "6px",
            backgroundColor: "#e2e8f0",
            borderRadius: "3px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${progressPercentage}%`,
              backgroundColor: isAudioSession ? "#0d9488" : "#2563eb",
              transition: "width 0.3s ease",
            }}
          />
        </div>
      </div>

      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
          <Icon name="alert-triangle" size={16} aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      {/* Main Question Card */}
      <div
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: "12px",
          padding: isAudioSession ? "32px" : "28px",
          marginBottom: "20px",
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.04)",
        }}
      >
        {activeQuestion.is_supplemental && (
          <span
            style={{
              display: "inline-block",
              fontSize: "11px",
              fontWeight: 700,
              backgroundColor: "#f1f5f9",
              color: "#475569",
              padding: "2px 8px",
              borderRadius: "4px",
              marginBottom: "10px",
            }}
          >
            Supplemental Question
          </span>
        )}

        {/* Question Text & Audio Playback */}
        <div style={{ marginBottom: "24px" }}>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              color: "#64748b",
              marginBottom: "6px",
            }}
          >
            Question Prompt
          </div>
          <h2
            style={{
              fontSize: "19px",
              fontWeight: 600,
              color: "#0f172a",
              lineHeight: 1.5,
              margin: "0 0 16px 0",
            }}
          >
            {activeQuestion.question_text}
          </h2>

          {isAudioSession && (
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={handleToggleQuestionAudio}
                disabled={isFetchingQuestionAudio || audioState === "RECORDING" || audioState === "PROCESSING"}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  whiteSpace: "nowrap",
                  borderColor: isPlayingQuestion ? "#0d9488" : undefined,
                  backgroundColor: isPlayingQuestion ? "#f0fdfa" : undefined,
                  color: isPlayingQuestion ? "#0f766e" : undefined,
                }}
              >
                <Icon
                  name={isPlayingQuestion ? "pause" : "volume-2"}
                  size={15}
                  aria-hidden="true"
                />
                <span>
                  {isFetchingQuestionAudio
                    ? "Generating speech..."
                    : isPlayingQuestion
                    ? "Pause Question Speech"
                    : "Play Question Speech"}
                </span>
              </button>

              {questionAudioUrl && !isPlayingQuestion && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleToggleQuestionAudio}
                  disabled={audioState === "RECORDING" || audioState === "PROCESSING"}
                  style={{ display: "inline-flex", alignItems: "center", gap: "4px", whiteSpace: "nowrap" }}
                >
                  <Icon name="refresh" size={13} aria-hidden="true" />
                  Replay
                </button>
              )}

              {/* Playback speed selector */}
              <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <label htmlFor="playback-speed-select" style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", fontWeight: 500 }}>
                  Playback speed:
                </label>
                <select
                  id="playback-speed-select"
                  className="form-input"
                  style={{
                    fontSize: "12px",
                    padding: "2px 8px",
                    height: "32px",
                    minHeight: "32px",
                    width: "auto",
                    backgroundColor: "var(--bg-surface, #ffffff)",
                    cursor: "pointer",
                  }}
                  value={playbackSpeed}
                  onChange={(e) => {
                    const speed = parseFloat(e.target.value);
                    setPlaybackSpeed(speed);
                    if (audioPlayerRef.current) {
                      audioPlayerRef.current.playbackRate = speed;
                    }
                  }}
                  aria-label="Question audio playback speed"
                >
                  <option value={0.75}>0.75× — Slower</option>
                  <option value={1.0}>1× — Normal</option>
                  <option value={1.25}>1.25× — Faster</option>
                  <option value={1.5}>1.5× — Fast</option>
                </select>
              </div>

              {isPlayingQuestion && (
                <VoiceAnalogDisplay
                  isActive={true}
                  barCount={20}
                  height={32}
                  barColor="#0d9488"
                />
              )}

              {questionAudioError && (
                <span style={{ fontSize: "12px", color: "#b45309" }}>
                  {questionAudioError}
                </span>
              )}
            </div>
          )}
        </div>

        {/* AUDIO MODE WORKSPACE */}
        {isAudioSession ? (
          <div>
            {/* Audio Error Alert */}
            {audioRecordingError && (
              <div
                style={{
                  backgroundColor: "#fffbeb",
                  border: "1px solid #fde68a",
                  borderRadius: "8px",
                  padding: "12px 16px",
                  marginBottom: "16px",
                  fontSize: "13px",
                  color: "#92400e",
                  display: "flex",
                  alignItems: "center",
                  gap: "8px",
                }}
              >
                <Icon name="alert-triangle" size={16} aria-hidden="true" />
                <span>{audioRecordingError}</span>
              </div>
            )}

            {/* 1. READY STATE */}
            {audioState === "READY" && (
              <div
                style={{
                  backgroundColor: "#f8fafc",
                  border: "1px dashed #cbd5e1",
                  borderRadius: "10px",
                  padding: "36px 24px",
                  textAlign: "center",
                }}
              >
                <div
                  style={{
                    width: "52px",
                    height: "52px",
                    borderRadius: "50%",
                    backgroundColor: "var(--accent-subtle)",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--accent-primary, #174F4A)",
                    marginBottom: "14px",
                  }}
                >
                  <Icon name="mic" size={24} aria-hidden="true" />
                </div>
                <h3 style={{ fontSize: "16px", fontWeight: 600, color: "#0f172a", margin: "0 0 6px 0" }}>
                  Record Your Spoken Answer
                </h3>
                <p style={{ fontSize: "14px", color: "#64748b", margin: "0 auto 20px auto", maxWidth: "520px", lineHeight: 1.5 }}>
                  Click below to begin speaking. Maximum recording length is {maxRecordingSecs} seconds. Audio is transcribed securely and deleted immediately.
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleStartRecording}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "10px 24px",
                    fontSize: "15px",
                    whiteSpace: "nowrap",
                  }}
                >
                  <Icon name="mic" size={16} aria-hidden="true" />
                  Start recording
                </button>
              </div>
            )}

            {/* 2. RECORDING STATE */}
            {audioState === "RECORDING" && (
              <div
                style={{
                  backgroundColor: "#f0fdfa",
                  border: "1px solid #99f6e4",
                  borderRadius: "10px",
                  padding: "24px 28px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "12px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <span
                      style={{
                        width: "12px",
                        height: "12px",
                        borderRadius: "50%",
                        backgroundColor: "#0d9488",
                        boxShadow: "0 0 0 4px rgba(13, 148, 136, 0.2)",
                        display: "inline-block",
                      }}
                    />
                    <strong style={{ fontSize: "15px", color: "#134e4a" }}>
                      Recording in progress... Speak clearly.
                    </strong>
                  </div>

                  <div style={{ fontSize: "14px", fontWeight: 700, color: "#0f766e" }}>
                    {remainingSeconds}s remaining ({recordingSeconds}s / {maxRecordingSecs}s)
                  </div>
                </div>

                {/* Voice Analog Waveform Display */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    padding: "12px 0 20px 0",
                  }}
                >
                  <VoiceAnalogDisplay
                    stream={activeMediaStream}
                    isActive={true}
                    barCount={30}
                    height={46}
                    barColor="#2563eb"
                  />
                </div>

                <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handleStopRecording}
                    style={{
                      backgroundColor: "#0d9488",
                      borderColor: "#0f766e",
                      padding: "10px 22px",
                      whiteSpace: "nowrap",
                    }}
                  >
                    Stop and transcribe
                  </button>

                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={handleCancelRecording}
                    style={{ padding: "10px 18px", whiteSpace: "nowrap" }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* 3. PROCESSING STATE */}
            {audioState === "PROCESSING" && (
              <div
                style={{
                  backgroundColor: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: "10px",
                  padding: "36px 24px",
                  textAlign: "center",
                }}
              >
                <div className="loading-spinner" style={{ margin: "0 auto 14px auto" }} />
                <strong style={{ fontSize: "15px", color: "#0f172a", display: "block", marginBottom: "6px" }}>
                  Transcribing locally…
                </strong>
                <span style={{ fontSize: "13px", color: "#64748b" }}>
                  Zero audio retained. Audio temporary files are purged immediately.
                </span>
              </div>
            )}

            {/* 4. REVIEW STATE */}
            {audioState === "REVIEW" && transcriptDraft && (
              <div
                style={{
                  backgroundColor: "#ffffff",
                  border: "1px solid #cbd5e1",
                  borderRadius: "10px",
                  padding: "24px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}>
                  <h3 style={{ fontSize: "15px", fontWeight: 700, color: "#0f172a", margin: 0 }}>
                    Review your transcript
                  </h3>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "4px",
                      fontSize: "12px",
                      color: "#166534",
                      backgroundColor: "#f0fdf4",
                      padding: "2px 8px",
                      borderRadius: "4px",
                      border: "1px solid #bbf7d0",
                    }}
                  >
                    <Icon name="check" size={12} aria-hidden="true" />
                    Recording deleted after transcription
                  </span>
                </div>

                <div
                  style={{
                    backgroundColor: "#f8fafc",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    padding: "16px",
                    fontSize: "15px",
                    color: "#0f172a",
                    lineHeight: 1.6,
                    marginBottom: "20px",
                    maxHeight: "220px",
                    overflowY: "auto",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {transcriptDraft}
                </div>

                <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={handleAcceptTranscript}
                    disabled={saveState === "saving"}
                    style={{
                      whiteSpace: "nowrap",
                    }}
                  >
                    {saveState === "saving" ? "Saving..." : "Use this answer"}
                  </button>

                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={handleDiscardAndRecordAgain}
                    disabled={saveState === "saving"}
                    style={{ whiteSpace: "nowrap" }}
                  >
                    Record again
                  </button>
                </div>
              </div>
            )}

            {/* 5. ACCEPTED STATE */}
            {audioState === "ACCEPTED" && (
              <div
                style={{
                  backgroundColor: "#ffffff",
                  border: "1px solid #bbf7d0",
                  borderRadius: "10px",
                  padding: "24px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", flexWrap: "wrap", gap: "8px" }}>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                      fontSize: "13px",
                      fontWeight: 700,
                      color: "#166534",
                    }}
                  >
                    <Icon name="check-circle" size={16} aria-hidden="true" />
                    Answer Accepted &amp; Saved
                  </span>
                  {activeQuestion.saved_at && (
                    <span style={{ fontSize: "12px", color: "#64748b" }}>
                      Saved: {new Date(activeQuestion.saved_at).toLocaleTimeString()}
                    </span>
                  )}
                </div>

                <div
                  style={{
                    backgroundColor: "#f8fafc",
                    border: "1px solid #e2e8f0",
                    borderRadius: "8px",
                    padding: "16px",
                    fontSize: "14px",
                    lineHeight: 1.6,
                    color: "#0f172a",
                    marginBottom: "16px",
                  }}
                >
                  {activeQuestion.saved_text_answer || transcriptDraft}
                </div>

                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleStartRecording}
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }}
                >
                  <Icon name="mic" size={14} aria-hidden="true" />
                  Record a new answer
                </button>
              </div>
            )}

            {/* 6. ERROR STATE */}
            {audioState === "ERROR" && !transcriptDraft && (
              <div
                style={{
                  backgroundColor: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: "10px",
                  padding: "28px 20px",
                  textAlign: "center",
                }}
              >
                <p style={{ fontSize: "14px", color: "#64748b", margin: "0 auto 16px auto" }}>
                  Click below to try recording your answer again.
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleStartRecording}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "8px",
                    whiteSpace: "nowrap",
                  }}
                >
                  <Icon name="mic" size={16} aria-hidden="true" />
                  Record Answer
                </button>
              </div>
            )}
          </div>
        ) : (
          /* TEXT MODE WORKSPACE */
          <div>
            {isConversationalQuestion(activeQuestion) ? (
              <div>
                <label
                  htmlFor={`answer-text-${activeQuestion.slot_id}`}
                  style={{
                    display: "block",
                    fontSize: "13px",
                    fontWeight: 600,
                    color: "var(--text-secondary, #475569)",
                    marginBottom: "8px",
                  }}
                >
                  Your Answer
                </label>
                <textarea
                  id={`answer-text-${activeQuestion.slot_id}`}
                  rows={6}
                  value={textDraft}
                  onChange={(e) => setTextDraft(e.target.value)}
                  placeholder="Type your response here..."
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    fontSize: "14px",
                    lineHeight: 1.5,
                    border: "1px solid var(--border-subtle, #cbd5e1)",
                    borderRadius: "8px",
                    boxSizing: "border-box",
                    fontFamily: "inherit",
                  }}
                />
              </div>
            ) : (
              <div>
                <fieldset style={{ border: "none", padding: 0, margin: 0 }}>
                  <legend
                    style={{
                      fontSize: "13px",
                      fontWeight: 600,
                      color: "var(--text-secondary, #475569)",
                      marginBottom: "12px",
                    }}
                  >
                    Select an option
                  </legend>

                  <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                    {(activeQuestion.options || []).map((option) => {
                      const optId = option.option_id || option.id || "";
                      const optLabel = option.label || option.text || optId;
                      const isChecked = mcqDraft === optId;
                      return (
                        <label
                          key={optId}
                          htmlFor={`opt-${optId}`}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "12px",
                            padding: "12px 16px",
                            borderRadius: "8px",
                            border: isChecked
                              ? "1.5px solid #2563eb"
                              : "1px solid var(--border-subtle, #e2e8f0)",
                            backgroundColor: isChecked ? "#eff6ff" : "var(--surface-base, #ffffff)",
                            cursor: "pointer",
                            transition: "all 0.15s ease",
                          }}
                        >
                          <input
                            type="radio"
                            id={`opt-${optId}`}
                            name={`question-mcq-${activeQuestion.slot_id}`}
                            value={optId}
                            checked={isChecked}
                            onChange={() => setMcqDraft(optId)}
                            style={{ accentColor: "#2563eb" }}
                          />
                          <span style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>
                            {optLabel}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
              </div>
            )}
          </div>
        )}

        {/* Live Saving Status Feedback */}
        <div
          aria-live="polite"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginTop: "16px",
            fontSize: "12px",
            color: "#64748b",
          }}
        >
          <div>
            {saveState === "saving" && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "#2563eb" }}>
                <Icon name="refresh" size={12} className="spinning" aria-hidden="true" />
                Saving answer...
              </span>
            )}
            {saveState === "saved" && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "#166534" }}>
                <Icon name="check" size={12} aria-hidden="true" />
                Saved {lastSavedTimestamp ? `at ${lastSavedTimestamp}` : ""}
              </span>
            )}
            {saveState === "error" && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", color: "#b45309" }}>
                <Icon name="alert-triangle" size={12} aria-hidden="true" />
                Could not save
              </span>
            )}
            {saveState === "idle" && activeQuestion.saved_at && (
              <span>Last saved: {new Date(activeQuestion.saved_at).toLocaleTimeString()}</span>
            )}
          </div>

          {!isAudioSession && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => handleSaveAnswer()}
              disabled={saveState === "saving"}
              style={{ fontSize: "12px", whiteSpace: "nowrap" }}
            >
              Save answer
            </button>
          )}
        </div>
      </div>

      {/* Navigation Controls inside Centered Workspace */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <button
          type="button"
          className="btn btn-secondary"
          onClick={handlePrevious}
          disabled={currentQuestionIndex === 0 || audioState === "RECORDING" || audioState === "PROCESSING"}
          style={{ minWidth: "120px", whiteSpace: "nowrap" }}
        >
          Previous
        </button>

        <div style={{ display: "flex", gap: "10px" }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSaveAndContinue}
            disabled={
              saveState === "saving" ||
              audioState === "RECORDING" ||
              audioState === "PROCESSING" ||
              (isAudioSession && !activeQuestion.is_answered && audioState !== "ACCEPTED")
            }
            style={{ minWidth: "160px", whiteSpace: "nowrap" }}
          >
            {isAudioSession
              ? (currentQuestionIndex < session.questions.length - 1 ? "Continue" : "Review answers")
              : (currentQuestionIndex < session.questions.length - 1 ? "Save and continue" : "Save & Review answers")}
          </button>
        </div>
      </div>
    </div>
  );
}
