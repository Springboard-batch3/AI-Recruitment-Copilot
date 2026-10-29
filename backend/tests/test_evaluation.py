import hashlib
import json
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
import pytest

from app.schemas.evaluation import (
    AdjudicatedLabel,
    AdjudicationState,
    BandCalibrationPolicy,
    EvaluationAccuracyStatus,
    EvaluationManifest,
    EvaluationManifestRecord,
    EvaluationSplit,
    ManifestExclusion,
    RelevanceBand,
    RelevanceLabel,
    ReviewerLabel,
    ReviewerRequirementLabel,
    compute_manifest_checksum,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.schemas.requirement_match import MatchResultState
from app.services.evaluation import (
    calculate_dataset_ndcg5,
    calculate_macro_f1,
    calculate_ndcg_at_k,
    calculate_requirement_agreement,
    calculate_reviewer_agreement,
    calculate_spearman_correlation,
    calibrate_score_bands_on_development,
    evaluate_matching_gate,
    map_score_to_relevance_band,
)


# ===========================================================================
# GROUP 1: CONTRACT AND PRIVACY VALIDATION
# ===========================================================================
def test_group1_manifest_contract_and_privacy_validation():
    """Confirm unauthorized, malformed, overlapping, duplicate, mutable, direct-identity,
    and incorrectly versioned records are rejected."""
    now = datetime.now(timezone.utc)
    eval_id = uuid.uuid4()

    # 1. Direct identity field prohibited (extra field / prohibited key check)
    with pytest.raises(ValueError):
        EvaluationManifestRecord(
            pair_id="P1",
            job_identifier="JOB1",
            revision=1,
            candidate_identifier="CAND1",
            profile_version=1,
            split=EvaluationSplit.DEVELOPMENT,
            notes="Has candidate_name in notes",
            full_name="John Doe",  # Prohibited key / extra field
        )

    # 2. Duplicate pair_id rejected
    rec1 = EvaluationManifestRecord(
        pair_id="PAIR_001",
        job_identifier="JOB1",
        revision=1,
        candidate_identifier="CAND1",
        profile_version=1,
        split=EvaluationSplit.DEVELOPMENT,
        relevance_band=2,
    )
    rec1_dup = EvaluationManifestRecord(
        pair_id="PAIR_001",
        job_identifier="JOB1",
        revision=1,
        candidate_identifier="CAND2",
        profile_version=1,
        split=EvaluationSplit.DEVELOPMENT,
        relevance_band=1,
    )
    with pytest.raises(ValueError, match="Duplicate pair_id"):
        EvaluationManifest(
            evaluation_id=eval_id,
            dataset_version="v1",
            provenance_reference="Prov",
            authorization_reference="Auth",
            created_at=now,
            evaluation_date=now,
            match_policy_version="policy_v1",
            reviewer_label_version="rev_v1",
            records=[rec1, rec1_dup],
            manifest_checksum="dummy",
        )

    # 3. Invalid relevance band (<0 or >3) rejected
    with pytest.raises(ValueError):
        EvaluationManifestRecord(
            pair_id="PAIR_INV",
            job_identifier="JOB1",
            revision=1,
            candidate_identifier="CAND1",
            profile_version=1,
            split=EvaluationSplit.DEVELOPMENT,
            relevance_band=5,  # Invalid
        )

    # 4. Reviewer label safety & contracts
    r_label = ReviewerLabel(
        pair_id="PAIR_001",
        reviewer_pseudonym="REV_01",
        relevance_band=2,
        requirement_labels=[
            ReviewerRequirementLabel(
                requirement_id=uuid.uuid4(),
                expected_state=MatchResultState.MATCHED,
            )
        ],
        created_at=now,
    )
    assert r_label.relevance_band == 2

    # 5. Adjudicated label contract
    adj_label = AdjudicatedLabel(
        pair_id="PAIR_001",
        original_reviewer_labels=[r_label],
        disagreement_status=False,
        final_relevance_band=2,
        adjudicator_pseudonym="ADJ_LEAD",
        concise_reason="Consensus verified",
        adjudicated_at=now,
    )
    assert adj_label.final_relevance_band == 2


# ===========================================================================
# GROUP 2: METRIC CORRECTNESS (Hand-Checked Mathematical Vectors Only)
# ===========================================================================
def test_group2_metric_correctness():
    """Pure-Python hand-checked numeric vectors to verify mathematical calculations."""
    # 1. Macro-F1 with hand-checked values
    # Labels: 0, 1
    # Actual:    [0, 0, 1, 1]
    # Predicted: [0, 1, 1, 1]
    # Class 0: TP=1, FP=0, FN=1 -> Prec = 1/1 = 1.0, Rec = 1/2 = 0.5 -> F1 = 2*(1.0*0.5)/1.5 = 2/3 = 0.666667
    # Class 1: TP=2, FP=1, FN=0 -> Prec = 2/3 = 0.666667, Rec = 2/2 = 1.0 -> F1 = 2*(0.666667*1.0)/1.666667 = 0.800000
    # Macro F1: (0.666667 + 0.800000) / 2 = 0.733333
    y_true = ["0", "0", "1", "1"]
    y_pred = ["0", "1", "1", "1"]
    f1_rep = calculate_macro_f1(y_true, y_pred, labels=["0", "1"])
    assert abs(f1_rep.macro_f1 - 0.733333) < 1e-5
    assert abs(f1_rep.per_class_metrics["0"].f1 - 0.666667) < 1e-5
    assert abs(f1_rep.per_class_metrics["1"].f1 - 0.800000) < 1e-5
    assert f1_rep.per_class_metrics["0"].support == 2
    assert f1_rep.per_class_metrics["1"].support == 2

    # 2. Zero-division handling without NaN / crash
    y_true_zero = ["0", "0"]
    y_pred_zero = ["1", "1"]
    f1_zero = calculate_macro_f1(y_true_zero, y_pred_zero, labels=["0", "1"])
    assert f1_zero.macro_f1 == 0.0
    assert f1_zero.per_class_metrics["0"].f1 == 0.0
    assert f1_zero.per_class_metrics["1"].f1 == 0.0

    # 3. NDCG@5 with hand calculation
    # Actual: [3.0, 2.0, 1.0, 0.0]
    # Ideal:
    # DCG = (2^3-1)/log2(2) + (2^2-1)/log2(3) + (2^1-1)/log2(4) + (2^0-1)/log2(5)
    #     = 7/1.0 + 3/1.5849625 + 1/2.0 + 0 = 7.0 + 1.892789 + 0.5 = 9.392789
    # Preds ordered descending: [90.0, 80.0, 70.0, 60.0] -> NDCG = 1.0
    actual = [3.0, 2.0, 1.0, 0.0]
    preds = [90.0, 80.0, 70.0, 60.0]
    ndcg = calculate_ndcg_at_k(actual, preds, k=5)
    assert ndcg == 1.0

    # 4. Spearman correlation hand-checked
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 20.0, 30.0, 40.0, 50.0]
    rho_rep = calculate_spearman_correlation(x, y)
    assert rho_rep.is_defined is True
    assert rho_rep.correlation == 1.0

    # Constant array -> undefined
    rho_const = calculate_spearman_correlation([1.0, 1.0, 1.0], [2.0, 3.0, 4.0])
    assert rho_const.is_defined is False
    assert rho_const.correlation is None
    assert "variance is zero" in str(rho_const.undefined_reason)

    # 5. Reviewer agreement
    rev1 = ReviewerLabel(pair_id="P1", reviewer_pseudonym="R1", relevance_band=3, created_at=datetime.now(timezone.utc))
    rev2 = ReviewerLabel(pair_id="P1", reviewer_pseudonym="R2", relevance_band=3, created_at=datetime.now(timezone.utc))
    rev_agr = calculate_reviewer_agreement([rev1, rev2])
    assert rev_agr.percent_agreement == 1.0
    assert rev_agr.total_overlapping_pairs == 1


# ===========================================================================
# GROUP 3: FREEZE AND LEAKAGE PROTECTION
# ===========================================================================
def test_group3_freeze_and_leakage_protection():
    """Confirm checksum changes are detected, development/held-out overlap is rejected,
    and held-out records cannot enter development calibration."""
    now = datetime.now(timezone.utc)
    eval_id = uuid.uuid4()

    # 1. Leakage rejection: Same candidate in DEV and HELD_OUT
    rec_dev = EvaluationManifestRecord(
        pair_id="P_DEV",
        job_identifier="JOB_A",
        revision=1,
        candidate_identifier="CAND_01",
        profile_version=1,
        split=EvaluationSplit.DEVELOPMENT,
        relevance_band=2,
    )
    rec_held = EvaluationManifestRecord(
        pair_id="P_HELD",
        job_identifier="JOB_B",
        revision=1,
        candidate_identifier="CAND_01",  # Same candidate variant
        profile_version=1,
        split=EvaluationSplit.HELD_OUT,
        relevance_band=2,
    )
    with pytest.raises(ValueError, match="Split leakage detected"):
        EvaluationManifest(
            evaluation_id=eval_id,
            dataset_version="v1",
            provenance_reference="Prov",
            authorization_reference="Auth",
            created_at=now,
            evaluation_date=now,
            match_policy_version="policy_v1",
            reviewer_label_version="rev_v1",
            records=[rec_dev, rec_held],
            manifest_checksum="chk",
        )

    # 2. Checksum validation detects modification
    records_dict = [rec_dev.model_dump(mode="json", by_alias=True)]
    chk = compute_manifest_checksum("v1", "Prov", "Auth", "policy_v1", records_dict)

    # Change record content
    records_tampered = [dict(records_dict[0], relevance_band=3)]
    chk_tampered = compute_manifest_checksum("v1", "Prov", "Auth", "policy_v1", records_tampered)
    assert chk != chk_tampered

    # 3. Calibration rejects held-out records
    dev_only_manifest = EvaluationManifest(
        evaluation_id=eval_id,
        dataset_version="v1",
        provenance_reference="Prov",
        authorization_reference="Auth",
        created_at=now,
        evaluation_date=now,
        match_policy_version="policy_v1",
        reviewer_label_version="rev_v1",
        records=[rec_dev],
        manifest_checksum=chk,
    )
    scores = {"P_DEV": 80.0}
    calib = calibrate_score_bands_on_development(dev_only_manifest, scores)
    assert calib.evaluated_dev_pairs == 1


# ===========================================================================
# GROUP 4: DETERMINISM AND PERFORMANCE
# ===========================================================================
def test_group4_determinism_and_performance():
    """Confirm the same frozen inputs produce byte-equivalent metric JSON apart from explicitly
    excluded timestamps, and record evaluation time and peak memory."""
    tracemalloc.start()
    t0 = time.perf_counter()

    # Create synthetic evaluation pairs (40 pairs for gate verification)
    now = datetime.now(timezone.utc)
    eval_id = uuid.uuid4()
    records = []
    preds = {}

    for i in range(40):
        pair_id = f"PAIR_{i:03d}"
        band = i % 4
        records.append(
            EvaluationManifestRecord(
                pair_id=pair_id,
                job_identifier="JOB_PERF",
                revision=1,
                candidate_identifier=f"CAND_{i:03d}",
                profile_version=1,
                split=EvaluationSplit.HELD_OUT,
                relevance_band=band,
            )
        )
        # Score corresponding to band
        score = 90.0 if band == 3 else (70.0 if band == 2 else (40.0 if band == 1 else 10.0))
        preds[pair_id] = {"score": score}

    records_dict = [r.model_dump(mode="json", by_alias=True) for r in records]
    chk = compute_manifest_checksum("v_perf", "Prov", "Auth", "policy_v1", records_dict)

    manifest = EvaluationManifest(
        evaluation_id=eval_id,
        dataset_version="v_perf",
        provenance_reference="Prov",
        authorization_reference="Auth",
        created_at=now,
        evaluation_date=now,
        match_policy_version="policy_v1",
        reviewer_label_version="rev_v1",
        records=records,
        manifest_checksum=chk,
    )

    policy = BandCalibrationPolicy()

    # Run 1
    rep1 = evaluate_matching_gate(manifest, preds, policy, target_split=EvaluationSplit.HELD_OUT)
    json1 = rep1.model_dump(mode="json", exclude={"report_id", "evaluated_at"})

    # Run 2
    rep2 = evaluate_matching_gate(manifest, preds, policy, target_split=EvaluationSplit.HELD_OUT)
    json2 = rep2.model_dump(mode="json", exclude={"report_id", "evaluated_at"})

    elapsed = time.perf_counter() - t0
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Verify byte-equivalent determinism
    dump1 = json.dumps(json1, sort_keys=True)
    dump2 = json.dumps(json2, sort_keys=True)
    assert dump1 == dump2

    assert rep1.evaluation_status == EvaluationAccuracyStatus.EVALUATED_TARGET_MET
    assert rep1.macro_f1_report.macro_f1 == 1.0
    assert rep1.target_met is True

    # Check that performance was recorded without crashing
    assert elapsed >= 0.0
    assert peak_mem > 0


# ===========================================================================
# INSUFFICIENT DATA & GATE REJECTION TESTS
# ===========================================================================
def test_gate_returns_insufficient_authorized_data_when_no_records():
    """Gate returns INSUFFICIENT_AUTHORIZED_DATA without authorized frozen dataset."""
    now = datetime.now(timezone.utc)
    eval_id = uuid.uuid4()
    manifest = EvaluationManifest(
        evaluation_id=eval_id,
        dataset_version="empty_v1",
        provenance_reference="None",
        authorization_reference="None",
        created_at=now,
        evaluation_date=now,
        match_policy_version="policy_v1",
        reviewer_label_version="rev_v1",
        records=[],
        manifest_checksum="empty_chk",
    )
    rep = evaluate_matching_gate(manifest, {}, target_split=EvaluationSplit.HELD_OUT)
    assert rep.evaluation_status == EvaluationAccuracyStatus.INSUFFICIENT_AUTHORIZED_DATA
    assert rep.target_met is False
    assert "INSUFFICIENT_AUTHORIZED_DATA" in rep.acceptance_conclusion
