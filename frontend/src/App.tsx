import React, { useEffect, useState } from "react";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { LoginPage } from "./components/auth/LoginPage";
import { ChangePasswordPage } from "./components/auth/ChangePasswordPage";
import { AuthenticatedShell } from "./components/AuthenticatedShell";
import { AdminWorkspace } from "./components/admin/AdminWorkspace";
import { ReviewerWorkspace } from "./components/reviewer/ReviewerWorkspace";
import { CandidatesDirectory, CandidateSummary } from "./components/CandidatesDirectory";
import { ResumeUpload } from "./components/ResumeUpload";
import { CandidateProfileReview, CandidateProfileData } from "./components/CandidateProfileReview";
import { ExtractionReview, ExtractionResultData } from "./components/ExtractionReview";
import { JobsDirectory } from "./components/jobs/JobsDirectory";
import { JobEditor } from "./components/jobs/JobEditor";
import { JobApplicantsView } from "./components/jobs/JobApplicantsView";
import { JobApplicationDetailView } from "./components/jobs/JobApplicationDetailView";
import { CandidateComparisonView } from "./components/jobs/CandidateComparisonView";
import { RequirementGapReportView } from "./components/jobs/RequirementGapReportView";
import { InterviewWorkspace } from "./components/interviews/InterviewWorkspace";
import { SimulationWorkspace } from "./components/interviews/SimulationWorkspace";
import { AiInterviewsDirectory } from "./components/ai_interviews/AiInterviewsDirectory";
import { AiQuestionSetWorkspace } from "./components/ai_interviews/AiQuestionSetWorkspace";
import { EvaluationWorkspace } from "./components/evaluation/EvaluationWorkspace";
import { AnalyticsLauncher } from "./components/analytics/AnalyticsLauncher";
import { apiFetch } from "./api/client";
import { fetchJobs, JobSummary } from "./api/jobs";
import { Icon } from "./components/Icon";
import { validateInternalReturnUrl, validateCandidateReturnUrl, ComparisonNavigationState } from "./utils/navigation";
import { getPortalContext, getCrossPortalUrl } from "./utils/portal";
import { CandidateAuthProvider, useCandidateAuth } from "./context/CandidateAuthContext";
import { CandidateRegisterPage } from "./components/candidate/CandidateRegisterPage";
import { CandidateLoginPage } from "./components/candidate/CandidateLoginPage";
import { CandidateShell } from "./components/candidate/CandidateShell";

// Re-export validation utility for direct callers and test assertions
export { validateInternalReturnUrl, validateCandidateReturnUrl };

type ConnectionState = "checking" | "connected" | "unavailable";

export interface ComparisonParamsState {
  jobId: string;
  jobTitle: string;
  jobVersionId: string;
  revision: number;
  requirementsCount: number;
  runId?: string;
  searchQuery?: string;
  resultsSearch?: string;
  page?: number;
  sortField?: "rank" | "score" | "name" | "evidence";
  sortDirection?: "asc" | "desc";
  selectedProfileIds?: string[];
}

export interface InterviewRouteState {
  jobId: string;
  resumeId: string;
}

function AuthenticatedApp() {
  const { user } = useAuth();
  const role = user?.role || "REVIEWER";
  const portalContext = getPortalContext();

  // Navigation tab state
  const [activeNavTab, setActiveNavTab] = useState<string>(
    role === "ADMIN" ? "users" : role === "RECRUITER" ? "candidates" : "reviews"
  );
  const [activeView, setActiveView] = useState<
    "jobs" | "new_job" | "edit_job" | "job_applicants" | "job_application_detail" | "compare_candidates" | "gap_report" | "candidates" | "upload" | "profile" | "extraction" | "interview_workspace" | "interview_simulation" | "ai_interviews" | "ai_interview_workspace" | "evaluation"
  >("candidates");
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null);
  const [selectedApplicationId, setSelectedApplicationId] = useState<string | null>(null);
  const [selectedJobTitle, setSelectedJobTitle] = useState<string>("");
  const [interviewRoute, setInterviewRoute] = useState<InterviewRouteState | null>(null);
  const [simulationRoute, setSimulationRoute] = useState<{ planId: string; jobId: string } | null>(null);
  const [selectedAiTemplateId, setSelectedAiTemplateId] = useState<string | null>(null);

  // Health check
  const [connectionState, setConnectionState] = useState<ConnectionState>("checking");

  // Jobs state
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [isLoadingJobs, setIsLoadingJobs] = useState<boolean>(false);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [comparisonParams, setComparisonParams] = useState<ComparisonParamsState | null>(null);

  // Candidates state
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState<boolean>(false);
  const [selectedProfile, setSelectedProfile] = useState<CandidateProfileData | null>(null);
  const [selectedExtraction, setSelectedExtraction] = useState<ExtractionResultData | null>(null);
  const [isLoadingProfile, setIsLoadingProfile] = useState<boolean>(false);
  const [profileLoadError, setProfileLoadError] = useState<string | null>(null);

  // Initialize from URL query parameters if present
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const searchParams = new URLSearchParams(window.location.search);
      const viewParam = searchParams.get("view");
      const tabParam = searchParams.get("tab");
      const returnUrlParam = searchParams.get("returnUrl");
      const validReturn = validateInternalReturnUrl(returnUrlParam);
      const reportIdParam = searchParams.get("reportId");
      const jobIdParam = searchParams.get("jobId");
      const runIdParam = searchParams.get("runId");

      if (viewParam === "gap_report" && reportIdParam) {
        setSelectedReportId(reportIdParam);
        setActiveView("gap_report");
        setActiveNavTab("jobs");
      } else if (viewParam === "compare_candidates" && jobIdParam) {
        setComparisonParams({
          jobId: jobIdParam,
          jobTitle: searchParams.get("jobTitle") || "Job Requisition",
          jobVersionId: searchParams.get("jobVersionId") || "",
          revision: Number(searchParams.get("revision")) || 1,
          requirementsCount: Number(searchParams.get("requirementsCount")) || 0,
          runId: runIdParam || undefined,
        });
        setActiveView("compare_candidates");
        setActiveNavTab("jobs");
      } else if (
        tabParam === "upload" ||
        viewParam === "upload" ||
        (validReturn && validReturn.includes("tab=upload"))
      ) {
        setActiveView("upload");
        setActiveNavTab("upload");
      } else if (
        tabParam === "ats_integrations" ||
        (validReturn && validReturn.includes("tab=ats_integrations"))
      ) {
        setActiveNavTab("ats_integrations");
      } else if (tabParam === "jobs") {
        if (jobIdParam) {
          setSelectedJobId(jobIdParam);
          setActiveView("edit_job");
        } else {
          setActiveView("jobs");
        }
        setActiveNavTab("jobs");
      } else if (tabParam === "candidates") {
        setActiveView("candidates");
        setActiveNavTab("candidates");
      } else if (tabParam === "users" || tabParam === "events" || tabParam === "ai_providers") {
        setActiveNavTab(tabParam);
      } else if (
        tabParam === "ai_questions" ||
        tabParam === "ai-questions" ||
        viewParam === "ai_questions" ||
        viewParam === "ai-questions" ||
        viewParam === "interview_template" ||
        viewParam === "interview_workspace" ||
        window.location.pathname.startsWith("/ai-questions") ||
        window.location.pathname.startsWith("/interview-templates") ||
        window.location.pathname.startsWith("/interviews")
      ) {
        // Safe redirection from legacy routes to AI Interviews workspace / directory
        setActiveView("ai_interviews");
        setActiveNavTab("ai_interviews");
        if (jobIdParam) {
          setSelectedJobId(jobIdParam);
        }
        if (typeof window !== "undefined" && window.history?.replaceState) {
          const targetUrl = jobIdParam ? `/ai-interviews?jobId=${encodeURIComponent(jobIdParam)}` : "/ai-interviews";
          window.history.replaceState(null, "", targetUrl);
        }
      } else if (window.location.pathname.startsWith("/ai-interviews")) {
        const parts = window.location.pathname.split("/").filter(Boolean);
        if (parts.length > 1 && parts[1]) {
          setSelectedAiTemplateId(parts[1]);
          setActiveView("ai_interview_workspace");
        } else {
          if (jobIdParam) {
            setSelectedJobId(jobIdParam);
          }
          setActiveView("ai_interviews");
        }
        setActiveNavTab("ai_interviews");
      } else if (viewParam === "ai_interviews" || tabParam === "ai_interviews") {
        const tmplParam = searchParams.get("templateId");
        if (tmplParam) {
          setSelectedAiTemplateId(tmplParam);
          setActiveView("ai_interview_workspace");
        } else {
          if (jobIdParam) {
            setSelectedJobId(jobIdParam);
          }
          setActiveView("ai_interviews");
        }
        setActiveNavTab("ai_interviews");
      } else if (
        window.location.pathname.startsWith("/evaluation") ||
        tabParam === "evaluation" ||
        tabParam === "evaluations" ||
        viewParam === "evaluation"
      ) {
        setActiveView("evaluation");
        setActiveNavTab("evaluation");
        if (typeof window !== "undefined" && window.history?.replaceState) {
          window.history.replaceState(null, "", "/evaluation");
        }
      } else if (
        window.location.pathname.startsWith("/analytics") ||
        tabParam === "analytics" ||
        viewParam === "analytics"
      ) {
        setActiveView("analytics" as any);
        setActiveNavTab("analytics");
        if (typeof window !== "undefined" && window.history?.replaceState) {
          window.history.replaceState(null, "", "/analytics");
        }
      }
    } catch {
      // Ignore URL parsing errors in test environments
    }
  }, []);

  useEffect(() => {
    const searchParams = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
    const tabParam = searchParams?.get("tab");
    const viewParam = searchParams?.get("view");
    const returnUrlParam = searchParams?.get("returnUrl");
    const validReturn = validateInternalReturnUrl(returnUrlParam);

    if (
      window.location.pathname.startsWith("/evaluation") ||
      tabParam === "evaluation" ||
      tabParam === "evaluations" ||
      viewParam === "evaluation"
    ) {
      setActiveNavTab("evaluation");
      setActiveView("evaluation");
      return;
    }
    if (
      tabParam === "upload" ||
      viewParam === "upload" ||
      (validReturn && validReturn.includes("tab=upload"))
    ) {
      setActiveNavTab("upload");
      setActiveView("upload");
      return;
    }
    if (
      tabParam === "ats_integrations" ||
      (validReturn && validReturn.includes("tab=ats_integrations"))
    ) {
      setActiveNavTab("ats_integrations");
      return;
    }
    if (tabParam) {
      setActiveNavTab(tabParam);
      if (tabParam === "jobs") setActiveView("jobs");
      if (tabParam === "candidates") setActiveView("candidates");
      return;
    }

    const defaultTab = role === "ADMIN" ? "users" : role === "RECRUITER" ? "candidates" : "reviews";
    setActiveNavTab(defaultTab);
    if (role === "RECRUITER" && activeView === "candidates") {
      setActiveView("candidates");
    }
  }, [role]);

  useEffect(() => {
    const controller = new AbortController();

    async function checkHealth() {
      try {
        const response = await fetch("/api/v1/health", {
          signal: controller.signal,
        });

        if (!response.ok) {
          setConnectionState("unavailable");
          return;
        }

        const data = await response.json();
        if (data.status === "healthy" && data.database === "connected") {
          setConnectionState("connected");
        } else {
          setConnectionState("unavailable");
        }
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          setConnectionState("unavailable");
        }
      }
    }

    checkHealth();
    return () => controller.abort();
  }, []);

  // Fetch Jobs if role is RECRUITER or ADMIN
  const loadJobsList = async () => {
    if (role !== "RECRUITER" && role !== "ADMIN") return;
    try {
      setIsLoadingJobs(true);
      setJobsError(null);
      const data = await fetchJobs();
      setJobs(data || []);
    } catch (e: any) {
      setJobsError(e?.message || "Failed to load jobs.");
    } finally {
      setIsLoadingJobs(false);
    }
  };

  // Fetch Candidates if role is RECRUITER
  const fetchCandidates = async () => {
    if (role !== "RECRUITER") return;
    try {
      setIsLoadingCandidates(true);
      const data = await apiFetch<CandidateSummary[]>("/api/v1/resumes");
      setCandidates(data || []);
    } catch (e) {
      console.error("Failed to fetch candidates", e);
    } finally {
      setIsLoadingCandidates(false);
    }
  };

  useEffect(() => {
    if (role === "RECRUITER") {
      loadJobsList();
      fetchCandidates();
    } else if (role === "ADMIN") {
      loadJobsList();
    }
  }, [role]);

  const handleSelectJob = (jobId: string) => {
    setSelectedJobId(jobId);
    setActiveView("edit_job");
  };

  const handleCreateJob = () => {
    setSelectedJobId(null);
    setActiveView("new_job");
  };

  const handleJobCreated = (newJobId: string) => {
    loadJobsList();
    setSelectedJobId(newJobId);
    setActiveView("edit_job");
  };

  const handleSelectCandidate = async (artifactId: string) => {
    setIsLoadingProfile(true);
    setProfileLoadError(null);

    try {
      const profData = await apiFetch<CandidateProfileData>(
        `/api/v1/resumes/${artifactId}/profile`
      );
      setSelectedProfile(profData);

      try {
        const extData = await apiFetch<ExtractionResultData>(
          `/api/v1/resumes/${artifactId}/extraction`
        );
        setSelectedExtraction(extData);
      } catch {
        setSelectedExtraction(null);
      }

      setActiveView("profile");
      fetchCandidates();
    } catch (err: any) {
      console.error("Failed to load candidate", err);
      setProfileLoadError(err?.message || "A network error occurred while loading profile.");
    } finally {
      setIsLoadingProfile(false);
    }
  };

  const handleCandidateProcessed = (artifactId: string) => {
    fetchCandidates();
    handleSelectCandidate(artifactId);
  };

  const handleOpenAiQuestionSet = (templateId: string) => {
    setSelectedAiTemplateId(templateId);
    setActiveView("ai_interview_workspace");
    setActiveNavTab("ai_interviews");
    if (typeof window !== "undefined" && window.history?.pushState) {
      window.history.pushState(null, "", `/ai-interviews/${templateId}`);
    }
  };

  const handleNavigateTab = (tab: string) => {
    setActiveNavTab(tab);
    if (tab === "jobs") {
      setActiveView("jobs");
      loadJobsList();
    } else if (tab === "candidates") {
      setActiveView("candidates");
      fetchCandidates();
    } else if (tab === "upload") {
      setActiveView("upload");
    } else if (tab === "ai_interviews" || tab === "ai-interviews") {
      setActiveView("ai_interviews");
      if (typeof window !== "undefined" && window.history?.pushState) {
        window.history.pushState(null, "", "/ai-interviews");
      }
    } else if (tab === "evaluation" || tab === "evaluations") {
      setActiveView("evaluation");
      if (typeof window !== "undefined" && window.history?.pushState) {
        window.history.pushState(null, "", "/evaluation");
      }
    } else if (tab === "analytics") {
      setActiveView("analytics" as any);
      if (typeof window !== "undefined" && window.history?.pushState) {
        window.history.pushState(null, "", "/analytics");
      }
    }
  };

  return (
    <AuthenticatedShell
      activeNavTab={activeNavTab}
      onNavigateTab={handleNavigateTab}
      candidateCount={candidates.length}
      jobsCount={jobs.length}
      connectionState={connectionState}
    >
      {portalContext === "admin" && role !== "ADMIN" && (
        <div className="portal-mismatch-banner" role="status" aria-live="polite">
          <Icon name="info" size={16} />
          <div className="portal-mismatch-content">
            <span className="portal-mismatch-title">Administrator Workspace</span>
            <p className="portal-mismatch-desc">
              You are signed in as a {role.toLowerCase()}. System administration tools require administrator authorization.
            </p>
            <a
              href={getCrossPortalUrl("recruiter")}
              className="btn btn-secondary btn-sm"
              style={{ marginTop: "8px", display: "inline-flex", alignItems: "center", gap: "6px" }}
            >
              <Icon name="external-link" size={13} />
              <span>Open Recruiter workspace</span>
            </a>
          </div>
        </div>
      )}

      {role === "ADMIN" && (
        <>
          {activeNavTab === "analytics" ? (
            <AnalyticsLauncher />
          ) : (
            <AdminWorkspace activeTab={activeNavTab} />
          )}
        </>
      )}

      {role === "REVIEWER" && <ReviewerWorkspace />}

      {role === "RECRUITER" && (
        <>
          {isLoadingProfile && (
            <div className="profile-loading-overlay">
              <div className="loading-spinner" />
              <p className="loading-text">Loading candidate profile…</p>
            </div>
          )}

          {profileLoadError && (
            <div className="feedback-box error" style={{ maxWidth: "600px", margin: "32px auto" }}>
              <Icon name="alert-triangle" size={18} />
              <div className="error-content">
                <p className="feedback-text error">{profileLoadError}</p>
                <button
                  type="button"
                  onClick={() => {
                    setActiveView("candidates");
                    setProfileLoadError(null);
                  }}
                  className="btn btn-secondary btn-sm"
                  style={{ marginTop: "10px" }}
                >
                  Back to Candidates
                </button>
              </div>
            </div>
          )}

          {!isLoadingProfile && !profileLoadError && (
            <>
              {activeView === "jobs" && (
                <JobsDirectory
                  jobs={jobs}
                  isLoading={isLoadingJobs}
                  error={jobsError}
                  onSelectJob={handleSelectJob}
                  onCreateJob={handleCreateJob}
                  onRefresh={loadJobsList}
                />
              )}

              {activeView === "new_job" && (
                <JobEditor
                  jobId={null}
                  onNavigateBack={() => {
                    setActiveView("jobs");
                    loadJobsList();
                  }}
                  onJobCreated={handleJobCreated}
                  onJobUpdated={loadJobsList}
                />
              )}

              {activeView === "edit_job" && selectedJobId && (
                <JobEditor
                  jobId={selectedJobId}
                  onNavigateBack={() => {
                    setActiveView("jobs");
                    loadJobsList();
                  }}
                  onJobCreated={handleJobCreated}
                  onJobUpdated={loadJobsList}
                  onCompareCandidates={(params) => {
                    setComparisonParams(params);
                    setActiveView("compare_candidates");
                  }}
                  onOpenApplicants={(jId, jTitle) => {
                    setSelectedJobId(jId);
                    setSelectedJobTitle(jTitle);
                    setActiveView("job_applicants");
                  }}
                  onOpenAiInterviews={(jId) => {
                    setSelectedJobId(jId);
                    setActiveView("ai_interviews");
                    setActiveNavTab("ai_interviews");
                    if (typeof window !== "undefined" && window.history?.pushState) {
                      window.history.pushState(null, "", `/ai-interviews?jobId=${encodeURIComponent(jId)}`);
                    }
                  }}
                />
              )}

              {activeView === "job_applicants" && selectedJobId && (
                <JobApplicantsView
                  jobId={selectedJobId}
                  jobTitle={selectedJobTitle}
                  onNavigateBack={() => setActiveView("edit_job")}
                  onSelectApplication={(appId) => {
                    setSelectedApplicationId(appId);
                    setActiveView("job_application_detail");
                  }}
                />
              )}

              {activeView === "job_application_detail" && selectedJobId && selectedApplicationId && (
                <JobApplicationDetailView
                  jobId={selectedJobId}
                  applicationId={selectedApplicationId}
                  onNavigateBack={() => setActiveView("job_applicants")}
                  onOpenAiInterviews={(jId) => {
                    setSelectedJobId(jId);
                    setActiveView("ai_interviews");
                    setActiveNavTab("ai_interviews");
                  }}
                  onOpenMatchingComparison={() => {
                    setActiveView("edit_job");
                  }}
                />
              )}

              {activeView === "compare_candidates" && comparisonParams && (
                <CandidateComparisonView
                  jobId={comparisonParams.jobId}
                  jobTitle={comparisonParams.jobTitle}
                  jobVersionId={comparisonParams.jobVersionId}
                  revision={comparisonParams.revision}
                  requirementsCount={comparisonParams.requirementsCount}
                  initialRunId={comparisonParams.runId}
                  initialSearchQuery={comparisonParams.searchQuery}
                  initialResultsSearch={comparisonParams.resultsSearch}
                  initialSelectedProfileIds={comparisonParams.selectedProfileIds}
                  initialPage={comparisonParams.page}
                  initialSortField={comparisonParams.sortField}
                  initialSortDirection={comparisonParams.sortDirection}
                  onNavigateBack={() => setActiveView("edit_job")}
                  onOpenGapReport={(reportId, returnContext) => {
                    if (returnContext) {
                      setComparisonParams((prev) => ({
                        ...prev,
                        ...returnContext,
                      }));
                    }
                    setSelectedReportId(reportId);
                    setActiveView("gap_report");
                  }}
                  onOpenInterviewWorkspace={(jobId, resumeId) => {
                    setInterviewRoute({ jobId, resumeId });
                    setActiveView("interview_workspace");
                  }}
                />
              )}

              {activeView === "gap_report" && selectedReportId && (
                <RequirementGapReportView
                  reportId={selectedReportId}
                  jobTitle={comparisonParams?.jobTitle}
                  returnUrl={`/?view=compare_candidates&jobId=${comparisonParams?.jobId || ""}&runId=${comparisonParams?.runId || ""}`}
                  onNavigateBack={() => setActiveView("compare_candidates")}
                  onNavigateBackWithFallback={(fallback) => {
                    setComparisonParams((prev) => ({
                      jobId: fallback.jobId,
                      jobTitle: prev?.jobTitle || `Job #${fallback.jobId.substring(0, 8)}`,
                      jobVersionId: fallback.jobVersionId,
                      revision: fallback.revision,
                      requirementsCount: prev?.requirementsCount || 0,
                      runId: fallback.runId,
                    }));
                    setActiveView("compare_candidates");
                  }}
                />
              )}

              {activeView === "candidates" && (
                <CandidatesDirectory
                  candidates={candidates}
                  isLoading={isLoadingCandidates}
                  onSelectCandidate={handleSelectCandidate}
                  onNavigateUpload={() => {
                    setActiveView("upload");
                    setActiveNavTab("upload");
                  }}
                  onRefreshCandidates={fetchCandidates}
                />
              )}

              {activeView === "upload" && (
                <ResumeUpload
                  onCandidateProcessed={handleCandidateProcessed}
                  onNavigateCandidates={() => {
                    setActiveView("candidates");
                    setActiveNavTab("candidates");
                    fetchCandidates();
                  }}
                />
              )}

              {activeView === "profile" && selectedProfile && (
                <CandidateProfileReview
                  profile={selectedProfile}
                  extraction={selectedExtraction}
                  onNavigateBack={() => {
                    setActiveView("candidates");
                    setActiveNavTab("candidates");
                    fetchCandidates();
                  }}
                  onNavigateExtraction={() => setActiveView("extraction")}
                />
              )}

              {activeView === "extraction" && selectedExtraction && (
                <ExtractionReview
                  extraction={selectedExtraction}
                  onNavigateBack={() => setActiveView("profile")}
                />
              )}

              {activeView === "interview_workspace" && interviewRoute && (
                <InterviewWorkspace
                  jobId={interviewRoute.jobId}
                  resumeId={interviewRoute.resumeId}
                  onNavigateToAiInterview={handleOpenAiQuestionSet}
                  onNavigateToSimulation={(planId, jId) => {
                    setSimulationRoute({ planId, jobId: jId });
                    setActiveView("interview_simulation");
                  }}
                  onNavigateBack={() => {
                    if (interviewRoute.resumeId && comparisonParams) {
                      setActiveView("compare_candidates");
                      setActiveNavTab("jobs");
                    } else if (selectedJobId) {
                      setActiveView("edit_job");
                      setActiveNavTab("jobs");
                    } else {
                      setActiveView("jobs");
                      setActiveNavTab("jobs");
                    }
                  }}
                />
              )}

              {activeView === "interview_simulation" && simulationRoute && (
                <SimulationWorkspace
                  planId={simulationRoute.planId}
                  jobId={simulationRoute.jobId}
                  onNavigateBack={() => {
                    setActiveView("interview_workspace");
                  }}
                />
              )}

              {activeView === "ai_interviews" && (
                <AiInterviewsDirectory
                  initialSelectedJobId={selectedJobId}
                  onSelectTemplate={handleOpenAiQuestionSet}
                  onNavigateToJobs={() => {
                    setActiveNavTab("jobs");
                    setActiveView("jobs");
                    loadJobsList();
                  }}
                />
              )}

              {activeView === "ai_interview_workspace" && selectedAiTemplateId && (
                <AiQuestionSetWorkspace
                  templateId={selectedAiTemplateId}
                  onNavigateBack={() => {
                    setActiveView("ai_interviews");
                    setActiveNavTab("ai_interviews");
                    if (typeof window !== "undefined" && window.history?.pushState) {
                      window.history.pushState(null, "", "/ai-interviews");
                    }
                  }}
                  onOpenApplicants={(jobId, jobTitle) => {
                    setSelectedJobId(jobId);
                    setSelectedJobTitle(jobTitle);
                    setActiveView("job_applicants");
                  }}
                />
              )}

              {activeView === "evaluation" && (
                <EvaluationWorkspace />
              )}

              {activeNavTab === "analytics" && (
                <AnalyticsLauncher />
              )}
            </>
          )}
        </>
      )}
    </AuthenticatedShell>
  );
}

function CandidateMainContent({ initialView }: { initialView?: "register" | "login" | "shell" }) {
  const { status } = useCandidateAuth();
  const [pathname, setPathname] = useState<string>(
    typeof window !== "undefined" ? window.location.pathname : "/candidate/jobs"
  );

  useEffect(() => {
    const handlePopState = () => {
      if (typeof window !== "undefined") {
        setPathname(window.location.pathname);
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigateCandidate = (path: string) => {
    if (typeof window !== "undefined" && window.history?.pushState) {
      window.history.pushState(null, "", path);
      setPathname(path);
    }
  };

  const getSafeReturnTo = (): string | null => {
    if (typeof window === "undefined") return null;
    const searchParams = new URLSearchParams(window.location.search);
    const returnToParam = searchParams.get("returnTo");
    return validateCandidateReturnUrl(returnToParam);
  };

  // 1. Session restoring state
  if (status === "RESTORING_SESSION") {
    return (
      <div className="auth-loading-splash" role="status" aria-live="polite">
        <div className="loading-spinner" />
        <p className="loading-text">Verifying session…</p>
      </div>
    );
  }

  // 2. Explicit Register page
  const isRegisterPath = (pathname === "/candidate/register" || initialView === "register") && status !== "AUTHENTICATED";
  if (isRegisterPath) {
    return (
      <CandidateRegisterPage
        onSuccess={() => {
          const safeReturn = getSafeReturnTo();
          navigateCandidate(safeReturn || "/candidate/jobs");
        }}
        onNavigateLogin={() => {
          const safeReturn = getSafeReturnTo();
          const target = safeReturn ? `/candidate/login?returnTo=${encodeURIComponent(safeReturn)}` : "/candidate/login";
          navigateCandidate(target);
        }}
      />
    );
  }

  // 3. Unauthenticated or Login route
  const isLoginPath = (pathname === "/candidate/login" || initialView === "login") && status !== "AUTHENTICATED";
  if (status === "UNAUTHENTICATED" || isLoginPath) {
    return (
      <CandidateLoginPage
        onSuccess={() => {
          const safeReturn = getSafeReturnTo();
          navigateCandidate(safeReturn || "/candidate/jobs");
        }}
        onNavigateRegister={() => {
          const safeReturn = getSafeReturnTo();
          const target = safeReturn ? `/candidate/register?returnTo=${encodeURIComponent(safeReturn)}` : "/candidate/register";
          navigateCandidate(target);
        }}
      />
    );
  }

  // 4. Authenticated Candidate Shell
  // Determine active tab from pathname
  let initialTab: "jobs" | "applications" | "interviews" | "profile" = "jobs";
  if (pathname.startsWith("/candidate/applications")) {
    initialTab = "applications";
  } else if (pathname.startsWith("/candidate/interviews")) {
    initialTab = "interviews";
  } else if (pathname.startsWith("/candidate/profile")) {
    initialTab = "profile";
  }

  return (
    <CandidateShell
      key={initialTab}
      initialTab={initialTab}
      onLogout={() => {
        navigateCandidate("/candidate/login");
      }}
    />
  );
}

function MainContent() {
  const { status } = useAuth();

  if (status === "RESTORING_SESSION") {
    return (
      <div className="auth-loading-splash" role="status" aria-live="polite">
        <div className="loading-spinner" />
        <p className="loading-text">Verifying session…</p>
      </div>
    );
  }

  if (status === "UNAUTHENTICATED") {
    return <LoginPage />;
  }

  if (status === "PASSWORD_CHANGE_REQUIRED") {
    return <ChangePasswordPage />;
  }

  return <AuthenticatedApp />;
}

export function App() {
  const [pathname, setPathname] = useState<string>(
    typeof window !== "undefined" ? window.location.pathname : "/"
  );
  const portal = getPortalContext();

  useEffect(() => {
    const handlePopState = () => {
      if (typeof window !== "undefined") {
        setPathname(window.location.pathname);
      }
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const isCandidateRoute =
    portal === "candidate" ||
    pathname.startsWith("/candidate");

  useEffect(() => {
    if (isCandidateRoute) {
      if (pathname.startsWith("/candidate/register")) {
        document.title = "Create Account — Candidate Portal | Smart Hiring";
      } else if (pathname.startsWith("/candidate/login")) {
        document.title = "Sign In — Candidate Portal | Smart Hiring";
      } else {
        document.title = "Candidate Portal | Smart Hiring";
      }
    } else {
      if (portal === "admin") {
        document.title = "Admin Workspace | Smart Hiring";
      } else if (portal === "recruiter") {
        document.title = "Recruiter Workspace | Smart Hiring";
      } else {
        document.title = "Smart Hiring — Recruiter & Staff Portal";
      }
    }
  }, [isCandidateRoute, pathname, portal]);

  if (isCandidateRoute) {
    const isRegister = pathname.startsWith("/candidate/register");
    const isLogin = pathname.startsWith("/candidate/login");
    return (
      <CandidateAuthProvider>
        <CandidateMainContent initialView={isRegister ? "register" : isLogin ? "login" : undefined} />
      </CandidateAuthProvider>
    );
  }

  return (
    <AuthProvider>
      <MainContent />
    </AuthProvider>
  );
}

export default App;
