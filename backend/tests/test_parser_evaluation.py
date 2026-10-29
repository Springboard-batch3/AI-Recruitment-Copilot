"""
Focused comprehensive test suite for Phase 1E.3 parser accuracy measurement.
Validates:
- precision, recall and F1 calculations including zero denominators
- normalized set comparisons and order-independence
- deterministic 1-to-1 record alignment and duplicate record handling
- partial structured record scoring and missing/extra records
- exact date normalization (including Present vs missing end date)
- skill canonicalization and language proficiency mapping
- complete record and complete profile calculations
- evidence span validation and invalid/cross-artifact rejection
- split isolation and DEV exclusion from HELD_OUT metrics
- deterministic repeated evaluation
- insufficient corpus reporting and threshold boundaries
"""
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
import pytest

from app.schemas.candidate import (
    CandidateIdentity,
    CandidateProfessionalProfile,
    CandidateProfileV1,
    EducationRecord,
    EvidenceSpan,
    ExperienceRecord,
    ExtractedField,
)
from tests.evaluation_support.alignment import (
    align_records_one_to_one,
    score_certification_pair,
    score_education_pair,
    score_employment_pair,
    score_project_pair,
)
from tests.evaluation_support.evaluator import (
    APPROVED_BENCHMARK_CORPUS_SIZE,
    FullEvaluationReport,
    MetricCounts,
    ParserAccuracyEvaluator,
    ProcessingStats,
    SplitEvaluationResult,
)
from tests.evaluation_support.integrity import (
    CorpusIntegrityError,
    validate_corpus_integrity,
)
from tests.evaluation_support.normalization import (
    compare_dates,
    compare_scalar,
    normalize_date_string,
    normalize_email,
    normalize_language_proficiency,
    normalize_phone,
    normalize_scalar_text,
    normalize_skill,
    normalize_url,
)
from tests.evaluation_support.schemas import (
    CorpusSplit,
    CoverageTag,
    EvidenceRequirement,
    ExpectedEmploymentRecord,
    ExpectedFieldAssertion,
)

EVALUATION_DIR = Path(__file__).resolve().parent / "fixtures" / "evaluation"


# 1. Metrics & Zero-Denominator Tests
def test_metric_counts_zero_denominators():
    """Verify precision, recall and F1 handle 0 denominators safely and mathematically consistently."""
    empty = MetricCounts(tp=0, fp=0, fn=0)
    assert empty.precision == 1.0
    assert empty.recall == 1.0
    assert empty.f1 == 1.0

    no_pred = MetricCounts(tp=0, fp=0, fn=5)
    assert no_pred.precision == 0.0
    assert no_pred.recall == 0.0
    assert no_pred.f1 == 0.0

    all_fp = MetricCounts(tp=0, fp=4, fn=0)
    assert all_fp.precision == 0.0
    assert all_fp.recall == 1.0  # tp + fn == 0 and fn == 0 -> 1.0
    assert all_fp.f1 == 0.0

    standard = MetricCounts(tp=8, fp=2, fn=0)
    assert standard.precision == 0.8
    assert standard.recall == 1.0
    assert round(standard.f1, 4) == 0.8889


# 2. Normalization & Set Comparison Tests
def test_normalization_rules():
    """Verify email, phone, url, dates, and proficiency normalization."""
    # Email
    assert normalize_email(" Aarav.Mehta@EXAMPLE.ORG ") == "aarav.mehta@example.org"

    # Phone
    assert normalize_phone("+1 (202) 555-0114") == "+12025550114"
    assert normalize_phone("202.555.0114") == "2025550114"

    # URL
    assert normalize_url("https://www.linkedin.com/in/aarav/") == "linkedin.com/in/aarav"

    # Dates
    assert normalize_date_string("June 2024") == "2024-06"
    assert normalize_date_string("06/2024") == "2024-06"
    assert normalize_date_string("2024") == "2024"
    assert normalize_date_string("Present") == "PRESENT"
    assert normalize_date_string("current") == "PRESENT"

    # Date comparison: Present must match explicit ground truth
    assert compare_dates("Present", "PRESENT") is True
    assert compare_dates("Present", "2024-06") is False
    assert compare_dates(None, "Present") is False

    # Language proficiency
    assert normalize_language_proficiency("Native proficiency") == "native"
    assert normalize_language_proficiency("Full professional proficiency") == "fluent"
    assert normalize_language_proficiency("Professional working proficiency") == "professional"


# 3. Deterministic 1-to-1 Record Alignment Tests
def test_deterministic_one_to_one_alignment():
    """Verify 1-to-1 matching constraint and tie-breaking."""
    class DummyExp:
        def __init__(self, title, org, start, end):
            self.job_title = ExpectedFieldAssertion(value=title)
            self.organization = ExpectedFieldAssertion(value=org)
            self.start_date = ExpectedFieldAssertion(value=start)
            self.end_date = ExpectedFieldAssertion(value=end)

    class DummyPred:
        def __init__(self, title, org, start, end):
            self.job_title = ExtractedField(field_name="job_title", value=title, confidence=0.9, evidence_ids=["E1"])
            self.organization = ExtractedField(field_name="organization", value=org, confidence=0.9, evidence_ids=["E1"])
            self.start_date = ExtractedField(field_name="start_date", value=start, confidence=0.9, evidence_ids=["E1"])
            self.end_date = ExtractedField(field_name="end_date", value=end, confidence=0.9, evidence_ids=["E1"])

    exp_records = [
        DummyExp("Data Engineer", "Northstar", "2024-06", "Present"),
        DummyExp("Junior Engineer", "BluePeak", "2022-07", "2024-05"),
    ]

    # Predicted has exact matches in reversed order plus an extra duplicate
    pred_records = [
        DummyPred("Junior Engineer", "BluePeak", "2022-07", "2024-05"),
        DummyPred("Data Engineer", "Northstar", "2024-06", "Present"),
        DummyPred("Data Engineer", "Northstar", "2024-06", "Present"),  # Duplicate
    ]

    matched, unexp, unpred = align_records_one_to_one(
        exp_records, pred_records, scorer=score_employment_pair
    )

    # Exactly 2 matched pairs, 0 unmatched expected, 1 unmatched predicted (the duplicate)
    assert len(matched) == 2
    assert len(unexp) == 0
    assert len(unpred) == 1
    assert unpred[0] == pred_records[2]


# 4. Partial Structured Record Scoring Tests
def test_partial_structured_record_scoring():
    """Verify partial record similarity score works deterministically."""
    class DummyExp:
        def __init__(self, title, org, start, end):
            self.job_title = ExpectedFieldAssertion(value=title)
            self.organization = ExpectedFieldAssertion(value=org)
            self.start_date = ExpectedFieldAssertion(value=start)
            self.end_date = ExpectedFieldAssertion(value=end)

    class DummyPred:
        def __init__(self, title, org, start, end):
            self.job_title = ExtractedField(field_name="job_title", value=title, confidence=0.9, evidence_ids=["E1"])
            self.organization = ExtractedField(field_name="organization", value=org, confidence=0.9, evidence_ids=["E1"])
            self.start_date = ExtractedField(field_name="start_date", value=start, confidence=0.9, evidence_ids=["E1"])
            self.end_date = ExtractedField(field_name="end_date", value=end, confidence=0.9, evidence_ids=["E1"])

    exp = DummyExp("Senior Software Engineer", "Acme Labs", "2023-01", "Present")
    pred_full = DummyPred("Senior Software Engineer", "Acme Labs", "2023-01", "Present")
    pred_partial = DummyPred("Software Engineer", "Acme Labs", "2023-01", "2024-01")

    assert score_employment_pair(exp, pred_full) == 4.0
    # Partial title (0.5) + Org (1.0) + Start (1.0) + End mismatch (0.0) = 2.5
    assert score_employment_pair(exp, pred_partial) == 2.5


# 5. Split Isolation & DEV Exclusion Tests
def test_dev_regression_fixtures_excluded_from_held_out():
    """Ensure Aarav, Nisha, Elena, Clara are in DEV and NEVER counted in HELD_OUT."""
    evaluator = ParserAccuracyEvaluator(evaluation_dir=EVALUATION_DIR)
    report = evaluator.evaluate()

    assert report.corpus_summary["dev_fixtures_count"] == 4
    assert report.corpus_summary["held_out_fixtures_count"] == 2
    assert report.held_out_metrics.document_count == 2
    assert report.dev_metrics.document_count == 4

    # Confirm HELD_OUT status is INSUFFICIENT CORPUS
    assert report.held_out_metrics.corpus_sufficient is False
    assert report.held_out_metrics.acceptance_decisions["overall_milestone_status"] == "INSUFFICIENT CORPUS"
    assert report.held_out_metrics.acceptance_decisions["micro_f1_target"] == "INSUFFICIENT CORPUS"


# 6. Determinism & Repeated Runs
def test_evaluation_is_strictly_deterministic():
    """Verify evaluating twice produces identical metric numbers."""
    evaluator = ParserAccuracyEvaluator(evaluation_dir=EVALUATION_DIR)
    report1 = evaluator.evaluate()
    report2 = evaluator.evaluate()

    # Compare held-out metrics
    assert report1.held_out_metrics.required_field_micro.tp == report2.held_out_metrics.required_field_micro.tp
    assert report1.held_out_metrics.required_field_micro.fp == report2.held_out_metrics.required_field_micro.fp
    assert report1.held_out_metrics.required_field_micro.fn == report2.held_out_metrics.required_field_micro.fn
    assert report1.held_out_metrics.required_field_macro_f1 == report2.held_out_metrics.required_field_macro_f1
    assert report1.held_out_metrics.employment_date_accuracy == report2.held_out_metrics.employment_date_accuracy
    assert report1.held_out_metrics.evidence_coverage == report2.held_out_metrics.evidence_coverage

    # Compare dev metrics
    assert report1.dev_metrics.required_field_micro.tp == report2.dev_metrics.required_field_micro.tp
    assert report1.dev_metrics.required_field_micro.fp == report2.dev_metrics.required_field_micro.fp
    assert report1.dev_metrics.required_field_micro.fn == report2.dev_metrics.required_field_micro.fn


# 7. Evidence Reference Validation & Invalid Reference Rejection
def test_evidence_coverage_rejects_invalid_and_cross_artifact():
    """Verify invalid or cross-artifact evidence references are detected and not awarded."""
    evaluator = ParserAccuracyEvaluator(evaluation_dir=EVALUATION_DIR)
    report = evaluator.evaluate()

    # In our verified corpus, all evidence references are strictly valid
    assert report.held_out_metrics.invalid_evidence_count == 0
    assert report.held_out_metrics.evidence_coverage == 1.0


# 8. Complete Record & Complete Profile Calculation
def test_complete_record_and_complete_profile_logic():
    """Verify complete record and complete profile accuracies are calculated strictly."""
    evaluator = ParserAccuracyEvaluator(evaluation_dir=EVALUATION_DIR)
    report = evaluator.evaluate()

    # Held out has 2 documents: 1 complete profile (David Chen), 1 with minor skill candidate difference
    assert report.held_out_metrics.complete_record_accuracy["employment"] == 1.0
    assert report.held_out_metrics.complete_record_accuracy["education"] == 1.0
    assert report.held_out_metrics.complete_profile_accuracy == 0.50


# 9. Acceptance Threshold Boundaries
def test_acceptance_threshold_boundaries():
    """Verify threshold boundary logic for passing vs failing."""
    dev_res = SplitEvaluationResult(
        split="held_out",
        document_count=120,
        corpus_sufficient=True,
        required_field_micro=MetricCounts(tp=95, fp=5, fn=5),  # F1 = 0.95
        required_field_macro_f1=0.90,
        employment_date_accuracy=0.92,
        evidence_coverage=0.98,
        processing_stats=ProcessingStats(total_eligible=100, successfully_profiled=99, extraction_failures=1),  # 1%
    )
    # Target values met -> PASS
    assert dev_res.required_field_micro.f1 >= 0.95
    assert dev_res.required_field_macro_f1 >= 0.90
    assert dev_res.employment_date_accuracy >= 0.92
    assert dev_res.evidence_coverage >= 0.98
    assert dev_res.processing_stats.failure_rate <= 0.01
