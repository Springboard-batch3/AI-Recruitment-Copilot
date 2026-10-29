import React, { useState, FormEvent } from "react";
import { useAuth } from "../../context/AuthContext";
import { Icon } from "../Icon";

export function ChangePasswordPage() {
  const { user, changePassword, logout } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [currentError, setCurrentError] = useState<string | null>(null);
  const [newPasswordError, setNewPasswordError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setCurrentError(null);
    setNewPasswordError(null);
    setConfirmError(null);
    setServerError(null);

    let hasError = false;

    if (!currentPassword) {
      setCurrentError("Please enter your current or temporary password.");
      hasError = true;
    }
    if (newPassword.length < 8) {
      setNewPasswordError("New password must be at least 8 characters.");
      hasError = true;
    } else if (currentPassword && currentPassword === newPassword) {
      setNewPasswordError("New password cannot be the same as your temporary password.");
      hasError = true;
    }

    if (newPassword !== confirmPassword) {
      setConfirmError("New password confirmation does not match.");
      hasError = true;
    }

    if (hasError) {
      return;
    }

    try {
      setIsSubmitting(true);
      await changePassword(currentPassword, newPassword, confirmPassword);
    } catch (err: any) {
      if (err?.code === "INVALID_CURRENT_PASSWORD") {
        setServerError("The current password provided is incorrect.");
      } else if (err?.code === "PASSWORD_REUSE_FORBIDDEN") {
        setServerError("You cannot reuse your previous password. Please choose a new one.");
      } else {
        setServerError(err?.message || "Failed to update password. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="auth-page-container">
      <div className="auth-card auth-card-narrow" role="region" aria-label="Password Change Required">
        <div className="auth-header">
          <div className="auth-logo-badge warn" aria-hidden="true">
            <Icon name="shield" size={22} />
          </div>
          <h1 className="auth-title">Password Update Required</h1>
          <p className="auth-subtitle">
            Welcome, <strong>{user?.displayName || "Staff Member"}</strong>. Your account was issued with a one-time temporary credential. Please set a new permanent password to access the platform.
          </p>
        </div>

        {serverError && (
          <div className="compact-alert error" role="alert" aria-live="polite">
            <Icon name="alert-triangle" size={15} className="alert-icon" />
            <p className="alert-message">{serverError}</p>
          </div>
        )}

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <div className="form-group">
            <label htmlFor="current-pw" className="form-label">
              Current / Temporary Password
            </label>
            <div className="input-with-icon">
              <span className="input-icon-prefix" aria-hidden="true">
                <Icon name="shield" size={16} />
              </span>
              <input
                id="current-pw"
                type={showCurrent ? "text" : "password"}
                className={`form-input ${currentError ? "input-has-error" : ""}`}
                value={currentPassword}
                onChange={(e) => {
                  setCurrentPassword(e.target.value);
                  if (currentError) setCurrentError(null);
                  if (serverError) setServerError(null);
                }}
                disabled={isSubmitting}
                autoComplete="current-password"
                aria-invalid={!!currentError}
                aria-describedby={currentError ? "current-pw-err" : undefined}
                required
              />
              <button
                type="button"
                className="password-toggle-btn"
                onClick={() => setShowCurrent(!showCurrent)}
                aria-label={showCurrent ? "Hide current password" : "Show current password"}
                title={showCurrent ? "Hide current password" : "Show current password"}
              >
                <Icon name={showCurrent ? "eye" : "eye"} size={16} />
              </button>
            </div>
            {currentError && (
              <p id="current-pw-err" className="field-error-text" role="alert">
                {currentError}
              </p>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="new-pw" className="form-label">
              New Password (min. 8 characters)
            </label>
            <div className="input-with-icon">
              <span className="input-icon-prefix" aria-hidden="true">
                <Icon name="shield" size={16} />
              </span>
              <input
                id="new-pw"
                type={showNew ? "text" : "password"}
                className={`form-input ${newPasswordError ? "input-has-error" : ""}`}
                value={newPassword}
                onChange={(e) => {
                  setNewPassword(e.target.value);
                  if (newPasswordError) setNewPasswordError(null);
                  if (serverError) setServerError(null);
                }}
                disabled={isSubmitting}
                autoComplete="new-password"
                aria-invalid={!!newPasswordError}
                aria-describedby={newPasswordError ? "new-pw-err" : undefined}
                required
              />
              <button
                type="button"
                className="password-toggle-btn"
                onClick={() => setShowNew(!showNew)}
                aria-label={showNew ? "Hide new password" : "Show new password"}
                title={showNew ? "Hide new password" : "Show new password"}
              >
                <Icon name={showNew ? "eye" : "eye"} size={16} />
              </button>
            </div>
            {newPasswordError && (
              <p id="new-pw-err" className="field-error-text" role="alert">
                {newPasswordError}
              </p>
            )}
          </div>

          <div className="form-group">
            <label htmlFor="confirm-pw" className="form-label">
              Confirm New Password
            </label>
            <div className="input-with-icon">
              <span className="input-icon-prefix" aria-hidden="true">
                <Icon name="shield" size={16} />
              </span>
              <input
                id="confirm-pw"
                type={showConfirm ? "text" : "password"}
                className={`form-input ${confirmError ? "input-has-error" : ""}`}
                value={confirmPassword}
                onChange={(e) => {
                  setConfirmPassword(e.target.value);
                  if (confirmError) setConfirmError(null);
                  if (serverError) setServerError(null);
                }}
                disabled={isSubmitting}
                autoComplete="new-password"
                aria-invalid={!!confirmError}
                aria-describedby={confirmError ? "confirm-pw-err" : undefined}
                required
              />
              <button
                type="button"
                className="password-toggle-btn"
                onClick={() => setShowConfirm(!showConfirm)}
                aria-label={showConfirm ? "Hide password confirmation" : "Show password confirmation"}
                title={showConfirm ? "Hide password confirmation" : "Show password confirmation"}
              >
                <Icon name={showConfirm ? "eye" : "eye"} size={16} />
              </button>
            </div>
            {confirmError && (
              <p id="confirm-pw-err" className="field-error-text" role="alert">
                {confirmError}
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
                <span>Updating Password...</span>
              </>
            ) : (
              <span>Update Password &amp; Continue</span>
            )}
          </button>
        </form>

        <div className="auth-footer-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={logout}
            disabled={isSubmitting}
          >
            <Icon name="arrow-left" size={14} />
            <span>Sign Out</span>
          </button>
        </div>
      </div>
    </div>
  );
}

