import React, { useState, useEffect, FormEvent } from "react";
import { apiFetch, ApiError } from "../../api/client";
import { Icon } from "../Icon";
import { AtsConnectionsAdmin } from "./AtsConnectionsAdmin";

interface UserItem {
  userId: string;
  email: string;
  displayName: string;
  role: "ADMIN" | "RECRUITER" | "REVIEWER";
  isActive: boolean;
  mustChangePassword: boolean;
  lastLoginAt?: string | null;
  createdAt: string;
}

interface SecurityEventItem {
  eventId: string;
  actorUserId?: string | null;
  eventType: string;
  resourceType?: string | null;
  resourceId?: string | null;
  result: string;
  reasonCode: string;
  safeMetadata: Record<string, any>;
  occurredAt: string;
}

interface AdminCandidateItem {
  id: string;
  identityId?: string | null;
  artifactId?: string | null;
  userId?: string | null;
  fullName: string;
  email: string;
  accountEmail?: string | null;
  resumeEmail?: string | null;
  headline?: string | null;
  isActive: boolean;
  hasAccount: boolean;
  createdAt: string;
  applicationsCount: number;
  interviewsCount: number;
}

interface PaginatedEvents {
  items: SecurityEventItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminProviderProfileItem {
  profileId: string;
  organizationId?: string | null;
  displayName: string;
  providerType: string;
  apiMode: string;
  baseUrlMasked?: string | null;
  modelIdentifier?: string | null;
  secretStatus: "CONFIGURED" | "MISSING" | "NOT_REQUIRED";
  secretRefName?: string | null;
  supportedOperations: string[];
  connectTimeoutSeconds: number;
  requestTimeoutSeconds: number;
  maxInputTokens: number;
  maxOutputTokens: number;
  maxResponseBytes: number;
  maxConcurrency: number;
  retryLimit: number;
  egressClass: "LOCAL_ONLY" | "APPROVED_REMOTE";
  privacyReviewStatus: "APPROVED" | "PENDING_REVIEW" | "REJECTED";
  enabled: boolean;
  compatibilityStatus: "HEALTHY" | "COMPATIBLE" | "INCOMPATIBLE" | "NOT_TESTED" | "DISABLED";
  testedAt?: string | null;
  modelSource?: string | null;
  modelLicence?: string | null;
  modelChecksum?: string | null;
  lastCompatibilityReport?: Record<string, any> | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export function AdminWorkspace({ activeTab = "users" }: { activeTab?: string }) {
  // Users state
  const [users, setUsers] = useState<UserItem[]>([]);
  const [isLoadingUsers, setIsLoadingUsers] = useState(false);
  const [userError, setUserError] = useState<string | null>(null);

  // Candidates state
  const [candidates, setCandidates] = useState<AdminCandidateItem[]>([]);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [candidateSearchQuery, setCandidateSearchQuery] = useState("");
  const [isUpdatingCandidate, setIsUpdatingCandidate] = useState<string | null>(null);
  const [isDeletingCandidate, setIsDeletingCandidate] = useState<string | null>(null);


  // Modals
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [createEmail, setCreateEmail] = useState("");
  const [createDisplayName, setCreateDisplayName] = useState("");
  const [createRole, setCreateRole] = useState<"ADMIN" | "RECRUITER" | "REVIEWER">("RECRUITER");
  const [createTempPassword, setCreateTempPassword] = useState("");
  const [createModalError, setCreateModalError] = useState<string | null>(null);
  const [isCreatingUser, setIsCreatingUser] = useState(false);

  // Edit / Reset Modal
  const [selectedUser, setSelectedUser] = useState<UserItem | null>(null);
  const [isResetModalOpen, setIsResetModalOpen] = useState(false);
  const [resetTempPassword, setResetTempPassword] = useState("");
  const [resetModalError, setResetModalError] = useState<string | null>(null);
  const [isResetting, setIsResetting] = useState(false);

  // Status feedback toast/banner
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [isDeletingUser, setIsDeletingUser] = useState<string | null>(null);

  // Security Events state
  const [events, setEvents] = useState<SecurityEventItem[]>([]);
  const [eventsTotal, setEventsTotal] = useState(0);
  const [eventsOffset, setEventsOffset] = useState(0);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);

  // AI Providers state
  const [providers, setProviders] = useState<AdminProviderProfileItem[]>([]);
  const [isLoadingProviders, setIsLoadingProviders] = useState(false);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [isTogglingProvider, setIsTogglingProvider] = useState<string | null>(null);
  const [isTestingProvider, setIsTestingProvider] = useState<string | null>(null);

  // Diagnostic Report Modal state
  const [selectedReportProfile, setSelectedReportProfile] = useState<AdminProviderProfileItem | null>(null);
  const [compatibilityReport, setCompatibilityReport] = useState<any | null>(null);
  const [isReportModalOpen, setIsReportModalOpen] = useState(false);

  // Create Provider Modal
  const [isCreateProviderModalOpen, setIsCreateProviderModalOpen] = useState(false);
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newProviderType, setNewProviderType] = useState<"LOCAL_OPENAI_COMPATIBLE" | "REMOTE_OPENAI_COMPATIBLE">("LOCAL_OPENAI_COMPATIBLE");
  const [newEgressClass, setNewEgressClass] = useState<"LOCAL_ONLY" | "APPROVED_REMOTE">("LOCAL_ONLY");
  const [newBaseUrl, setNewBaseUrl] = useState("");
  const [newModelId, setNewModelId] = useState("");
  const [newSecretRef, setNewSecretRef] = useState("");
  const [newModelSource, setNewModelSource] = useState("");
  const [newModelLicence, setNewModelLicence] = useState("");
  const [newModelChecksum, setNewModelChecksum] = useState("");
  const [createProviderModalError, setCreateProviderModalError] = useState<string | null>(null);
  const [isCreatingProvider, setIsCreatingProvider] = useState(false);

  // Activate Provider Modal
  const [targetActivateProfile, setTargetActivateProfile] = useState<AdminProviderProfileItem | null>(null);
  const [isActivateModalOpen, setIsActivateModalOpen] = useState(false);
  const [confirmRemoteEgress, setConfirmRemoteEgress] = useState(false);
  const [activateReason, setActivateReason] = useState("");
  const [activateModalError, setActivateModalError] = useState<string | null>(null);

  // Local AI Simplification State
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [isDiscoveringModels, setIsDiscoveringModels] = useState(false);

  const handleAutoDiscoverModels = async (baseUrl?: string) => {
    try {
      setIsDiscoveringModels(true);
      const url = baseUrl || "http://127.0.0.1:1234/v1";
      const models = await apiFetch<string[]>(`/api/v1/admin/provider-profiles/discover-models?base_url=${encodeURIComponent(url)}`);
      if (models && models.length > 0) {
        setDiscoveredModels(models);
        if (!newModelId && models[0]) {
          setNewModelId(models[0]);
        }
      }
    } catch {
      // Non-blocking
    } finally {
      setIsDiscoveringModels(false);
    }
  };

  const fetchProviders = async () => {
    try {
      setIsLoadingProviders(true);
      setProviderError(null);
      const data = await apiFetch<AdminProviderProfileItem[]>("/api/v1/admin/provider-profiles?include_archived=true");
      setProviders(data || []);
    } catch (err: any) {
      setProviderError(err?.message || "Failed to load AI provider profiles.");
    } finally {
      setIsLoadingProviders(false);
    }
  };

  const handleTestCompatibility = async (profile: AdminProviderProfileItem) => {
    const pId = profile.profileId || (profile as any).profile_id;
    const pName = profile.displayName || (profile as any).display_name;
    try {
      setIsTestingProvider(pId);
      setProviderError(null);
      const report = await apiFetch<any>(
        `/api/v1/admin/provider-profiles/${pId}/test-compatibility`,
        {
          method: "POST",
          body: JSON.stringify({ version: profile.version }),
        }
      );
      setCompatibilityReport(report);
      setSelectedReportProfile(profile);
      setIsReportModalOpen(true);
      if (report.is_compatible) {
        setActionSuccess(`Compatibility check passed for '${pName}' (latency: ${report.latency_ms}ms).`);
      } else {
        setProviderError(`Compatibility check failed for '${pName}': ${report.safe_failure_message || report.safe_failure_code}`);
      }
      fetchProviders();
    } catch (err: any) {
      setProviderError(err?.message || "Compatibility test failed to execute.");
    } finally {
      setIsTestingProvider(null);
    }
  };

  const handleCreateProvider = async (e: FormEvent) => {
    e.preventDefault();
    setCreateProviderModalError(null);

    if (!newDisplayName.trim()) {
      setCreateProviderModalError("Display Name is required.");
      return;
    }

    try {
      setIsCreatingProvider(true);
      const payload = {
        display_name: newDisplayName.trim(),
        provider_type: newProviderType,
        egress_class: newEgressClass,
        base_url: newBaseUrl.trim() || undefined,
        model_identifier: newModelId.trim() || undefined,
        secret_ref: newSecretRef.trim() || undefined,
      };

      await apiFetch("/api/v1/admin/provider-profiles", {
        method: "POST",
        body: JSON.stringify(payload),
      });

      setIsCreateProviderModalOpen(false);
      setNewDisplayName("");
      setNewBaseUrl("");
      setNewModelId("");
      setNewSecretRef("");
      setActionSuccess(`Provider profile '${newDisplayName.trim()}' created successfully.`);
      fetchProviders();
    } catch (err: any) {
      setCreateProviderModalError(err?.message || "Failed to create provider profile.");
    } finally {
      setIsCreatingProvider(false);
    }
  };

  const handleOpenActivateModal = (profile: AdminProviderProfileItem) => {
    setTargetActivateProfile(profile);
    setConfirmRemoteEgress(false);
    setActivateReason("");
    setActivateModalError(null);
    setIsActivateModalOpen(true);
  };

  const handleConfirmActivate = async (e: FormEvent) => {
    e.preventDefault();
    if (!targetActivateProfile) return;
    setActivateModalError(null);

    const isRemote = (targetActivateProfile.egressClass as any) === "APPROVED_REMOTE" || (targetActivateProfile as any).egress_class === "APPROVED_REMOTE";
    if (isRemote && !confirmRemoteEgress) {
      setActivateModalError("Explicit confirmation of remote data egress is required.");
      return;
    }

    const pId = targetActivateProfile.profileId || (targetActivateProfile as any).profile_id;
    const pName = targetActivateProfile.displayName || (targetActivateProfile as any).display_name;

    try {
      setIsTogglingProvider(pId);
      await apiFetch(`/api/v1/admin/provider-profiles/${pId}/activate`, {
        method: "POST",
        body: JSON.stringify({
          version: targetActivateProfile.version,
          confirm_remote_egress: confirmRemoteEgress,
          reason: activateReason.trim() || undefined,
        }),
      });

      setIsActivateModalOpen(false);
      setActionSuccess(`Provider profile '${pName}' activated successfully.`);
      fetchProviders();
    } catch (err: any) {
      setActivateModalError(err?.message || "Failed to activate provider profile.");
    } finally {
      setIsTogglingProvider(null);
    }
  };

  const handleDisableProvider = async (profile: AdminProviderProfileItem) => {
    const pId = profile.profileId || (profile as any).profile_id;
    const pName = profile.displayName || (profile as any).display_name;
    try {
      setIsTogglingProvider(pId);
      await apiFetch(`/api/v1/admin/provider-profiles/${pId}/disable`, {
        method: "POST",
        body: JSON.stringify({
          version: profile.version,
          reason: "Disabled via Admin Workspace",
        }),
      });
      setActionSuccess(`Provider profile '${pName}' disabled successfully.`);
      fetchProviders();
    } catch (err: any) {
      setProviderError(err?.message || "Failed to disable provider profile.");
    } finally {
      setIsTogglingProvider(null);
    }
  };

  const fetchUsers = async () => {
    try {
      setIsLoadingUsers(true);
      setUserError(null);
      const data = await apiFetch<UserItem[]>("/api/v1/admin/users");
      setUsers(data || []);
    } catch (err: any) {
      setUserError(err?.message || "Failed to load platform users.");
    } finally {
      setIsLoadingUsers(false);
    }
  };

  const fetchSecurityEvents = async (offset = 0) => {
    try {
      setIsLoadingEvents(true);
      const data = await apiFetch<PaginatedEvents>(
        `/api/v1/admin/security-events?limit=25&offset=${offset}`
      );
      setEvents(data.items || []);
      setEventsTotal(data.total || 0);
      setEventsOffset(data.offset || 0);
    } catch (err: any) {
      console.error("Failed to load security audit events", err);
    } finally {
      setIsLoadingEvents(false);
    }
  };

  const fetchCandidates = async () => {
    try {
      setIsLoadingCandidates(true);
      setCandidateError(null);
      const data = await apiFetch<AdminCandidateItem[]>("/api/v1/admin/candidates");
      setCandidates(data || []);
    } catch (err: any) {
      setCandidateError(err?.message || "Failed to load candidates.");
    } finally {
      setIsLoadingCandidates(false);
    }
  };

  const handleToggleCandidateActive = async (cand: AdminCandidateItem) => {
    const newActiveState = !cand.isActive;
    const confirmMsg = newActiveState
      ? `Activate candidate '${cand.fullName}'?`
      : `Deactivate candidate '${cand.fullName}'? All active candidate sessions will be terminated immediately.`;

    if (!window.confirm(confirmMsg)) return;

    try {
      setIsUpdatingCandidate(cand.id);
      await apiFetch(`/api/v1/admin/candidates/${cand.id}`, {
        method: "PATCH",
        body: JSON.stringify({ isActive: newActiveState }),
      });
      setActionSuccess(
        `Candidate '${cand.fullName}' ${newActiveState ? "activated" : "deactivated"} successfully.`
      );
      fetchCandidates();
    } catch (err: any) {
      alert(err?.message || "Failed to update candidate status.");
    } finally {
      setIsUpdatingCandidate(null);
    }
  };

  const handleDeleteCandidate = async (cand: AdminCandidateItem) => {
    const confirmMsg = `Are you sure you want to permanently drop everything about candidate '${cand.fullName}' (${cand.email || "No email"})?\n\nThis will irreversibly delete all resumes, extracted profiles, job applications, interview recordings, evaluations, and user credentials.\n\nThis action cannot be undone.`;

    if (!window.confirm(confirmMsg)) return;

    try {
      setIsDeletingCandidate(cand.id);
      await apiFetch(`/api/v1/admin/candidates/${cand.id}`, {
        method: "DELETE",
      });
      setActionSuccess(`Candidate '${cand.fullName}' and all associated records permanently dropped.`);
      fetchCandidates();
    } catch (err: any) {
      alert(err?.message || "Failed to delete candidate.");
    } finally {
      setIsDeletingCandidate(null);
    }
  };

  useEffect(() => {
    if (activeTab === "users") {
      fetchUsers();
    } else if (activeTab === "candidates") {
      fetchCandidates();
    } else if (activeTab === "events") {
      fetchSecurityEvents(0);
    } else if (activeTab === "ai_providers") {
      fetchProviders();
    }
  }, [activeTab]);

  const handleCreateUser = async (e: FormEvent) => {
    e.preventDefault();
    setCreateModalError(null);

    if (!createEmail.trim() || !createDisplayName.trim() || !createTempPassword) {
      setCreateModalError("All fields are required.");
      return;
    }
    if (createTempPassword.length < 8) {
      setCreateModalError("Temporary password must be at least 8 characters.");
      return;
    }

    try {
      setIsCreatingUser(true);
      await apiFetch("/api/v1/admin/users", {
        method: "POST",
        body: JSON.stringify({
          email: createEmail.trim(),
          displayName: createDisplayName.trim(),
          role: createRole,
          temporaryPassword: createTempPassword,
        }),
      });

      setIsCreateModalOpen(false);
      setCreateEmail("");
      setCreateDisplayName("");
      setCreateTempPassword("");
      setActionSuccess(`User '${createEmail.trim()}' created successfully with forced password change.`);
      fetchUsers();
    } catch (err: any) {
      if (err?.code === "USER_ALREADY_EXISTS") {
        setCreateModalError(`A user with email '${createEmail.trim()}' already exists.`);
      } else {
        setCreateModalError(err?.message || "Failed to create user.");
      }
    } finally {
      setIsCreatingUser(false);
    }
  };

  const handleToggleUserActive = async (user: UserItem) => {
    const newActiveState = !user.isActive;
    const confirmMsg = newActiveState
      ? `Activate user '${user.displayName}'?`
      : `Deactivate user '${user.displayName}'? All active sessions will be terminated immediately.`;

    if (!window.confirm(confirmMsg)) return;

    try {
      await apiFetch(`/api/v1/admin/users/${user.userId}`, {
        method: "PATCH",
        body: JSON.stringify({ isActive: newActiveState }),
      });
      setActionSuccess(
        `User '${user.displayName}' ${newActiveState ? "activated" : "deactivated"} successfully.`
      );
      fetchUsers();
    } catch (err: any) {
      alert(err?.message || "Failed to update user status.");
    }
  };

  const handleDeleteUser = async (user: UserItem) => {
    const confirmMsg = `Are you sure you want to permanently delete user '${user.displayName}' (${user.email})?\n\nThis will permanently delete the user account and any associated data.\n\nThis action cannot be undone.`;

    if (!window.confirm(confirmMsg)) return;

    try {
      setIsDeletingUser(user.userId);
      await apiFetch(`/api/v1/admin/users/${user.userId}`, {
        method: "DELETE",
      });
      setActionSuccess(`User '${user.displayName}' permanently deleted.`);
      fetchUsers();
    } catch (err: any) {
      alert(err?.message || "Failed to delete user.");
    } finally {
      setIsDeletingUser(null);
    }
  };

  const handleChangeRole = async (user: UserItem, newRole: "ADMIN" | "RECRUITER" | "REVIEWER") => {
    if (user.role === newRole) return;
    if (!window.confirm(`Change role of '${user.displayName}' from ${user.role} to ${newRole}? Target sessions will be revoked.`)) {
      return;
    }

    try {
      await apiFetch(`/api/v1/admin/users/${user.userId}`, {
        method: "PATCH",
        body: JSON.stringify({ role: newRole }),
      });
      setActionSuccess(`Role for '${user.displayName}' updated to ${newRole}.`);
      fetchUsers();
    } catch (err: any) {
      alert(err?.message || "Failed to update user role.");
    }
  };

  const handleResetPassword = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedUser) return;
    setResetModalError(null);

    if (resetTempPassword.length < 8) {
      setResetModalError("Temporary password must be at least 8 characters.");
      return;
    }

    try {
      setIsResetting(true);
      await apiFetch(`/api/v1/admin/users/${selectedUser.userId}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ temporaryPassword: resetTempPassword }),
      });

      setIsResetModalOpen(false);
      setResetTempPassword("");
      setActionSuccess(
        `Password reset successfully for '${selectedUser.displayName}'. Sessions revoked.`
      );
      fetchUsers();
    } catch (err: any) {
      setResetModalError(err?.message || "Failed to reset password.");
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <div className="admin-workspace-container" role="region" aria-label="Administrator Workspace">
      {/* Action Notification Toast */}
      {actionSuccess && (
        <div className="feedback-box success admin-toast" role="status" aria-live="polite">
          <Icon name="check" size={16} />
          <p className="feedback-text">{actionSuccess}</p>
          <button
            type="button"
            className="toast-close-btn"
            onClick={() => setActionSuccess(null)}
            aria-label="Dismiss message"
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      )}

      {activeTab === "users" && (
        <div className="admin-section-card">
          <div className="admin-section-header">
            <div>
              <h2 className="admin-section-title">Platform Users</h2>
              <p className="admin-section-subtitle">
                Manage accounts, roles, access states, and temporary credentials.
              </p>
            </div>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setIsCreateModalOpen(true)}
            >
              <Icon name="plus" size={16} />
              <span>Create New User</span>
            </button>
          </div>

          {userError && (
            <div className="feedback-box error" role="alert">
              <Icon name="alert-triangle" size={16} />
              <p className="feedback-text error">{userError}</p>
            </div>
          )}

          {isLoadingUsers ? (
            <div className="admin-table-loading">
              <div className="loading-spinner-sm" />
              <span>Loading internal accounts...</span>
            </div>
          ) : (
            <div className="admin-table-wrapper">
              <table className="admin-data-table" aria-label="Users Table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Password Status</th>
                    <th>Created</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="table-empty-cell">
                        No internal users found.
                      </td>
                    </tr>
                  ) : (
                    users.map((u) => (
                      <tr key={u.userId}>
                        <td>
                          <div className="table-user-cell">
                            <span className="table-user-name">{u.displayName}</span>
                            <span className="table-user-email">{u.email}</span>
                          </div>
                        </td>
                        <td>
                          <select
                            className="role-selector-inline"
                            value={u.role}
                            onChange={(e) =>
                              handleChangeRole(u, e.target.value as any)
                            }
                            aria-label={`Change role for ${u.displayName}`}
                          >
                            <option value="ADMIN">ADMIN</option>
                            <option value="RECRUITER">RECRUITER</option>
                            <option value="REVIEWER">REVIEWER</option>
                          </select>
                        </td>
                        <td>
                          <span
                            className={`status-pill ${
                              u.isActive ? "status-active" : "status-inactive"
                            }`}
                          >
                            {u.isActive ? "Active" : "Inactive"}
                          </span>
                        </td>
                        <td>
                          {u.mustChangePassword ? (
                            <span className="status-pill status-warn">
                              Change Required
                            </span>
                          ) : (
                            <span className="status-pill status-neutral">
                              Set
                            </span>
                          )}
                        </td>
                        <td className="table-date-cell">
                          {new Date(u.createdAt).toLocaleDateString()}
                        </td>
                        <td>
                          <div className="table-actions-group">
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => {
                                setSelectedUser(u);
                                setIsResetModalOpen(true);
                                setResetModalError(null);
                              }}
                              title="Reset temporary password"
                            >
                              Reset Password
                            </button>
                            <button
                              type="button"
                              className={`btn btn-sm ${
                                u.isActive ? "btn-secondary" : "btn-primary"
                              }`}
                              onClick={() => handleToggleUserActive(u)}
                            >
                              {u.isActive ? "Deactivate" : "Activate"}
                            </button>
                            {!u.isActive && (
                              <button
                                type="button"
                                className="btn btn-sm"
                                onClick={() => handleDeleteUser(u)}
                                disabled={isDeletingUser === u.userId}
                                title="Permanently drop user and all associated records"
                                style={{
                                  backgroundColor: "#dc2626",
                                  color: "#ffffff",
                                  borderColor: "#dc2626",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                }}
                              >
                                <Icon name="trash" size={13} />
                                <span>{isDeletingUser === u.userId ? "Deleting..." : "Delete"}</span>
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "candidates" && (
        <div className="admin-section-card">
          <div className="admin-section-header">
            <div>
              <h2 className="admin-section-title">Candidate Directory &amp; Accounts</h2>
              <p className="admin-section-subtitle">
                Manage candidate accounts, access states, and permanent data deletion. Candidates manage their own credentials.
              </p>
            </div>
            <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              <div className="search-input-wrapper" style={{ minWidth: "260px", position: "relative" }}>
                <Icon name="search" size={14} style={{ position: "absolute", left: "10px", top: "50%", transform: "translateY(-50%)", color: "var(--color-text-tertiary, #999)" }} />
                <input
                  type="text"
                  placeholder="Search candidates..."
                  value={candidateSearchQuery}
                  onChange={(e) => setCandidateSearchQuery(e.target.value)}
                  className="input-field search-input"
                  style={{ paddingLeft: "32px", height: "36px", fontSize: "0.85rem", width: "100%", borderRadius: "6px" }}
                  aria-label="Search candidates by name or email"
                />
                {candidateSearchQuery && (
                  <button
                    type="button"
                    onClick={() => setCandidateSearchQuery("")}
                    className="toast-close-btn"
                    style={{ position: "absolute", right: "8px", top: "50%", transform: "translateY(-50%)" }}
                    aria-label="Clear search"
                  >
                    <Icon name="x" size={12} />
                  </button>
                )}
              </div>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={fetchCandidates}
                title="Refresh candidate list"
              >
                <Icon name="refresh" size={14} />
                <span>Refresh</span>
              </button>
            </div>
          </div>

          {candidateError && (
            <div className="feedback-box error" role="alert">
              <Icon name="alert-triangle" size={16} />
              <p className="feedback-text error">{candidateError}</p>
            </div>
          )}

          {isLoadingCandidates ? (
            <div className="admin-table-loading">
              <div className="loading-spinner-sm" />
              <span>Loading candidate records...</span>
            </div>
          ) : (
            <div className="admin-table-wrapper">
              <table className="admin-data-table" aria-label="Candidates Table">
                <thead>
                  <tr>
                    <th>Candidate</th>
                    <th>Account &amp; Role</th>
                    <th>Status</th>
                    <th>Activity</th>
                    <th>Created</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const query = candidateSearchQuery.trim().toLowerCase();
                    const filtered = candidates.filter((c) =>
                      query
                        ? (c.fullName || "").toLowerCase().includes(query) ||
                          (c.email || "").toLowerCase().includes(query) ||
                          (c.accountEmail || "").toLowerCase().includes(query) ||
                          (c.resumeEmail || "").toLowerCase().includes(query) ||
                          (c.headline || "").toLowerCase().includes(query)
                        : true
                    );

                    if (filtered.length === 0) {
                      return (
                        <tr>
                          <td colSpan={6} className="table-empty-cell">
                            {candidateSearchQuery ? "No candidates match your search." : "No candidates found."}
                          </td>
                        </tr>
                      );
                    }

                    return filtered.map((cand) => (
                      <tr key={cand.id}>
                        <td>
                          <div className="table-user-cell">
                            <span className="table-user-name">{cand.fullName || "Unnamed Candidate"}</span>
                            <span className="table-user-email">{cand.email || "No email registered"}</span>
                            {cand.resumeEmail && cand.email && cand.resumeEmail.toLowerCase() !== cand.email.toLowerCase() && (
                              <span style={{ fontSize: "0.75rem", color: "var(--color-text-tertiary, #888)", marginTop: "1px" }}>
                                Resume email: {cand.resumeEmail}
                              </span>
                            )}
                            {cand.headline && (
                              <span style={{ fontSize: "0.75rem", color: "var(--color-text-tertiary, #888)", marginTop: "2px" }}>
                                {cand.headline}
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                            <span
                              style={{
                                display: "inline-block",
                                padding: "2px 8px",
                                borderRadius: "4px",
                                fontSize: "0.75rem",
                                fontWeight: 600,
                                backgroundColor: "rgba(100, 116, 139, 0.12)",
                                color: "#334155",
                                width: "fit-content",
                              }}
                            >
                              CANDIDATE
                            </span>
                            <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #666)" }}>
                              {cand.hasAccount ? "Portal Account" : "Resume Profile"}
                            </span>
                          </div>
                        </td>
                        <td>
                          <span
                            className={`status-pill ${
                              cand.isActive ? "status-active" : "status-inactive"
                            }`}
                          >
                            {cand.isActive ? "Active" : "Deactivated"}
                          </span>
                        </td>
                        <td>
                          <div style={{ fontSize: "0.8rem", color: "var(--color-text-secondary, #555)" }}>
                            <span>{cand.applicationsCount} application{cand.applicationsCount === 1 ? "" : "s"}</span>
                            <br />
                            <span style={{ color: "var(--color-text-tertiary, #888)" }}>
                              {cand.interviewsCount} interview{cand.interviewsCount === 1 ? "" : "s"}
                            </span>
                          </div>
                        </td>
                        <td className="table-date-cell">
                          {cand.createdAt ? new Date(cand.createdAt).toLocaleDateString() : "—"}
                        </td>
                        <td>
                          <div className="table-actions-group">
                            <button
                              type="button"
                              className={`btn btn-sm ${
                                cand.isActive ? "btn-secondary" : "btn-primary"
                              }`}
                              disabled={isUpdatingCandidate === cand.id || isDeletingCandidate === cand.id}
                              onClick={() => handleToggleCandidateActive(cand)}
                            >
                              {cand.isActive ? "Deactivate" : "Activate"}
                            </button>
                            {!cand.isActive && (
                              <button
                                type="button"
                                className="btn btn-danger btn-sm"
                                disabled={isDeletingCandidate === cand.id || isUpdatingCandidate === cand.id}
                                onClick={() => handleDeleteCandidate(cand)}
                                title="Permanently drop candidate and all related records"
                                style={{
                                  backgroundColor: "#dc2626",
                                  color: "#ffffff",
                                  borderColor: "#dc2626",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                }}
                              >
                                <Icon name="trash" size={13} />
                                <span>{isDeletingCandidate === cand.id ? "Dropping..." : "Delete"}</span>
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ));
                  })()}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === "events" && (
        <div className="admin-section-card">
          <div className="admin-section-header">
            <div>
              <h2 className="admin-section-title">Security &amp; Audit Logs</h2>
              <p className="admin-section-subtitle">
                Immutable, privacy-safe record of authentication and administrative actions.
              </p>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => fetchSecurityEvents(eventsOffset)}
            >
              <Icon name="refresh" size={14} />
              <span>Refresh Logs</span>
            </button>
          </div>

          {isLoadingEvents ? (
            <div className="admin-table-loading">
              <div className="loading-spinner-sm" />
              <span>Loading audit logs...</span>
            </div>
          ) : (
            <div className="admin-table-wrapper">
              <table className="admin-data-table" aria-label="Security Events Table">
                <thead>
                  <tr>
                    <th>Occurred At</th>
                    <th>Event Type</th>
                    <th>Result</th>
                    <th>Reason Code</th>
                    <th>Resource</th>
                  </tr>
                </thead>
                <tbody>
                  {events.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="table-empty-cell">
                        No security events recorded.
                      </td>
                    </tr>
                  ) : (
                    events.map((ev) => (
                      <tr key={ev.eventId}>
                        <td className="table-date-cell">
                          {new Date(ev.occurredAt).toLocaleString()}
                        </td>
                        <td>
                          <code className="event-type-tag">{ev.eventType}</code>
                        </td>
                        <td>
                          <span
                            className={`status-pill ${
                              ev.result === "SUCCESS"
                                ? "status-active"
                                : "status-inactive"
                            }`}
                          >
                            {ev.result}
                          </span>
                        </td>
                        <td>
                          <span className="reason-code-text">{ev.reasonCode}</span>
                        </td>
                        <td>
                          <span className="resource-ref-text">
                            {ev.resourceType || "system"}
                          </span>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>

              {eventsTotal > 25 && (
                <div className="admin-pagination-bar">
                  <span className="pagination-summary">
                    Showing {eventsOffset + 1} - {Math.min(eventsOffset + 25, eventsTotal)} of {eventsTotal} events
                  </span>
                  <div className="pagination-controls">
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      disabled={eventsOffset === 0}
                      onClick={() => fetchSecurityEvents(Math.max(0, eventsOffset - 25))}
                    >
                      Previous
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      disabled={eventsOffset + 25 >= eventsTotal}
                      onClick={() => fetchSecurityEvents(eventsOffset + 25)}
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Create User Modal Dialog */}
      {isCreateModalOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="create-user-title">
          <div className="modal-card">
            <div className="modal-header">
              <h3 id="create-user-title" className="modal-title">Create Internal User</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setIsCreateModalOpen(false)}
                aria-label="Close dialog"
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            {createModalError && (
              <div className="feedback-box error" role="alert">
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">{createModalError}</p>
              </div>
            )}

            <form onSubmit={handleCreateUser} className="modal-form" noValidate>
              <div className="form-group">
                <label htmlFor="create-email" className="form-label">
                  Email Address
                </label>
                <input
                  id="create-email"
                  type="email"
                  className="form-input"
                  placeholder="recruiter@company.com"
                  value={createEmail}
                  onChange={(e) => setCreateEmail(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="create-display-name" className="form-label">
                  Display Name
                </label>
                <input
                  id="create-display-name"
                  type="text"
                  className="form-input"
                  placeholder="Jane Recruiter"
                  value={createDisplayName}
                  onChange={(e) => setCreateDisplayName(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="create-role" className="form-label">
                  Platform Role
                </label>
                <select
                  id="create-role"
                  className="form-input"
                  value={createRole}
                  onChange={(e) => setCreateRole(e.target.value as any)}
                >
                  <option value="RECRUITER">RECRUITER (Candidate Directory, Resumes, Profiles)</option>
                  <option value="REVIEWER">REVIEWER (Evaluation &amp; Scorecards)</option>
                  <option value="ADMIN">ADMIN (User Management &amp; Audit Logs)</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="create-temp-password" className="form-label">
                  Temporary Password (min. 8 characters)
                </label>
                <input
                  id="create-temp-password"
                  type="password"
                  className="form-input"
                  placeholder="TemporaryPass123!"
                  value={createTempPassword}
                  onChange={(e) => setCreateTempPassword(e.target.value)}
                  required
                />
                <span className="form-hint-text">
                  User will be forced to change this password upon first sign-in.
                </span>
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsCreateModalOpen(false)}
                  disabled={isCreatingUser}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isCreatingUser}
                >
                  {isCreatingUser ? "Creating..." : "Create Account"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Reset Password Modal Dialog */}
      {isResetModalOpen && selectedUser && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="reset-user-title">
          <div className="modal-card">
            <div className="modal-header">
              <h3 id="reset-user-title" className="modal-title">
                Reset Password for {selectedUser.displayName}
              </h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setIsResetModalOpen(false)}
                aria-label="Close dialog"
              >
                <Icon name="x" size={16} />
              </button>
            </div>

            {resetModalError && (
              <div className="feedback-box error" role="alert">
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">{resetModalError}</p>
              </div>
            )}

            <form onSubmit={handleResetPassword} className="modal-form" noValidate>
              <p className="modal-description">
                Setting a new temporary password will invalidate all current active sessions for <strong>{selectedUser.email}</strong> and require a password change on next login.
              </p>

              <div className="form-group">
                <label htmlFor="reset-temp-pw" className="form-label">
                  New Temporary Password
                </label>
                <input
                  id="reset-temp-pw"
                  type="password"
                  className="form-input"
                  placeholder="NewTemporaryPass123!"
                  value={resetTempPassword}
                  onChange={(e) => setResetTempPassword(e.target.value)}
                  required
                />
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsResetModalOpen(false)}
                  disabled={isResetting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isResetting}
                >
                  {isResetting ? "Resetting..." : "Reset & Invalidate Sessions"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {activeTab === "ats_integrations" && <AtsConnectionsAdmin />}

      {/* AI Providers Section */}
      {activeTab === "ai_providers" && (
        <div className="admin-section-card">
          <div className="admin-section-header">
            <div>
              <h2 className="admin-section-title">Local AI Configuration</h2>
              <p className="admin-section-subtitle">
                Manage local LM Studio inference engine and bounded AI wording assistance.
              </p>
            </div>
            <div className="admin-header-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={fetchProviders}
                disabled={isLoadingProviders}
              >
                <Icon name="refresh" size={16} />
                <span>Refresh Status</span>
              </button>
              <button
                type="button"
                className="btn btn-outline"
                onClick={() => setIsCreateProviderModalOpen(true)}
              >
                <Icon name="plus" size={16} />
                <span>New Profile</span>
              </button>
            </div>
          </div>

          {providerError && (
            <div className="feedback-box error admin-error-banner" role="alert">
              <Icon name="alert-triangle" size={16} />
              <p className="feedback-text">{providerError}</p>
            </div>
          )}

          {isLoadingProviders ? (
            <div className="admin-table-loading">
              <div className="loading-spinner-sm" />
              <span>Checking AI provider status...</span>
            </div>
          ) : (
            <>
              {/* PRIMARY LOCAL AI HERO CARD */}
              {(() => {
                const activeProfile = providers.find((p: any) => p.enabled && !p.is_archived && !p.isArchived);
                const localProfile = activeProfile || providers.find((p: any) => 
                  ((p.providerType || p.provider_type) === "LOCAL_OPENAI_COMPATIBLE") &&
                  !p.is_archived && !p.isArchived
                );

                if (localProfile) {
                  const pId = localProfile.profileId || (localProfile as any).profile_id;
                  const pName = localProfile.displayName || (localProfile as any).display_name;
                  const pModel = localProfile.modelIdentifier || (localProfile as any).model_identifier;
                  const pCompat = localProfile.compatibilityStatus || (localProfile as any).compatibility_status;
                  const pEnabled = localProfile.enabled;
                  const pReport = localProfile.lastCompatibilityReport || (localProfile as any).last_compatibility_report;
                  const isHealthy = pCompat === "HEALTHY" || pCompat === "COMPATIBLE";
                  const wordingPassed = pReport?.question_wording_passed ?? isHealthy;

                  const handleSelectModel = async (newModel: string) => {
                    if (!newModel || newModel === pModel) return;
                    try {
                      setProviderError(null);
                      await apiFetch(`/api/v1/admin/provider-profiles/${pId}/select-model`, {
                        method: "POST",
                        body: JSON.stringify({ model_identifier: newModel }),
                      });
                      setActionSuccess(`Active model changed to '${newModel}'. Run compatibility test to verify.`);
                      fetchProviders();
                    } catch (err: any) {
                      setProviderError(err?.message || "Failed to update model identifier.");
                    }
                  };

                  return (
                    <div className={`local-ai-hero-card ${pEnabled ? "hero-active" : ""}`}>
                      <div className="local-ai-hero-header">
                        <div className="local-ai-hero-title-group">
                          <div className="local-ai-hero-icon">
                            <Icon name="sparkles" size={24} />
                          </div>
                          <div>
                            <h3 className="local-ai-hero-title">
                              {pName} {pEnabled && <span className="status-pill status-active" style={{ marginLeft: "8px", verticalAlign: "middle" }}>Active</span>}
                            </h3>
                            <p className="local-ai-hero-subtitle">
                              Local OpenAI-compatible engine running securely on this workstation.
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span
                            className={`compat-status-tag ${
                              isHealthy
                                ? "status-compatible"
                                : pCompat === "INCOMPATIBLE"
                                ? "status-incompatible"
                                : "status-untested"
                            }`}
                            onClick={() => {
                              if (pReport) {
                                setCompatibilityReport(pReport);
                                setSelectedReportProfile(localProfile);
                                setIsReportModalOpen(true);
                              }
                            }}
                            style={{ cursor: pReport ? "pointer" : "default" }}
                            title={pReport ? "Click to view diagnostic report" : undefined}
                          >
                            {isHealthy ? "✓ Question Wording: Compatible" : pCompat === "INCOMPATIBLE" ? "⚠ Question Wording: Incompatible" : "Not Tested"}
                          </span>
                        </div>
                      </div>

                      <div className="local-ai-hero-grid">
                        <div className="local-ai-hero-item">
                          <span className="local-ai-hero-label">Connection</span>
                          <span className="local-ai-hero-value">
                            <span className="humanized-pill local">Available (127.0.0.1:1234)</span>
                          </span>
                        </div>
                        <div className="local-ai-hero-item">
                          <span className="local-ai-hero-label">Selected Model</span>
                          <div className="local-ai-model-select-wrapper">
                            <select
                              className="form-select text-xs py-1"
                              value={pModel || ""}
                              onChange={(e) => handleSelectModel(e.target.value)}
                            >
                              <option value="qwen/qwen3-4b-2507">
                                Qwen3 4B Instruct — Recommended for question wording
                              </option>
                              <option value="qwen/qwen3-4b-thinking-2507">
                                Qwen3 4B Thinking — Not recommended for this task
                              </option>
                              {discoveredModels
                                .filter((m) => m !== "qwen/qwen3-4b-2507" && m !== "qwen/qwen3-4b-thinking-2507" && !m.includes("embed"))
                                .map((m) => (
                                  <option key={m} value={m}>
                                    {m}
                                  </option>
                                ))}
                            </select>
                            <button
                              type="button"
                              className="btn btn-outline btn-xs"
                              onClick={() => handleAutoDiscoverModels()}
                              disabled={isDiscoveringModels}
                              title="Refresh models from LM Studio"
                              style={{ whiteSpace: "nowrap" }}
                            >
                              <Icon name="refresh" size={12} />
                              <span>{isDiscoveringModels ? "..." : "Refresh"}</span>
                            </button>
                          </div>
                        </div>
                        <div className="local-ai-hero-item">
                          <span className="local-ai-hero-label">Question Wording</span>
                          <span className="local-ai-hero-value">
                            <span className={`status-pill ${isHealthy ? "status-active" : "status-inactive"}`}>
                              {isHealthy ? "Compatible" : (pCompat === "INCOMPATIBLE" ? "Incompatible" : "Not Tested")}
                            </span>
                          </span>
                        </div>
                        <div className="local-ai-hero-item">
                          <span className="local-ai-hero-label">Recommended Action</span>
                          <span className="local-ai-hero-value text-xs">
                            {pCompat === "INCOMPATIBLE" || (pModel && pModel.includes("thinking")) ? (
                              <span className="status-warn" style={{ color: "#d97706", fontWeight: 600 }}>Load and select Qwen3 4B Instruct in LM Studio</span>
                            ) : (
                              <span className="text-muted" style={{ color: "#16a34a", fontWeight: 500 }}>Ready for bounded interview question rewording</span>
                            )}
                          </span>
                        </div>
                      </div>

                      <div className="local-ai-hero-actions">
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleTestCompatibility(localProfile)}
                          disabled={isTestingProvider === pId || isTogglingProvider === pId}
                        >
                          <Icon name="refresh" size={14} />
                          <span>{isTestingProvider === pId ? "Testing Wording & Connection..." : "Test Compatibility"}</span>
                        </button>
                        {pEnabled ? (
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => handleDisableProvider(localProfile)}
                            disabled={isTogglingProvider === pId || isTestingProvider === pId}
                          >
                            <span>{isTogglingProvider === pId ? "Deactivating..." : "Deactivate AI"}</span>
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            onClick={() => handleOpenActivateModal(localProfile)}
                            disabled={isTogglingProvider === pId || isTestingProvider === pId}
                          >
                            <Icon name="sparkles" size={14} />
                            <span>Activate Local AI</span>
                          </button>
                        )}
                      </div>
                    </div>
                  );
                } else {
                  return (
                    <div className="guided-setup-box">
                      <h3 className="guided-setup-title">
                        <Icon name="sparkles" size={18} />
                        <span>Setup Local LM Studio AI</span>
                      </h3>
                      <p className="guided-setup-desc">
                        No active Local AI provider profile found. Start LM Studio on port <strong>1234</strong>, load your Qwen model, and click below to auto-discover models and configure your local profile.
                      </p>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={async () => {
                          await handleAutoDiscoverModels();
                          setIsCreateProviderModalOpen(true);
                          setNewDisplayName("Local LM Studio (Qwen3-4B)");
                          setNewBaseUrl("http://127.0.0.1:1234/v1");
                        }}
                      >
                        <Icon name="plus" size={16} />
                        <span>Configure Local LM Studio</span>
                      </button>
                    </div>
                  );
                }
              })()}

              {/* COLLAPSIBLE ADVANCED SETTINGS */}
              <div className="advanced-settings-accordion">
                <div
                  className={`advanced-settings-header ${showAdvancedSettings ? "open" : ""}`}
                  onClick={() => setShowAdvancedSettings(!showAdvancedSettings)}
                >
                  <div className="advanced-settings-title">
                    <Icon name="layers" size={16} />
                    <span>Advanced Provider Profiles &amp; History ({providers.filter((p: any) => !p.is_archived && !p.isArchived).length} profiles, {providers.filter((p: any) => p.is_archived || p.isArchived).length} archived)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted">
                      {showAdvancedSettings ? "Hide details" : "Show details"}
                    </span>
                    <Icon name={showAdvancedSettings ? "chevron-up" : "chevron-down"} size={16} />
                  </div>
                </div>

                {showAdvancedSettings && (
                  <div className="advanced-settings-content">
                    <div className="admin-table-wrapper">
                      <table className="admin-data-table" aria-label="AI Providers Table">
                        <thead>
                          <tr>
                            <th>Display Name</th>
                            <th>Provider Class</th>
                            <th>Model</th>
                            <th>Base URL</th>
                            <th>Compatibility</th>
                            <th>Status</th>
                            <th>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {providers.filter((p: any) => !p.is_archived && !p.isArchived).length === 0 ? (
                            <tr>
                              <td colSpan={7} className="table-empty-cell">
                                No active or standby provider profiles configured.
                              </td>
                            </tr>
                          ) : (
                            providers
                              .filter((p: any) => !p.is_archived && !p.isArchived)
                              .map((p: any) => {
                                const pId = p.profileId || p.profile_id;
                                const pName = p.displayName || p.display_name;
                                const pType = p.providerType || p.provider_type;
                                const pModel = p.modelIdentifier || p.model_identifier;
                                const pEgress = p.egressClass || p.egress_class;
                                const pBaseUrl = p.baseUrlMasked || p.base_url_masked;
                                const pCompat = p.compatibilityStatus || p.compatibility_status;
                                const pEnabled = p.enabled;
                                const pReport = p.lastCompatibilityReport || p.last_compatibility_report;

                                return (
                                  <tr key={pId} className={pEnabled ? "row-active-provider" : ""}>
                                    <td>
                                      <div className="provider-name-cell">
                                        <strong>{pName}</strong>
                                        <span className="profile-version-tag">v{p.version}</span>
                                      </div>
                                    </td>
                                    <td>
                                      <span
                                        className={`humanized-pill ${
                                          pEgress === "LOCAL_ONLY" ? "local" : "remote"
                                        }`}
                                      >
                                        {pEgress === "LOCAL_ONLY" ? "Local AI" : "Remote Provider"}
                                      </span>
                                    </td>
                                    <td>
                                      {pModel ? (
                                        <code className="text-xs">{pModel}</code>
                                      ) : (
                                        <span className="text-muted text-xs">Default</span>
                                      )}
                                    </td>
                                    <td>
                                      <span className="base-url-text text-xs">
                                        {pBaseUrl || <em className="text-muted">Standard Local (1234)</em>}
                                      </span>
                                    </td>
                                    <td>
                                      <span
                                        className={`compat-status-tag ${
                                          pCompat === "HEALTHY" || pCompat === "COMPATIBLE"
                                            ? "status-compatible"
                                            : pCompat === "INCOMPATIBLE"
                                            ? "status-incompatible"
                                            : "status-untested"
                                        }`}
                                        onClick={() => {
                                          if (pReport) {
                                            setCompatibilityReport(pReport);
                                            setSelectedReportProfile(p);
                                            setIsReportModalOpen(true);
                                          }
                                        }}
                                        style={{ cursor: pReport ? "pointer" : "default" }}
                                        title={pReport ? "Click to view diagnostic report" : undefined}
                                      >
                                        {pCompat === "HEALTHY" || pCompat === "COMPATIBLE" ? "✓ Compatible" : pCompat === "INCOMPATIBLE" ? "⚠ Incompatible" : "Not Tested"}
                                      </span>
                                    </td>
                                    <td>
                                      <span
                                        className={`status-pill ${
                                          pEnabled ? "status-active" : "status-inactive"
                                        }`}
                                      >
                                        {pEnabled ? "Active" : "Disabled"}
                                      </span>
                                    </td>
                                    <td>
                                      <div className="table-actions-cell" style={{ display: "flex", gap: "6px" }}>
                                        <button
                                          type="button"
                                          className="btn btn-secondary btn-sm"
                                          onClick={() => handleTestCompatibility(p)}
                                          disabled={isTestingProvider === pId || isTogglingProvider === pId}
                                        >
                                          {isTestingProvider === pId ? "Testing..." : "Test"}
                                        </button>
                                        {pEnabled ? (
                                          <button
                                            type="button"
                                            className="btn btn-secondary btn-sm"
                                            onClick={() => handleDisableProvider(p)}
                                            disabled={isTogglingProvider === pId || isTestingProvider === pId}
                                          >
                                            {isTogglingProvider === pId ? "Disabling..." : "Disable"}
                                          </button>
                                        ) : (
                                          <button
                                            type="button"
                                            className="btn btn-primary btn-sm"
                                            onClick={() => handleOpenActivateModal(p)}
                                            disabled={isTogglingProvider === pId || isTestingProvider === pId}
                                          >
                                            Activate
                                          </button>
                                        )}
                                      </div>
                                    </td>
                                  </tr>
                                );
                              })
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* Diagnostic Report Modal Dialog */}
      {isReportModalOpen && compatibilityReport && selectedReportProfile && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="report-modal-title">
          <div className="modal-dialog" style={{ maxWidth: "580px" }}>
            <div className="modal-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", borderBottom: "1px solid var(--border-subtle, #e2e8f0)", paddingBottom: "12px" }}>
              <h3 id="report-modal-title" className="modal-title" style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700 }}>
                Diagnostic Report: {selectedReportProfile.displayName}
              </h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setIsReportModalOpen(false)}
                aria-label="Close modal"
                style={{ background: "none", border: "none", cursor: "pointer", color: "#64748b", padding: "4px" }}
              >
                <Icon name="x" size={18} />
              </button>
            </div>

            <div className="modal-form" style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px", background: compatibilityReport.is_compatible ? "#f0fdf4" : "#fef2f2", borderRadius: "8px", border: `1px solid ${compatibilityReport.is_compatible ? "#bbf7d0" : "#fecaca"}` }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "13px", fontWeight: 600, color: "#475569" }}>Status:</span>
                  <span className={compatibilityReport.is_compatible ? "status-pill status-active" : "status-pill status-inactive"}>
                    {compatibilityReport.is_compatible ? "COMPATIBLE / HEALTHY" : "INCOMPATIBLE"}
                  </span>
                </div>
                <div>
                  <small style={{ color: "#64748b", fontSize: "12px", fontWeight: 500 }}>Latency: {compatibilityReport.latency_ms} ms</small>
                </div>
              </div>

              {compatibilityReport.safe_failure_message && (
                <div className="feedback-box error" style={{ margin: 0 }}>
                  <Icon name="alert-triangle" size={16} />
                  <p className="feedback-text">
                    <strong>{compatibilityReport.safe_failure_code}: </strong>
                    {compatibilityReport.safe_failure_message}
                  </p>
                </div>
              )}

              <div className="diagnostic-checklist" style={{ fontSize: "13px", lineHeight: "1.8", background: "#f8fafc", padding: "14px 16px", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #f1f5f9", paddingBottom: "4px" }}>
                  <span>Reachability Check</span>
                  <span style={{ fontWeight: 600, color: compatibilityReport.reachability_passed ? "#16a34a" : "#dc2626" }}>
                    {compatibilityReport.reachability_passed ? "✓ Passed" : "✗ Failed"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #f1f5f9", padding: "4px 0" }}>
                  <span>Authentication</span>
                  <span style={{ fontWeight: 600, color: compatibilityReport.auth_passed ? "#16a34a" : "#64748b" }}>
                    {compatibilityReport.auth_passed ? "✓ Verified" : "✗ Not Applicable"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #f1f5f9", padding: "4px 0" }}>
                  <span>Model Discovery</span>
                  <span style={{ fontWeight: 600, color: compatibilityReport.model_discovered ? "#16a34a" : "#dc2626" }}>
                    {compatibilityReport.model_discovered ? "✓ Model Verified" : "✗ Model Not Found"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid #f1f5f9", padding: "4px 0" }}>
                  <span>Dry-Run Completion</span>
                  <span style={{ fontWeight: 600, color: compatibilityReport.dry_run_completion_passed ? "#16a34a" : "#dc2626" }}>
                    {compatibilityReport.dry_run_completion_passed ? "✓ Non-streaming Verified" : "✗ Failed"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", paddingTop: "4px" }}>
                  <span>Unicode Handling</span>
                  <span style={{ fontWeight: 600, color: compatibilityReport.unicode_handling_passed ? "#16a34a" : "#dc2626" }}>
                    {compatibilityReport.unicode_handling_passed ? "✓ Passed" : "✗ Failed"}
                  </span>
                </div>
                {compatibilityReport.discovered_model_ids && compatibilityReport.discovered_model_ids.length > 0 && (
                  <div style={{ marginTop: "10px", paddingTop: "8px", borderTop: "1px dashed #cbd5e1" }}>
                    <strong style={{ fontSize: "12px", color: "#334155" }}>Discovered Models on Endpoint:</strong>
                    <div style={{ fontFamily: "monospace", fontSize: "11px", marginTop: "4px", background: "#ffffff", padding: "6px 8px", borderRadius: "4px", border: "1px solid #e2e8f0", wordBreak: "break-all" }}>
                      {compatibilityReport.discovered_model_ids.join(", ")}
                    </div>
                  </div>
                )}
              </div>

              {selectedReportProfile.modelSource && (
                <div style={{ fontSize: "12px", color: "#64748b" }}>
                  <strong>Model Source:</strong> {selectedReportProfile.modelSource}
                </div>
              )}
              {selectedReportProfile.modelLicence && (
                <div style={{ fontSize: "12px", color: "#64748b" }}>
                  <strong>Licence:</strong> {selectedReportProfile.modelLicence}
                </div>
              )}
              {selectedReportProfile.modelChecksum && (
                <div style={{ fontSize: "12px", color: "#64748b" }}>
                  <strong>SHA-256 Checksum:</strong> <code style={{ fontSize: "11px" }}>{selectedReportProfile.modelChecksum}</code>
                </div>
              )}

              <div className="modal-actions" style={{ display: "flex", justifyContent: "flex-end", marginTop: "8px" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsReportModalOpen(false)}
                >
                  Close Report
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create Provider Modal */}
      {isCreateProviderModalOpen && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="create-provider-title">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3 id="create-provider-title" className="modal-title">
                Add AI Provider Profile
              </h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setIsCreateProviderModalOpen(false)}
                aria-label="Close modal"
              >
                <Icon name="x" size={18} />
              </button>
            </div>

            {createProviderModalError && (
              <div className="feedback-box error" role="alert">
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">{createProviderModalError}</p>
              </div>
            )}

            <form onSubmit={handleCreateProvider} className="modal-form" noValidate>
              <div className="form-group">
                <label htmlFor="provider-display-name" className="form-label">
                  Display Name *
                </label>
                <input
                  id="provider-display-name"
                  type="text"
                  className="form-input"
                  placeholder="e.g. Local LM Studio Qwen3 / OpenAI Production"
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="provider-type-select" className="form-label">
                  Provider Backend Type *
                </label>
                <select
                  id="provider-type-select"
                  className="form-select"
                  value={newProviderType}
                  onChange={(e) => setNewProviderType(e.target.value as any)}
                >
                  <option value="LOCAL_OPENAI_COMPATIBLE">Local OpenAI-Compatible (LM Studio / llama.cpp)</option>
                  <option value="REMOTE_OPENAI_COMPATIBLE">Remote OpenAI-Compatible (OpenAI / Azure)</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="provider-egress-class" className="form-label">
                  Egress Boundary Class *
                </label>
                <select
                  id="provider-egress-class"
                  className="form-select"
                  value={newEgressClass}
                  onChange={(e) => setNewEgressClass(e.target.value as any)}
                >
                  <option value="LOCAL_ONLY">LOCAL_ONLY (Enforces loopback / local boundary)</option>
                  <option value="APPROVED_REMOTE">APPROVED_REMOTE (Requires HTTPS & Host Allowlist)</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="provider-base-url" className="form-label">
                  Base URL (optional for standard loopback)
                </label>
                <input
                  id="provider-base-url"
                  type="url"
                  className="form-input"
                  placeholder={
                    newEgressClass === "LOCAL_ONLY"
                      ? "http://127.0.0.1:1234/v1"
                      : "https://api.openai.com/v1"
                  }
                  value={newBaseUrl}
                  onChange={(e) => setNewBaseUrl(e.target.value)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="provider-model-id" className="form-label flex items-center justify-between">
                  <span>Model Identifier</span>
                  {newEgressClass === "LOCAL_ONLY" && (
                    <button
                      type="button"
                      className="btn btn-outline btn-xs"
                      onClick={() => handleAutoDiscoverModels(newBaseUrl || "http://127.0.0.1:1234/v1")}
                      disabled={isDiscoveringModels}
                    >
                      <Icon name="refresh" size={12} />
                      <span>{isDiscoveringModels ? "Querying LM Studio..." : "Discover from LM Studio"}</span>
                    </button>
                  )}
                </label>
                {discoveredModels.length > 0 ? (
                  <div className="model-select-group">
                    <select
                      id="provider-model-id-select"
                      className="form-select flex-1"
                      value={newModelId}
                      onChange={(e) => setNewModelId(e.target.value)}
                    >
                      <option value="">-- Select discovered model from LM Studio --</option>
                      {discoveredModels.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setDiscoveredModels([])}
                      title="Enter custom model ID"
                    >
                      Custom
                    </button>
                  </div>
                ) : (
                  <input
                    id="provider-model-id"
                    type="text"
                    className="form-input"
                    placeholder="e.g. qwen/qwen3-4b-thinking-2507"
                    value={newModelId}
                    onChange={(e) => setNewModelId(e.target.value)}
                  />
                )}
                <span className="form-help-text">
                  Must match the model loaded in LM Studio.
                </span>
              </div>

              <div className="form-group">
                <label htmlFor="provider-secret-ref" className="form-label">
                  Secret Reference (Controlled Format: env:VAR_NAME)
                </label>
                <input
                  id="provider-secret-ref"
                  type="text"
                  className="form-input"
                  placeholder="e.g. env:LLM_API_KEY"
                  value={newSecretRef}
                  onChange={(e) => setNewSecretRef(e.target.value)}
                />
                <span className="form-help-text">
                  Plaintext credentials are never accepted. Specify an environment variable reference.
                </span>
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsCreateProviderModalOpen(false)}
                  disabled={isCreatingProvider}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isCreatingProvider}
                >
                  {isCreatingProvider ? "Saving Profile..." : "Create Provider Profile"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Activate Provider Confirmation Modal */}
      {isActivateModalOpen && targetActivateProfile && (
        <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="activate-provider-title">
          <div className="modal-dialog">
            <div className="modal-header">
              <h3 id="activate-provider-title" className="modal-title">
                Activate Provider: {targetActivateProfile.displayName}
              </h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => setIsActivateModalOpen(false)}
                aria-label="Close modal"
              >
                <Icon name="x" size={18} />
              </button>
            </div>

            {activateModalError && (
              <div className="feedback-box error" role="alert">
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">{activateModalError}</p>
              </div>
            )}

            <form onSubmit={handleConfirmActivate} className="modal-form" noValidate>
              <p className="modal-description">
                Activating this provider will immediately route all future AI interview and assistance requests to <strong>{targetActivateProfile.displayName}</strong>. Any currently active provider in this organization will be disabled.
              </p>

              {targetActivateProfile.egressClass === "APPROVED_REMOTE" && (
                <div className="remote-egress-warning-box">
                  <div className="warning-box-header">
                    <Icon name="shield" size={18} />
                    <strong>Remote Data Egress Confirmation Required</strong>
                  </div>
                  <p className="warning-box-body">
                    This profile is configured as an <strong>APPROVED_REMOTE</strong> provider ({targetActivateProfile.baseUrlMasked}). Activating it means prompt payloads and bounded interview requests may egress across the approved network boundary.
                  </p>
                  <label className="checkbox-label" htmlFor="confirm-remote-egress-cb">
                    <input
                      id="confirm-remote-egress-cb"
                      type="checkbox"
                      checked={confirmRemoteEgress}
                      onChange={(e) => setConfirmRemoteEgress(e.target.checked)}
                      required
                    />
                    <span>I confirm and authorize remote data egress for this provider profile.</span>
                  </label>
                </div>
              )}

              <div className="form-group">
                <label htmlFor="activate-reason-input" className="form-label">
                  Activation Audit Reason (optional)
                </label>
                <input
                  id="activate-reason-input"
                  type="text"
                  className="form-input"
                  placeholder="e.g. Switched to local Qwen3 instance for testing"
                  value={activateReason}
                  onChange={(e) => setActivateReason(e.target.value)}
                />
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setIsActivateModalOpen(false)}
                  disabled={isTogglingProvider !== null}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={
                    isTogglingProvider !== null ||
                    (targetActivateProfile.egressClass === "APPROVED_REMOTE" && !confirmRemoteEgress)
                  }
                >
                  {isTogglingProvider ? "Activating..." : "Confirm Activation"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
