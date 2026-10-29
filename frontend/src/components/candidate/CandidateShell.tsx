import React, { useState, useEffect } from "react";
import { useCandidateAuth } from "../../context/CandidateAuthContext";
import { Icon } from "../Icon";
import { CandidatePracticeDirectory } from "./CandidatePracticeDirectory";
import { CandidateOpenJobs } from "./CandidateOpenJobs";
import { CandidateJobDetailView } from "./CandidateJobDetail";
import { CandidateMyApplications } from "./CandidateMyApplications";
import { CandidateApplicationDetailView } from "./CandidateApplicationDetail";

export type CandidateTab = "jobs" | "applications" | "interviews" | "profile";

interface CandidateShellProps {
  onLogout?: () => void;
  initialTab?: CandidateTab;
}

export function CandidateShell({ onLogout, initialTab }: CandidateShellProps) {
  const { candidateUser, logout } = useCandidateAuth();

  const getTabFromPath = (): CandidateTab => {
    if (typeof window === "undefined") return initialTab || "jobs";
    const path = window.location.pathname.toLowerCase();
    if (path.startsWith("/candidate/applications")) return "applications";
    if (path.startsWith("/candidate/interviews")) return "interviews";
    if (path.startsWith("/candidate/profile")) return "profile";
    if (path.startsWith("/candidate/jobs")) return "jobs";
    return initialTab || "jobs";
  };

  const [activeTab, setActiveTab] = useState<CandidateTab>(getTabFromPath);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | undefined>(undefined);
  const [selectedApplicationId, setSelectedApplicationId] = useState<string | null>(null);

  // Sync tab with popstate (Back / Forward)
  useEffect(() => {
    const handlePopState = () => {
      const tab = getTabFromPath();
      setActiveTab(tab);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Update browser tab title dynamically per candidate tab
  useEffect(() => {
    const tabTitles: Record<CandidateTab, string> = {
      jobs: "Open Jobs — Candidate Portal | Smart Hiring",
      applications: "My Applications — Candidate Portal | Smart Hiring",
      interviews: "Practice Interviews — Candidate Portal | Smart Hiring",
      profile: "My Profile — Candidate Portal | Smart Hiring",
    };
    document.title = tabTitles[activeTab] || "Candidate Portal | Smart Hiring";
  }, [activeTab]);

  const navigateToTab = (tab: CandidateTab) => {
    setActiveTab(tab);
    const targetUrl = `/candidate/${tab}`;
    if (typeof window !== "undefined" && window.location.pathname !== targetUrl && window.history?.pushState) {
      window.history.pushState(null, "", targetUrl);
    }
  };

  const handleLogout = async () => {
    await logout();
    if (onLogout) {
      onLogout();
    }
  };

  const handleSelectJob = (jobId: string, versionId?: string) => {
    setSelectedJobId(jobId);
    setSelectedVersionId(versionId);
  };

  const handleBackToJobs = () => {
    setSelectedJobId(null);
    setSelectedVersionId(undefined);
  };

  const handleSelectApplication = (applicationId: string) => {
    setSelectedApplicationId(applicationId);
    navigateToTab("applications");
  };

  const handleBackToApplications = () => {
    setSelectedApplicationId(null);
  };

  const tabItems: { key: CandidateTab; label: string; icon: string }[] = [
    { key: "jobs", label: "Open Jobs", icon: "briefcase" },
    { key: "applications", label: "My Applications", icon: "file-text" },
    { key: "interviews", label: "Practice Interviews", icon: "message-square" },
  ];

  return (
    <div className="candidate-shell-layout">
      <header className="candidate-shell-header" role="banner">
        <div className="candidate-shell-brand">
          <Icon name="briefcase" className="brand-icon" aria-hidden="true" />
          <span className="brand-title">Smart Hiring</span>
          <span className="candidate-portal-chip">Candidate Portal</span>
        </div>

        <nav
          className="candidate-shell-nav"
          role="navigation"
          aria-label="Candidate navigation"
        >
          {tabItems.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`nav-tab-btn ${activeTab === item.key ? "tab-active" : ""}`}
              onClick={() => {
                navigateToTab(item.key);
                if (item.key === "jobs") handleBackToJobs();
                if (item.key === "applications") handleBackToApplications();
              }}
              aria-current={activeTab === item.key ? "page" : undefined}
            >
              <Icon name={item.icon as any} size={15} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>

        <div className="candidate-shell-user-menu">
          <span className="candidate-user-name" aria-label="Authenticated candidate">
            {candidateUser?.displayName || "Candidate"}
          </span>
          <button
            type="button"
            className="btn btn-secondary btn-sm candidate-logout-btn"
            onClick={handleLogout}
            aria-label="Sign out of candidate portal"
          >
            <Icon name="log-out" aria-hidden="true" />
            <span>Sign out</span>
          </button>
        </div>
      </header>

      <main className="candidate-shell-main" role="main">
        {activeTab === "jobs" && (
          selectedJobId ? (
            <CandidateJobDetailView
              jobId={selectedJobId}
              versionId={selectedVersionId}
              onBack={handleBackToJobs}
              onNavigateApplication={handleSelectApplication}
            />
          ) : (
            <CandidateOpenJobs onSelectJob={handleSelectJob} />
          )
        )}

        {activeTab === "applications" && (
          selectedApplicationId ? (
            <CandidateApplicationDetailView
              applicationId={selectedApplicationId}
              onBack={handleBackToApplications}
              onNavigateJobs={() => {
                navigateToTab("jobs");
                handleBackToJobs();
              }}
              onNavigateInterviews={() => {
                navigateToTab("interviews");
              }}
            />
          ) : (
            <CandidateMyApplications
              onSelectApplication={handleSelectApplication}
              onNavigateJobs={() => {
                navigateToTab("jobs");
                handleBackToJobs();
              }}
            />
          )
        )}

        {activeTab === "interviews" && (
          <CandidatePracticeDirectory />
        )}

        {activeTab === "profile" && (
          <div className="candidate-profile-container" style={{ maxWidth: "var(--max-reading-width)", margin: "0 auto", padding: "24px" }}>
            <div style={{ padding: "24px", backgroundColor: "var(--bg-surface-raised)", borderRadius: "var(--radius-lg)", border: "1px solid var(--border-subtle)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
                <div
                  style={{
                    width: "48px",
                    height: "48px",
                    borderRadius: "50%",
                    backgroundColor: "var(--accent-subtle)",
                    color: "var(--accent-primary)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: "18px",
                    fontWeight: 700,
                  }}
                >
                  {(candidateUser?.displayName || "C").charAt(0).toUpperCase()}
                </div>
                <div>
                  <h2 style={{ fontSize: "var(--text-lg)", fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
                    {candidateUser?.displayName || "Candidate"}
                  </h2>
                  <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: 0 }}>
                    {candidateUser?.email}
                  </p>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", borderTop: "1px solid var(--border-subtle)", paddingTop: "16px", fontSize: "var(--text-sm)" }}>
                <div>
                  <span style={{ color: "var(--text-muted)", display: "block", fontSize: "var(--text-xs)", marginBottom: "4px" }}>Account type</span>
                  <strong>Candidate</strong>
                </div>
                <div>
                  <span style={{ color: "var(--text-muted)", display: "block", fontSize: "var(--text-xs)", marginBottom: "4px" }}>Status</span>
                  <span className="badge badge-status-approved" style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
                    <Icon name="check-circle" size={12} />
                    <span>Active</span>
                  </span>
                </div>
              </div>

              <div style={{ marginTop: "24px", padding: "16px", backgroundColor: "var(--bg-surface)", borderRadius: "var(--radius-md)", border: "1px solid var(--border-subtle)" }}>
                <h3 style={{ fontSize: "var(--text-sm)", fontWeight: 600, margin: "0 0 6px", color: "var(--text-primary)" }}>
                  Your workspace
                </h3>
                <p style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", margin: 0, lineHeight: 1.5 }}>
                  Browse and apply for job postings, view your submitted applications, and complete scheduled interviews from this portal.
                </p>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
