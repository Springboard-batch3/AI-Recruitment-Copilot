import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services.ai_provenance_service import (
    AuthoringOrigin,
    validate_conversational_question_text,
    validate_mcq_structure_and_safety,
    build_canonical_question_provenance,
    evaluate_release_eligibility,
    ReleaseEligibilityResult,
    ItemValidationOutcome,
)
from app.models.interview import AiInterviewQuestionSetRecord
from app.models.job import JobVersion


def test_local_ai_conversational_question_passes_strict_validation():
    valid_text = "Can you describe a scenario where you architected a distributed data pipeline and how you handled eventual consistency?"
    outcome = validate_conversational_question_text(valid_text, competency_name="SYSTEM_DESIGN")
    assert outcome.is_valid
    assert outcome.error_code is None


def test_local_ai_mcq_passes_strict_validation():
    stem = "Which HTTP status code should be returned when a requested resource is not found on the server?"
    options = [
        {"id": "A", "text": "200 OK"},
        {"id": "B", "text": "400 Bad Request"},
        {"id": "C", "text": "404 Not Found"},
        {"id": "D", "text": "500 Internal Server Error"}
    ]
    correct_option_id = "C"
    explanation = "HTTP 404 indicates that the server cannot find the requested resource."
    competency = "WEB_DEVELOPMENT"
    outcome = validate_mcq_structure_and_safety(
        stem=stem,
        options=options,
        correct_option_id=correct_option_id,
        explanation=explanation,
        competency_name=competency,
    )
    assert outcome.is_valid
    assert outcome.error_code is None


def test_deterministic_origin_set_is_rejected_for_release():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 1

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=mock_job_version.job_version_id,
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LEGACY_DETERMINISTIC.value,
                "question_text": "Sample deterministic wording.",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert not res.is_eligible
    assert any("legacy deterministic" in item["message"].lower() for item in res.blocking_items)


def test_manual_origin_set_is_rejected():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 1

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=mock_job_version.job_version_id,
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LEGACY_MANUAL.value,
                "question_text": "Sample manual wording written by recruiter.",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert not res.is_eligible


def test_mixed_origin_set_is_rejected():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 1

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=mock_job_version.job_version_id,
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
                "question_text": "Explain how you implement binary search safely.",
                "model_identifier": "qwen/qwen3-4b-2507",
                "validation_status": "PASSED"
            },
            {
                "slot_id": "slot-2",
                "authoring_origin": AuthoringOrigin.LEGACY_DETERMINISTIC.value,
                "question_text": "Deterministic question 2.",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert not res.is_eligible


def test_generated_recruiter_edited_set_remains_eligible():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 1

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=mock_job_version.job_version_id,
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        provider_class="LMStudioProvider",
        model_identifier="qwen/qwen3-4b-2507",
        prompt_template_version="practice-prompt.v1",
        guardrail_policy_version="guardrail.v1",
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED_RECRUITER_EDITED.value,
                "question_text": "How do you handle memory leaks in large Python applications?",
                "model_identifier": "qwen/qwen3-4b-2507",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert res.is_eligible


def test_missing_provider_provenance_blocks_approval_and_release():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 1

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=mock_job_version.job_version_id,
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        provider_class=None,  # missing provenance
        model_identifier=None,
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
                "question_text": "How do you optimize SQL database queries?",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert not res.is_eligible


def test_stale_job_revision_blocks_release():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_job_version.job_version_id = uuid.uuid4()
    mock_job_version.version_number = 2  # Current version is 2

    rec = AiInterviewQuestionSetRecord(
        ai_set_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),  # Approved for old revision
        job_revision=1,
        set_version=1,
        lifecycle_status="APPROVED",
        provider_class="LMStudioProvider",
        model_identifier="qwen/qwen3-4b-2507",
        prompt_template_version="practice-prompt.v1",
        guardrail_policy_version="guardrail.v1",
        items=[
            {
                "slot_id": "slot-1",
                "authoring_origin": AuthoringOrigin.LOCAL_LLM_GENERATED.value,
                "question_text": "How do you optimize SQL database queries?",
                "model_identifier": "qwen/qwen3-4b-2507",
                "validation_status": "PASSED"
            }
        ]
    )
    res = evaluate_release_eligibility(rec, target_job_version=mock_job_version)
    assert not res.is_eligible


def test_protected_topic_content_is_rejected():
    protected_text = "What is your marital status and religion when considering relocations?"
    outcome = validate_conversational_question_text(protected_text, competency_name="GENERAL")
    assert not outcome.is_valid
    assert "PROHIBITED_TOPIC" in outcome.error_code


def test_multiple_primary_asks_are_rejected():
    multi_ask_text = "Can you explain how you design microservices? Furthermore, how do you manage database migrations across versions?"
    outcome = validate_conversational_question_text(multi_ask_text, competency_name="ARCHITECTURE")
    assert not outcome.is_valid
    assert outcome.error_code == "MULTIPLE_PRIMARY_ASKS"


def test_mcq_duplicate_options_are_rejected():
    stem = "What is Python?"
    options = [
        {"id": "A", "text": "A programming language"},
        {"id": "B", "text": "A programming language"},
        {"id": "C", "text": "A database"},
        {"id": "D", "text": "An OS"}
    ]
    outcome = validate_mcq_structure_and_safety(
        stem=stem,
        options=options,
        correct_option_id="A",
        explanation="Python is a language.",
        competency_name="CODING"
    )
    assert not outcome.is_valid
    assert outcome.error_code == "MCQ_DUPLICATE_OPTION_TEXT"


def test_mcq_invalid_correct_option_is_rejected():
    stem = "What is Python?"
    options = [
        {"id": "A", "text": "A programming language"},
        {"id": "B", "text": "A database"}
    ]
    outcome = validate_mcq_structure_and_safety(
        stem=stem,
        options=options,
        correct_option_id="Z",  # Non-existent
        explanation="Python is a language.",
        competency_name="CODING"
    )
    assert not outcome.is_valid
    assert outcome.error_code == "MCQ_INVALID_CORRECT_OPTION"


def test_mcq_html_script_and_cue_rejected():
    stem = "Which option is correct? <script>alert(1)</script>"
    options = [
        {"id": "A", "text": "Option A"},
        {"id": "B", "text": "Option B"}
    ]
    outcome = validate_mcq_structure_and_safety(
        stem=stem,
        options=options,
        correct_option_id="B",
        explanation="Valid explanation.",
        competency_name="CODING"
    )
    assert not outcome.is_valid
    assert outcome.error_code == "MCQ_STEM_UNSAFE"


def test_forged_requirement_ids_are_rejected():
    mock_job_version = MagicMock(spec=JobVersion)
    mock_req = MagicMock()
    mock_req.requirement_id = uuid.uuid4()
    mock_job_version.requirements = [mock_req]

    stem = "What is Docker?"
    options = [
        {"id": "A", "text": "Containerization tool"},
        {"id": "B", "text": "Database"}
    ]
    outcome = validate_mcq_structure_and_safety(
        stem=stem,
        options=options,
        correct_option_id="A",
        explanation="Docker runs containers.",
        competency_name="DEVOPS",
        job_version=mock_job_version,
        source_requirement_ids=["forged-req-999"]
    )
    assert not outcome.is_valid
    assert outcome.error_code == "FORGED_REQUIREMENT_ID"
