import React, { useState, useEffect } from "react";
import { useCandidateAuth } from "../../context/CandidateAuthContext";
import { Icon } from "../Icon";

interface CandidateLoginPageProps {
  onSuccess?: () => void;
  onNavigateRegister?: () => void;
}

export function CandidateLoginPage({
  onSuccess,
  onNavigateRegister,
}: CandidateLoginPageProps) {
  const { login } = useCandidateAuth();

  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Sign In — Candidate Portal | Smart Hiring";
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const cleanEmail = email.trim();
    if (!cleanEmail || !password) {
      setErrorMessage("Please enter both your email address and password.");
      return;
    }

    setIsSubmitting(true);
    try {
      await login(cleanEmail, password);
      if (onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      setErrorMessage(
        err.message || "Unable to sign in with the provided credentials."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="candidate-auth-layout">
      <div className="candidate-auth-card" role="main">
        <header className="candidate-auth-header">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", marginBottom: "8px" }}>
            <Icon name="briefcase" size={20} className="text-primary" aria-hidden="true" />
            <span style={{ fontSize: "16px", fontWeight: 700, color: "var(--text-primary, #0f172a)" }}>
              Smart Hiring
            </span>
          </div>
          <div className="candidate-badge-container">
            <span className="candidate-badge">Candidate portal</span>
          </div>
          <h1 className="candidate-auth-title">Welcome back</h1>
          <p className="candidate-auth-subtitle">
            Sign in to manage applications and complete released interviews.
          </p>
        </header>

        {errorMessage && (
          <div
            className="candidate-auth-error-banner"
            role="alert"
            aria-live="assertive"
            style={{
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "10px 14px",
              backgroundColor: "#fef2f2",
              border: "1px solid #fecaca",
              borderRadius: "6px",
              color: "#b91c1c",
              fontSize: "13px",
              marginBottom: "16px",
            }}
          >
            <Icon name="alert-triangle" className="error-icon" aria-hidden="true" />
            <span>{errorMessage}</span>
          </div>
        )}

        <form className="candidate-auth-form" onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <label htmlFor="candidate-email">Email address</label>
            <input
              id="candidate-email"
              name="email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="e.g. alex@example.com"
              required
              autoComplete="email"
              disabled={isSubmitting}
            />
          </div>

          <div className="form-group">
            <label htmlFor="candidate-login-password">Password</label>
            <div className="password-input-wrapper">
              <input
                id="candidate-login-password"
                name="password"
                type={showPassword ? "text" : "password"}
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                required
                autoComplete="current-password"
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

          <div className="form-actions" style={{ marginTop: "4px" }}>
            <button
              type="submit"
              className="btn btn-primary btn-block candidate-submit-btn"
              disabled={isSubmitting || !email.trim() || !password}
              aria-label="Sign In to Candidate Portal"
              style={{ width: "100%", height: "46px", fontSize: "15px", fontWeight: 600 }}
            >
              {isSubmitting ? (
                <>
                  <span className="spinner-inline" aria-hidden="true" />
                  <span>Signing in…</span>
                </>
              ) : (
                "Sign in"
              )}
            </button>
          </div>
        </form>

        <footer className="candidate-auth-footer" style={{ marginTop: "24px", paddingTop: "16px", textAlign: "center" }}>
          <p className="footer-text" style={{ margin: 0, fontSize: "14px", color: "var(--text-secondary, #64748b)" }}>
            New to Smart Hiring?{" "}
            <button
              type="button"
              className="link-button"
              onClick={onNavigateRegister}
              style={{ fontWeight: 600, color: "var(--accent-primary, #3b82f6)", background: "none", border: "none", cursor: "pointer", padding: "0 4px" }}
            >
              Create candidate account
            </button>
          </p>
        </footer>
      </div>
    </div>
  );
}
