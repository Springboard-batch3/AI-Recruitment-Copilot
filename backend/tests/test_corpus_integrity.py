import json
import shutil
from pathlib import Path
import pytest

from tests.evaluation_support.integrity import (
    CorpusIntegrityError,
    compute_sha256_hash,
    load_and_validate_ground_truth,
    load_and_validate_manifest,
    validate_corpus_integrity,
)
from tests.evaluation_support.schemas import CorpusSplit, CoverageTag

EVALUATION_DIR = Path(__file__).resolve().parent / "fixtures" / "evaluation"


def test_manifest_schema_and_integrity():
    """Verify manifest.json parses strictly without schema or ID violations."""
    manifest_path = EVALUATION_DIR / "manifest.json"
    manifest = load_and_validate_manifest(manifest_path)

    assert manifest.manifest_version == "1.0"
    assert len(manifest.fixtures) == 6

    # Verify uniqueness of fixture_ids and paths
    fixture_ids = [f.fixture_id for f in manifest.fixtures]
    assert len(fixture_ids) == len(set(fixture_ids))

    rel_paths = [f.relative_path for f in manifest.fixtures]
    assert len(rel_paths) == len(set(rel_paths))


def test_all_fixtures_and_ground_truth_files_exist_and_hashes_match():
    """Verify all fixture files and ground truth JSON files exist with exact SHA-256."""
    manifest, gt_map = validate_corpus_integrity(EVALUATION_DIR)

    assert len(manifest.fixtures) == 6
    assert len(gt_map) == 6

    for entry in manifest.fixtures:
        fixture_path = EVALUATION_DIR / entry.relative_path
        assert fixture_path.exists()
        assert compute_sha256_hash(fixture_path) == entry.sha256.lower()

        gt_path = EVALUATION_DIR / entry.ground_truth_relative_path
        assert gt_path.exists()


def test_two_provided_integrity_fixtures_hashes():
    """Explicitly verify the exact two provided integrity fixtures."""
    docx_path = EVALUATION_DIR / "documents/Phase_1E2_Integrity_Table_Reordered_Resume.docx"
    pdf_path = EVALUATION_DIR / "documents/Phase_1E2_Integrity_Two_Column_Resume.pdf"

    assert docx_path.exists()
    assert pdf_path.exists()

    # Exact hashes from user specification
    assert compute_sha256_hash(docx_path) == "2737d0a308c43ae5d2ccb8e10bf92b3e4be9f723006732607fb35d89b1b7f23f"
    assert compute_sha256_hash(pdf_path) == "479109a44eee16c6c044c1893d1134ad1c5964ef1edf951fce0285c95f436800"


def test_split_isolation_and_no_leakage():
    """Verify development and held_out splits have zero candidate overlap."""
    manifest, gt_map = validate_corpus_integrity(EVALUATION_DIR)

    dev_names = {
        gt.identity.full_name.value.lower()
        for gt in gt_map.values()
        if gt.split == CorpusSplit.DEVELOPMENT
    }
    held_names = {
        gt.identity.full_name.value.lower()
        for gt in gt_map.values()
        if gt.split == CorpusSplit.HELD_OUT
    }

    assert len(dev_names) == 4
    assert len(held_names) == 2
    assert dev_names.isdisjoint(held_names)


def test_corpus_coverage_tags_completeness():
    """Verify that all required layout and feature tags are represented across the corpus."""
    manifest, _ = validate_corpus_integrity(EVALUATION_DIR)
    all_tags = {tag for f in manifest.fixtures for tag in f.coverage_tags}

    assert CoverageTag.SINGLE_COLUMN_PDF in all_tags
    assert CoverageTag.TWO_COLUMN_PDF in all_tags
    assert CoverageTag.MULTI_PAGE_PDF in all_tags
    assert CoverageTag.DOCX_PARAGRAPHS in all_tags
    assert CoverageTag.DOCX_TABLES in all_tags
    assert CoverageTag.REORDERED_SECTIONS in all_tags
    assert CoverageTag.MISSING_OPTIONAL_SECTIONS in all_tags
    assert CoverageTag.YEAR_ONLY_DATES in all_tags
    assert CoverageTag.MONTH_YEAR_DATES in all_tags
    assert CoverageTag.MULTIPLE_EMPLOYMENT in all_tags
    assert CoverageTag.MULTIPLE_LANGUAGES in all_tags


def test_tampered_fixture_fails_integrity(tmp_path):
    """Verify that altering a fixture file by even one byte fails integrity validation."""
    # Copy evaluation tree to tmp_path
    eval_copy = tmp_path / "evaluation"
    shutil.copytree(EVALUATION_DIR, eval_copy)

    # Validate intact copy succeeds
    validate_corpus_integrity(eval_copy)

    # Tamper with a fixture
    target_fixture = eval_copy / "documents/Phase_1E2_Integrity_Table_Reordered_Resume.docx"
    content = target_fixture.read_bytes()
    target_fixture.write_bytes(content + b"\x00")

    with pytest.raises(CorpusIntegrityError) as exc:
        validate_corpus_integrity(eval_copy)

    assert "SHA-256 mismatch" in str(exc.value)


def test_tampered_ground_truth_fails_integrity(tmp_path):
    """Verify that referencing an invalid evidence key fails integrity validation."""
    eval_copy = tmp_path / "evaluation"
    shutil.copytree(EVALUATION_DIR, eval_copy)

    gt_file = eval_copy / "ground_truth/Phase_1E2_Integrity_Table_Reordered_Resume.json"
    gt_data = json.loads(gt_file.read_text(encoding="utf-8"))
    # Invalidate evidence key
    gt_data["identity"]["full_name"]["evidence_keys"] = ["NON_EXISTENT_KEY"]
    gt_file.write_text(json.dumps(gt_data), encoding="utf-8")

    with pytest.raises(CorpusIntegrityError) as exc:
        validate_corpus_integrity(eval_copy)

    assert "undeclared evidence key" in str(exc.value)


def test_production_runtime_isolation():
    """Verify that production API routers and models do not import from evaluation support."""
    import inspect
    from app.api import resumes
    from app.services import extraction_orchestration, profile_assembly
    import app.main

    # None of the production services, routers or schemas should import evaluation support
    for mod in (resumes, extraction_orchestration, profile_assembly, app.main):
        source = inspect.getsource(mod)
        assert "tests.evaluation_support" not in source
        assert "app.evaluation" not in source

    # Production routes must not expose ground truth
    app_instance = app.main.app
    routes = [getattr(r, "path", getattr(r, "name", "")) for r in app_instance.routes]
    assert not any("ground_truth" in r.lower() or "evaluation" in r.lower() for r in routes)
