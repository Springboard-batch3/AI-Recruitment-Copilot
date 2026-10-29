import hashlib
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from app.schemas.evaluation import (
    EVALUATION_CALIBRATION_VERSION,
    EVALUATION_MANIFEST_SCHEMA_VERSION,
    EVALUATION_POLICY_VERSION,
    AdjudicatedLabel,
    AdjudicationState,
    BandCalibrationPolicy,
    CalibrationRunReport,
    ClassMetrics,
    EvaluationAccuracyStatus,
    EvaluationErrorCategory,
    EvaluationManifest,
    EvaluationManifestRecord,
    EvaluationReport,
    EvaluationSplit,
    InterRaterAgreementReport,
    MacroF1Report,
    ManifestExclusion,
    NDCG5Report,
    RelevanceBand,
    RelevanceLabel,
    RequirementLevelAgreementReport,
    ReviewerLabel,
    ReviewerRequirementLabel,
    SpearmanReport,
    compute_manifest_checksum,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_match import MatchResultState

logger = logging.getLogger("ai_hiring.services.evaluation")


# ---------------------------------------------------------------------------
# 1. Macro-F1 Metric (Pure Python with Zero-Division Handling)
# ---------------------------------------------------------------------------
def calculate_macro_f1(
    y_true: List[str],
    y_pred: List[str],
    labels: Optional[List[str]] = None,
) -> MacroF1Report:
    """Calculates macro-F1 across multi-class labels with strict zero-division handling.

    Returns macro-F1, per-class metrics (precision, recall, F1, support), confusion matrix,
    and handles mathematically undefined classes without raising.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"y_true (len {len(y_true)}) and y_pred (len {len(y_pred)}) must have equal length.")

    if labels is None:
        labels = sorted(list(set(y_true).union(set(y_pred))))

    if not labels:
        return MacroF1Report(
            macro_f1=0.0,
            per_class_metrics={},
            per_label_f1={},
            support_per_label={},
            confusion_matrix={},
            classes_evaluated=[],
            zero_division_handled=True,
        )

    # Initialize confusion matrix: cm[actual][predicted]
    cm: Dict[str, Dict[str, int]] = {
        true_lbl: {pred_lbl: 0 for pred_lbl in labels} for true_lbl in labels
    }
    support_per_label: Dict[str, int] = {lbl: 0 for lbl in labels}

    for true_val, pred_val in zip(y_true, y_pred):
        if true_val in cm and pred_val in cm[true_val]:
            cm[true_val][pred_val] += 1
            support_per_label[true_val] += 1

    per_class_metrics: Dict[str, ClassMetrics] = {}
    per_label_f1: Dict[str, float] = {}

    for lbl in labels:
        tp = cm[lbl][lbl]
        fp = sum(cm[other][lbl] for other in labels if other != lbl)
        fn = sum(cm[lbl][other] for other in labels if other != lbl)
        support = support_per_label[lbl]

        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0

        if (precision + recall) > 0.0:
            f1 = 2.0 * (precision * recall) / (precision + recall)
        else:
            f1 = 0.0

        per_label_f1[lbl] = round(f1, 6)
        per_class_metrics[lbl] = ClassMetrics(
            precision=round(precision, 6),
            recall=round(recall, 6),
            f1=round(f1, 6),
            support=support,
        )

    # Macro-average across all defined classes
    macro_val = sum(per_label_f1.values()) / len(labels)

    return MacroF1Report(
        macro_f1=round(macro_val, 6),
        per_class_metrics=per_class_metrics,
        per_label_f1=per_label_f1,
        support_per_label=support_per_label,
        confusion_matrix=cm,
        classes_evaluated=labels,
        zero_division_handled=True,
    )


# ---------------------------------------------------------------------------
# 2. NDCG@5 Ranking Metric (Pure Python with Tie Handling)
# ---------------------------------------------------------------------------
def calculate_ndcg_at_k(
    actual_relevance: List[float],
    predicted_scores: List[float],
    k: int = 5,
) -> float:
    """Calculates Normalized Discounted Cumulative Gain at rank k (NDCG@k).

    Formula:
      DCG@k = sum_{i=1}^k (2^{rel_i} - 1) / log2(i + 1)
      IDCG@k = DCG@k for ideal ranking (sorted actual_relevance descending)
      NDCG@k = DCG@k / IDCG@k (or 1.0 if IDCG@k == 0.0)
    """
    if len(actual_relevance) != len(predicted_scores):
        raise ValueError("actual_relevance and predicted_scores must have the same length.")

    if not actual_relevance:
        return 0.0

    # Deterministic stable sort: higher predicted score first, then preserve order
    ranked_indices = sorted(
        range(len(predicted_scores)),
        key=lambda idx: predicted_scores[idx],
        reverse=True,
    )

    actual_at_k = [actual_relevance[i] for i in ranked_indices[:k]]
    ideal_at_k = sorted(actual_relevance, reverse=True)[:k]

    def _compute_dcg(relevances: List[float]) -> float:
        dcg = 0.0
        for rank_idx, rel in enumerate(relevances, start=1):
            numerator = (2.0 ** rel) - 1.0
            denominator = math.log2(rank_idx + 1.0)
            dcg += numerator / denominator
        return dcg

    dcg = _compute_dcg(actual_at_k)
    idcg = _compute_dcg(ideal_at_k)

    if idcg <= 0.0:
        # If all candidates have 0 relevance, ranking cannot improve or degrade
        return 1.0 if dcg <= 0.0 else 0.0

    return round(min(1.0, max(0.0, dcg / idcg)), 6)


def calculate_dataset_ndcg5(
    job_candidate_pairs: List[Dict[str, Any]],
) -> NDCG5Report:
    """Calculates NDCG@5 per eligible job and aggregate mean.

    Jobs with fewer than 5 candidates are tracked and excluded from mean if < 5 candidates,
    unless explicitly configured.
    """
    if not job_candidate_pairs:
        return NDCG5Report(
            mean_ndcg_at_5=None,
            per_job_ndcg={},
            candidates_per_job={},
            eligible_jobs_count=0,
            excluded_jobs_count=0,
            is_computable=False,
            uncomputable_reason="No candidate pairs provided for NDCG calculation.",
        )

    # Group by job_identifier
    jobs_map: Dict[str, List[Dict[str, Any]]] = {}
    for pair in job_candidate_pairs:
        job_id = str(pair.get("job_identifier", "unknown"))
        jobs_map.setdefault(job_id, []).append(pair)

    per_job_ndcg: Dict[str, Optional[float]] = {}
    candidates_per_job: Dict[str, int] = {}
    eligible_scores: List[float] = []
    excluded_count = 0

    for job_id, pairs in jobs_map.items():
        candidates_per_job[job_id] = len(pairs)
        if len(pairs) < 5:
            # Safe handling: calculate bounded NDCG@5 for available count, but flag small support
            excluded_count += 1

        actuals = [float(p.get("relevance", 0.0)) for p in pairs]
        preds = [float(p.get("score", 0.0)) for p in pairs]
        val = calculate_ndcg_at_k(actuals, preds, k=5)
        per_job_ndcg[job_id] = val
        eligible_scores.append(val)

    if not eligible_scores:
        return NDCG5Report(
            mean_ndcg_at_5=None,
            per_job_ndcg=per_job_ndcg,
            candidates_per_job=candidates_per_job,
            eligible_jobs_count=0,
            excluded_jobs_count=len(jobs_map),
            is_computable=False,
            uncomputable_reason="No eligible jobs for NDCG@5.",
        )

    mean_val = sum(eligible_scores) / len(eligible_scores)
    return NDCG5Report(
        mean_ndcg_at_5=round(mean_val, 6),
        per_job_ndcg=per_job_ndcg,
        candidates_per_job=candidates_per_job,
        eligible_jobs_count=len(eligible_scores),
        excluded_jobs_count=excluded_count,
        is_computable=True,
        uncomputable_reason=None,
    )


# ---------------------------------------------------------------------------
# 3. Spearman Rank Correlation (Pure Python with Tie Handling)
# ---------------------------------------------------------------------------
def _compute_fractional_ranks(values: List[float]) -> List[float]:
    """Computes standard average fractional ranks for ties."""
    n = len(values)
    sorted_indices = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n

    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[sorted_indices[j + 1]] == values[sorted_indices[i]]:
            j += 1
        avg_rank = sum(r for r in range(i + 1, j + 2)) / (j - i + 1)
        for k in range(i, j + 1):
            ranks[sorted_indices[k]] = avg_rank
        i = j + 1

    return ranks


def calculate_spearman_correlation(
    x: List[float],
    y: List[float],
) -> SpearmanReport:
    """Calculates Spearman rank correlation coefficient with fractional tie handling.

    Reports undefined safely when variance is zero (e.g. constant human labels or constant system scores).
    """
    if len(x) != len(y):
        raise ValueError("x and y must have the same length.")

    n = len(x)
    if n < 2:
        return SpearmanReport(
            correlation=None,
            evaluated_pairs_count=n,
            evaluated_jobs_count=1,
            is_defined=False,
            undefined_reason="Sample size less than 2.",
        )

    # Check for constant input
    if len(set(x)) <= 1 or len(set(y)) <= 1:
        return SpearmanReport(
            correlation=None,
            evaluated_pairs_count=n,
            evaluated_jobs_count=1,
            is_defined=False,
            undefined_reason="Constant input: variance is zero.",
        )

    rx = _compute_fractional_ranks(x)
    ry = _compute_fractional_ranks(y)

    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    if var_x <= 0.0 or var_y <= 0.0:
        return SpearmanReport(
            correlation=None,
            evaluated_pairs_count=n,
            evaluated_jobs_count=1,
            is_defined=False,
            undefined_reason="Zero variance after ranking.",
        )

    rho = cov / math.sqrt(var_x * var_y)
    clamped_rho = max(-1.0, min(1.0, rho))

    return SpearmanReport(
        correlation=round(clamped_rho, 6),
        evaluated_pairs_count=n,
        evaluated_jobs_count=1,
        is_defined=True,
        undefined_reason=None,
    )


# ---------------------------------------------------------------------------
# 4. Reviewer Agreement & Adjudication
# ---------------------------------------------------------------------------
def calculate_reviewer_agreement(
    reviewer_labels: List[ReviewerLabel],
    adjudicated_labels: Optional[List[AdjudicatedLabel]] = None,
) -> InterRaterAgreementReport:
    """Calculates inter-rater agreement and Cohen's Kappa for pairs evaluated by multiple reviewers."""
    # Group reviewer labels by pair_id
    pair_labels: Dict[str, List[int]] = {}
    for r in reviewer_labels:
        pair_labels.setdefault(r.pair_id, []).append(r.relevance_band)

    overlapping_pairs = [p for p, bands in pair_labels.items() if len(bands) >= 2]
    if not overlapping_pairs:
        return InterRaterAgreementReport(
            percent_agreement=1.0 if reviewer_labels else 0.0,
            cohen_kappa=None,
            total_overlapping_pairs=0,
            reviewer_disagreement_count=0,
            adjudicated_count=len(adjudicated_labels or []),
        )

    r1_list: List[int] = []
    r2_list: List[int] = []
    disagreement_count = 0

    for p in overlapping_pairs:
        bands = pair_labels[p]
        b1, b2 = bands[0], bands[1]
        r1_list.append(b1)
        r2_list.append(b2)
        if b1 != b2:
            disagreement_count += 1

    n = len(r1_list)
    agreed_count = sum(1 for a, b in zip(r1_list, r2_list) if a == b)
    observed_agreement = agreed_count / n

    # Chance agreement
    all_classes = sorted(list(set(r1_list).union(set(r2_list))))
    p_e = 0.0
    for cls in all_classes:
        p1 = sum(1 for a in r1_list if a == cls) / n
        p2 = sum(1 for b in r2_list if b == cls) / n
        p_e += (p1 * p2)

    if (1.0 - p_e) <= 0.0:
        kappa: Optional[float] = 1.0 if observed_agreement == 1.0 else 0.0
    else:
        kappa = (observed_agreement - p_e) / (1.0 - p_e)
        kappa = round(max(-1.0, min(1.0, kappa)), 6)

    return InterRaterAgreementReport(
        percent_agreement=round(observed_agreement, 6),
        cohen_kappa=kappa,
        total_overlapping_pairs=n,
        reviewer_disagreement_count=disagreement_count,
        adjudicated_count=len(adjudicated_labels or []),
    )


# ---------------------------------------------------------------------------
# 5. Requirement-Level Agreement
# ---------------------------------------------------------------------------
def calculate_requirement_agreement(
    reviewer_req_labels: List[ReviewerRequirementLabel],
    system_req_states: List[MatchResultState],
) -> RequirementLevelAgreementReport:
    """Calculates requirement-level agreement rate when requirement-level human ground truth is collected."""
    if not reviewer_req_labels or len(reviewer_req_labels) != len(system_req_states):
        return RequirementLevelAgreementReport(
            total_evaluated_requirements=len(reviewer_req_labels),
            agreed_requirement_count=0,
            agreement_rate=0.0,
            per_state_confusion={},
            is_computable=False,
            uncomputable_reason="Requirement-level truth not provided or mismatched length.",
        )

    all_states = [s.value for s in MatchResultState]
    cm: Dict[str, Dict[str, int]] = {s1: {s2: 0 for s2 in all_states} for s1 in all_states}
    agreed = 0

    for rev, sys_st in zip(reviewer_req_labels, system_req_states):
        exp = rev.expected_state.value
        obs = sys_st.value
        if exp in cm and obs in cm[exp]:
            cm[exp][obs] += 1
        if exp == obs:
            agreed += 1

    total = len(reviewer_req_labels)
    rate = agreed / total if total > 0 else 0.0

    return RequirementLevelAgreementReport(
        total_evaluated_requirements=total,
        agreed_requirement_count=agreed,
        agreement_rate=round(rate, 6),
        per_state_confusion=cm,
        is_computable=True,
        uncomputable_reason=None,
    )


# ---------------------------------------------------------------------------
# 6. Score-to-Band Mapping
# ---------------------------------------------------------------------------
def map_score_to_relevance_band(
    score: float,
    policy: BandCalibrationPolicy,
) -> int:
    """Maps a 0..100 compatibility score to relevance band 0, 1, 2, or 3 using calibrated thresholds."""
    b1_min = policy.band_thresholds.get("band_1_min", 25.0)
    b2_min = policy.band_thresholds.get("band_2_min", 60.0)
    b3_min = policy.band_thresholds.get("band_3_min", 85.0)

    if score >= b3_min:
        return 3
    elif score >= b2_min:
        return 2
    elif score >= b1_min:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 7. Development-Only Calibration Service
# ---------------------------------------------------------------------------
def calibrate_score_bands_on_development(
    manifest: EvaluationManifest,
    system_scores: Dict[str, float],
    candidate_threshold_configs: Optional[List[Dict[str, float]]] = None,
) -> CalibrationRunReport:
    """Deterministic development-only calibration of score-to-band boundaries.

    OPERATES ONLY ON DEVELOPMENT SPLIT. Rejects any execution containing held-out records.
    """
    dev_records = [r for r in manifest.records if r.split == EvaluationSplit.DEVELOPMENT]
    if not dev_records:
        raise ValueError("No DEVELOPMENT split records available for calibration.")

    # Guard: Never allow held-out records in calibration
    held_out_in_dev = [r for r in dev_records if r.split == EvaluationSplit.HELD_OUT]
    if held_out_in_dev:
        raise ValueError("Security violation: HELD_OUT records detected in development calibration set.")

    # Default grid of boundary candidates
    configs_to_test = candidate_threshold_configs or [
        {"band_1_min": 25.0, "band_2_min": 50.0, "band_3_min": 75.0},
        {"band_1_min": 25.0, "band_2_min": 60.0, "band_3_min": 85.0},
        {"band_1_min": 30.0, "band_2_min": 60.0, "band_3_min": 80.0},
        {"band_1_min": 35.0, "band_2_min": 65.0, "band_3_min": 85.0},
    ]

    best_config = configs_to_test[0]
    best_macro_f1 = -1.0
    tested_results: List[Dict[str, Any]] = []

    # Ground truth bands for dev
    y_true_bands: List[int] = []
    y_scores: List[float] = []

    for r in dev_records:
        true_band = r.adjudicated_band if r.adjudicated_band is not None else r.relevance_band
        if true_band is None and r.relevance_label is not None:
            true_band = 3 if r.relevance_label == RelevanceLabel.HIGHLY_RELEVANT else (2 if r.relevance_label == RelevanceLabel.RELEVANT else (1 if r.relevance_label == RelevanceLabel.PARTIALLY_RELEVANT else 0))
        if true_band is None:
            true_band = 0

        score = system_scores.get(r.pair_id, 0.0)
        y_true_bands.append(true_band)
        y_scores.append(score)

    y_true_labels = [f"BAND_{b}" for b in y_true_bands]
    all_band_labels = ["BAND_0", "BAND_1", "BAND_2", "BAND_3"]

    for cfg in configs_to_test:
        test_policy = BandCalibrationPolicy(band_thresholds=cfg)
        pred_bands = [map_score_to_relevance_band(s, test_policy) for s in y_scores]
        pred_labels = [f"BAND_{b}" for b in pred_bands]

        f1_rep = calculate_macro_f1(y_true_labels, pred_labels, labels=all_band_labels)
        tested_results.append({
            "thresholds": cfg,
            "macro_f1": f1_rep.macro_f1,
            "per_class_f1": f1_rep.per_label_f1,
        })

        if f1_rep.macro_f1 > best_macro_f1:
            best_macro_f1 = f1_rep.macro_f1
            best_config = cfg

    selected_policy = BandCalibrationPolicy(
        policy_version=EVALUATION_CALIBRATION_VERSION,
        band_thresholds=best_config,
    )

    return CalibrationRunReport(
        calibration_version=EVALUATION_CALIBRATION_VERSION,
        tested_configurations=tested_results,
        selected_configuration=best_config,
        dev_macro_f1=round(best_macro_f1, 6),
        evaluated_dev_pairs=len(dev_records),
        policy_checksum=selected_policy.policy_checksum,
        status_message=f"Calibrated successfully on {len(dev_records)} development pairs.",
    )


# ---------------------------------------------------------------------------
# 8. Held-Out Accuracy Gate & Comprehensive Evaluator
# ---------------------------------------------------------------------------
def evaluate_matching_gate(
    manifest: EvaluationManifest,
    system_predictions: Dict[str, Dict[str, Any]],
    calibration_policy: Optional[BandCalibrationPolicy] = None,
    target_split: EvaluationSplit = EvaluationSplit.HELD_OUT,
    reviewer_labels: Optional[List[ReviewerLabel]] = None,
    adjudicated_labels: Optional[List[AdjudicatedLabel]] = None,
) -> EvaluationReport:
    """Evaluates match quality against authorized manifest while enforcing the locked accuracy gate.

    Target Acceptance:
      - Split must be HELD_OUT
      - Macro-F1 >= 0.85
      - NDCG@5 and Spearman reported
      - Adequate sample support (minimum 30 authorized pairs)
      - Manifest is frozen and valid

    Returns accurate evaluation status and complete audit trail.
    """
    report_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    policy = calibration_policy or BandCalibrationPolicy()

    # Fingerprint computation
    manifest_fingerprint = hashlib.sha256(
        f"{manifest.manifest_version}:{manifest.dataset_version}:{manifest.manifest_checksum}".encode("utf-8")
    ).hexdigest()

    exclusions_dict: Dict[str, int] = {}
    for exc in manifest.exclusions:
        exclusions_dict[exc.reason_code] = exclusions_dict.get(exc.reason_code, 0) + 1

    # Filter records for target split
    target_records = [r for r in manifest.records if r.split == target_split]

    # Handle insufficient data or empty manifest
    if not target_records or len(target_records) == 0:
        return EvaluationReport(
            report_id=report_id,
            evaluation_status=EvaluationAccuracyStatus.INSUFFICIENT_AUTHORIZED_DATA,
            target_met=False,
            dataset_version=manifest.dataset_version,
            provenance_reference=manifest.provenance_reference,
            authorization_reference=manifest.authorization_reference,
            manifest_fingerprint=manifest_fingerprint,
            match_policy_version=manifest.match_policy_version,
            calibration_policy_fingerprint=policy.policy_checksum,
            split_evaluated=target_split,
            total_manifest_pairs=len(manifest.records),
            evaluated_pairs_count=0,
            excluded_records_count=len(manifest.exclusions),
            exclusions_by_reason=exclusions_dict,
            status_reason=f"No authorized evaluation pairs found for split '{target_split.value}'.",
            limitations=["Evaluation dataset contains 0 records in target split."],
            sample_sizes={"target_records": 0, "evaluated_pairs": 0},
            acceptance_conclusion="INSUFFICIENT_AUTHORIZED_DATA: No authorized ground truth dataset populated.",
            evaluated_at=now,
        )

    # Pair evaluation mapping
    y_true: List[str] = []
    y_pred: List[str] = []
    true_scores: List[float] = []
    pred_scores: List[float] = []
    job_candidate_pairs: List[Dict[str, Any]] = []

    missing_preds_count = 0

    for r in target_records:
        true_band = r.adjudicated_band if r.adjudicated_band is not None else r.relevance_band
        if true_band is None and r.relevance_label is not None:
            true_band = 3 if r.relevance_label == RelevanceLabel.HIGHLY_RELEVANT else (2 if r.relevance_label == RelevanceLabel.RELEVANT else (1 if r.relevance_label == RelevanceLabel.PARTIALLY_RELEVANT else 0))
        if true_band is None:
            true_band = 0

        true_label = f"BAND_{true_band}"

        if r.pair_id in system_predictions:
            pred_data = system_predictions[r.pair_id]
            raw_score = float(pred_data.get("score", 0.0))
            pred_band = map_score_to_relevance_band(raw_score, policy)
            pred_label = f"BAND_{pred_band}"

            y_true.append(true_label)
            y_pred.append(pred_label)
            true_scores.append(float(true_band))
            pred_scores.append(raw_score)

            job_candidate_pairs.append({
                "job_identifier": r.job_identifier,
                "relevance": float(true_band),
                "score": raw_score,
            })
        else:
            missing_preds_count += 1

    if not y_true:
        return EvaluationReport(
            report_id=report_id,
            evaluation_status=EvaluationAccuracyStatus.INSUFFICIENT_AUTHORIZED_DATA,
            target_met=False,
            dataset_version=manifest.dataset_version,
            provenance_reference=manifest.provenance_reference,
            authorization_reference=manifest.authorization_reference,
            manifest_fingerprint=manifest_fingerprint,
            match_policy_version=manifest.match_policy_version,
            calibration_policy_fingerprint=policy.policy_checksum,
            split_evaluated=target_split,
            total_manifest_pairs=len(manifest.records),
            evaluated_pairs_count=0,
            excluded_records_count=len(manifest.exclusions),
            exclusions_by_reason=exclusions_dict,
            status_reason="Zero system predictions matched manifest records.",
            limitations=["System match results are missing for all manifest pairs."],
            sample_sizes={"target_records": len(target_records), "evaluated_pairs": 0},
            acceptance_conclusion="INSUFFICIENT_AUTHORIZED_DATA: Missing match run predictions.",
            evaluated_at=now,
        )

    all_classes = ["BAND_0", "BAND_1", "BAND_2", "BAND_3"]
    macro_report = calculate_macro_f1(y_true=y_true, y_pred=y_pred, labels=all_classes)
    ndcg_report = calculate_dataset_ndcg5(job_candidate_pairs)
    spearman_report = calculate_spearman_correlation(true_scores, pred_scores)
    agreement_report = calculate_reviewer_agreement(reviewer_labels or [], adjudicated_labels)

    limitations: List[str] = []
    if missing_preds_count > 0:
        limitations.append(f"{missing_preds_count} pairs could not be scored by the system.")
    if len(y_true) < 30:
        limitations.append(f"Sample size ({len(y_true)}) is smaller than required gate standard (min 30).")

    # Accuracy Gate Assessment
    if target_split == EvaluationSplit.DEVELOPMENT:
        status = EvaluationAccuracyStatus.DEVELOPMENT_ONLY
        target_met = False
        reason = "Evaluation was executed on DEVELOPMENT split only; held-out evaluation is required for target claim."
        conclusion = "DEVELOPMENT_ONLY: Accuracy claim cannot be made on development data."
    elif len(y_true) < 30:
        status = EvaluationAccuracyStatus.INSUFFICIENT_AUTHORIZED_DATA
        target_met = False
        reason = f"Held-out sample size ({len(y_true)} pairs) is insufficient for statistical confidence (minimum 30 required)."
        conclusion = "INSUFFICIENT_AUTHORIZED_DATA: Dataset size insufficient."
    elif macro_report.macro_f1 < 0.85:
        status = EvaluationAccuracyStatus.EVALUATED_BELOW_TARGET
        target_met = False
        reason = f"Held-out macro-F1 ({macro_report.macro_f1:.4f}) is below the required 0.85 threshold."
        conclusion = f"EVALUATED_BELOW_TARGET: Held-out macro-F1 {macro_report.macro_f1:.4f} < 0.85."
    else:
        status = EvaluationAccuracyStatus.EVALUATED_TARGET_MET
        target_met = True
        reason = f"Held-out evaluation target met: macro-F1 {macro_report.macro_f1:.4f} >= 0.85."
        conclusion = f"EVALUATED_TARGET_MET: Macro-F1 {macro_report.macro_f1:.4f} satisfies target >= 0.85."

    return EvaluationReport(
        report_id=report_id,
        evaluation_status=status,
        target_met=target_met,
        dataset_version=manifest.dataset_version,
        provenance_reference=manifest.provenance_reference,
        authorization_reference=manifest.authorization_reference,
        manifest_fingerprint=manifest_fingerprint,
        match_policy_version=manifest.match_policy_version,
        calibration_policy_fingerprint=policy.policy_checksum,
        split_evaluated=target_split,
        total_manifest_pairs=len(manifest.records),
        evaluated_pairs_count=len(y_true),
        excluded_records_count=len(manifest.exclusions),
        exclusions_by_reason=exclusions_dict,
        macro_f1_report=macro_report,
        ndcg_report=ndcg_report,
        spearman_report=spearman_report,
        inter_rater_agreement=agreement_report,
        status_reason=reason,
        limitations=limitations,
        sample_sizes={
            "total_records": len(manifest.records),
            "target_split_records": len(target_records),
            "evaluated_pairs": len(y_true),
            "missing_predictions": missing_preds_count,
        },
        acceptance_conclusion=conclusion,
        evaluated_at=now,
    )


# ---------------------------------------------------------------------------
# 9. Markdown & JSON Report Generators
# ---------------------------------------------------------------------------
def generate_evaluation_markdown_report(report: EvaluationReport) -> str:
    """Generates concise human-readable Markdown evaluation report without candidate identities."""
    lines: List[str] = [
        "# Milestone 2 Matching Evaluation & Accuracy Gate Report",
        "",
        f"**Evaluation Status:** `{report.evaluation_status.value}`",
        f"**Target Met:** `{'YES' if report.target_met else 'NO'}`",
        f"**Conclusion:** {report.acceptance_conclusion}",
        "",
        "## Evaluation Package Provenance",
        f"- **Dataset Version:** `{report.dataset_version}`",
        f"- **Split Evaluated:** `{report.split_evaluated.value}`",
        f"- **Manifest Fingerprint:** `{report.manifest_fingerprint}`",
        f"- **Match Policy Version:** `{report.match_policy_version}`",
        f"- **Calibration Policy Hash:** `{report.calibration_policy_fingerprint}`",
        f"- **Authorization Reference:** {report.authorization_reference}",
        f"- **Provenance Reference:** {report.provenance_reference}",
        f"- **Evaluated At:** `{report.evaluated_at.isoformat()}`",
        "",
        "## Dataset & Sample Statistics",
        f"- **Total Manifest Pairs:** {report.total_manifest_pairs}",
        f"- **Evaluated Pairs:** {report.evaluated_pairs_count}",
        f"- **Excluded Pairs:** {report.excluded_records_count}",
    ]

    if report.exclusions_by_reason:
        lines.append("### Exclusions by Reason")
        for reason, count in report.exclusions_by_reason.items():
            lines.append(f"- `{reason}`: {count}")

    if report.macro_f1_report:
        lines.extend([
            "",
            "## Accuracy & Classification Metrics",
            f"- **Macro-F1:** `{report.macro_f1_report.macro_f1:.4f}` (Gate Target >= 0.85)",
            "",
            "| Relevance Band | Class Precision | Class Recall | Class F1 | Support |",
            "|---|---|---|---|---|",
        ])
        for cls, m in report.macro_f1_report.per_class_metrics.items():
            prec = f"{m.precision:.4f}" if m.precision is not None else "N/A"
            rec = f"{m.recall:.4f}" if m.recall is not None else "N/A"
            f1 = f"{m.f1:.4f}" if m.f1 is not None else "N/A"
            lines.append(f"| `{cls}` | {prec} | {rec} | {f1} | {m.support} |")

        lines.extend([
            "",
            "### Confusion Matrix",
            "| Actual \\ Pred | BAND_0 | BAND_1 | BAND_2 | BAND_3 |",
            "|---|---|---|---|---|",
        ])
        for actual in ["BAND_0", "BAND_1", "BAND_2", "BAND_3"]:
            row = report.macro_f1_report.confusion_matrix.get(actual, {})
            b0 = row.get("BAND_0", 0)
            b1 = row.get("BAND_1", 0)
            b2 = row.get("BAND_2", 0)
            b3 = row.get("BAND_3", 0)
            lines.append(f"| `{actual}` | {b0} | {b1} | {b2} | {b3} |")

    if report.ndcg_report:
        lines.extend([
            "",
            "## Ranking Metrics (NDCG@5)",
            f"- **Mean NDCG@5:** `{report.ndcg_report.mean_ndcg_at_5 if report.ndcg_report.mean_ndcg_at_5 is not None else 'Not Computable'}`",
            f"- **Eligible Jobs:** {report.ndcg_report.eligible_jobs_count}",
            f"- **Excluded Jobs (<5 candidates):** {report.ndcg_report.excluded_jobs_count}",
        ])
        if report.ndcg_report.uncomputable_reason:
            lines.append(f"- **Note:** {report.ndcg_report.uncomputable_reason}")

    if report.spearman_report:
        lines.extend([
            "",
            "## Rank Correlation (Spearman rho)",
            f"- **Spearman Correlation:** `{report.spearman_report.correlation if report.spearman_report.correlation is not None else 'Undefined'}`",
            f"- **Evaluated Pairs:** {report.spearman_report.evaluated_pairs_count}",
        ])
        if report.spearman_report.undefined_reason:
            lines.append(f"- **Undefined Reason:** {report.spearman_report.undefined_reason}")

    if report.inter_rater_agreement:
        lines.extend([
            "",
            "## Reviewer Agreement & Adjudication",
            f"- **Percent Agreement:** `{report.inter_rater_agreement.percent_agreement:.4f}`",
            f"- **Cohen's Kappa:** `{report.inter_rater_agreement.cohen_kappa if report.inter_rater_agreement.cohen_kappa is not None else 'N/A'}`",
            f"- **Overlapping Pairs:** {report.inter_rater_agreement.total_overlapping_pairs}",
            f"- **Disagreements:** {report.inter_rater_agreement.reviewer_disagreement_count}",
            f"- **Adjudicated Pairs:** {report.inter_rater_agreement.adjudicated_count}",
        ])

    if report.limitations:
        lines.extend([
            "",
            "## Limitations & Error Analysis",
        ])
        for lim in report.limitations:
            lines.append(f"- {lim}")

    return "\n".join(lines)
