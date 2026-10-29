import enum
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.candidate import StrictSchema
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_match import MatchResultState

EVALUATION_MANIFEST_SCHEMA_VERSION: str = "phase2e3_eval_manifest_v1"
EVALUATION_POLICY_VERSION: str = "phase2e3_matching_eval_v1"
EVALUATION_CALIBRATION_VERSION: str = "phase2e3_calibration_v1"


class RelevanceBand(int, enum.Enum):
    BAND_0 = 0
    BAND_1 = 1
    BAND_2 = 2
    BAND_3 = 3


class RelevanceLabel(str, enum.Enum):
    NOT_RELEVANT = "NOT_RELEVANT"
    PARTIALLY_RELEVANT = "PARTIALLY_RELEVANT"
    RELEVANT = "RELEVANT"
    HIGHLY_RELEVANT = "HIGHLY_RELEVANT"


RELEVANCE_BAND_TO_LABEL: Dict[int, RelevanceLabel] = {
    0: RelevanceLabel.NOT_RELEVANT,
    1: RelevanceLabel.PARTIALLY_RELEVANT,
    2: RelevanceLabel.RELEVANT,
    3: RelevanceLabel.HIGHLY_RELEVANT,
}

RELEVANCE_LABEL_TO_BAND: Dict[RelevanceLabel, int] = {
    RelevanceLabel.NOT_RELEVANT: 0,
    RelevanceLabel.PARTIALLY_RELEVANT: 1,
    RelevanceLabel.RELEVANT: 2,
    RelevanceLabel.HIGHLY_RELEVANT: 3,
}

RELEVANCE_GRADED_VALUES: Dict[RelevanceLabel, float] = {
    RelevanceLabel.NOT_RELEVANT: 0.0,
    RelevanceLabel.PARTIALLY_RELEVANT: 1.0,
    RelevanceLabel.RELEVANT: 2.0,
    RelevanceLabel.HIGHLY_RELEVANT: 3.0,
}


class EvaluationSplit(str, enum.Enum):
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"


class EvaluationAccuracyStatus(str, enum.Enum):
    READY_NOT_EVALUATED = "READY_NOT_EVALUATED"
    EVALUATED_TARGET_MET = "EVALUATED_TARGET_MET"
    EVALUATED_BELOW_TARGET = "EVALUATED_BELOW_TARGET"
    INSUFFICIENT_AUTHORIZED_DATA = "INSUFFICIENT_AUTHORIZED_DATA"
    INVALID_EVALUATION_PACKAGE = "INVALID_EVALUATION_PACKAGE"
    DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"


class AdjudicationState(str, enum.Enum):
    UNREVIEWED = "UNREVIEWED"
    SINGLE_REVIEWER = "SINGLE_REVIEWER"
    AGREED = "AGREED"
    DISPUTED = "DISPUTED"
    ADJUDICATED = "ADJUDICATED"


class EvaluationErrorCategory(str, enum.Enum):
    FALSE_CREDIT_UNSAFE_ALIAS = "FALSE_CREDIT_UNSAFE_ALIAS"
    MISSED_APPROVED_EQUIVALENCE = "MISSED_APPROVED_EQUIVALENCE"
    EXPERIENCE_INTERVAL_ERROR = "EXPERIENCE_INTERVAL_ERROR"
    RECENCY_ERROR = "RECENCY_ERROR"
    IRRELEVANT_EVIDENCE_CREDIT = "IRRELEVANT_EVIDENCE_CREDIT"
    PARSER_PROFILE_ERROR = "PARSER_PROFILE_ERROR"
    TAXONOMY_GAP = "TAXONOMY_GAP"
    WEIGHT_THRESHOLD_ISSUE = "WEIGHT_THRESHOLD_ISSUE"
    REVIEWER_DISAGREEMENT = "REVIEWER_DISAGREEMENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReviewerRequirementLabel(StrictSchema):
    requirement_id: UUID
    expected_state: MatchResultState
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_safety(self) -> "ReviewerRequirementLabel":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class ReviewerLabel(StrictSchema):
    pair_id: str = Field(min_length=1)
    reviewer_pseudonym: str = Field(min_length=1)
    relevance_band: int = Field(ge=0, le=3)
    requirement_labels: List[ReviewerRequirementLabel] = Field(default_factory=list)
    reviewer_note: Optional[str] = None
    created_at: datetime
    adjudication_state: AdjudicationState = AdjudicationState.SINGLE_REVIEWER

    @model_validator(mode="after")
    def validate_safety(self) -> "ReviewerLabel":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class AdjudicatedLabel(StrictSchema):
    pair_id: str = Field(min_length=1)
    original_reviewer_labels: List[ReviewerLabel] = Field(min_length=1)
    disagreement_status: bool
    final_relevance_band: int = Field(ge=0, le=3)
    adjudicator_pseudonym: str = Field(min_length=1)
    concise_reason: str = Field(min_length=1)
    adjudicated_at: datetime
    adjudicated_requirement_labels: List[ReviewerRequirementLabel] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_safety(self) -> "AdjudicatedLabel":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class ManifestExclusion(StrictSchema):
    pair_id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    details: Optional[str] = None

    @model_validator(mode="after")
    def validate_safety(self) -> "ManifestExclusion":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        return self


class EvaluationManifestRecord(StrictSchema):
    pair_id: str = Field(min_length=1)
    job_identifier: str = Field(min_length=1)
    revision: int = Field(ge=1)
    candidate_identifier: str = Field(min_length=1)
    profile_version: int = Field(ge=1)
    split: EvaluationSplit
    reviewer_code: Optional[str] = None
    relevance_band: Optional[int] = Field(default=None, ge=0, le=3)
    relevance_label: Optional[RelevanceLabel] = None
    adjudicated_band: Optional[int] = Field(default=None, ge=0, le=3)
    adjudicated_label: Optional[RelevanceLabel] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_safety_and_sync(self) -> "EvaluationManifestRecord":
        verify_no_prohibited_keys(self.model_dump(mode="json"))
        if self.relevance_band is not None and self.relevance_label is None:
            self.relevance_label = RELEVANCE_BAND_TO_LABEL.get(self.relevance_band)
        elif self.relevance_label is not None and self.relevance_band is None:
            self.relevance_band = RELEVANCE_LABEL_TO_BAND.get(self.relevance_label)

        if self.adjudicated_band is not None and self.adjudicated_label is None:
            self.adjudicated_label = RELEVANCE_BAND_TO_LABEL.get(self.adjudicated_band)
        elif self.adjudicated_label is not None and self.adjudicated_band is None:
            self.adjudicated_label = RELEVANCE_LABEL_TO_BAND.get(self.adjudicated_label)

        return self


class EvaluationManifest(StrictSchema):
    manifest_version: str = EVALUATION_MANIFEST_SCHEMA_VERSION
    evaluation_id: UUID
    dataset_version: str = Field(min_length=1)
    provenance_reference: str = Field(min_length=1)
    authorization_reference: str = Field(min_length=1)
    created_at: datetime
    evaluation_date: datetime
    match_policy_version: str = Field(min_length=1)
    reviewer_label_version: str = Field(min_length=1)
    taxonomy_version: Optional[str] = None
    records: List[EvaluationManifestRecord] = Field(default_factory=list)
    exclusions: List[ManifestExclusion] = Field(default_factory=list)
    manifest_checksum: str

    @model_validator(mode="after")
    def validate_integrity(self) -> "EvaluationManifest":
        verify_no_prohibited_keys(self.model_dump(mode="json"))

        seen_pair_ids: set[str] = set()
        dev_pairs: set[str] = set()
        held_pairs: set[str] = set()
        dev_candidates: set[tuple[str, int]] = set()
        held_candidates: set[tuple[str, int]] = set()

        for r in self.records:
            if r.pair_id in seen_pair_ids:
                raise ValueError(f"Duplicate pair_id detected in manifest: {r.pair_id}")
            seen_pair_ids.add(r.pair_id)

            cand_key = (r.candidate_identifier, r.profile_version)
            if r.split == EvaluationSplit.DEVELOPMENT:
                dev_pairs.add(r.pair_id)
                dev_candidates.add(cand_key)
            elif r.split == EvaluationSplit.HELD_OUT:
                held_pairs.add(r.pair_id)
                held_candidates.add(cand_key)

        # Check split overlap on candidate profiles
        candidate_overlap = dev_candidates.intersection(held_candidates)
        if candidate_overlap:
            raise ValueError(
                f"Split leakage detected! Candidate(s) present in both DEVELOPMENT and HELD_OUT splits: {candidate_overlap}"
            )

        pair_overlap = dev_pairs.intersection(held_pairs)
        if pair_overlap:
            raise ValueError(f"Pair ID split overlap detected: {pair_overlap}")

        return self


def compute_manifest_checksum(
    dataset_version: str,
    provenance_reference: str,
    authorization_reference: str,
    match_policy_version: str,
    records: List[Dict[str, Any]],
    exclusions: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Computes deterministic canonical SHA-256 checksum for an evaluation manifest."""
    sorted_records = sorted(
        records,
        key=lambda r: (
            str(r.get("pair_id", r.get("pairId", ""))),
            str(r.get("job_identifier", r.get("jobIdentifier", ""))),
            int(r.get("revision", 1)),
            str(r.get("candidate_identifier", r.get("candidateIdentifier", ""))),
            int(r.get("profile_version", r.get("profileVersion", 1))),
            str(r.get("split", "")),
        ),
    )
    sorted_exclusions = sorted(
        exclusions or [],
        key=lambda e: (
            str(e.get("pair_id", e.get("pairId", ""))),
            str(e.get("reason_code", e.get("reasonCode", ""))),
        ),
    )
    payload = {
        "dataset_version": dataset_version,
        "provenance_reference": provenance_reference,
        "authorization_reference": authorization_reference,
        "match_policy_version": match_policy_version,
        "records": sorted_records,
        "exclusions": sorted_exclusions,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


class ClassMetrics(StrictSchema):
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1: Optional[float] = None
    support: int


class MacroF1Report(StrictSchema):
    macro_f1: float
    per_class_metrics: Dict[str, ClassMetrics]
    per_label_f1: Dict[str, float]
    support_per_label: Dict[str, int]
    confusion_matrix: Dict[str, Dict[str, int]]
    classes_evaluated: List[str]
    zero_division_handled: bool = True


class NDCG5Report(StrictSchema):
    mean_ndcg_at_5: Optional[float] = None
    per_job_ndcg: Dict[str, Optional[float]]
    candidates_per_job: Dict[str, int]
    eligible_jobs_count: int
    excluded_jobs_count: int
    is_computable: bool
    uncomputable_reason: Optional[str] = None


class SpearmanReport(StrictSchema):
    correlation: Optional[float] = None
    per_job_spearman: Dict[str, Optional[float]] = Field(default_factory=dict)
    evaluated_pairs_count: int
    evaluated_jobs_count: int
    is_defined: bool
    undefined_reason: Optional[str] = None


class InterRaterAgreementReport(StrictSchema):
    percent_agreement: float
    cohen_kappa: Optional[float] = None
    total_overlapping_pairs: int
    reviewer_disagreement_count: int
    adjudicated_count: int


class RequirementLevelAgreementReport(StrictSchema):
    total_evaluated_requirements: int
    agreed_requirement_count: int
    agreement_rate: float
    per_state_confusion: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    is_computable: bool
    uncomputable_reason: Optional[str] = None


class BandCalibrationPolicy(StrictSchema):
    policy_version: str = EVALUATION_CALIBRATION_VERSION
    band_thresholds: Dict[str, float] = Field(
        default_factory=lambda: {
            "band_1_min": 25.0,
            "band_2_min": 60.0,
            "band_3_min": 85.0,
        }
    )
    description: str = "Standard 4-band score boundary calibration"
    policy_checksum: str = ""

    @model_validator(mode="after")
    def compute_policy_hash(self) -> "BandCalibrationPolicy":
        if not self.policy_checksum:
            serialized = json.dumps(
                {"version": self.policy_version, "thresholds": self.band_thresholds},
                sort_keys=True,
                separators=(",", ":"),
            )
            self.policy_checksum = hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()
        return self


class CalibrationRunReport(StrictSchema):
    calibration_version: str
    tested_configurations: List[Dict[str, Any]]
    selected_configuration: Dict[str, Any]
    dev_macro_f1: float
    evaluated_dev_pairs: int
    policy_checksum: str
    status_message: str


class EvaluationReport(StrictSchema):
    report_id: UUID
    evaluation_status: EvaluationAccuracyStatus
    target_met: bool
    dataset_version: str
    provenance_reference: str
    authorization_reference: str
    manifest_fingerprint: str
    match_policy_version: str
    calibration_policy_fingerprint: str
    split_evaluated: EvaluationSplit
    total_manifest_pairs: int
    evaluated_pairs_count: int
    excluded_records_count: int
    exclusions_by_reason: Dict[str, int] = Field(default_factory=dict)
    macro_f1_report: Optional[MacroF1Report] = None
    ndcg_report: Optional[NDCG5Report] = None
    spearman_report: Optional[SpearmanReport] = None
    inter_rater_agreement: Optional[InterRaterAgreementReport] = None
    requirement_agreement: Optional[RequirementLevelAgreementReport] = None
    reviewed_error_categories: Dict[str, int] = Field(default_factory=dict)
    status_reason: str
    limitations: List[str]
    sample_sizes: Dict[str, int]
    acceptance_conclusion: str
    evaluated_at: datetime
