import pytest
from pydantic import ValidationError

from app.schemas.candidate import (
    CandidateIdentity,
    CandidateProfessionalProfile,
    CandidateProfileV1,
    ConfidenceBand,
    EvidenceSpan,
    ExtractedField,
    ReviewStatus,
    StrictSchema,
)


def test_high_confidence_at_0_90():
    field = ExtractedField(
        field_name="full_name",
        value="Jane Doe",
        confidence=0.90,
        evidence_ids=["ev-1"],
    )
    assert field.confidence_band == ConfidenceBand.HIGH


def test_medium_confidence_at_0_89_and_0_70():
    field_89 = ExtractedField(
        field_name="full_name",
        value="Jane Doe",
        confidence=0.89,
        evidence_ids=["ev-1"],
    )
    assert field_89.confidence_band == ConfidenceBand.MEDIUM

    field_70 = ExtractedField(
        field_name="full_name",
        value="Jane Doe",
        confidence=0.70,
        evidence_ids=["ev-1"],
    )
    assert field_70.confidence_band == ConfidenceBand.MEDIUM


def test_low_confidence_below_0_70():
    field = ExtractedField(
        field_name="full_name",
        value="Jane Doe",
        confidence=0.69,
        evidence_ids=["ev-1"],
    )
    assert field.confidence_band == ConfidenceBand.LOW


def test_null_value_producing_unknown():
    field = ExtractedField(
        field_name="full_name",
        value=None,
    )
    assert field.confidence_band == ConfidenceBand.UNKNOWN
    assert field.confidence is None
    assert field.evidence_ids == []


def test_rejection_of_known_value_without_evidence():
    with pytest.raises(ValidationError) as exc_info:
        ExtractedField(
            field_name="full_name",
            value="Jane Doe",
            confidence=0.95,
            evidence_ids=[],
        )
    assert "at least one evidence ID is required" in str(exc_info.value)


def test_rejection_of_null_value_with_confidence_or_evidence():
    with pytest.raises(ValidationError) as exc_info_conf:
        ExtractedField(
            field_name="full_name",
            value=None,
            confidence=0.85,
        )
    assert "confidence must be null" in str(exc_info_conf.value)

    with pytest.raises(ValidationError) as exc_info_ev:
        ExtractedField(
            field_name="full_name",
            value=None,
            evidence_ids=["ev-1"],
        )
    assert "evidence_ids must be empty" in str(exc_info_ev.value)


def test_rejection_of_confidence_outside_0_to_1():
    with pytest.raises(ValidationError):
        ExtractedField(
            field_name="full_name",
            value="Jane Doe",
            confidence=1.05,
            evidence_ids=["ev-1"],
        )

    with pytest.raises(ValidationError):
        ExtractedField(
            field_name="full_name",
            value="Jane Doe",
            confidence=-0.1,
            evidence_ids=["ev-1"],
        )


def test_evidence_span_requiring_page_or_block_location():
    with pytest.raises(ValidationError) as exc_info:
        EvidenceSpan(
            evidence_id="ev-1",
            resume_artifact_id="art-1",
            page_number=None,
            block_number=None,
            character_start=0,
            character_end=10,
            original_text="Jane Doe",
            extraction_method="pdfplumber",
        )
    assert "At least one of page_number or block_number must be present" in str(exc_info.value)

    # Valid with page_number only
    span_page = EvidenceSpan(
        evidence_id="ev-1",
        resume_artifact_id="art-1",
        page_number=1,
        character_start=0,
        character_end=10,
        original_text="Jane Doe",
        extraction_method="pdfplumber",
    )
    assert span_page.page_number == 1
    assert span_page.block_number is None

    # Valid with block_number only
    span_block = EvidenceSpan(
        evidence_id="ev-2",
        resume_artifact_id="art-1",
        block_number=0,
        character_start=0,
        character_end=10,
        original_text="Jane Doe",
        extraction_method="docx_text",
    )
    assert span_block.page_number is None
    assert span_block.block_number == 0


def test_character_end_being_greater_than_character_start():
    with pytest.raises(ValidationError) as exc_info_equal:
        EvidenceSpan(
            evidence_id="ev-1",
            resume_artifact_id="art-1",
            page_number=1,
            character_start=5,
            character_end=5,
            original_text="Jane Doe",
            extraction_method="pdfplumber",
        )
    assert "character_end must be greater than character_start" in str(exc_info_equal.value)

    with pytest.raises(ValidationError) as exc_info_less:
        EvidenceSpan(
            evidence_id="ev-2",
            resume_artifact_id="art-1",
            page_number=1,
            character_start=5,
            character_end=4,
            original_text="Jane Doe",
            extraction_method="pdfplumber",
        )
    assert "character_end must be greater than character_start" in str(exc_info_less.value)


def test_rejection_of_unexpected_fields_because_extra_fields_are_forbidden():
    with pytest.raises(ValidationError) as exc_info:
        ExtractedField(
            field_name="full_name",
            value="Jane Doe",
            confidence=0.95,
            evidence_ids=["ev-1"],
            unexpected_field="disallowed",
        )
    assert "Extra inputs are not permitted" in str(exc_info.value)
