# Human Labelling Protocol for Matching Evaluation

This document outlines the mandatory operational protocol for collecting and adjudicating human relevance labels for the AI-Driven Smart Hiring Platform matching evaluation framework.

---

## 1. Grounding Against Recruiter-Approved Requirements Only
Reviewers must assess candidate profiles exclusively against the frozen, recruiter-approved job requirements (`READY_FOR_MATCHING` requisition revision). Reviewers must not evaluate candidates against external personal opinions, unstated corporate preferences, or subjective intuitions.

## 2. Independent Double-Blind Review
At least two qualified reviewers must independently label each candidate–requisition pair. Reviewers must not communicate during the labelling period or observe another reviewer's ratings prior to formal submission.

## 3. Blind to System Predictions
Reviewers must never see the algorithmic compatibility score, breakdown, ranking position, or warnings before recording their independent label. This prevents cognitive anchoring to model outputs.

## 4. Disagreement Recording
Reviewer differences must be explicitly logged and retained in the evaluation manifest. The system computes inter-rater agreement and Cohen's kappa across all double-rated pairs.

## 5. Structured Adjudication
When reviewer disagreement occurs, an authorized senior adjudicator reviews both assessments along with the requirement definitions to determine the final reference label (`adjudicated_label`). The initial reviewer labels remain preserved for auditability.

## 6. Development vs Held-Out Split Discipline
- **Development Split**: May be used for inspecting edge cases, calibration, and tuning threshold/weight parameters.
- **Held-Out Split**: Must remain completely sequestered and hidden during algorithm or policy development.
- **Pair Disjointness**: A candidate profile variant must never cross splits.

## 7. Frozen Hashes Prior to Evaluation
Before held-out evaluation is conducted, the evaluation manifest checksum and scoring configuration hashes must be computed and frozen. Any subsequent modification to the manifest invalidates the run.

## 8. Provenance and Authorization Safeguards
Evaluation manifests must declare:
- Approved dataset identifier;
- Recorded provenance of how the data was compiled;
- Explicit project authorization note.
No real candidate records may be evaluated without logged provenance and project approval.

## 9. Exclusion of Protected Characteristics
Under no circumstances may protected demographic traits, candidate contact information, or operational storage paths be included in evaluation manifests or operational matching inputs.

## 10. Role in Decision Support
This protocol exists to measure matching consistency against recruiter-approved requirements. It does not certify legal hiring compliance, and high inter-rater agreement does not constitute proof of candidate quality or job suitability.
