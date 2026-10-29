import React, { ReactNode } from "react";

export interface FormFieldProps {
  id: string;
  label: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
  className?: string;
}

export function FormField({
  id,
  label,
  hint,
  error,
  required = false,
  children,
  className = "",
}: FormFieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={`form-field ${error ? "has-error" : ""} ${className}`}>
      <label htmlFor={id} className="form-field-label">
        <span>{label}</span>
        {required && <span className="form-field-required" aria-hidden="true">*</span>}
      </label>

      {hint && (
        <p id={hintId} className="form-field-hint">
          {hint}
        </p>
      )}

      <div className="form-field-control" data-describedby={describedBy}>
        {children}
      </div>

      {error && (
        <p id={errorId} className="form-field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
