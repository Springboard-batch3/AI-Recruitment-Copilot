import React, { useState, useEffect } from "react";
import { useCandidateAuth } from "../../context/CandidateAuthContext";
import { Icon } from "../Icon";

interface CandidateActivationPageProps {
  onSuccess?: () => void;
  onNavigateLogin?: () => void;
}

export function CandidateActivationPage({
  onSuccess,
  onNavigateLogin,
}: CandidateActivationPageProps) {
  const { activate } = useCandidateAuth();

  // Capture token immediately on component initialization before any URL rewrite or auth probes
  const [token, setToken] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    try {
      const hashStr = window.location.hash.replace(/^#/, "");
      const fragmentParams = new URLSearchParams(hashStr);
      const queryParams = new URLSearchParams(window.location.search);
      const extracted = fragmentParams.get("token") || queryParams.get("token") || "";
      return extracted.trim();
    } catch {
      return "";
    }
  });

  const [hasToken, setHasToken] = useState<boolean>(() => Boolean(token));
  const [password, setPassword] = useState<string>("");
  const [confirmation, setConfirmation] = useState<string>("");
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [showConfirmation, setShowConfirmation] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [activatedEmail, setActivatedEmail] = useState<string | null>(null);

  // Strip token from visible URL bar once captured into component memory
  useEffect(() => {
    if (typeof window === "undefined") return;

    if (token) {
      try {
        const cleanUrl = window.location.pathname + window.location.search;
        window.history.replaceState(null, "", cleanUrl);
      } catch {
        // Safe ignore in non-browser test runners
      }
    } else {
      setHasToken(false);
      setErrorMessage(
        "No activation token detected. Please ensure you clicked the complete activation link provided by your recruiter."
      );
    }
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    if (!token) {
      setErrorMessage("Activation token is missing. Please ask your recruiter for a new link.");
      return;
    }

    if (password.length < 8) {
      setErrorMessage("Password must be at least 8 characters long.");
      return;
    }

    if (password !== confirmation) {
      setErrorMessage("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    try {
      const res = await activate(token, password, confirmation);
      if (res?.email) {
        setActivatedEmail(res.email);
      }
      if (onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      const errMsg = err?.message || "";
      const errCode = err?.code || "";

      if (errCode === "INVITATION_ALREADY_CONSUMED" || errMsg.includes("already been used")) {
        setErrorMessage("This invitation link has already been used. Please sign in with your registered password or request a new link.");
      } else if (errCode === "INVITATION_EXPIRED" || errMsg.includes("expired")) {
        setErrorMessage("This invitation link has expired. Please ask your recruiter to generate a new activation link.");
      } else if (errCode === "INVITATION_REVOKED" || errMsg.includes("revoked")) {
        setErrorMessage("This invitation link has been revoked. Please contact your recruiter for a replacement link.");
      } else {
        setErrorMessage(
          errMsg || "Unable to activate practice account. Please verify your link and try again."
        );
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="candidate-auth-layout">
      <div className="candidate-auth-card" role="main">
        <header className="candidate-auth-header">
          <div className="candidate-badge-container">
            <span className="candidate-portal-badge">Candidate Practice Portal</span>
          </div>
          <h1 className="candidate-auth-title">Activate Practice Account</h1>
          <p className="candidate-auth-subtitle">
            Set a secure password to access your candidate practice interview portal.
          </p>
        </header>

        {errorMessage && (
          <div
            className="candidate-auth-error-banner"
            role="alert"
            aria-live="assertive"
          >
            <Icon name="alert-triangle" className="error-icon" aria-hidden="true" />
            <span>{errorMessage}</span>
          </div>
        )}

        {activatedEmail ? (
          <div className="candidate-auth-success-state" style={{ textAlign: "center", padding: "16px 0" }}>
            <div style={{ display: "inline-flex", padding: "12px", borderRadius: "50%", backgroundColor: "#f0fdf4", color: "#16a34a", marginBottom: "12px" }}>
              <Icon name="check-circle" size={32} aria-hidden="true" />
            </div>
            <h2 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary, #0f172a)", marginBottom: "8px" }}>
              Account Activated!
            </h2>
            <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", marginBottom: "16px" }}>
              Your candidate practice account is ready. Future sign-in email: <strong>{activatedEmail}</strong>
            </p>
            <p style={{ fontSize: "13px", color: "var(--text-tertiary, #94a3b8)" }}>
              Redirecting you to the candidate practice portal...
            </p>
          </div>
        ) : !hasToken ? (
          <div className="candidate-auth-empty-state">
            <p className="empty-description">
              If your link expired or is invalid, please ask your recruiter to generate a fresh invitation.
            </p>
            {onNavigateLogin && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={onNavigateLogin}
              >
                Go to Candidate Sign In
              </button>
            )}
          </div>
        ) : (
          <form className="candidate-auth-form" onSubmit={handleSubmit} noValidate>
            <div className="form-group">
              <label htmlFor="candidate-password">New Password</label>
              <div className="password-input-wrapper">
                <input
                  id="candidate-password"
                  name="candidate-password"
                  type={showPassword ? "text" : "password"}
                  className="form-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 8 characters"
                  required
                  autoComplete="new-password"
                  disabled={isSubmitting}
                />
                <button
                  type="button"
                  className="password-toggle-btn"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  tabIndex={0}
                >
                  <Icon
                    name={showPassword ? "eye-off" : "eye"}
                    aria-hidden="true"
                  />
                </button>
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="candidate-password-confirm">Confirm Password</label>
              <div className="password-input-wrapper">
                <input
                  id="candidate-password-confirm"
                  name="candidate-password-confirm"
                  type={showConfirmation ? "text" : "password"}
                  className="form-input"
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                  placeholder="Re-enter password"
                  required
                  autoComplete="new-password"
                  disabled={isSubmitting}
                />
                <button
                  type="button"
                  className="password-toggle-btn"
                  onClick={() => setShowConfirmation(!showConfirmation)}
                  aria-label={showConfirmation ? "Hide confirmation password" : "Show confirmation password"}
                  tabIndex={0}
                >
                  <Icon
                    name={showConfirmation ? "eye-off" : "eye"}
                    aria-hidden="true"
                  />
                </button>
              </div>
            </div>

            <div className="form-actions">
              <button
                type="submit"
                className="btn btn-primary btn-block"
                disabled={isSubmitting || !password || !confirmation}
              >
                {isSubmitting ? (
                  <>
                    <span className="spinner-inline" aria-hidden="true" />
                    <span>Activating Account...</span>
                  </>
                ) : (
                  "Activate Account & Continue"
                )}
              </button>
            </div>
          </form>
        )}

        <footer className="candidate-auth-footer">
          <p className="footer-text">
            Already activated?{" "}
            <button
              type="button"
              className="link-button"
              onClick={onNavigateLogin}
            >
              Sign In to Practice Portal
            </button>
          </p>
        </footer>
      </div>
    </div>
  );
}
