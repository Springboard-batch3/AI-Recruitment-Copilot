import React from "react";
import { Icon } from "../Icon";

export function ReviewerWorkspace() {
  return (
    <div className="reviewer-workspace-container" role="region" aria-label="Reviewer Workspace">
      <div className="empty-state-card">
        <div className="empty-state-icon-wrapper" aria-hidden="true">
          <Icon name="award" size={36} />
        </div>
        <h2 className="empty-state-title">No assigned reviews yet</h2>
        <p className="empty-state-description">
          You are signed in as a <strong>Reviewer</strong>. Assigned job reviews and evaluation scorecards will appear here once job requisitions are configured.
        </p>
        <div className="empty-state-guidelines">
          <div className="guideline-item">
            <Icon name="check" size={16} className="guideline-check" />
            <span>Multi-factor candidate assessment rubrics</span>
          </div>
          <div className="guideline-item">
            <Icon name="check" size={16} className="guideline-check" />
            <span>Assigned requisition review workflows</span>
          </div>
          <div className="guideline-item">
            <Icon name="check" size={16} className="guideline-check" />
            <span>Audited consensus decision-making</span>
          </div>
        </div>
      </div>
    </div>
  );
}
