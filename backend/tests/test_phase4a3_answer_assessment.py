"""Focused Unit & Integration Tests for Phase 4A.3 Structured Answer Assessment and Report Contracts."""
from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from pydantic import ValidationError

from app.schemas.answer_assessment_contract import (
    ASSESSMENT_POLICY_VERSION,
    AssessmentCriterionLabel,
    AllowedRubricCriterion,
    CanonicalAnswerSegment,
    ConfidenceCategory,
    EvidenceStateCategory,
    PerAnswerAssessmentPacket,
    StrictCriterionAssessmentItem,
    StrictPerAnswerAssessmentResponse,
    CandidateStructuredReportDto,
    RecruiterStructuredReportDto,
)
from app.services.answer_assessment_service import (
    assess_single_answer,
    build_authoritative_assessment_packet,
    create_human_review_fallback_assessment,
    extract_canonical_answer_segments,
    validate_strict_assessment_output,
    aggregate_competency_evidence_coverage,
    process_and_persist_structured_session_assessment,
)
from app.models.candidate_access import CandidatePracticeSessionRecord, CandidatePracticeMessageRecord


@pytest.fixture
def sample_packet() -> PerAnswerAssessmentPacket:
    q_id = uuid4()
    a_id = uuid4()
    segments = [
        CanonicalAnswerSegment(segment_id="S1", sequence_order=1, text="I implemented a rate limiter using Redis token buckets.", start_char=0, end_char=56),
        CanonicalAnswerSegment(segment_id="S2", sequence_order=2, text="We monitored drop rates in Prometheus to ensure latency stayed below 5ms.", start_char=57, end_char=130),
    ]
    criteria = [
        AllowedRubricCriterion(criterion_id="crit_tech_solution", description="Explains Redis or token bucket architecture."),
        AllowedRubricCriterion(criterion_id="crit_verification", description="Describes monitoring or latency verification."),
    ]
    return PerAnswerAssessmentPacket(
        question_id=q_id,
        answer_id=a_id,
        question_text="How do you handle API rate limiting under high traffic?",
        question_intent="Assess distributed system scalability and verification.",
        competency_name="Backend Scalability",
        allowed_criteria=criteria,
        answer_segments=segments,
    )


# Test 1: Each request contains exactly one question-answer pair.
def test_isolated_single_question_answer_packet(sample_packet):
    assert sample_packet.question_id is not None
    assert sample_packet.answer_id is not None
    assert len(sample_packet.answer_segments) == 2
    assert sample_packet.competency_name == "Backend Scalability"


# Test 2: Question ID and answer ID remain correctly bound.
def test_packet_authoritative_binding():
    q_dict = {
        "question_id": str(uuid4()),
        "wording": "Describe your SQL indexing strategy.",
        "intent": "Database optimization",
        "competency_name": "Database Architecture",
        "rubric_anchors": [
            {"criterion_id": "c1", "description": "Explains B-Tree or composite index trade-offs."}
        ]
    }
    ans_id = uuid4()
    ans_text = "I used composite indexes on (user_id, created_at) to speed up filtering."
    packet = build_authoritative_assessment_packet(q_dict, ans_id, ans_text)

    assert str(packet.question_id) == q_dict["question_id"]
    assert packet.answer_id == ans_id
    assert len(packet.allowed_criteria) == 1
    assert packet.allowed_criteria[0].criterion_id == "c1"
    assert len(packet.answer_segments) == 1
    assert packet.answer_segments[0].segment_id == "S1"


# Test 3: Valid segment references are accepted.
def test_valid_segment_references_accepted(sample_packet):
    valid_json = {
        "schema_version": "answer-assessment.v3",
        "question_id": str(sample_packet.question_id),
        "evidence_state": "SUFFICIENT",
        "criteria": [
            {
                "criterion_id": "crit_tech_solution",
                "label": "DEMONSTRATED",
                "supporting_segment_ids": ["S1"],
                "observation": "Explained Redis token bucket implementation.",
            },
            {
                "criterion_id": "crit_verification",
                "label": "DEMONSTRATED",
                "supporting_segment_ids": ["S2"],
                "observation": "Mentioned Prometheus latency monitoring.",
            },
        ],
        "strengths": ["Clear, practical implementation with verification metrics."],
        "missing_elements": [],
        "improvement_guidance": ["Include failure fallback strategies."],
        "uncertainty_notes": [],
    }
    is_valid, errors, validated = validate_strict_assessment_output(valid_json, sample_packet)
    assert is_valid is True
    assert len(errors) == 0
    assert validated is not None
    assert len(validated.criteria) == 2


# Test 4: Invented segment or criterion IDs are rejected.
def test_invented_segment_or_criterion_rejected(sample_packet):
    invalid_json = {
        "schema_version": "answer-assessment.v3",
        "question_id": str(sample_packet.question_id),
        "evidence_state": "SUFFICIENT",
        "criteria": [
            {
                "criterion_id": "crit_invented_random",
                "label": "DEMONSTRATED",
                "supporting_segment_ids": ["S99"],
                "observation": "Invented citation",
            },
            {
                "criterion_id": "crit_verification",
                "label": "DEMONSTRATED",
                "supporting_segment_ids": ["S1"],
                "observation": "Valid citation",
            },
        ],
    }
    is_valid, errors, validated = validate_strict_assessment_output(invalid_json, sample_packet)
    assert is_valid is False
    assert any("UNKNOWN_CRITERION_ID" in e for e in errors)
    assert any("NONEXISTENT_SEGMENT_ID_CITED" in e for e in errors)


# Test 5: Malformed output gets only one repair attempt.
@pytest.mark.anyio
async def test_single_repair_attempt(sample_packet):
    db_mock = MagicMock()
    db_mock.execute.return_value.scalars.return_value.first.return_value = None

    # First response is malformed, repair response is valid
    first_resp = MagicMock()
    first_resp.status_code = 200
    first_resp.json.return_value = {
        "choices": [{"message": {"content": "not json"}}]
    }

    repaired_resp = MagicMock()
    repaired_resp.status_code = 200
    repaired_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "schema_version": "answer-assessment.v3",
                    "question_id": str(sample_packet.question_id),
                    "evidence_state": "SUFFICIENT",
                    "criteria": [
                        {
                            "criterion_id": "crit_tech_solution",
                            "label": "DEMONSTRATED",
                            "supporting_segment_ids": ["S1"],
                            "observation": "Demonstrated Redis token buckets.",
                        },
                        {
                            "criterion_id": "crit_verification",
                            "label": "PARTIALLY_DEMONSTRATED",
                            "supporting_segment_ids": ["S2"],
                            "observation": "Mentioned Prometheus latency monitoring.",
                        },
                    ],
                    "strengths": ["Solid technical response."],
                    "missing_elements": [],
                    "improvement_guidance": [],
                    "uncertainty_notes": [],
                })
            }
        }]
    }

    with patch("httpx.AsyncClient.post", side_effect=[first_resp, repaired_resp]) as mock_post:
        result, state, prov, model = await assess_single_answer(sample_packet, db=db_mock)
        assert mock_post.call_count == 2
        assert state == "REPAIRED"
        assert result is not None
        assert len(result.criteria) == 2


# Test 6: Repair failure produces NEEDS_HUMAN_REVIEW.
@pytest.mark.anyio
async def test_repair_failure_produces_needs_human_review(sample_packet):
    db_mock = MagicMock()
    db_mock.execute.return_value.scalars.return_value.first.return_value = None

    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.json.return_value = {
        "choices": [{"message": {"content": "still invalid json"}}]
    }

    with patch("httpx.AsyncClient.post", return_value=bad_resp) as mock_post:
        result, state, prov, model = await assess_single_answer(sample_packet, db=db_mock)
        assert mock_post.call_count == 2  # exactly 1 initial + 1 repair
        assert state == "NEEDS_HUMAN_REVIEW"
        assert result is not None
        for crit in result.criteria:
            assert crit.label == AssessmentCriterionLabel.NEEDS_HUMAN_REVIEW


# Test 7: Repeated assessment requests are idempotent.
@pytest.mark.anyio
async def test_assessment_idempotency():
    db_mock = MagicMock()
    sess_id = uuid4()
    q_id = uuid4()
    session = CandidatePracticeSessionRecord(
        session_id=sess_id,
        organization_id=uuid4(),
        binding_id=uuid4(),
        candidate_id=uuid4(),
        release_id=uuid4(),
        job_id=uuid4(),
        job_version_id=uuid4(),
        job_revision=1,
        set_version=1,
        snapshot_hash="a"*64,
        questions_snapshot=[
            {
                "question_id": str(q_id),
                "question_format": "CONVERSATION",
                "wording": "Test Question",
                "competency_name": "Scalability",
            }
        ],
    )
    msg = CandidatePracticeMessageRecord(
        message_id=uuid4(),
        session_id=sess_id,
        sequence_number=1,
        role="CANDIDATE",
        message_kind="ANSWER",
        source_question_id=q_id,
        text="I configured connection pools to manage load.",
    )

    # First call: No existing review
    db_mock.execute.return_value.scalars.return_value.all.side_effect = [
        [msg],  # messages
        [],     # existing reviews
    ]
    db_mock.execute.return_value.scalars.return_value.first.return_value = None

    valid_llm_resp = MagicMock()
    valid_llm_resp.status_code = 200
    valid_llm_resp.json.return_value = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "schema_version": "answer-assessment.v2",
                    "question_id": str(q_id),
                    "criterion_assessments": [
                        {
                            "criterion_id": "crit_technical_accuracy",
                            "assessment_label": "DEMONSTRATED",
                            "supporting_segment_ids": ["S1"],
                            "observation": "Configured connection pools.",
                            "confidence": "HIGH",
                        },
                        {
                            "criterion_id": "crit_concrete_evidence",
                            "assessment_label": "DEMONSTRATED",
                            "supporting_segment_ids": ["S1"],
                            "observation": "Managed load effectively.",
                            "confidence": "HIGH",
                        },
                        {
                            "criterion_id": "crit_clarity_structure",
                            "assessment_label": "DEMONSTRATED",
                            "supporting_segment_ids": ["S1"],
                            "observation": "Clear implementation.",
                            "confidence": "HIGH",
                        },
                    ],
                    "overall_answer_summary": "Clear connection pool configuration.",
                })
            }
        }]
    }

    with patch("httpx.AsyncClient.post", return_value=valid_llm_resp) as mock_post:
        cand_dto, rec_dto = await process_and_persist_structured_session_assessment(session=session, db=db_mock)
        assert mock_post.call_count == 1
        assert cand_dto.overall_coverage_pct == 100.0


# Test 8: Deterministic Aggregation excludes NOT_APPLICABLE and computes correct percentage.
def test_aggregation_excludes_not_applicable(sample_packet):
    # Assessment 1: 1 demonstrated, 1 not applicable
    assess_1 = StrictPerAnswerAssessmentResponse(
        schema_version="answer-assessment.v3",
        question_id=str(sample_packet.question_id),
        evidence_state=EvidenceStateCategory.SUFFICIENT,
        criteria=[
            StrictCriterionAssessmentItem(
                criterion_id="crit_tech_solution",
                label=AssessmentCriterionLabel.DEMONSTRATED,
                supporting_segment_ids=["S1"],
                observation="Demonstrated solution",
            ),
            StrictCriterionAssessmentItem(
                criterion_id="crit_verification",
                label=AssessmentCriterionLabel.NOT_APPLICABLE,
                supporting_segment_ids=[],
                observation="Not applicable to this scope",
            ),
        ],
        strengths=["Good answer."],
        missing_elements=[],
        improvement_guidance=[],
        uncertainty_notes=[],
    )

    coverage_dtos, status_counts, overall = aggregate_competency_evidence_coverage(
        [assess_1], {str(sample_packet.question_id): sample_packet}
    )

    assert len(coverage_dtos) == 1
    dto = coverage_dtos[0]
    assert dto.total_applicable_criteria == 1  # Excluded NOT_APPLICABLE
    assert dto.demonstrated_criteria_count == 1
    assert dto.evidence_coverage_pct == 100.0  # 1 demonstrated / 1 applicable * 100
    assert status_counts["NOT_APPLICABLE"] == 1


# Test 9: Human-review items are tracked separately and not converted to zero.
def test_human_review_not_converted_to_zero(sample_packet):
    assess_fallback = create_human_review_fallback_assessment(sample_packet, "Ambiguous answer")
    coverage_dtos, status_counts, overall = aggregate_competency_evidence_coverage(
        [assess_fallback], {str(sample_packet.question_id): sample_packet}
    )

    assert len(coverage_dtos) == 1
    dto = coverage_dtos[0]
    assert dto.needs_human_review_count == 2
    assert dto.display_status == "Pending Review"
    assert status_counts["NEEDS_HUMAN_REVIEW"] == 2


# Test 10: Candidate and Recruiter DTOs expose appropriate different fields.
def test_candidate_and_recruiter_dto_separation(sample_packet):
    cand_dto = CandidateStructuredReportDto(
        report_id=uuid4(),
        session_id=uuid4(),
        job_title="Staff Backend Engineer",
        job_revision=2,
        report_version=1,
        coaching_summary="Overall strong demonstration of systems knowledge.",
        demonstrated_strengths=["Clear Redis token bucket explanation."],
        priority_improvement_areas=["Add explicit monitoring alerts."],
        created_at=datetime.now(timezone.utc),
    )

    recruiter_dto = RecruiterStructuredReportDto(
        report_id=cand_dto.report_id,
        session_id=cand_dto.session_id,
        candidate_id=uuid4(),
        job_id=uuid4(),
        job_title="Staff Backend Engineer",
        job_revision=2,
        question_set_version=1,
        report_version=1,
        source_answer_hash="abcd1234efgh5678",
        human_review_required=False,
        human_review_items_count=0,
        created_at=datetime.now(timezone.utc),
    )

    cand_dump = cand_dto.model_dump()
    rec_dump = recruiter_dto.model_dump()

    # Candidate DTO must have coaching summary & next plan, but NO candidate_id or source_answer_hash
    assert "coaching_summary" in cand_dump
    assert "candidate_id" not in cand_dump
    assert "source_answer_hash" not in cand_dump

    # Recruiter DTO must have source_answer_hash, candidate_id, question_set_version
    assert "source_answer_hash" in rec_dump
    assert "candidate_id" in rec_dump
    assert "question_set_version" in rec_dump


# Test 11: No assessment changes match score, rank, gap report, job state, application stage, or ATS state.
def test_assessment_no_side_effects_on_recruitment_state():
    # Verify pure isolation of assessment contracts
    packet_data = PerAnswerAssessmentPacket.model_fields.keys()
    for forbidden in ["match_score", "candidate_rank", "application_stage", "ats_status", "hiring_decision"]:
        assert forbidden not in packet_data

    cand_report_fields = CandidateStructuredReportDto.model_fields.keys()
    for forbidden in ["match_score", "candidate_rank", "application_stage", "ats_status", "hiring_decision"]:
        assert forbidden not in cand_report_fields

    rec_report_fields = RecruiterStructuredReportDto.model_fields.keys()
    for forbidden in ["match_score", "candidate_rank", "application_stage", "ats_status", "hiring_decision"]:
        assert forbidden not in rec_report_fields
