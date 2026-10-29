"""
Deterministic formal accuracy evaluation engine for the direct-text parser.
Computes field-aware metrics, complete-record accuracy, evidence coverage,
processing failure rates, and acceptance threshold compliance.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from app.config import Settings
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.candidate import CandidateProfileV1, EvidenceSpan, ExtractedField
from app.schemas.extraction import ExtractionResult
from app.services.date_extraction import DATE_RULES_VERSION
from app.services.docx_text_extraction import DOCXExtractionOutcome, extract_docx_text
from app.services.education_project_certification_extraction import (
    EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION,
)
from app.services.education_project_certification_rules import (
    EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION,
)
from app.services.employment_extraction import EMPLOYMENT_PARSER_VERSION
from app.services.identity_extraction import IDENTITY_PARSER_VERSION
from app.services.pdf_text_extraction import PDFDirectExtractionResult, extract_pdf_direct_text
from app.services.profile_assembly import PROFILE_PIPELINE_VERSION, assemble_candidate_profile
from app.services.section_detection import SECTION_PARSER_VERSION
from app.services.skill_catalog import SKILL_CATALOG_VERSION
from app.services.skill_summary_extraction import SKILL_SUMMARY_PARSER_VERSION
from app.services.text_normalization import normalize_extraction_result

from .alignment import (
    align_records_one_to_one,
    score_certification_pair,
    score_education_pair,
    score_employment_pair,
    score_project_pair,
)
from .integrity import (
    CorpusIntegrityError,
    compute_sha256_hash,
    validate_corpus_integrity,
)
from .normalization import (
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
from .schemas import (
    CorpusManifest,
    CorpusSplit,
    DocumentType,
    EvidenceRequirement,
    ExpectedFieldAssertion,
    GroundTruthDocument,
)

EVALUATOR_VERSION = "evaluator-v1.0"
EVALUATION_SCHEMA_VERSION = "1.0"
APPROVED_BENCHMARK_CORPUS_SIZE = 120

# Acceptance Thresholds
TARGET_MICRO_F1 = 0.95
TARGET_MACRO_F1 = 0.90
TARGET_EMPLOYMENT_DATE_ACCURACY = 0.92
TARGET_EVIDENCE_COVERAGE = 0.98
TARGET_PROCESSING_FAILURE_RATE = 0.01


@dataclass
class MetricCounts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        if self.tp + self.fp == 0:
            return 1.0 if self.fn == 0 else 0.0
        return self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float:
        if self.tp + self.fn == 0:
            return 1.0 if self.fn == 0 else 0.0
        return self.tp / (self.tp + self.fn)

    @property
    def f1(self) -> float:
        if self.tp == 0 and self.fp == 0 and self.fn == 0:
            return 1.0
        p = self.precision
        r = self.recall
        if p + r == 0.0:
            return 0.0
        return 2.0 * (p * r) / (p + r)


@dataclass
class FailedFieldCase:
    fixture_id: str
    field_category: str
    error_type: str  # "false_positive", "false_negative", "mismatch"
    expected_value_repr: Optional[str] = None
    predicted_value_repr: Optional[str] = None


@dataclass
class CompleteRecordStats:
    total_expected: int = 0
    total_correct: int = 0

    @property
    def accuracy(self) -> float:
        if self.total_expected == 0:
            return 1.0
        return self.total_correct / self.total_expected


@dataclass
class ProcessingStats:
    total_eligible: int = 0
    successfully_profiled: int = 0
    extraction_failures: int = 0
    parsing_failures: int = 0
    pdf_total: int = 0
    pdf_failures: int = 0
    docx_total: int = 0
    docx_failures: int = 0

    @property
    def failure_rate(self) -> float:
        if self.total_eligible == 0:
            return 0.0
        return (self.extraction_failures + self.parsing_failures) / self.total_eligible

    @property
    def pdf_failure_rate(self) -> float:
        if self.pdf_total == 0:
            return 0.0
        return self.pdf_failures / self.pdf_total

    @property
    def docx_failure_rate(self) -> float:
        if self.docx_total == 0:
            return 0.0
        return self.docx_failures / self.docx_total


@dataclass
class SplitEvaluationResult:
    split: str
    document_count: int
    corpus_sufficient: bool
    required_field_micro: MetricCounts = field(default_factory=MetricCounts)
    required_field_macro_f1: float = 0.0
    per_field_metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    complete_record_accuracy: Dict[str, float] = field(default_factory=dict)
    complete_profile_accuracy: float = 0.0
    evidence_coverage: float = 0.0
    invalid_evidence_count: int = 0
    employment_date_accuracy: float = 0.0
    processing_stats: ProcessingStats = field(default_factory=ProcessingStats)
    breakdown_metrics: Dict[str, Any] = field(default_factory=dict)
    failed_cases: List[FailedFieldCase] = field(default_factory=list)
    acceptance_decisions: Dict[str, str] = field(default_factory=dict)


@dataclass
class FullEvaluationReport:
    evaluator_version: str
    evaluation_schema_version: str
    parser_pipeline_version: str
    date_rules_version: str
    education_project_certification_rules_version: str
    education_project_certification_parser_version: str
    employment_parser_version: str
    identity_parser_version: str
    section_parser_version: str
    skill_catalog_version: str
    skill_summary_parser_version: str
    manifest_hash: str
    evaluated_fixture_ids: List[str]
    execution_timestamp: str
    integrity_status: str
    corpus_summary: Dict[str, Any]
    dev_metrics: SplitEvaluationResult
    held_out_metrics: SplitEvaluationResult


def _build_candidate_profile_from_file(
    file_path: Path, doc_type: DocumentType, settings: Settings
) -> Tuple[Optional[CandidateProfileV1], Optional[str], Optional[str]]:
    """
    Directly extracts, normalizes and parses a CandidateProfileV1 from a file.
    Returns (profile, error_stage, error_message).
    """
    content = file_path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    art_id = uuid4()
    ext_dir = "pdf" if doc_type == DocumentType.PDF else "docx"
    target = settings.get_quarantine_path() / ext_dir / f"{art_id}.{doc_type.value}"
    target.write_bytes(content)

    mime = (
        "application/pdf"
        if doc_type == DocumentType.PDF
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename=file_path.name,
        size_bytes=file_path.stat().st_size,
        sha256_hash=sha,
        declared_mime_type=mime,
        quarantine_key=f"{ext_dir}/{art_id}.{doc_type.value}",
        detected_format=ArtifactFormat.pdf if doc_type == DocumentType.PDF else ArtifactFormat.docx,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )

    # 1. Extraction Stage
    try:
        if doc_type == DocumentType.PDF:
            ext_res = extract_pdf_direct_text(art, settings)
        else:
            ext_res = extract_docx_text(art, settings)
    except Exception as e:
        return None, "extraction", str(e)

    # 2. Normalization Stage
    try:
        norm_input = ExtractionResult(
            extractor_version=ext_res.extractor_version,
            blocks=ext_res.blocks,
        )
        norm_res = normalize_extraction_result(norm_input)
    except Exception as e:
        return None, "normalization", str(e)

    # 3. Profile Assembly Stage
    try:
        profile = assemble_candidate_profile(norm_res.extraction_result.blocks, str(art_id))
        return profile, None, None
    except Exception as e:
        return None, "parsing", str(e)


class ParserAccuracyEvaluator:
    def __init__(self, evaluation_dir: Path, settings: Optional[Settings] = None):
        self.evaluation_dir = evaluation_dir
        self.settings = settings or Settings()

    def evaluate(self) -> FullEvaluationReport:
        # 1. Validate manifest & integrity
        manifest, ground_truth_map = validate_corpus_integrity(self.evaluation_dir)
        manifest_hash = compute_sha256_hash(self.evaluation_dir / "manifest.json")

        # 2. Split fixtures into DEV and HELD_OUT
        dev_fixtures = [f for f in manifest.fixtures if f.split == CorpusSplit.DEVELOPMENT]
        held_fixtures = [f for f in manifest.fixtures if f.split == CorpusSplit.HELD_OUT]

        # 3. Evaluate DEV fixtures
        dev_result = self._evaluate_split_fixtures(
            split_name="development",
            fixtures=dev_fixtures,
            ground_truth_map=ground_truth_map,
        )

        # 4. Evaluate HELD_OUT fixtures
        held_result = self._evaluate_split_fixtures(
            split_name="held_out",
            fixtures=held_fixtures,
            ground_truth_map=ground_truth_map,
        )

        corpus_summary = {
            "total_fixtures": len(manifest.fixtures),
            "dev_fixtures_count": len(dev_fixtures),
            "held_out_fixtures_count": len(held_fixtures),
            "required_benchmark_held_out_count": APPROVED_BENCHMARK_CORPUS_SIZE,
            "additional_held_out_needed": max(0, APPROVED_BENCHMARK_CORPUS_SIZE - len(held_fixtures)),
        }

        report = FullEvaluationReport(
            evaluator_version=EVALUATOR_VERSION,
            evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
            parser_pipeline_version=PROFILE_PIPELINE_VERSION,
            date_rules_version=DATE_RULES_VERSION,
            education_project_certification_rules_version=EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION,
            education_project_certification_parser_version=EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION,
            employment_parser_version=EMPLOYMENT_PARSER_VERSION,
            identity_parser_version=IDENTITY_PARSER_VERSION,
            section_parser_version=SECTION_PARSER_VERSION,
            skill_catalog_version=SKILL_CATALOG_VERSION,
            skill_summary_parser_version=SKILL_SUMMARY_PARSER_VERSION,
            manifest_hash=manifest_hash,
            evaluated_fixture_ids=[f.fixture_id for f in manifest.fixtures],
            execution_timestamp=datetime.now(timezone.utc).isoformat(),
            integrity_status="PASSED",
            corpus_summary=corpus_summary,
            dev_metrics=dev_result,
            held_out_metrics=held_result,
        )

        return report

    def _evaluate_split_fixtures(
        self,
        split_name: str,
        fixtures: List[Any],
        ground_truth_map: Dict[str, GroundTruthDocument],
    ) -> SplitEvaluationResult:
        is_sufficient = (
            len(fixtures) >= APPROVED_BENCHMARK_CORPUS_SIZE if split_name == "held_out" else True
        )

        proc_stats = ProcessingStats(total_eligible=len(fixtures))
        for f in fixtures:
            if f.document_type == DocumentType.PDF:
                proc_stats.pdf_total += 1
            else:
                proc_stats.docx_total += 1

        field_categories = [
            "name",
            "email",
            "phone",
            "location",
            "headline",
            "skills",
            "employment_job_title",
            "employment_organization",
            "employment_start_date",
            "employment_end_date",
            "education_degree",
            "education_field",
            "education_institution",
            "education_dates",
            "project_name",
            "project_description",
            "project_technologies",
            "certification_name",
            "certification_issuer",
            "certification_date",
            "languages",
            "language_proficiency",
        ]

        per_field_counts: Dict[str, MetricCounts] = {
            cat: MetricCounts() for cat in field_categories
        }
        overall_required_micro = MetricCounts()

        complete_rec_stats: Dict[str, CompleteRecordStats] = {
            "employment": CompleteRecordStats(),
            "education": CompleteRecordStats(),
            "projects": CompleteRecordStats(),
            "certifications": CompleteRecordStats(),
        }

        total_profiles_count = len(fixtures)
        perfect_profiles_count = 0

        # Evidence tracking
        total_correct_accepted_fields = 0
        correct_fields_with_valid_evidence = 0
        invalid_evidence_references = 0

        # Date accuracy tracking
        total_expected_emp_dates = 0
        correct_emp_dates = 0

        failed_cases: List[FailedFieldCase] = []

        # Subgroup tracking for breakdown
        breakdowns: Dict[str, Dict[str, List[float]]] = {
            "format": {"pdf": [], "docx": []},
            "layout_family": {},
            "page_count": {},
        }

        for f_entry in fixtures:
            gt_doc = ground_truth_map[f_entry.fixture_id]
            f_path = self.evaluation_dir / f_entry.relative_path

            profile, err_stage, err_msg = _build_candidate_profile_from_file(
                f_path, f_entry.document_type, self.settings
            )

            if profile is None:
                if err_stage == "extraction":
                    proc_stats.extraction_failures += 1
                else:
                    proc_stats.parsing_failures += 1
                if f_entry.document_type == DocumentType.PDF:
                    proc_stats.pdf_failures += 1
                else:
                    proc_stats.docx_failures += 1
                continue

            proc_stats.successfully_profiled += 1

            # Check evidence map validity
            evidence_span_map: Dict[str, EvidenceSpan] = {
                e.evidence_id: e for e in profile.evidence_spans
            }

            def check_field_evidence(fld: Optional[ExtractedField], expected_artifact_id: str) -> bool:
                nonlocal invalid_evidence_references
                if not fld or not fld.value:
                    return False
                if not fld.evidence_ids:
                    return False
                valid_any = False
                for ev_id in fld.evidence_ids:
                    if ev_id not in evidence_span_map:
                        invalid_evidence_references += 1
                        continue
                    span = evidence_span_map[ev_id]
                    # Enforce artifact isolation and bounds
                    if span.resume_artifact_id != expected_artifact_id:
                        invalid_evidence_references += 1
                        continue
                    if span.character_end <= span.character_start or not span.original_text:
                        invalid_evidence_references += 1
                        continue
                    valid_any = True
                return valid_any

            # Evaluate profile fields
            profile_all_required_correct = True
            profile_fixture_micro = MetricCounts()

            # Helper for scalar comparisons
            def eval_scalar(
                cat: str,
                exp_field: Optional[ExpectedFieldAssertion],
                pred_field: Optional[ExtractedField],
                normalizer_fn=normalize_scalar_text,
            ):
                nonlocal total_correct_accepted_fields, correct_fields_with_valid_evidence, profile_all_required_correct
                exp_val = getattr(exp_field, "value", None) if exp_field else None
                pred_val = getattr(pred_field, "value", None) if pred_field else None
                is_req = (
                    getattr(exp_field, "requirement", None) == EvidenceRequirement.REQUIRED
                    if exp_field
                    else False
                )

                if exp_val and pred_val:
                    if normalizer_fn(exp_val) == normalizer_fn(pred_val):
                        per_field_counts[cat].tp += 1
                        if is_req:
                            overall_required_micro.tp += 1
                            profile_fixture_micro.tp += 1
                        total_correct_accepted_fields += 1
                        if check_field_evidence(pred_field, profile.identity.resume_artifact_id):
                            correct_fields_with_valid_evidence += 1
                    else:
                        per_field_counts[cat].fp += 1
                        per_field_counts[cat].fn += 1
                        if is_req:
                            overall_required_micro.fp += 1
                            overall_required_micro.fn += 1
                            profile_fixture_micro.fp += 1
                            profile_fixture_micro.fn += 1
                            profile_all_required_correct = False
                        failed_cases.append(
                            FailedFieldCase(
                                fixture_id=f_entry.fixture_id,
                                field_category=cat,
                                error_type="mismatch",
                                expected_value_repr=str(exp_val),
                                predicted_value_repr=str(pred_val),
                            )
                        )
                elif exp_val and not pred_val:
                    per_field_counts[cat].fn += 1
                    if is_req:
                        overall_required_micro.fn += 1
                        profile_fixture_micro.fn += 1
                        profile_all_required_correct = False
                    failed_cases.append(
                        FailedFieldCase(
                            fixture_id=f_entry.fixture_id,
                            field_category=cat,
                            error_type="false_negative",
                            expected_value_repr=str(exp_val),
                        )
                    )
                elif not exp_val and pred_val:
                    per_field_counts[cat].fp += 1
                    if is_req:
                        overall_required_micro.fp += 1
                        profile_fixture_micro.fp += 1
                        profile_all_required_correct = False
                    failed_cases.append(
                        FailedFieldCase(
                            fixture_id=f_entry.fixture_id,
                            field_category=cat,
                            error_type="false_positive",
                            predicted_value_repr=str(pred_val),
                        )
                    )

            # 1. Full Name
            eval_scalar("name", gt_doc.identity.full_name, profile.identity.full_name)

            # 2. Headline
            eval_scalar(
                "headline",
                gt_doc.professional_profile.headline,
                profile.professional_profile.headline,
            )

            # 3. Location
            eval_scalar("location", gt_doc.identity.location, profile.identity.location)

            # 4. Set-Valued: Emails
            exp_emails = {
                normalize_email(e.value) for e in gt_doc.identity.emails if e.value
            }
            pred_emails = {
                normalize_email(e.value) for e in profile.identity.emails if e.value
            }
            for em in exp_emails.intersection(pred_emails):
                per_field_counts["email"].tp += 1
                overall_required_micro.tp += 1
                profile_fixture_micro.tp += 1
                total_correct_accepted_fields += 1
                # Find matching pred field for evidence check
                matching_fld = next(
                    (e for e in profile.identity.emails if normalize_email(e.value) == em), None
                )
                if check_field_evidence(matching_fld, profile.identity.resume_artifact_id):
                    correct_fields_with_valid_evidence += 1

            for em in pred_emails - exp_emails:
                per_field_counts["email"].fp += 1
                overall_required_micro.fp += 1
                profile_fixture_micro.fp += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="email",
                        error_type="false_positive",
                        predicted_value_repr=em,
                    )
                )
            for em in exp_emails - pred_emails:
                per_field_counts["email"].fn += 1
                overall_required_micro.fn += 1
                profile_fixture_micro.fn += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="email",
                        error_type="false_negative",
                        expected_value_repr=em,
                    )
                )

            # 5. Set-Valued: Phones
            exp_phones = {
                normalize_phone(p.value) for p in gt_doc.identity.phone_numbers if p.value
            }
            pred_phones = {
                normalize_phone(p.value) for p in profile.identity.phone_numbers if p.value
            }
            for ph in exp_phones.intersection(pred_phones):
                per_field_counts["phone"].tp += 1
                overall_required_micro.tp += 1
                profile_fixture_micro.tp += 1
                total_correct_accepted_fields += 1
                matching_fld = next(
                    (p for p in profile.identity.phone_numbers if normalize_phone(p.value) == ph),
                    None,
                )
                if check_field_evidence(matching_fld, profile.identity.resume_artifact_id):
                    correct_fields_with_valid_evidence += 1

            for ph in pred_phones - exp_phones:
                per_field_counts["phone"].fp += 1
                overall_required_micro.fp += 1
                profile_fixture_micro.fp += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="phone",
                        error_type="false_positive",
                        predicted_value_repr=ph,
                    )
                )
            for ph in exp_phones - pred_phones:
                per_field_counts["phone"].fn += 1
                overall_required_micro.fn += 1
                profile_fixture_micro.fn += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="phone",
                        error_type="false_negative",
                        expected_value_repr=ph,
                    )
                )

            # 6. Set-Valued: Skills
            exp_skills = {
                normalize_skill(s.value)
                for s in gt_doc.professional_profile.skills
                if s.value
            }
            pred_skills = {
                normalize_skill(s.normalized_value or s.value)
                for s in profile.professional_profile.skills
                if (s.normalized_value or s.value)
            }
            for sk in exp_skills.intersection(pred_skills):
                per_field_counts["skills"].tp += 1
                overall_required_micro.tp += 1
                profile_fixture_micro.tp += 1
                total_correct_accepted_fields += 1
                matching_fld = next(
                    (
                        s
                        for s in profile.professional_profile.skills
                        if normalize_skill(s.normalized_value or s.value) == sk
                    ),
                    None,
                )
                if check_field_evidence(matching_fld, profile.identity.resume_artifact_id):
                    correct_fields_with_valid_evidence += 1

            for sk in pred_skills - exp_skills:
                per_field_counts["skills"].fp += 1
                overall_required_micro.fp += 1
                profile_fixture_micro.fp += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="skills",
                        error_type="false_positive",
                        predicted_value_repr=sk,
                    )
                )
            for sk in exp_skills - pred_skills:
                per_field_counts["skills"].fn += 1
                overall_required_micro.fn += 1
                profile_fixture_micro.fn += 1
                profile_all_required_correct = False
                failed_cases.append(
                    FailedFieldCase(
                        fixture_id=f_entry.fixture_id,
                        field_category="skills",
                        error_type="false_negative",
                        expected_value_repr=sk,
                    )
                )

            # 7. Structured: Employment Records Alignment
            emp_matches, emp_unexp, emp_unpred = align_records_one_to_one(
                gt_doc.professional_profile.employment,
                profile.professional_profile.employment,
                scorer=score_employment_pair,
            )

            complete_rec_stats["employment"].total_expected += len(
                gt_doc.professional_profile.employment
            )

            for exp_emp, pred_emp in emp_matches:
                emp_rec_perfect = True

                # Job Title (Required)
                eval_scalar(
                    "employment_job_title",
                    exp_emp.job_title,
                    pred_emp.job_title,
                )
                if not compare_scalar(
                    getattr(exp_emp.job_title, "value", None),
                    getattr(pred_emp.job_title, "value", None),
                ):
                    emp_rec_perfect = False

                # Organization (Required)
                eval_scalar(
                    "employment_organization",
                    exp_emp.organization,
                    pred_emp.organization,
                )
                if not compare_scalar(
                    getattr(exp_emp.organization, "value", None),
                    getattr(pred_emp.organization, "value", None),
                ):
                    emp_rec_perfect = False

                # Start Date (Required)
                total_expected_emp_dates += 1
                exp_start_norm = normalize_date_string(
                    getattr(exp_emp.start_date, "normalized_value", None)
                    or getattr(exp_emp.start_date, "value", None)
                )
                pred_start_norm = normalize_date_string(
                    getattr(pred_emp.start_date, "normalized_value", None)
                    or getattr(pred_emp.start_date, "value", None)
                )
                eval_scalar(
                    "employment_start_date",
                    exp_emp.start_date,
                    pred_emp.start_date,
                    normalizer_fn=normalize_date_string,
                )
                if exp_start_norm and pred_start_norm and exp_start_norm == pred_start_norm:
                    correct_emp_dates += 1
                else:
                    emp_rec_perfect = False

                # End Date (Required)
                total_expected_emp_dates += 1
                exp_end_norm = normalize_date_string(
                    getattr(exp_emp.end_date, "normalized_value", None)
                    or getattr(exp_emp.end_date, "value", None)
                )
                pred_end_norm = normalize_date_string(
                    getattr(pred_emp.end_date, "normalized_value", None)
                    or getattr(pred_emp.end_date, "value", None)
                )
                eval_scalar(
                    "employment_end_date",
                    exp_emp.end_date,
                    pred_emp.end_date,
                    normalizer_fn=normalize_date_string,
                )
                if exp_end_norm and pred_end_norm and exp_end_norm == pred_end_norm:
                    correct_emp_dates += 1
                else:
                    emp_rec_perfect = False

                if emp_rec_perfect:
                    complete_rec_stats["employment"].total_correct += 1

            for un_exp in emp_unexp:
                # Missing entire record
                for cat, fld in [
                    ("employment_job_title", un_exp.job_title),
                    ("employment_organization", un_exp.organization),
                    ("employment_start_date", un_exp.start_date),
                    ("employment_end_date", un_exp.end_date),
                ]:
                    eval_scalar(cat, fld, None)
                    if cat in ("employment_start_date", "employment_end_date"):
                        total_expected_emp_dates += 1

            for un_pred in emp_unpred:
                # Hallucinated / extra record
                for cat, fld in [
                    ("employment_job_title", un_pred.job_title),
                    ("employment_organization", un_pred.organization),
                    ("employment_start_date", un_pred.start_date),
                    ("employment_end_date", un_pred.end_date),
                ]:
                    eval_scalar(cat, None, fld)

            # 8. Structured: Education Records Alignment
            edu_matches, edu_unexp, edu_unpred = align_records_one_to_one(
                gt_doc.professional_profile.education,
                profile.professional_profile.education,
                scorer=score_education_pair,
            )

            complete_rec_stats["education"].total_expected += len(
                gt_doc.professional_profile.education
            )

            for exp_edu, pred_edu in edu_matches:
                edu_rec_perfect = True

                # Degree (Required)
                eval_scalar("education_degree", exp_edu.degree, pred_edu.degree)
                if not compare_scalar(
                    getattr(exp_edu.degree, "value", None),
                    getattr(pred_edu.degree, "value", None),
                ):
                    edu_rec_perfect = False

                # Institution (Required)
                eval_scalar("education_institution", exp_edu.institution, pred_edu.institution)
                if not compare_scalar(
                    getattr(exp_edu.institution, "value", None),
                    getattr(pred_edu.institution, "value", None),
                ):
                    edu_rec_perfect = False

                # Field of study (Optional/Required depending on GT)
                eval_scalar("education_field", exp_edu.field_of_study, pred_edu.field_of_study)

                # Dates
                eval_scalar(
                    "education_dates",
                    exp_edu.end_date or exp_edu.start_date,
                    pred_edu.end_date or pred_edu.start_date,
                    normalizer_fn=normalize_date_string,
                )

                if edu_rec_perfect:
                    complete_rec_stats["education"].total_correct += 1

            for un_exp in edu_unexp:
                for cat, fld in [
                    ("education_degree", un_exp.degree),
                    ("education_institution", un_exp.institution),
                    ("education_field", un_exp.field_of_study),
                    ("education_dates", un_exp.end_date or un_exp.start_date),
                ]:
                    if fld:
                        eval_scalar(cat, fld, None)

            for un_pred in edu_unpred:
                for cat, fld in [
                    ("education_degree", un_pred.degree),
                    ("education_institution", un_pred.institution),
                    ("education_field", un_pred.field_of_study),
                    ("education_dates", un_pred.end_date or un_pred.start_date),
                ]:
                    if fld:
                        eval_scalar(cat, None, fld)

            # 9. Structured: Project Records Alignment
            prj_matches, prj_unexp, prj_unpred = align_records_one_to_one(
                gt_doc.professional_profile.projects,
                profile.professional_profile.projects,
                scorer=score_project_pair,
            )

            complete_rec_stats["projects"].total_expected += len(
                gt_doc.professional_profile.projects
            )

            for exp_prj, pred_prj in prj_matches:
                prj_rec_perfect = True

                # Project Name (Required)
                eval_scalar("project_name", exp_prj.name, pred_prj.name)
                if not compare_scalar(
                    getattr(exp_prj.name, "value", None),
                    getattr(pred_prj.name, "value", None),
                ):
                    prj_rec_perfect = False

                # Description
                eval_scalar("project_description", exp_prj.description, pred_prj.description)

                # Technologies
                exp_techs = {
                    normalize_skill(t.value) for t in exp_prj.technologies if t.value
                }
                pred_techs = {
                    normalize_skill(t.value) for t in pred_prj.technologies if t.value
                }
                for tech in exp_techs.intersection(pred_techs):
                    per_field_counts["project_technologies"].tp += 1
                for tech in pred_techs - exp_techs:
                    per_field_counts["project_technologies"].fp += 1
                for tech in exp_techs - pred_techs:
                    per_field_counts["project_technologies"].fn += 1

                if prj_rec_perfect:
                    complete_rec_stats["projects"].total_correct += 1

            for un_exp in prj_unexp:
                eval_scalar("project_name", un_exp.name, None)
                if un_exp.description:
                    eval_scalar("project_description", un_exp.description, None)

            for un_pred in prj_unpred:
                eval_scalar("project_name", None, un_pred.name)
                if un_pred.description:
                    eval_scalar("project_description", None, un_pred.description)

            # 10. Structured: Certification Records Alignment
            cert_matches, cert_unexp, cert_unpred = align_records_one_to_one(
                gt_doc.professional_profile.certifications,
                profile.professional_profile.certifications,
                scorer=score_certification_pair,
            )

            complete_rec_stats["certifications"].total_expected += len(
                gt_doc.professional_profile.certifications
            )

            for exp_cert, pred_cert in cert_matches:
                cert_rec_perfect = True

                # Name (Required)
                eval_scalar("certification_name", exp_cert.name, pred_cert.name)
                if not compare_scalar(
                    getattr(exp_cert.name, "value", None),
                    getattr(pred_cert.name, "value", None),
                ):
                    cert_rec_perfect = False

                # Issuer (Required)
                eval_scalar("certification_issuer", exp_cert.issuer, pred_cert.issuer)
                if not compare_scalar(
                    getattr(exp_cert.issuer, "value", None),
                    getattr(pred_cert.issuer, "value", None),
                ):
                    cert_rec_perfect = False

                # Issue Date
                eval_scalar(
                    "certification_date",
                    exp_cert.issued_date,
                    pred_cert.issued_date,
                    normalizer_fn=normalize_date_string,
                )

                if cert_rec_perfect:
                    complete_rec_stats["certifications"].total_correct += 1

            for un_exp in cert_unexp:
                eval_scalar("certification_name", un_exp.name, None)
                eval_scalar("certification_issuer", un_exp.issuer, None)

            for un_pred in cert_unpred:
                eval_scalar("certification_name", None, un_pred.name)
                eval_scalar("certification_issuer", None, un_pred.issuer)

            # 11. Languages & Proficiency
            exp_lang_map = {
                normalize_scalar_text(l.language.value): l for l in gt_doc.professional_profile.languages if l.language.value
            }
            pred_lang_map = {
                normalize_scalar_text(l.value): l for l in profile.professional_profile.languages if l.value
            }
            for l_key in set(exp_lang_map.keys()).intersection(set(pred_lang_map.keys())):
                per_field_counts["languages"].tp += 1
                overall_required_micro.tp += 1
                profile_fixture_micro.tp += 1
                total_correct_accepted_fields += 1
                matching_pred = pred_lang_map[l_key]
                if check_field_evidence(matching_pred, profile.identity.resume_artifact_id):
                    correct_fields_with_valid_evidence += 1

                exp_l_rec = exp_lang_map[l_key]
                if exp_l_rec.proficiency and matching_pred.normalized_value:
                    if normalize_language_proficiency(
                        exp_l_rec.proficiency.value
                    ) == normalize_language_proficiency(matching_pred.normalized_value):
                        per_field_counts["language_proficiency"].tp += 1
                    else:
                        per_field_counts["language_proficiency"].fp += 1
                        per_field_counts["language_proficiency"].fn += 1

            for l_key in set(pred_lang_map.keys()) - set(exp_lang_map.keys()):
                per_field_counts["languages"].fp += 1
                overall_required_micro.fp += 1
                profile_fixture_micro.fp += 1
                profile_all_required_correct = False

            for l_key in set(exp_lang_map.keys()) - set(pred_lang_map.keys()):
                per_field_counts["languages"].fn += 1
                overall_required_micro.fn += 1
                profile_fixture_micro.fn += 1
                profile_all_required_correct = False

            if profile_all_required_correct and profile_fixture_micro.fn == 0 and profile_fixture_micro.fp == 0:
                perfect_profiles_count += 1

            # Subgroup breakdowns
            fmt_key = f_entry.document_type.value
            breakdowns["format"][fmt_key].append(profile_fixture_micro.f1)

            fam_key = f_entry.layout_family.value
            if fam_key not in breakdowns["layout_family"]:
                breakdowns["layout_family"][fam_key] = []
            breakdowns["layout_family"][fam_key].append(profile_fixture_micro.f1)

            pg_key = str(f_entry.page_count or 1)
            if pg_key not in breakdowns["page_count"]:
                breakdowns["page_count"][pg_key] = []
            breakdowns["page_count"][pg_key].append(profile_fixture_micro.f1)

        # Macro F1 calculation across required field categories
        f1_list = [
            per_field_counts[cat].f1
            for cat in field_categories
            if (per_field_counts[cat].tp + per_field_counts[cat].fp + per_field_counts[cat].fn) > 0
        ]
        macro_f1 = sum(f1_list) / len(f1_list) if f1_list else 0.0

        per_field_metrics_dict = {
            cat: {
                "tp": per_field_counts[cat].tp,
                "fp": per_field_counts[cat].fp,
                "fn": per_field_counts[cat].fn,
                "precision": round(per_field_counts[cat].precision, 4),
                "recall": round(per_field_counts[cat].recall, 4),
                "f1": round(per_field_counts[cat].f1, 4),
            }
            for cat in field_categories
        }

        complete_rec_accuracy_dict = {
            k: round(v.accuracy, 4) for k, v in complete_rec_stats.items()
        }

        complete_profile_acc = (
            round(perfect_profiles_count / total_profiles_count, 4)
            if total_profiles_count > 0
            else 0.0
        )

        ev_coverage = (
            round(correct_fields_with_valid_evidence / total_correct_accepted_fields, 4)
            if total_correct_accepted_fields > 0
            else 1.0
        )

        emp_date_acc = (
            round(correct_emp_dates / total_expected_emp_dates, 4)
            if total_expected_emp_dates > 0
            else 1.0
        )

        # Process breakdown summaries
        breakdown_summary = {}
        for dim, groups in breakdowns.items():
            breakdown_summary[dim] = {}
            for grp, f1s in groups.items():
                breakdown_summary[dim][grp] = {
                    "sample_size": len(f1s),
                    "mean_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
                }

        # Acceptance Decisions
        acceptance_decisions = {}
        if not is_sufficient:
            acceptance_decisions["micro_f1_target"] = "INSUFFICIENT CORPUS"
            acceptance_decisions["macro_f1_target"] = "INSUFFICIENT CORPUS"
            acceptance_decisions["employment_date_accuracy_target"] = "INSUFFICIENT CORPUS"
            acceptance_decisions["evidence_coverage_target"] = "INSUFFICIENT CORPUS"
            acceptance_decisions["processing_failure_rate_target"] = "INSUFFICIENT CORPUS"
            acceptance_decisions["overall_milestone_status"] = "INSUFFICIENT CORPUS"
        else:
            micro_pass = overall_required_micro.f1 >= TARGET_MICRO_F1
            macro_pass = macro_f1 >= TARGET_MACRO_F1
            date_pass = emp_date_acc >= TARGET_EMPLOYMENT_DATE_ACCURACY
            ev_pass = ev_coverage >= TARGET_EVIDENCE_COVERAGE
            fail_rate_pass = proc_stats.failure_rate <= TARGET_PROCESSING_FAILURE_RATE

            acceptance_decisions["micro_f1_target"] = "PASS" if micro_pass else "FAIL"
            acceptance_decisions["macro_f1_target"] = "PASS" if macro_pass else "FAIL"
            acceptance_decisions["employment_date_accuracy_target"] = (
                "PASS" if date_pass else "FAIL"
            )
            acceptance_decisions["evidence_coverage_target"] = "PASS" if ev_pass else "FAIL"
            acceptance_decisions["processing_failure_rate_target"] = (
                "PASS" if fail_rate_pass else "FAIL"
            )
            all_pass = (
                micro_pass and macro_pass and date_pass and ev_pass and fail_rate_pass
            )
            acceptance_decisions["overall_milestone_status"] = "PASS" if all_pass else "FAIL"

        return SplitEvaluationResult(
            split=split_name,
            document_count=len(fixtures),
            corpus_sufficient=is_sufficient,
            required_field_micro=overall_required_micro,
            required_field_macro_f1=round(macro_f1, 4),
            per_field_metrics=per_field_metrics_dict,
            complete_record_accuracy=complete_rec_accuracy_dict,
            complete_profile_accuracy=complete_profile_acc,
            evidence_coverage=ev_coverage,
            invalid_evidence_count=invalid_evidence_references,
            employment_date_accuracy=emp_date_acc,
            processing_stats=proc_stats,
            breakdown_metrics=breakdown_summary,
            failed_cases=failed_cases,
            acceptance_decisions=acceptance_decisions,
        )
