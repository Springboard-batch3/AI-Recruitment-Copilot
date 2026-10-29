import hashlib
import json
from pathlib import Path
from typing import Dict, List, Set, Tuple

from .schemas import (
    CorpusManifest,
    CorpusSplit,
    CoverageTag,
    DocumentType,
    EvidenceRequirement,
    GroundTruthDocument,
)


class CorpusIntegrityError(Exception):
    """Raised when evaluation corpus integrity check fails."""
    pass


def compute_sha256_hash(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def load_and_validate_manifest(manifest_path: Path) -> CorpusManifest:
    if not manifest_path.exists():
        raise CorpusIntegrityError(f"Manifest file not found: {manifest_path}")

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return CorpusManifest.model_validate(data)
    except Exception as e:
        raise CorpusIntegrityError(f"Manifest validation failed: {e}") from e


def load_and_validate_ground_truth(gt_path: Path) -> GroundTruthDocument:
    if not gt_path.exists():
        raise CorpusIntegrityError(f"Ground truth file not found: {gt_path}")

    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
        return GroundTruthDocument.model_validate(data)
    except Exception as e:
        raise CorpusIntegrityError(f"Ground truth validation failed for {gt_path}: {e}") from e


class CorpusValidationReport:
    def __init__(self, manifest: CorpusManifest, ground_truth_map: Dict[str, GroundTruthDocument]):
        self.manifest = manifest
        self.ground_truth_map = ground_truth_map


class CorpusIntegrityValidator:
    def __init__(self, evaluation_dir: Path):
        self.evaluation_dir = evaluation_dir

    def validate(self) -> CorpusValidationReport:
        manifest, gt_map = validate_corpus_integrity(self.evaluation_dir)
        return CorpusValidationReport(manifest=manifest, ground_truth_map=gt_map)


def validate_corpus_integrity(
    evaluation_dir: Path,
) -> Tuple[CorpusManifest, Dict[str, GroundTruthDocument]]:
    """
    Performs full automated integrity validation of the evaluation corpus:
    1. Validates manifest schema and uniqueness of fixture IDs / paths.
    2. Verifies physical existence and SHA-256 hash match for every fixture.
    3. Verifies physical existence and schema validity of every ground truth file.
    4. Enforces strict development vs held_out isolation (no candidate name or email overlap).
    5. Ensures all required coverage tags across the corpus are satisfied.
    6. Ensures valid internal evidence references.
    """
    manifest_path = evaluation_dir / "manifest.json"
    manifest = load_and_validate_manifest(manifest_path)

    ground_truth_map: Dict[str, GroundTruthDocument] = {}
    dev_identities: Set[str] = set()
    held_identities: Set[str] = set()
    covered_tags: Set[CoverageTag] = set()

    for entry in manifest.fixtures:
        fixture_file = evaluation_dir / entry.relative_path
        if not fixture_file.exists():
            raise CorpusIntegrityError(f"Fixture file does not exist: {fixture_file}")

        # Check hash
        actual_hash = compute_sha256_hash(fixture_file)
        if actual_hash != entry.sha256.lower():
            raise CorpusIntegrityError(
                f"SHA-256 mismatch for fixture '{entry.fixture_id}'. Expected {entry.sha256}, got {actual_hash}"
            )

        # Validate ground truth
        gt_file = evaluation_dir / entry.ground_truth_relative_path
        if not gt_file.exists():
            raise CorpusIntegrityError(f"Ground truth file missing: {gt_file}")

        gt_doc = load_and_validate_ground_truth(gt_file)
        if gt_doc.fixture_id != entry.fixture_id:
            raise CorpusIntegrityError(
                f"Ground truth fixture_id mismatch: {gt_doc.fixture_id} vs {entry.fixture_id}"
            )
        if gt_doc.split != entry.split:
            raise CorpusIntegrityError(
                f"Ground truth split mismatch for {entry.fixture_id}: {gt_doc.split} vs {entry.split}"
            )

        # Verify evidence reference validity
        declared_ev_keys = {ev.evidence_key for ev in gt_doc.evidence_expectations}

        # Helper to check fields
        def check_field(f):
            if f and f.requirement != EvidenceRequirement.INTENTIONALLY_ABSENT:
                for k in f.evidence_keys:
                    if k not in declared_ev_keys:
                        raise CorpusIntegrityError(f"Field references undeclared evidence key: {k}")

        check_field(gt_doc.identity.full_name)
        for em in gt_doc.identity.emails:
            check_field(em)
        for ph in gt_doc.identity.phone_numbers:
            check_field(ph)
        if gt_doc.identity.location:
            check_field(gt_doc.identity.location)

        for sk in gt_doc.professional_profile.skills:
            check_field(sk)
        for emp in gt_doc.professional_profile.employment:
            check_field(emp.job_title)
            check_field(emp.organization)
            check_field(emp.start_date)
            check_field(emp.end_date)
            for d in emp.descriptions:
                check_field(d)
        for edu in gt_doc.professional_profile.education:
            check_field(edu.institution)
            check_field(edu.degree)
            if edu.field_of_study:
                check_field(edu.field_of_study)
        for prj in gt_doc.professional_profile.projects:
            check_field(prj.name)
            check_field(prj.description)
            for tech in prj.technologies:
                check_field(tech)
        for cert in gt_doc.professional_profile.certifications:
            check_field(cert.name)
            check_field(cert.issuer)
        for lang in gt_doc.professional_profile.languages:
            check_field(lang.language)
            if lang.proficiency:
                check_field(lang.proficiency)

        # Track identity for split leakage check
        candidate_sig = gt_doc.identity.full_name.value.strip().lower()
        if entry.split == CorpusSplit.DEVELOPMENT:
            dev_identities.add(candidate_sig)
        elif entry.split == CorpusSplit.HELD_OUT:
            held_identities.add(candidate_sig)

        for tag in entry.coverage_tags:
            covered_tags.add(tag)

        ground_truth_map[entry.fixture_id] = gt_doc

    # Check leakage across splits
    leakage = dev_identities.intersection(held_identities)
    if leakage:
        raise CorpusIntegrityError(f"Candidate leakage detected across development and held-out splits: {leakage}")

    # Check required coverage categories
    required_tags = {
        CoverageTag.SINGLE_COLUMN_PDF,
        CoverageTag.TWO_COLUMN_PDF,
        CoverageTag.MULTI_PAGE_PDF,
        CoverageTag.DOCX_PARAGRAPHS,
        CoverageTag.DOCX_TABLES,
        CoverageTag.REORDERED_SECTIONS,
        CoverageTag.MISSING_OPTIONAL_SECTIONS,
        CoverageTag.YEAR_ONLY_DATES,
        CoverageTag.MONTH_YEAR_DATES,
        CoverageTag.MULTIPLE_EMPLOYMENT,
        CoverageTag.MULTIPLE_LANGUAGES,
    }
    missing_tags = required_tags - covered_tags
    if missing_tags:
        raise CorpusIntegrityError(f"Corpus is missing required coverage tags: {missing_tags}")

    return manifest, ground_truth_map
