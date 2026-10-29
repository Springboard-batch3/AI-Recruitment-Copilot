# Phase 2E.3 Reviewer Ground-Truth Labelling Guide

## 1. Purpose and Operational Role
This guide establishes reproducible, non-discriminatory protocol for manual human ground-truth labelling to evaluate the Milestone 2 matching system offline.
- **Offline Evaluation Only**: These relevance bands and requirement states exist solely for offline evaluation and calibration. They must never be exposed as automated candidate filtering or pass/fail thresholds in the product.
- **Blind Review Standard**: Human reviewers must not be shown candidate real identities, protected attributes, existing system scores, rank orders, system explanations, or other reviewers' labels.

---

## 2. Reviewer Evidence Rules
When reviewing candidate-job pairs:
1. **Permitted Information**:
   - Approved Job Requirements (title, categories, necessity: must-have vs nice-to-have, priorities).
   - Candidate Professional Profile (anonymized/pseudonymous ID, documented skills, employment history, education, certifications, projects, languages).
   - Permitted evidence excerpts and context snippets.
2. **Prohibited Information**:
   - Candidate direct identifiers (names, emails, phones, photos, URLs, home addresses, dates of birth).
   - Protected demographic attributes (gender, race, age, religion, nationality, disability).
   - AI system match scores, rank positions, or system generated rationales.
   - Another reviewer's scores or notes.

---

## 3. Four-Band Offline Relevance Definitions

| Relevance Band | Label | Operational Definition |
|---|---|---|
| **0** | `BAND_0` / `NOT_RELEVANT` | **Little or No Documented Alignment**: Candidate exhibits virtually no documented evidence for critical/must-have job requirements. Major qualifications are missing or unrelated. |
| **1** | `BAND_1` / `PARTIALLY_RELEVANT` | **Limited Documented Alignment**: Candidate demonstrates limited alignment, matching secondary/nice-to-have skills or showing superficial/early-stage evidence without required core depth. |
| **2** | `BAND_2` / `RELEVANT` | **Moderate Documented Alignment**: Candidate satisfies substantial portions of core/must-have requirements with verified evidence, but may miss minor optional requirements or have slight experience recency gaps. |
| **3** | `BAND_3` / `HIGHLY_RELEVANT` | **Strong Documented Alignment**: Candidate demonstrates clear, robust, direct evidence for all must-have requirements and substantial nice-to-have requirements. |

---

## 4. Requirement-Level Evidence Ground Truth States

When requirement-level ground truth is collected, use evidence-centered states rather than subjective candidate ability claims:

- `MATCHED`: Clear, unequivocal documented evidence satisfies the requirement.
- `PARTIALLY_MATCHED`: Documented evidence partially satisfies requirement (e.g. adjacent framework, shorter duration).
- `NOT_EVIDENCED`: No evidence found in the documented professional profile for this requirement.
- `INSUFFICIENT_EVIDENCE`: Fragmentary or ambiguous text that cannot be verified with confidence.
- `CONFLICTING_EVIDENCE`: Contradictory records in profile (e.g. conflicting dates or titles).
- `NOT_APPLICABLE`: Requirement not applicable to this role or review track.

---

## 5. Reviewer JSON Template Example

```json
{
  "pairId": "PAIR-JOB001-CAND042",
  "reviewerPseudonym": "REV_ALPHA_01",
  "relevanceBand": 2,
  "requirementLabels": [
    {
      "requirementId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "expectedState": "MATCHED",
      "notes": "Verified 4 years of Python backend experience across 2 companies"
    },
    {
      "requirementId": "7ca85f64-5717-4562-b3fc-2c963f66afb7",
      "expectedState": "PARTIALLY_MATCHED",
      "notes": "Docker present, Kubernetes not explicitly documented"
    }
  ],
  "reviewerNote": "Solid mid-to-senior profile; satisfies all core backend requirements.",
  "createdAt": "2026-09-10T12:00:00Z",
  "adjudicationState": "SINGLE_REVIEWER"
}
```

---

## 6. Adjudication JSON Template Example

```json
{
  "pairId": "PAIR-JOB001-CAND042",
  "originalReviewerLabels": [
    {
      "pairId": "PAIR-JOB001-CAND042",
      "reviewerPseudonym": "REV_ALPHA_01",
      "relevanceBand": 2,
      "createdAt": "2026-09-10T12:00:00Z",
      "adjudicationState": "SINGLE_REVIEWER"
    },
    {
      "pairId": "PAIR-JOB001-CAND042",
      "reviewerPseudonym": "REV_BETA_02",
      "relevanceBand": 3,
      "createdAt": "2026-09-10T12:30:00Z",
      "adjudicationState": "SINGLE_REVIEWER"
    }
  ],
  "disagreementStatus": true,
  "finalRelevanceBand": 3,
  "adjudicatorPseudonym": "ADJ_OMEGA_LEAD",
  "conciseReason": "Adjudicated to Band 3 due to strong evidence in open source repository section verifying Kubernetes deployment leadership.",
  "adjudicatedAt": "2026-09-10T14:00:00Z"
}
```

---

## 7. Frozen Evaluation Manifest JSON Template Example

```json
{
  "manifestVersion": "phase2e3_eval_manifest_v1",
  "evaluationId": "e1f72b9a-4c28-4e89-b811-9a706509f789",
  "datasetVersion": "M2_HOLD_EVAL_V1",
  "provenanceReference": "Authorized Evaluation Protocol 2026-09",
  "authorizationReference": "Evaluation Committee Approval #M2-2026",
  "createdAt": "2026-09-10T10:00:00Z",
  "evaluationDate": "2026-09-10T10:00:00Z",
  "matchPolicyVersion": "phase2c3_scoring_v1",
  "reviewerLabelVersion": "rev_labels_v1",
  "taxonomyVersion": "onet_28_0",
  "records": [
    {
      "pairId": "PAIR-JOB001-CAND042",
      "jobIdentifier": "JOB-ENG-001",
      "revision": 1,
      "candidateIdentifier": "CAND-PROF-042",
      "profileVersion": 1,
      "split": "HELD_OUT",
      "reviewerCode": "REV_ALPHA_01",
      "relevanceBand": 3,
      "adjudicatedBand": 3,
      "notes": "Adjudicated consensus"
    }
  ],
  "exclusions": [
    {
      "pairId": "PAIR-JOB001-CAND099",
      "reasonCode": "INCOMPLETE_PROFILE_DOCUMENT",
      "details": "Resume file missing second page during ingestion"
    }
  ],
  "manifestChecksum": "computed_canonical_sha256_hash"
}
```
