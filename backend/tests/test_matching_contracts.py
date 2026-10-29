import uuid
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.models.job import (
    JobLifecycleStatus,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.schemas.matching import (
    MATCH_INPUT_SCHEMA_VERSION,
    MATCHER_ALGORITHM_VERSION,
    PROHIBITED_IDENTITY_KEYS,
    CandidateCertificationFeature,
    CandidateEducationFeature,
    CandidateEmploymentRoleFeature,
    CandidateFeatureView,
    CandidateLanguageFeature,
    CandidateProjectFeature,
    CandidateSkillFeature,
    EvidenceReferenceFeature,
    FrozenRequisitionMatchingView,
    MatchInputSnapshot,
    RequirementMatchingItem,
    verify_no_prohibited_keys,
)


def _make_valid_candidate_view() -> CandidateFeatureView:
    return CandidateFeatureView(
        profile_id=uuid.uuid4(),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        headline="Senior Systems Architect",
        skills=[
            CandidateSkillFeature(
                name="Python",
                normalized_name="python",
                confidence=0.95,
                confidence_band="HIGH",
                evidence_ids=["ev-1"],
                review_status="CONFIRMED",
            )
        ],
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Software Engineer",
                organization="Acme Tech",
                start_date="2020-01",
                end_date="2022-12",
                descriptions=["Designed backend pipelines."],
                evidence_ids=["ev-2"],
                confidence=0.92,
                confidence_band="HIGH",
            )
        ],
        education=[
            CandidateEducationFeature(
                degree="Bachelor of Science",
                field_of_study="Computer Science",
                institution="State University",
                start_date="2016-09",
                end_date="2020-05",
                grade="3.8 GPA",
                evidence_ids=["ev-3"],
                confidence=0.98,
                confidence_band="HIGH",
            )
        ],
        projects=[
            CandidateProjectFeature(
                name="Distributed Cache",
                description="In-memory key-value cache engine.",
                technologies=["Go", "Raft"],
                evidence_ids=["ev-4"],
                confidence=0.90,
                confidence_band="HIGH",
            )
        ],
        certifications=[
            CandidateCertificationFeature(
                name="AWS Solutions Architect",
                issuer="Amazon Web Services",
                issued_date="2021-06",
                expiry_date="2024-06",
                credential_id="AWS-12345",
                evidence_ids=["ev-5"],
                confidence=0.99,
                confidence_band="HIGH",
            )
        ],
        languages=[
            CandidateLanguageFeature(
                language="English",
                normalized_language="english",
                proficiency="Native / Bilingual",
                evidence_ids=["ev-6"],
                confidence=0.95,
                confidence_band="HIGH",
            )
        ],
        evidence_references=[
            EvidenceReferenceFeature(
                evidence_id="ev-1",
                page_number=1,
                block_number=2,
                character_start=10,
                character_end=50,
                extraction_method="DIRECT_TEXT",
            )
        ],
        review_required=False,
        warning_metadata=[],
    )


def _make_valid_requisition_view() -> FrozenRequisitionMatchingView:
    return FrozenRequisitionMatchingView(
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        version_number=1,
        revision=1,
        lifecycle_status=JobLifecycleStatus.ACTIVE,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        owner_user_id=uuid.uuid4(),
        title="Senior Python Backend Engineer",
        occupation_concept_id=uuid.uuid4(),
        occupation_title="Software Developers",
        occupation_soc_code="15-1252.00",
        taxonomy_version="31.0",
        requirements=[
            RequirementMatchingItem(
                requirement_id=uuid.uuid4(),
                display_order=0,
                category=RequirementCategory.SKILL,
                label="Python Programming",
                normalized_label="python",
                necessity=RequirementNecessity.MUST_HAVE,
                priority=RequirementPriority.CRITICAL,
                approved_weight=3.0,
                min_experience_months=36,
                recency_years=3,
                accepted_evidence_types=["EMPLOYMENT", "PROJECT"],
                accepted_equivalents=["Python 3", "CPython"],
                rationale="Core backend infrastructure is written in Python.",
                origin=RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED,
                taxonomy_skill_concept_id=uuid.uuid4(),
                taxonomy_canonical_label="Python",
                taxonomy_external_id="1234",
                taxonomy_source="ONET",
                taxonomy_source_version="31.0",
                review_required=False,
                warning_metadata=[],
            )
        ],
    )


def test_candidate_feature_view_includes_approved_professional_fields():
    """1. Candidate feature view includes approved professional fields."""
    view = _make_valid_candidate_view()
    assert view.headline == "Senior Systems Architect"
    assert len(view.skills) == 1
    assert view.skills[0].name == "Python"
    assert view.skills[0].normalized_name == "python"
    assert len(view.employment) == 1
    assert view.employment[0].job_title == "Software Engineer"
    assert view.employment[0].organization == "Acme Tech"
    assert len(view.education) == 1
    assert view.education[0].degree == "Bachelor of Science"
    assert len(view.projects) == 1
    assert view.projects[0].name == "Distributed Cache"
    assert len(view.certifications) == 1
    assert view.certifications[0].name == "AWS Solutions Architect"
    assert len(view.languages) == 1
    assert view.languages[0].language == "English"
    assert len(view.evidence_references) == 1
    assert view.evidence_references[0].evidence_id == "ev-1"


def test_candidate_feature_view_excludes_identity_and_contact_fields():
    """2. Candidate feature view excludes identity and contact fields."""
    view = _make_valid_candidate_view()
    serialized = view.model_dump(mode="json")

    # Positive check: none of the standard identity keys exist at top level
    for forbidden in ["full_name", "emails", "phone_numbers", "linkedin", "github", "portfolio", "location", "address", "identity_id"]:
        assert forbidden not in serialized


def test_recursive_serialized_output_contains_no_prohibited_keys():
    """3. Recursive serialized output contains none of the prohibited keys."""
    view = _make_valid_candidate_view()
    serialized = view.model_dump(mode="json")
    # This must not raise
    verify_no_prohibited_keys(serialized)

    # Injecting a prohibited key anywhere must trigger a ValueError
    dirty_dict = {"profile_id": str(uuid.uuid4()), "skills": [{"name": "Python", "full_name": "John Doe"}]}
    with pytest.raises(ValueError, match="Prohibited identity or protected key 'full_name'"):
        verify_no_prohibited_keys(dirty_dict)

    dirty_dict_email = {"profile_id": str(uuid.uuid4()), "email": "candidate@example.com"}
    with pytest.raises(ValueError, match="Prohibited identity or protected key 'email'"):
        verify_no_prohibited_keys(dirty_dict_email)

    dirty_dict_path = {"profile_id": str(uuid.uuid4()), "storage_path": "/var/data/resume.pdf"}
    with pytest.raises(ValueError, match="Prohibited identity or protected key 'storage_path'"):
        verify_no_prohibited_keys(dirty_dict_path)


def test_unexpected_fields_are_rejected_where_strict_schemas_apply():
    """4. Unexpected fields are rejected where strict schemas apply."""
    valid_kwargs = _make_valid_candidate_view().model_dump()
    valid_kwargs["unexpected_field"] = "malicious_payload"

    with pytest.raises(ValidationError):
        CandidateFeatureView(**valid_kwargs)

    req_kwargs = _make_valid_requisition_view().requirements[0].model_dump()
    req_kwargs["extra_unknown_field"] = 123
    with pytest.raises(ValidationError):
        RequirementMatchingItem(**req_kwargs)


def test_matching_input_requires_explicit_candidate_profile_version():
    """5. Matching input requires an explicit candidate profile version."""
    from app.services.matching_preflight import ExplicitProfileVersionRequiredError, prepare_match_input_snapshot

    class DummyActor:
        role = "RECRUITER"

    with pytest.raises(ExplicitProfileVersionRequiredError):
        prepare_match_input_snapshot(
            actor=DummyActor(),
            job_id=uuid.uuid4(),
            expected_revision=1,
            profile_version=None,
            artifact_id=uuid.uuid4(),
            db=None,
        )


def test_matching_input_requires_exact_requisition_revision():
    """6. Matching input requires an exact requisition revision."""
    from app.services.matching_preflight import ExplicitRevisionRequiredError, prepare_match_input_snapshot

    class DummyActor:
        role = "RECRUITER"

    with pytest.raises(ExplicitRevisionRequiredError):
        prepare_match_input_snapshot(
            actor=DummyActor(),
            job_id=uuid.uuid4(),
            expected_revision=None,
            profile_version=1,
            artifact_id=uuid.uuid4(),
            db=None,
        )


def test_frozen_contracts_cannot_be_mutated():
    """7. Frozen contracts cannot be mutated."""
    candidate_view = _make_valid_candidate_view()
    req_view = _make_valid_requisition_view()
    snapshot = MatchInputSnapshot(
        snapshot_created_at=datetime.now(timezone.utc),
        requisition_view=req_view,
        candidate_view=candidate_view,
    )

    with pytest.raises(ValidationError):
        candidate_view.headline = "Modified Headline"

    with pytest.raises(ValidationError):
        req_view.title = "Modified Title"

    with pytest.raises(ValidationError):
        snapshot.matcher_algorithm_version = "2.0.0"


def test_requirement_order_is_stable():
    """8. Requirement order is stable."""
    r1 = RequirementMatchingItem(
        requirement_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="First Requirement",
        normalized_label="first requirement",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        rationale="Essential foundational requirement.",
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    r2 = RequirementMatchingItem(
        requirement_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        display_order=1,
        category=RequirementCategory.EXPERIENCE,
        label="Second Requirement",
        normalized_label="second requirement",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.MEDIUM,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )

    view = FrozenRequisitionMatchingView(
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        version_number=1,
        revision=1,
        lifecycle_status=JobLifecycleStatus.ACTIVE,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        owner_user_id=uuid.uuid4(),
        title="Title",
        requirements=[r1, r2],
    )
    assert view.requirements[0].requirement_id == r1.requirement_id
    assert view.requirements[1].requirement_id == r2.requirement_id


def test_contract_and_source_version_fields_are_retained():
    """9. Contract and source version fields are retained."""
    candidate_view = _make_valid_candidate_view()
    req_view = _make_valid_requisition_view()
    snapshot = MatchInputSnapshot(
        snapshot_created_at=datetime.now(timezone.utc),
        requisition_view=req_view,
        candidate_view=candidate_view,
    )

    assert snapshot.match_input_schema_version == MATCH_INPUT_SCHEMA_VERSION
    assert snapshot.matcher_algorithm_version == MATCHER_ALGORITHM_VERSION
    assert req_view.taxonomy_version == "31.0"
    assert req_view.requirements[0].taxonomy_source == "ONET"
    assert req_view.requirements[0].taxonomy_source_version == "31.0"
    assert candidate_view.schema_version == "1.0"
    assert candidate_view.parser_pipeline_version == "1.0.0"
