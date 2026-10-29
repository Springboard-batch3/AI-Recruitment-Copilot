import React, { useState } from "react";
import { createDashboardSession, ApiError } from "../../api/client";
import { useOptionalAuth } from "../../context/AuthContext";
import { Icon } from "../Icon";

export function AnalyticsLauncher() {
  const [isLaunching, setIsLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const auth = useOptionalAuth();

  const handleLaunch = async () => {
    try {
      setIsLaunching(true);
      setError(null);
      let data;
      try {
        data = await createDashboardSession();
      } catch (err: any) {
        if (err instanceof ApiError && err.code === "CSRF_DENIED" && auth?.refreshSession) {
          // Attempt to refresh the session token and retry once
          await auth.refreshSession();
          data = await createDashboardSession();
        } else {
          throw err;
        }
      }

      if (data?.launch_url) {
        // Open local Streamlit dashboard in new tab or navigate
        window.open(data.launch_url, "_blank", "noopener,noreferrer");
      } else {
        setError("Backend did not return a valid launch URL.");
      }
    } catch (err: any) {
      setError(err?.message || "Failed to create secure analytics session. Ensure FastAPI backend is running.");
    } finally {
      setIsLaunching(false);
    }
  };

  return (
    <div className="analytics-launcher-container" style={{ maxWidth: "800px", margin: "2.5rem auto", padding: "0 1rem" }}>
      <div className="mineral-card" style={{ padding: "2.5rem 2rem", background: "#FCFBF6", border: "1px solid #D6D1C4", borderRadius: "8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "0.75rem" }}>
          <div style={{ background: "#E2ECE9", color: "#174F4A", padding: "8px", borderRadius: "6px", display: "flex" }}>
            <Icon name="award" size={20} />
          </div>
          <div>
            <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "#5A6561", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Editorial Mineral Analytics
            </span>
            <h2 style={{ fontSize: "1.6rem", fontWeight: 700, color: "#174F4A", margin: "0" }}>
              Recruitment Analytics Dashboard
            </h2>
          </div>
        </div>

        <p style={{ color: "#4A5652", fontSize: "0.95rem", lineHeight: 1.6, margin: "1rem 0 1.75rem 0" }}>
          Access the local-only Streamlit analytics dashboard for operational requisition summaries, deterministic match distributions, and independent Text and Audio interview evaluations.
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "2rem" }}>
          <div style={{ padding: "1rem", background: "#F3F0E6", borderRadius: "6px", border: "1px solid #E2DDD2" }}>
            <strong style={{ color: "#202927", fontSize: "0.9rem", display: "block", marginBottom: "0.25rem" }}>
              Single-Use Secure Ticket
            </strong>
            <span style={{ fontSize: "0.8rem", color: "#5A6561", lineHeight: 1.4 }}>
              Tickets expire in 120 seconds and are automatically cleared from the browser URL upon exchange.
            </span>
          </div>
          <div style={{ padding: "1rem", background: "#F3F0E6", borderRadius: "6px", border: "1px solid #E2DDD2" }}>
            <strong style={{ color: "#202927", fontSize: "0.9rem", display: "block", marginBottom: "0.25rem" }}>
              Zero Database Exposure
            </strong>
            <span style={{ fontSize: "0.8rem", color: "#5A6561", lineHeight: 1.4 }}>
              Streamlit communicates strictly via authenticated HTTP to FastAPI with zero direct SQL connections.
            </span>
          </div>
        </div>

        {error && (
          <div className="feedback-box error" style={{ marginBottom: "1.5rem" }} role="alert">
            <Icon name="alert-triangle" size={18} />
            <div className="error-content">
              <p className="feedback-text error">{error}</p>
              <button
                type="button"
                onClick={handleLaunch}
                className="btn btn-secondary btn-sm"
                style={{ marginTop: "8px" }}
              >
                Retry Launch
              </button>
            </div>
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleLaunch}
            disabled={isLaunching}
            style={{ padding: "0.75rem 1.5rem", fontSize: "0.95rem", fontWeight: 600 }}
          >
            <Icon name="external-link" size={16} />
            <span>{isLaunching ? "Requesting ticket…" : "Open Analytics Dashboard"}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
