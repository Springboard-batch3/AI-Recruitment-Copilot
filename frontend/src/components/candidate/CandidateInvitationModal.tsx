import React, { useState, useEffect, useCallback } from "react";
import {
  getCandidateAccessStatus,
  createCandidateInvitation,
  listCandidateInvitations,
  revokeCandidateInvitation,
  revokeCandidateAccess,
  CandidateAccessStatusResponse,
  CandidateInvitationSummary,
  CreateInvitationResponse,
} from "../../api/candidateAuth";
import { Icon } from "../Icon";

interface CandidateInvitationModalProps {
  candidateId: string;
  candidateName: string;
  isOpen: boolean;
  onClose: () => void;
  onStatusChange?: () => void;
}

export function CandidateInvitationModal({
  candidateId,
  candidateName,
  isOpen,
  onClose,
  onStatusChange,
}: CandidateInvitationModalProps) {
  const [accessStatus, setAccessStatus] = useState<CandidateAccessStatusResponse | null>(null);
  const [invitations, setInvitations] = useState<CandidateInvitationSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isCreating, setIsCreating] = useState<boolean>(false);
  const [isRevokingAccess, setIsRevokingAccess] = useState<boolean>(false);
  const [confirmRevoke, setConfirmRevoke] = useState<boolean>(false);
  const [createdResult, setCreatedResult] = useState<CreateInvitationResponse | null>(null);
  const [copied, setCopied] = useState<boolean>(false);
  const [portalCopied, setPortalCopied] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [validHours, setValidHours] = useState<number>(24);

  const fetchData = useCallback(async () => {
    if (!candidateId) return;
    setIsLoading(true);
    setError(null);
    try {
      const [statusRes, invList] = await Promise.all([
        getCandidateAccessStatus(candidateId),
        listCandidateInvitations(candidateId).catch(() => []),
      ]);
      setAccessStatus(statusRes);
      setInvitations(invList || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load candidate access status.");
    } finally {
      setIsLoading(false);
    }
  }, [candidateId]);

  useEffect(() => {
    if (isOpen) {
      fetchData();
      setCreatedResult(null);
      setCopied(false);
      setPortalCopied(false);
      setConfirmRevoke(false);
      setError(null);
      setSuccessMessage(null);
    }
  }, [isOpen, fetchData]);

  if (!isOpen) return null;

  const handleCreateInvitation = async () => {
    if (isCreating) return;
    setIsCreating(true);
    setError(null);
    setCreatedResult(null);
    setSuccessMessage(null);
    try {
      const result = await createCandidateInvitation(candidateId, validHours);
      setCreatedResult(result);
      await fetchData();
      if (onStatusChange) onStatusChange();
    } catch (err: any) {
      setError(err?.message || "Failed to create invitation link.");
    } finally {
      setIsCreating(false);
    }
  };

  const handleRevoke = async (invitationId: string) => {
    setError(null);
    setSuccessMessage(null);
    try {
      await revokeCandidateInvitation(invitationId);
      setCreatedResult(null);
      setSuccessMessage("Invitation link has been revoked.");
      setTimeout(() => setSuccessMessage(null), 4000);
      await fetchData();
      if (onStatusChange) onStatusChange();
    } catch (err: any) {
      setError(err?.message || "Failed to revoke invitation.");
    }
  };

  const handleRevokeCandidateAccess = async () => {
    if (isRevokingAccess) return;
    setIsRevokingAccess(true);
    setError(null);
    setSuccessMessage(null);
    try {
      await revokeCandidateAccess(candidateId);
      setConfirmRevoke(false);
      setSuccessMessage("Candidate portal access has been revoked.");
      setTimeout(() => setSuccessMessage(null), 5000);
      await fetchData();
      if (onStatusChange) onStatusChange();
    } catch (err: any) {
      setError(err?.message || "Failed to revoke candidate access.");
    } finally {
      setIsRevokingAccess(false);
    }
  };

  const handleCopyLink = async () => {
    if (!createdResult?.activationUrl) return;
    try {
      await navigator.clipboard.writeText(createdResult.activationUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      setCopied(true);
    }
  };

  const portalUrl = `${window.location.origin}/candidate/login`;

  const handleCopyPortalUrl = async () => {
    try {
      await navigator.clipboard.writeText(portalUrl);
      setPortalCopied(true);
      setTimeout(() => setPortalCopied(false), 3000);
    } catch {
      setPortalCopied(true);
    }
  };

  const accountStatus = accessStatus?.accountStatus || (accessStatus?.hasActiveAccount ? "ACTIVE" : "NOT_INVITED");
  const isAccountActive = accountStatus === "ACTIVE";
  const isInvited = accountStatus === "INVITED";
  const isSuspended = accountStatus === "SUSPENDED";
  const activeInvitation = accessStatus?.invitation && accessStatus.invitation.status === "ACTIVE"
    ? accessStatus.invitation
    : invitations.find((i) => i.status === "ACTIVE");

  return (
    <div
      className="modal-overlay dialog-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="invitation-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-container dialog-container candidate-invitation-modal" style={{ maxWidth: "620px" }}>
        <header className="modal-header dialog-header">
          <div className="modal-title-group" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <Icon name="key" className="modal-title-icon" aria-hidden="true" />
            <h2 id="invitation-modal-title" className="modal-title dialog-title">
              Candidate Access: {candidateName}
            </h2>
          </div>
          <button
            type="button"
            className="btn-icon-close dialog-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            <Icon name="x" aria-hidden="true" />
          </button>
        </header>

        <div className="modal-body dialog-body" style={{ padding: "20px 24px" }}>
          {/* Success Banner */}
          {successMessage && (
            <div
              className="feedback-box success"
              role="status"
              style={{
                marginBottom: "16px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
                padding: "10px 14px",
                borderRadius: "6px",
                backgroundColor: "#f0fdf4",
                border: "1px solid #86efac",
                color: "#15803d",
                fontSize: "13px",
              }}
            >
              <Icon name="check-circle" size={16} aria-hidden="true" />
              <span>{successMessage}</span>
            </div>
          )}

          {/* Error Banner with Retry */}
          {error && (
            <div
              className="feedback-box error"
              role="alert"
              style={{
                marginBottom: "16px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 14px",
                borderRadius: "6px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon name="alert-triangle" aria-hidden="true" />
                <span>{error}</span>
              </div>
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={fetchData}
                disabled={isLoading}
              >
                Retry
              </button>
            </div>
          )}

          {/* Loading State */}
          {isLoading && !accessStatus ? (
            <div className="loading-state-inline" role="status" style={{ padding: "32px 0", textAlign: "center" }}>
              <div className="spinner-inline" />
              <p style={{ marginTop: "12px", color: "var(--text-secondary, #64748b)", fontSize: "14px" }}>
                Loading candidate access status...
              </p>
            </div>
          ) : (
            <>
              {/* Account Status Overview Card */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "14px 18px",
                  borderRadius: "8px",
                  backgroundColor: isAccountActive
                    ? "#f0fdf4"
                    : isInvited
                    ? "#eff6ff"
                    : isSuspended
                    ? "#fffbeb"
                    : "var(--bg-surface-subtle, #f8fafc)",
                  border: `1px solid ${
                    isAccountActive
                      ? "#bbf7d0"
                      : isInvited
                      ? "#bfdbfe"
                      : isSuspended
                      ? "#fde68a"
                      : "var(--border-subtle, #e2e8f0)"
                  }`,
                  marginBottom: "20px",
                }}
              >
                <div>
                  <div style={{ fontSize: "12px", textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.5px", color: "var(--text-secondary, #64748b)", marginBottom: "4px" }}>
                    Account Status
                  </div>
                  <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                    {isAccountActive && "Candidate Account Active"}
                    {isInvited && "Invitation Pending Activation"}
                    {isSuspended && "Candidate Access Suspended"}
                    {!isAccountActive && !isInvited && !isSuspended && "Not Yet Invited"}
                  </div>
                  {accessStatus?.email && (
                    <div style={{ fontSize: "13px", color: "var(--text-secondary, #64748b)", marginTop: "2px" }}>
                      Email: {accessStatus.email}
                    </div>
                  )}
                </div>

                <div>
                  {isAccountActive && (
                    <span className="badge badge-success" style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "6px 12px", backgroundColor: "#22c55e", color: "#ffffff", borderRadius: "16px", fontWeight: 600, fontSize: "12px" }}>
                      <Icon name="check-circle" size={14} aria-hidden="true" /> ACTIVE
                    </span>
                  )}
                  {isInvited && (
                    <span className="badge badge-info" style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "6px 12px", backgroundColor: "#3b82f6", color: "#ffffff", borderRadius: "16px", fontWeight: 600, fontSize: "12px" }}>
                      <Icon name="clock" size={14} aria-hidden="true" /> INVITED
                    </span>
                  )}
                  {isSuspended && (
                    <span className="badge badge-warning" style={{ display: "inline-flex", alignItems: "center", gap: "4px", padding: "6px 12px", backgroundColor: "#f59e0b", color: "#ffffff", borderRadius: "16px", fontWeight: 600, fontSize: "12px" }}>
                      <Icon name="slash" size={14} aria-hidden="true" /> SUSPENDED
                    </span>
                  )}
                  {!isAccountActive && !isInvited && !isSuspended && (
                    <span className="badge badge-neutral" style={{ padding: "6px 12px", backgroundColor: "#e2e8f0", color: "#475569", borderRadius: "16px", fontWeight: 600, fontSize: "12px" }}>
                      NOT ACTIVATED
                    </span>
                  )}
                </div>
              </div>

              {/* Newly Created Single-Use Link Banner */}
              {createdResult && (
                <div
                  className="invitation-created-banner"
                  role="region"
                  aria-label="Newly generated invitation link"
                  style={{
                    backgroundColor: "#f0fdf4",
                    border: "1px solid #86efac",
                    borderRadius: "8px",
                    padding: "16px",
                    marginBottom: "20px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px", color: "#15803d", fontWeight: 600, fontSize: "14px" }}>
                    <Icon name="check-circle" size={16} aria-hidden="true" />
                    <span>Single-Use Activation Link Generated</span>
                  </div>
                  <p style={{ fontSize: "13px", color: "#166534", margin: "0 0 12px 0" }}>
                    Share this link directly with the candidate. For security, this raw link is displayed only once and expires in {validHours} hours.
                  </p>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <input
                      type="text"
                      readOnly
                      className="form-input"
                      value={createdResult.activationUrl}
                      aria-label="Activation URL"
                      style={{
                        fontFamily: "monospace",
                        fontSize: "12px",
                        backgroundColor: "#ffffff",
                        borderColor: "#86efac",
                        color: "#0f172a",
                        flex: 1,
                      }}
                    />
                    <button
                      type="button"
                      className={`btn ${copied ? "btn-success" : "btn-primary"} btn-sm`}
                      onClick={handleCopyLink}
                    >
                      <Icon name={copied ? "check" : "copy"} aria-hidden="true" />
                      <span>{copied ? "Copied!" : "Copy Link"}</span>
                    </button>
                  </div>
                </div>
              )}

              {/* State-specific Body Content */}
              {isAccountActive ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "16px", marginBottom: "20px" }}>
                  {/* Candidate Portal Access Link Box */}
                  <div
                    style={{
                      padding: "16px",
                      backgroundColor: "var(--surface-muted, #f8fafc)",
                      borderRadius: "8px",
                      border: "1px solid var(--border-subtle, #e2e8f0)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                      <Icon name="link" size={16} style={{ color: "#2563eb" }} aria-hidden="true" />
                      <span style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                        Candidate Practice Portal Link
                      </span>
                    </div>
                    <p style={{ fontSize: "13px", color: "var(--text-secondary, #475569)", margin: "0 0 12px 0" }}>
                      {candidateName} has an active account and can log in at any time to access their candidate portal and practice assessments.
                    </p>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      <input
                        type="text"
                        readOnly
                        className="form-input"
                        value={portalUrl}
                        aria-label="Candidate Portal URL"
                        style={{
                          fontFamily: "monospace",
                          fontSize: "12px",
                          backgroundColor: "#ffffff",
                          color: "#0f172a",
                          flex: 1,
                        }}
                      />
                      <button
                        type="button"
                        className={`btn ${portalCopied ? "btn-success" : "btn-primary"} btn-sm`}
                        onClick={handleCopyPortalUrl}
                        title="Copy Candidate Portal Link"
                      >
                        <Icon name={portalCopied ? "check" : "copy"} aria-hidden="true" />
                        <span>{portalCopied ? "Copied!" : "Copy Portal Link"}</span>
                      </button>
                      <a
                        href="/candidate/login"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="btn btn-secondary btn-sm"
                        title="Open Candidate Portal in new tab"
                        style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: "4px" }}
                      >
                        <Icon name="external-link" size={14} aria-hidden="true" />
                        <span>Open</span>
                      </a>
                    </div>
                  </div>

                  {/* Revoke Access Action Card */}
                  <div
                    style={{
                      padding: "14px 16px",
                      backgroundColor: confirmRevoke ? "#fffbeb" : "var(--surface-muted, #f8fafc)",
                      borderRadius: "8px",
                      border: `1px solid ${confirmRevoke ? "#fde68a" : "var(--border-subtle, #e2e8f0)"}`,
                      display: "flex",
                      flexDirection: confirmRevoke ? "column" : "row",
                      alignItems: confirmRevoke ? "flex-start" : "center",
                      justifyContent: "space-between",
                      gap: "12px",
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px", color: "var(--text-primary, #0f172a)", fontWeight: 600, fontSize: "13px" }}>
                        <Icon name="slash" size={15} style={{ color: "#b45309" }} aria-hidden="true" />
                        <span>Revoke Portal Access</span>
                      </div>
                      <p style={{ fontSize: "12px", color: "var(--text-secondary, #475569)", margin: "4px 0 0 0" }}>
                        {confirmRevoke
                          ? "Are you sure? This will immediately terminate all active sessions and block portal login until re-invited."
                          : "Disable candidate portal login and terminate all active candidate sessions."}
                      </p>
                    </div>

                    <div style={{ display: "flex", gap: "8px", alignSelf: confirmRevoke ? "flex-end" : "center" }}>
                      {confirmRevoke ? (
                        <>
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => setConfirmRevoke(false)}
                            disabled={isRevokingAccess}
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            className="btn btn-primary btn-xs"
                            onClick={handleRevokeCandidateAccess}
                            disabled={isRevokingAccess}
                          >
                            <Icon name="slash" size={12} aria-hidden="true" />
                            <span>{isRevokingAccess ? "Revoking..." : "Confirm Revoke"}</span>
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn btn-secondary btn-xs"
                          onClick={() => setConfirmRevoke(true)}
                        >
                          <Icon name="slash" size={12} aria-hidden="true" />
                          <span>Revoke Access</span>
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ) : isSuspended ? (
                <div style={{ marginBottom: "20px" }}>
                  <div
                    style={{
                      padding: "14px 16px",
                      backgroundColor: "#fffbeb",
                      borderRadius: "8px",
                      border: "1px solid #fde68a",
                      marginBottom: "16px",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", color: "#92400e", fontWeight: 600, fontSize: "13px", marginBottom: "4px" }}>
                      <Icon name="alert-circle" size={15} aria-hidden="true" />
                      <span>Candidate Access Suspended</span>
                    </div>
                    <p style={{ fontSize: "13px", color: "#78350f", margin: 0 }}>
                      Access for {candidateName} was revoked. You can restore access at any time by generating a new activation invitation link below.
                    </p>
                  </div>

                  {/* Re-invite form */}
                  <div className="invitation-create-section">
                    <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <label htmlFor="invitation-expiry-suspended" style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-secondary, #475569)" }}>
                          Link Expiry:
                        </label>
                        <select
                          id="invitation-expiry-suspended"
                          className="form-select form-select-sm"
                          value={validHours}
                          onChange={(e) => setValidHours(Number(e.target.value))}
                          disabled={isCreating}
                        >
                          <option value={12}>12 hours</option>
                          <option value={24}>24 hours (Default)</option>
                          <option value={48}>48 hours</option>
                          <option value={72}>3 days</option>
                          <option value={168}>7 days</option>
                        </select>
                      </div>

                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={handleCreateInvitation}
                        disabled={isCreating}
                      >
                        <Icon name="plus" aria-hidden="true" />
                        <span>{isCreating ? "Generating Link..." : "Re-invite & Restore Access"}</span>
                      </button>
                    </div>
                  </div>
                </div>
              ) : isInvited && activeInvitation && !createdResult ? (
                <div style={{ padding: "16px", backgroundColor: "#f8fafc", borderRadius: "8px", border: "1px solid #e2e8f0", marginBottom: "20px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: "#1e293b" }}>
                      Active Invitation Expiry:
                    </span>
                    <span style={{ fontSize: "13px", color: "#475569" }}>
                      {new Date(activeInvitation.expiresAt).toLocaleString()}
                    </span>
                  </div>
                  <p style={{ fontSize: "13px", color: "#64748b", margin: "0 0 14px 0" }}>
                    An active invitation was previously generated. Raw activation links cannot be reconstructed from token hashes. If the candidate needs a replacement link, revoke this invitation and create a new one.
                  </p>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleRevoke(activeInvitation.invitationId)}
                  >
                    <Icon name="slash" aria-hidden="true" />
                    <span>Revoke and Create Replacement</span>
                  </button>
                </div>
              ) : (
                /* NOT_INVITED - Show Create Link Form */
                <div className="invitation-create-section" style={{ marginBottom: "20px" }}>
                  <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", marginBottom: "14px" }}>
                    Candidate login has not been activated. Generate a secure, single-use activation link granting {candidateName} access to their candidate practice interview portal.
                  </p>
                  <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <label htmlFor="invitation-expiry" style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-secondary, #475569)" }}>
                        Link Expiry:
                      </label>
                      <select
                        id="invitation-expiry"
                        className="form-select form-select-sm"
                        value={validHours}
                        onChange={(e) => setValidHours(Number(e.target.value))}
                        disabled={isCreating}
                      >
                        <option value={12}>12 hours</option>
                        <option value={24}>24 hours (Default)</option>
                        <option value={48}>48 hours</option>
                        <option value={72}>3 days</option>
                        <option value={168}>7 days</option>
                      </select>
                    </div>

                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={handleCreateInvitation}
                      disabled={isCreating}
                    >
                      <Icon name="plus" aria-hidden="true" />
                      <span>{isCreating ? "Generating Link..." : "Create access link"}</span>
                    </button>
                  </div>
                </div>
              )}

              {/* Invitation History Table */}
              <section className="invitation-history-section" aria-label="Invitation History">
                <h3 className="section-subtitle" style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary, #0f172a)", marginBottom: "8px" }}>
                  Invitation History
                </h3>

                {invitations.length === 0 ? (
                  <div className="empty-history-text" style={{ fontSize: "13px", color: "var(--text-secondary, #64748b)", fontStyle: "italic", padding: "12px 0" }}>
                    No previous invitations generated for this candidate.
                  </div>
                ) : (
                  <div className="invitation-table-wrapper">
                    <table className="invitation-table" style={{ width: "100%", fontSize: "13px" }}>
                      <thead>
                        <tr>
                          <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Status</th>
                          <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Created At</th>
                          <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Expires At</th>
                          <th scope="col" style={{ textAlign: "right", padding: "8px" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {invitations.map((inv) => {
                          const isRevocable = (inv.status || "").toUpperCase() === "ACTIVE";
                          return (
                            <tr key={inv.invitationId} style={{ borderTop: "1px solid var(--border-subtle, #e2e8f0)" }}>
                              <td style={{ padding: "8px" }}>
                                <span className={`status-pill status-${inv.status?.toLowerCase()}`}>
                                  {inv.status}
                                </span>
                              </td>
                              <td style={{ padding: "8px" }}>{new Date(inv.createdAt).toLocaleString()}</td>
                              <td style={{ padding: "8px" }}>{new Date(inv.expiresAt).toLocaleString()}</td>
                              <td style={{ padding: "8px", textAlign: "right" }}>
                                {isRevocable ? (
                                  <button
                                    type="button"
                                    className="btn btn-secondary btn-xs"
                                    onClick={() => handleRevoke(inv.invitationId)}
                                    aria-label="Revoke unused invitation"
                                  >
                                    <Icon name="slash" size={12} aria-hidden="true" />
                                    <span>Revoke</span>
                                  </button>
                                ) : (
                                  <span className="text-muted" style={{ color: "#94a3b8" }}>—</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </div>

        <footer className="modal-footer dialog-footer" style={{ padding: "16px 24px", borderTop: "1px solid var(--border-subtle, #e2e8f0)", display: "flex", justifyContent: "flex-end" }}>
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
