import React, { useState, useEffect } from "react";
import { useCandidateAuth } from "../../context/CandidateAuthContext";
import { Icon } from "../Icon";

interface CandidateRegisterPageProps {
  onSuccess?: () => void;
  onNavigateLogin?: () => void;
}

export function CandidateRegisterPage({
  onSuccess,
  onNavigateLogin,
}: CandidateRegisterPageProps) {
  const { register } = useCandidateAuth();

  const [fullName, setFullName] = useState<string>("");
  const [email, setEmail] = useState<string>("");
  const [password, setPassword] = useState<string>("");
  const [confirmPassword, setConfirmPassword] = useState<string>("");
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Create Account — Candidate Portal | Smart Hiring";
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const cleanFullName = fullName.trim();
    const cleanEmail = email.trim();

    if (!cleanFullName) {
      setErrorMessage("Please enter your full name.");
      return;
    }

    if (!cleanEmail) {
      setErrorMessage("Please enter your email address.");
      return;
    }

    if (password.length < 8) {
      setErrorMessage("Password must be at least 8 characters long.");
      return;
    }

    if (password !== confirmPassword) {
      setErrorMessage("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    try {
      await register(cleanFullName, cleanEmail, password);
      if (onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      setErrorMessage(
        err.message || "Registration failed. Please check your information."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="candidate-auth-layout">
      <div className="candidate-auth-card" role="main">
        <header className="candidate-auth-header">
          <div className="candidate-badge-container">
            <span className="candidate-portal-badge">Candidate Portal</span>
          </div>
          <h1 className="candidate-auth-title">Create Candidate Account</h1>
          <p className="candidate-auth-subtitle">
            Sign up to apply for open positions and access practice interviews.
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

        <form className="candidate-auth-form" onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <label htmlFor="candidate-register-name">Full Name</label>
            <input
              id="candidate-register-name"
              name="candidate-register-name"
              type="text"
              className="form-input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Jane Doe"
              required
              autoComplete="name"
              disabled={isSubmitting}
            />
          </div>

          <div className="form-group">
            <label htmlFor="candidate-register-email">Email Address</label>
            <input
              id="candidate-register-email"
              name="candidate-register-email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="jane@example.com"
              required
              autoComplete="email"
              disabled={isSubmitting}
            />
          </div>

          <div className="form-group">
            <label htmlFor="candidate-register-password">Password</label>
            <div className="password-input-wrapper">
              <input
                id="candidate-register-password"
                name="candidate-register-password"
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
            <label htmlFor="candidate-register-confirm-password">Confirm Password</label>
            <div className="password-input-wrapper">
              <input
                id="candidate-register-confirm-password"
                name="candidate-register-confirm-password"
                type={showPassword ? "text" : "password"}
                className="form-input"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Re-enter your password"
                required
                autoComplete="new-password"
                disabled={isSubmitting}
              />
            </div>
          </div>

          <div className="form-actions">
            <button
              type="submit"
              className="btn btn-primary btn-block"
              disabled={isSubmitting || !fullName.trim() || !email.trim() || !password || !confirmPassword}
            >
              {isSubmitting ? (
                <>
                  <span className="spinner-inline" aria-hidden="true" />
                  <span>Creating Account...</span>
                </>
              ) : (
                "Create Account"
              )}
            </button>
          </div>
        </form>

        <footer className="candidate-auth-footer" style={{ textAlign: "center" }}>
          <p className="footer-text" style={{ margin: 0, fontSize: "14px" }}>
            Already have an account?{" "}
            <button
              type="button"
              className="link-button"
              onClick={onNavigateLogin}
              style={{ fontWeight: 600, color: "var(--accent-primary, #3b82f6)", background: "none", border: "none", cursor: "pointer", padding: "0 4px" }}
            >
              Sign in
            </button>
          </p>
        </footer>
      </div>
    </div>
  );
}
