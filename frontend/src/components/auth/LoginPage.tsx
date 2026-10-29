import React, { useState, useEffect, FormEvent } from "react";
import { useAuth } from "../../context/AuthContext";
import { Icon } from "../Icon";
import { getPortalContext, getLoginPortalSubtitle } from "../../utils/portal";

export function LoginPage() {
  const { login, sessionError, clearSessionError } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const portalContext = getPortalContext();

  useEffect(() => {
    const portalTitle =
      portalContext === "admin"
        ? "Admin Sign In | Smart Hiring"
        : portalContext === "recruiter"
        ? "Recruiter Sign In | Smart Hiring"
        : "Staff Sign In | Smart Hiring";
    document.title = portalTitle;
  }, [portalContext]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setEmailError(null);
    setPasswordError(null);
    setServerError(null);
    clearSessionError();

    const trimmedEmail = email.trim();
    let hasError = false;

    if (!trimmedEmail) {
      setEmailError("Please enter your email address.");
      hasError = true;
    }
    if (!password) {
      setPasswordError("Please enter your password.");
      hasError = true;
    }

    if (hasError) {
      return;
    }

    try {
      setIsSubmitting(true);
      await login(trimmedEmail, password);
    } catch (err: any) {
      setServerError(
        err?.message || "Unable to sign in with the provided credentials."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="auth-page-container">
      <div className="auth-split-surface" role="region" aria-label="Staff Sign In Surface">
        {/* Left Side: Product Context Panel */}
        <div className="auth-brand-panel">
          <div className="auth-brand-top">
            <div className="auth-brand-badge" aria-hidden="true">
              <Icon name="briefcase" size={20} />
            </div>
            <div className="auth-brand-headings">
              <h1 className="auth-brand-title">Smart Hiring Platform</h1>
              <p className="auth-brand-subtitle">
                {portalContext === "admin"
                  ? "Admin Workspace"
                  : "Recruiter Workspace"}
              </p>
            </div>
          </div>

          <div className="auth-capability-list">
            <div className="auth-capability-item">
              <div className="auth-cap-icon" aria-hidden="true">
                <Icon name="check" size={15} />
              </div>
              <div className="auth-cap-text">
                <span className="auth-cap-title">Structured candidate profiles</span>
                <span className="auth-cap-desc">
                  Parse multi-page resumes into standardised, auditable profiles.
                </span>
              </div>
            </div>

            <div className="auth-capability-item">
              <div className="auth-cap-icon" aria-hidden="true">
                <Icon name="check" size={15} />
              </div>
              <div className="auth-cap-text">
                <span className="auth-cap-title">Evidence-backed review</span>
                <span className="auth-cap-desc">
                  Full text-span provenance, confidence scores and correction audit.
                </span>
              </div>
            </div>

            <div className="auth-capability-item">
              <div className="auth-cap-icon" aria-hidden="true">
                <Icon name="check" size={15} />
              </div>
              <div className="auth-cap-text">
                <span className="auth-cap-title">Role-based access</span>
                <span className="auth-cap-desc">
                  Separate workspaces for Recruiters, Reviewers and Administrators.
                </span>
              </div>
            </div>
          </div>

          <div className="auth-brand-footer">
            <p className="auth-system-tag">Manage jobs, applicants and structured interviews from one place.</p>
          </div>
        </div>

        {/* Right Side: Focused Sign In Form */}
        <div className="auth-form-panel">
          <div className="auth-form-header">
            {portalContext !== "default" && (
              <span className="auth-portal-badge" aria-label={`Portal: ${portalContext}`}>
                {portalContext === "admin" ? "Admin workspace" : "Recruiter workspace"}
              </span>
            )}
            <h2 className="auth-form-title">Welcome back</h2>
            <p className="auth-form-subtitle">
              {getLoginPortalSubtitle(portalContext)}
            </p>
          </div>

          {sessionError && (
            <div className="compact-alert info" role="status" aria-live="polite">
              <Icon name="info" size={15} className="alert-icon" />
              <p className="alert-message">{sessionError}</p>
            </div>
          )}

          {serverError && (
            <div className="compact-alert error" role="alert" aria-live="polite">
              <Icon name="alert-triangle" size={15} className="alert-icon" />
              <p className="alert-message">{serverError}</p>
            </div>
          )}

          <form className="auth-form" onSubmit={handleSubmit} noValidate>
            <div className="form-group">
              <label htmlFor="login-email" className="form-label">
                Work email
              </label>
              <div className="input-with-icon">
                <span className="input-icon-prefix" aria-hidden="true">
                  <Icon name="mail" size={16} />
                </span>
                <input
                  id="login-email"
                  type="email"
                  className={`form-input ${emailError ? "input-has-error" : ""}`}
                  placeholder="name@company.com"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    if (emailError) setEmailError(null);
                    if (serverError) setServerError(null);
                  }}
                  disabled={isSubmitting}
                  autoComplete="email"
                  aria-invalid={!!emailError}
                  aria-describedby={emailError ? "email-error-msg" : undefined}
                  required
                />
              </div>
              {emailError && (
                <p id="email-error-msg" className="field-error-text" role="alert">
                  {emailError}
                </p>
              )}
            </div>

            <div className="form-group">
              <label htmlFor="login-password" className="form-label">
                Password
              </label>
              <div className="input-with-icon">
                <span className="input-icon-prefix" aria-hidden="true">
                  <Icon name="shield" size={16} />
                </span>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  className={`form-input ${passwordError ? "input-has-error" : ""}`}
                  placeholder="••••••••••••"
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    if (passwordError) setPasswordError(null);
                    if (serverError) setServerError(null);
                  }}
                  disabled={isSubmitting}
                  autoComplete="current-password"
                  aria-invalid={!!passwordError}
                  aria-describedby={passwordError ? "password-error-msg" : undefined}
                  required
                />
                <button
                  type="button"
                  className="password-toggle-btn"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  title={showPassword ? "Hide password" : "Show password"}
                >
                  <Icon name={showPassword ? "eye" : "eye"} size={16} />
                </button>
              </div>
              {passwordError && (
                <p id="password-error-msg" className="field-error-text" role="alert">
                  {passwordError}
                </p>
              )}
            </div>

            <button
              type="submit"
              className="btn btn-primary auth-submit-btn"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <span className="loading-spinner-sm" aria-hidden="true" />
                  <span>Signing in...</span>
                </>
              ) : (
                <span>Sign in</span>
              )}
            </button>
          </form>

          <div className="auth-footer">
            <p className="auth-footer-note">
              Access is limited to authorised recruitment staff. Unauthorised access attempts are monitored and logged.
            </p>
            <div style={{ marginTop: "12px", textAlign: "center", borderTop: "1px solid var(--border-subtle)", paddingTop: "12px" }}>
              <a
                href="/candidate/login"
                className="link-button"
                style={{ fontSize: "var(--text-sm)", fontWeight: 500, color: "var(--accent-primary)", textDecoration: "none" }}
                onClick={(e) => {
                  if (typeof window !== "undefined" && window.history?.pushState) {
                    e.preventDefault();
                    window.history.pushState(null, "", "/candidate/login");
                    window.dispatchEvent(new PopStateEvent("popstate"));
                  }
                }}
              >
                Candidate login &rarr;
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

