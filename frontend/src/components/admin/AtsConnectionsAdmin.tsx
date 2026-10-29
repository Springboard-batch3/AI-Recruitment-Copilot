import React, { useEffect, useState } from "react";
import {
  createATSConnection,
  listAdminATSConnections,
  testATSConnection,
  updateATSConnection,
} from "../../api/ats";
import { ATSAdapterType, ATSConnection } from "../../types/ats";
import { Icon } from "../Icon";
import { getCrossPortalUrl } from "../../utils/portal";

export function AtsConnectionsAdmin() {
  const [connections, setConnections] = useState<ATSConnection[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Form State
  const [showAddForm, setShowAddForm] = useState<boolean>(false);
  const [name, setName] = useState<string>("");
  const [adapterType, setAdapterType] = useState<ATSAdapterType>("MOCK");
  const [tenantLabel, setTenantLabel] = useState<string>("");
  const [secretRef, setSecretRef] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  const loadConnections = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await listAdminATSConnections();
      setConnections(res.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load ATS connections.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadConnections();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;

    setIsSubmitting(true);
    setError(null);
    setSuccessMsg(null);

    try {
      await createATSConnection({
        connection_name: name.trim(),
        adapter_type: adapterType,
        tenant_label: tenantLabel.trim() || undefined,
        secret_ref: secretRef.trim() || undefined,
      });

      setSuccessMsg("ATS connection created successfully.");
      setName("");
      setTenantLabel("");
      setSecretRef("");
      setShowAddForm(false);
      loadConnections();
    } catch (err: any) {
      setError(err?.message || "Failed to create ATS connection.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleToggleEnabled = async (conn: ATSConnection) => {
    try {
      await updateATSConnection(conn.connection_id, {
        is_enabled: !conn.is_enabled,
      });
      loadConnections();
    } catch (err: any) {
      setError(err?.message || "Failed to update connection state.");
    }
  };

  const handleToggleKillSwitch = async (conn: ATSConnection) => {
    try {
      await updateATSConnection(conn.connection_id, {
        kill_switch_active: !conn.kill_switch_active,
      });
      loadConnections();
    } catch (err: any) {
      setError(err?.message || "Failed to update kill switch state.");
    }
  };

  const handleTestConnection = async (connId: string) => {
    try {
      await testATSConnection(connId);
      setSuccessMsg("Connection health test completed.");
      loadConnections();
    } catch (err: any) {
      setError(err?.message || "Health test failed.");
    }
  };

  const formatDate = (isoString?: string | null) => {
    if (!isoString) return "Never";
    try {
      return new Date(isoString).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="ats-admin-container" style={{ padding: "24px", maxWidth: "1100px", margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "24px" }}>
        <div>
          <h2 style={{ fontSize: "20px", fontWeight: 700, margin: "0 0 4px 0", color: "var(--text-primary)" }}>
            ATS Integrations
          </h2>
          <p style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary)" }}>
            Configure and monitor external ATS adapter connections, authentication, and sync policies.
          </p>
        </div>

        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <a
            href={getCrossPortalUrl("recruiter", "?tab=upload")}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-secondary btn-md"
            aria-label="Open recruiter import in new tab"
          >
            <Icon name="external-link" size={14} />
            <span>Open recruiter import</span>
          </a>

          <button
            type="button"
            className="btn btn-primary btn-md"
            onClick={() => setShowAddForm(!showAddForm)}
          >
            <Icon name={showAddForm ? "x" : "plus"} size={15} />
            <span>{showAddForm ? "Cancel" : "Add Connection"}</span>
          </button>
        </div>
      </div>

      {successMsg && (
        <div className="feedback-box success" role="alert" style={{ marginBottom: "16px" }}>
          <Icon name="check-circle" size={16} />
          <p className="feedback-text success">{successMsg}</p>
        </div>
      )}

      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
          <Icon name="alert-triangle" size={16} />
          <p className="feedback-text error">{error}</p>
        </div>
      )}

      {/* Add Connection Form */}
      {showAddForm && (
        <div
          className="ats-form-panel"
          style={{
            backgroundColor: "var(--bg-surface)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-lg)",
            padding: "20px 24px",
            marginBottom: "24px",
            boxShadow: "var(--shadow-card)",
          }}
        >
          <h3 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "16px", color: "var(--text-primary)" }}>
            Configure New ATS Connection
          </h3>

          <form onSubmit={handleCreate}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px" }}>
              <div>
                <label className="form-label" htmlFor="conn-name" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                  Connection Name *
                </label>
                <input
                  id="conn-name"
                  type="text"
                  className="form-input"
                  style={{ width: "100%" }}
                  placeholder="e.g. Primary Greenhouse / Mock ATS"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </div>

              <div>
                <label className="form-label" htmlFor="adapter-type" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                  Adapter Type *
                </label>
                <select
                  id="adapter-type"
                  className="form-input"
                  style={{ width: "100%" }}
                  value={adapterType}
                  onChange={(e) => setAdapterType(e.target.value as ATSAdapterType)}
                >
                  <option value="LOCAL_FOLDER_DEMO">Local ATS Demo (local_ats_demo/inbox)</option>
                  <option value="MOCK">MOCK (Deterministic Mock Adapter)</option>
                  <option value="CSV">CSV (Local File Ingestion)</option>
                  <option value="JSON">JSON (Local JSON Ingestion)</option>
                </select>
              </div>

              <div>
                <label className="form-label" htmlFor="tenant-label" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                  Tenant / Environment Label
                </label>
                <input
                  id="tenant-label"
                  type="text"
                  className="form-input"
                  style={{ width: "100%" }}
                  placeholder="e.g. sandbox-prod-1"
                  value={tenantLabel}
                  onChange={(e) => setTenantLabel(e.target.value)}
                />
              </div>

              <div>
                <label className="form-label" htmlFor="secret-ref" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                  Controlled Secret Reference (Write-Only)
                </label>
                <input
                  id="secret-ref"
                  type="text"
                  className="form-input"
                  style={{ width: "100%" }}
                  placeholder="e.g. env:ATS_API_KEY or secret:sk-..."
                  value={secretRef}
                  onChange={(e) => setSecretRef(e.target.value)}
                />
                <span style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px", display: "block" }}>
                  Controlled reference to environment variable or vault secret. Never stored in plaintext.
                </span>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                type="button"
                className="btn btn-secondary btn-md"
                onClick={() => setShowAddForm(false)}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary btn-md"
                disabled={isSubmitting || !name.trim()}
              >
                {isSubmitting ? "Creating..." : "Save Connection"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Connections List */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: "40px" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 12px auto" }} />
          <p style={{ color: "var(--text-secondary)", fontSize: "14px" }}>Loading connections...</p>
        </div>
      ) : connections.length === 0 ? (
        <div className="empty-state-card">
          <div className="empty-icon-circle">
            <Icon name="external-link" size={30} />
          </div>
          <h3 className="empty-title">No ATS connections configured</h3>
          <p className="empty-description">
            Add an ATS connection to import candidate profiles and sync job requisitions.
          </p>
          <button
            type="button"
            className="btn btn-primary btn-md"
            onClick={() => setShowAddForm(true)}
          >
            Add Connection
          </button>
        </div>
      ) : (
        <div className="ats-connections-list" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {connections.map((conn) => (
            <div
              key={conn.connection_id}
              style={{
                backgroundColor: "var(--bg-surface)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "16px 20px",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "16px",
                flexWrap: "wrap",
              }}
            >
              <div style={{ flex: 1, minWidth: "240px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                  <span style={{ fontWeight: 600, fontSize: "15px", color: "var(--text-primary)" }}>
                    {conn.connection_name}
                  </span>
                  <span className="badge" style={{ backgroundColor: "var(--bg-surface-subtle)", fontSize: "11px" }}>
                    {conn.adapter_type}
                  </span>
                  {conn.tenant_label && (
                    <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                      ({conn.tenant_label})
                    </span>
                  )}
                </div>

                <div style={{ display: "flex", gap: "12px", fontSize: "12px", color: "var(--text-secondary)" }}>
                  <span>Health: <strong>{conn.health_status}</strong></span>
                  <span>·</span>
                  <span>Last sync: {formatDate(conn.last_sync_at)}</span>
                  {conn.secret_ref_masked && (
                    <>
                      <span>·</span>
                      <span style={{ fontFamily: "var(--font-mono)" }}>{conn.secret_ref_masked}</span>
                    </>
                  )}
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => handleTestConnection(conn.connection_id)}
                >
                  Test
                </button>

                <button
                  type="button"
                  className={`btn btn-sm ${conn.is_enabled ? "btn-secondary" : "btn-primary"}`}
                  onClick={() => handleToggleEnabled(conn)}
                >
                  {conn.is_enabled ? "Disable" : "Enable"}
                </button>

                <button
                  type="button"
                  className="btn btn-sm"
                  style={{
                    backgroundColor: conn.kill_switch_active ? "#fffbeb" : "#f8fafc",
                    color: conn.kill_switch_active ? "#92400e" : "#475569",
                    borderColor: conn.kill_switch_active ? "#fde68a" : "#cbd5e1",
                  }}
                  onClick={() => handleToggleKillSwitch(conn)}
                  title="Emergency kill switch prevents all data exchange through this connector"
                >
                  {conn.kill_switch_active ? "Kill Switch Active" : "Kill Switch"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
