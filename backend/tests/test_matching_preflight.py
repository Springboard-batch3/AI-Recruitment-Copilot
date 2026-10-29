import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.auth import Base, UserRecord, UserRole
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.job import (
    JobDescriptionImport,
    JobImportSourceType,
    JobImportStatus,
    JobLifecycleStatus,
    JobRequirement,
    JobRequisition,
    JobVersion,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.models.taxonomy import (
    OccupationConcept,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.schemas.matching import verify_no_prohibited_keys
from app.services.matching_preflight import (
    CandidateProfileNotFoundError,
    EmptyRequirementCollectionError,
    ExplicitProfileVersionRequiredError,
    ExplicitRevisionRequiredError,
    InvalidLocalTaxonomyReferenceError,
    InvalidRequirementDataError,
    RequisitionNotFoundError,
    RequisitionNotReadyError,
    UnauthorizedPreflightError,
    prepare_match_input_snapshot,
)


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def active_taxonomy(test_db):
    snapshot = TaxonomySnapshot(
        snapshot_id=uuid.uuid4(),
        source="ONET",
        source_version="31.0",
        combined_sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        state=TaxonomySnapshotState.ACTIVE,
    )
    test_db.add(snapshot)
    test_db.flush()

    occ = OccupationConcept(
        concept_id=uuid.uuid4(),
        snapshot_id=snapshot.snapshot_id,
        onet_soc_code="15-1252.00",
        canonical_title="Software Developers",
        normalized_title="software developers",
    )
    skill = SkillConcept(
        concept_id=uuid.uuid4(),
        snapshot_id=snapshot.snapshot_id,
        canonical_label="Python",
        normalized_label="python",
        category="Technology Skills",
        source="ONET_31_0",
        external_id="12345",
    )
    test_db.add_all([occ, skill])
    test_db.commit()
    return snapshot, occ, skill


def _seed_recruiter(test_db, email="recruiter@example.com") -> UserRecord:
    recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email=email,
        display_name="Test Recruiter",
        password_hash="mock_hash",
        role=UserRole.RECRUITER,
        is_active=True,
    )
    test_db.add(recruiter)
    test_db.commit()
    return recruiter


def _seed_candidate_profile(
    test_db,
    artifact_id: uuid.UUID,
    profile_version: int = 1,
    candidate_name: str = "Jane Doe",
    skills: list = None,
) -> CandidateProfileRecord:
    skills = skills or ["Python", "FastAPI"]
    identity_payload = {
        "fullName": {"value": candidate_name},
        "emails": [{"value": f"{candidate_name.lower().replace(' ', '.')}@example.com"}],
        "phoneNumbers": [{"value": "+1 555-0199"}],
        "location": {"value": "San Francisco, CA"},
    }
    ident = CandidateIdentityRecord(
        identity_id=uuid.uuid4(),
        artifact_id=artifact_id,
        full_name=candidate_name,
        primary_email=f"{candidate_name.lower().replace(' ', '.')}@example.com",
        primary_phone="+1 555-0199",
        identity_payload=identity_payload,
    )
    test_db.add(ident)
    test_db.flush()

    prof_payload = {
        "headline": {"value": "Senior Backend Developer"},
        "summary": {"value": "Experienced developer with strong architecture background."},
        "skills": [
            {
                "value": s,
                "normalizedValue": s.lower(),
                "confidence": 0.95,
                "confidenceBand": "HIGH",
                "evidenceIds": [f"ev-skill-{idx}"],
                "reviewStatus": "CONFIRMED",
            }
            for idx, s in enumerate(skills)
        ],
        "employment": [
            {
                "jobTitle": {"value": "Staff Engineer"},
                "organization": {"value": "Acme Inc"},
                "startDate": {"value": "2020-01"},
                "endDate": {"value": "2023-12"},
                "descriptions": [{"value": "Led architecture of core data platform."}],
            }
        ],
        "education": [
            {
                "degree": {"value": "B.S. in Computer Science"},
                "institution": {"value": "State University"},
            }
        ],
        "projects": [
            {
                "name": {"value": "High Throughput Queue"},
                "description": {"value": "Async processing library."},
                "technologies": [{"value": "Python"}],
                "projectUrl": {"value": "https://github.com/secret/repo"},  # Must NOT be included
            }
        ],
        "certifications": [
            {
                "name": {"value": "AWS Certified Solutions Architect"},
                "issuer": {"value": "AWS"},
                "credentialUrl": {"value": "https://aws.cert/123"},  # Must NOT be included
            }
        ],
        "languages": [
            {"value": "English", "proficiency": "Native"}
        ],
    }

    evidence_spans = [
        {
            "evidenceId": "ev-skill-0",
            "pageNumber": 1,
            "blockNumber": 2,
            "characterStart": 10,
            "characterEnd": 25,
            "originalText": "Python Programming",  # Full text must NOT be copied into locator
            "extractionMethod": "DIRECT_TEXT",
        }
    ]

    profile_rec = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=profile_version,
        artifact_id=artifact_id,
        extraction_id=uuid.uuid4(),
        identity_id=ident.identity_id,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        professional_profile=prof_payload,
        evidence_spans=evidence_spans,
        review_required=False,
        warning_metadata=[],
    )
    test_db.add(profile_rec)
    test_db.commit()
    return profile_rec


def _seed_requisition_with_version(
    test_db,
    owner_user_id: uuid.UUID,
    version_status: JobVersionStatus = JobVersionStatus.READY_FOR_MATCHING,
    revision: int = 1,
    taxonomy_skill_id: uuid.UUID = None,
    with_requirements: bool = True,
    is_custom_only: bool = False,
) -> tuple[JobRequisition, JobVersion]:
    job = JobRequisition(
        job_id=uuid.uuid4(),
        created_by_user_id=owner_user_id,
        status=JobLifecycleStatus.ACTIVE,
    )
    test_db.add(job)
    test_db.flush()

    version = JobVersion(
        job_version_id=uuid.uuid4(),
        job_id=job.job_id,
        version_number=1,
        revision=revision,
        version_status=version_status,
        title="Backend Software Engineer",
        created_by_user_id=owner_user_id,
        ready_by_user_id=owner_user_id if version_status == JobVersionStatus.READY_FOR_MATCHING else None,
        ready_at=datetime.now(timezone.utc) if version_status == JobVersionStatus.READY_FOR_MATCHING else None,
    )
    test_db.add(version)
    test_db.flush()

    if with_requirements:
        if is_custom_only or taxonomy_skill_id is None:
            req = JobRequirement(
                requirement_id=uuid.uuid4(),
                job_version_id=version.job_version_id,
                display_order=0,
                category=RequirementCategory.SKILL,
                label="Custom Proprietary Framework",
                normalized_label="custom proprietary framework",
                necessity=RequirementNecessity.MUST_HAVE,
                priority=RequirementPriority.HIGH,
                rationale="Required for maintaining proprietary core engine.",
                origin=RequirementOrigin.RECRUITER_ENTERED,
                taxonomy_skill_concept_id=None,
            )
            test_db.add(req)
        else:
            req1 = JobRequirement(
                requirement_id=uuid.uuid4(),
                job_version_id=version.job_version_id,
                display_order=0,
                category=RequirementCategory.SKILL,
                label="Python",
                normalized_label="python",
                necessity=RequirementNecessity.MUST_HAVE,
                priority=RequirementPriority.CRITICAL,
                rationale="Backend services written in Python.",
                origin=RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED,
                taxonomy_skill_concept_id=taxonomy_skill_id,
            )
            req2 = JobRequirement(
                requirement_id=uuid.uuid4(),
                job_version_id=version.job_version_id,
                display_order=1,
                category=RequirementCategory.EXPERIENCE,
                label="Distributed Systems",
                normalized_label="distributed systems",
                necessity=RequirementNecessity.PREFERRED,
                priority=RequirementPriority.MEDIUM,
                origin=RequirementOrigin.RECRUITER_ENTERED,
                taxonomy_skill_concept_id=None,
            )
            test_db.add_all([req1, req2])

    test_db.commit()
    test_db.refresh(job)
    test_db.refresh(version)
    return job, version


# 1. A recruiter can prepare inputs from their own READY_FOR_MATCHING revision
def test_recruiter_can_prepare_inputs_from_own_ready_revision(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, version = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    art_id = uuid.uuid4()
    profile = _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    assert snapshot is not None
    assert snapshot.requisition_view.job_version_id == version.job_version_id
    assert snapshot.requisition_view.revision == 1
    assert snapshot.requisition_view.version_status == JobVersionStatus.READY_FOR_MATCHING
    assert snapshot.candidate_view.profile_id == profile.profile_id
    assert snapshot.candidate_view.profile_version == 1
    assert len(snapshot.requisition_view.requirements) == 2


# 2. A draft revision is rejected
def test_draft_revision_is_rejected(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.DRAFT,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    with pytest.raises(RequisitionNotReadyError, match="status is DRAFT, expected READY_FOR_MATCHING"):
        prepare_match_input_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            profile_version=1,
            artifact_id=art_id,
            db=test_db,
        )


# 3. A nonexistent revision fails with a controlled error
def test_nonexistent_revision_fails_with_controlled_error(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    with pytest.raises(RequisitionNotFoundError, match="Requisition revision 99 not found"):
        prepare_match_input_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=99,
            profile_version=1,
            artifact_id=art_id,
            db=test_db,
        )


# 4. Another recruiter cannot prepare inputs from an inaccessible requisition
def test_another_recruiter_cannot_prepare_inputs_from_inaccessible_requisition(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter1 = _seed_recruiter(test_db, email="recruiter1@company.test")
    recruiter2 = _seed_recruiter(test_db, email="recruiter2@company.test")

    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter1.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    # Recruiter2 attempts to load recruiter1's requisition -> concealed as 404
    with pytest.raises(RequisitionNotFoundError, match="Requisition not found."):
        prepare_match_input_snapshot(
            actor=recruiter2,
            job_id=job.job_id,
            expected_revision=1,
            profile_version=1,
            artifact_id=art_id,
            db=test_db,
        )


# 5. An empty requirement collection is rejected
def test_empty_requirement_collection_is_rejected(test_db):
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        with_requirements=False,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    with pytest.raises(EmptyRequirementCollectionError, match="no structured requirements"):
        prepare_match_input_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            profile_version=1,
            artifact_id=art_id,
            db=test_db,
        )


# 6. A custom-only ready requisition is accepted
def test_custom_only_ready_requisition_is_accepted(test_db):
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        is_custom_only=True,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    assert snapshot is not None
    req_item = snapshot.requisition_view.requirements[0]
    assert req_item.label == "Custom Proprietary Framework"
    assert req_item.origin == RequirementOrigin.RECRUITER_ENTERED
    assert req_item.taxonomy_skill_concept_id is None
    assert req_item.taxonomy_canonical_label is None


# 7. A valid local O*NET reference is preserved with its snapshot version
def test_valid_local_onet_reference_preserved_with_snapshot_version(test_db, active_taxonomy):
    snapshot, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    match_snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    req1 = match_snapshot.requisition_view.requirements[0]
    assert req1.taxonomy_skill_concept_id == skill.concept_id
    assert req1.taxonomy_canonical_label == "Python"
    assert req1.taxonomy_source == "ONET"
    assert req1.taxonomy_source_version == "31.0"


# 8. A missing or inconsistent O*NET reference is rejected
def test_missing_or_inconsistent_onet_reference_is_rejected(test_db):
    recruiter = _seed_recruiter(test_db)
    fake_skill_id = uuid.uuid4()

    job, version = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        with_requirements=False,
    )
    # Add requirement pointing to nonexistent skill concept in active snapshot
    bad_req = JobRequirement(
        requirement_id=uuid.uuid4(),
        job_version_id=version.job_version_id,
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Ghost Skill",
        normalized_label="ghost skill",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        rationale="Business rationale.",
        origin=RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED,
        taxonomy_skill_concept_id=fake_skill_id,
    )
    test_db.add(bad_req)
    test_db.commit()

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    with pytest.raises(InvalidLocalTaxonomyReferenceError):
        prepare_match_input_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            profile_version=1,
            artifact_id=art_id,
            db=test_db,
        )


# 9. Pending/rejected import suggestions are not included
def test_pending_rejected_import_suggestions_not_included(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, version = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )

    # Add an import session with pending and rejected suggestions
    import_session = JobDescriptionImport(
        session_id=uuid.uuid4(),
        job_id=job.job_id,
        job_version_id=version.job_version_id,
        expected_revision=1,
        created_by_user_id=recruiter.user_id,
        source_type=JobImportSourceType.PASTED_TEXT,
        source_text="Senior Kubernetes Architect with 10 years experience",
        parser_version="1.0.0",
        status=JobImportStatus.PENDING_REVIEW,
        suggestions=[
            {"suggestion_id": "sug-1", "target_field": "title", "proposed_value": "Kubernetes Architect", "review_status": "PENDING"},
            {"suggestion_id": "sug-2", "target_field": "skill", "proposed_value": "Kubernetes", "review_status": "REJECTED"},
        ],
    )
    test_db.add(import_session)
    test_db.commit()

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    # Requirements must only contain confirmed requirements, not the suggestions
    labels = [r.label for r in snapshot.requisition_view.requirements]
    assert "Kubernetes" not in labels
    assert "Kubernetes Architect" not in labels
    assert snapshot.requisition_view.title == "Backend Software Engineer"


# 10. Candidate profile version 1 and version 2 remain separately addressable
def test_candidate_profile_v1_and_v2_separately_addressable(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1, skills=["Python"])
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=2, skills=["Python", "FastAPI", "Rust"])

    # Load version 1
    snap1 = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )
    assert snap1.candidate_view.profile_version == 1
    assert len(snap1.candidate_view.skills) == 1
    assert snap1.candidate_view.skills[0].name == "Python"

    # Load version 2
    snap2 = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=2,
        artifact_id=art_id,
        db=test_db,
    )
    assert snap2.candidate_view.profile_version == 2
    assert len(snap2.candidate_view.skills) == 3


# 11. Requesting one exact version never silently returns the latest version
def test_requesting_exact_version_never_returns_latest_silently(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=2)

    # Requesting nonexistent version 3 must raise 404 CandidateProfileNotFoundError, NOT return v2
    with pytest.raises(CandidateProfileNotFoundError, match="Candidate profile version 3 not found"):
        prepare_match_input_snapshot(
            actor=recruiter,
            job_id=job.job_id,
            expected_revision=1,
            profile_version=3,
            artifact_id=art_id,
            db=test_db,
        )


# 12. Identity modifications do not alter the professional feature projection
def test_identity_modifications_do_not_alter_professional_projection(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )

    art_id = uuid.uuid4()
    prof = _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1, candidate_name="Alice Smith")

    snap1 = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    # Modify the identity record in database
    ident = prof.identity
    ident.full_name = "Bob Jones"
    ident.primary_email = "bob.jones@evil.com"
    ident.primary_phone = "+1 800-000-0000"
    test_db.commit()

    snap2 = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    # Feature view must remain completely unchanged and devoid of identity names
    dict1 = snap1.candidate_view.model_dump(mode="json")
    dict2 = snap2.candidate_view.model_dump(mode="json")
    assert dict1 == dict2
    assert "Alice" not in str(dict2)
    assert "Bob" not in str(dict2)
    assert "evil.com" not in str(dict2)


# 13. Evidence IDs and target paths are preserved
def test_evidence_ids_and_target_paths_preserved(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, _ = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    assert len(snapshot.candidate_view.evidence_references) == 1
    ev_ref = snapshot.candidate_view.evidence_references[0]
    assert ev_ref.evidence_id == "ev-skill-0"
    assert ev_ref.page_number == 1
    assert ev_ref.block_number == 2
    assert ev_ref.character_start == 10
    assert ev_ref.character_end == 25
    assert ev_ref.extraction_method == "DIRECT_TEXT"


# 14. Raw resume and raw job-description text are absent
def test_raw_resume_and_raw_job_description_text_absent(test_db, active_taxonomy):
    _, _, skill = active_taxonomy
    recruiter = _seed_recruiter(test_db)
    job, version = _seed_requisition_with_version(
        test_db,
        owner_user_id=recruiter.user_id,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        revision=1,
        taxonomy_skill_id=skill.concept_id,
    )
    version.job_description_text = "CONFIDENTIAL INTERNAL JOB DESCRIPTION TEXT WITH SENSITIVE COMPANY DATA"
    test_db.commit()

    art_id = uuid.uuid4()
    _seed_candidate_profile(test_db, artifact_id=art_id, profile_version=1)

    snapshot = prepare_match_input_snapshot(
        actor=recruiter,
        job_id=job.job_id,
        expected_revision=1,
        profile_version=1,
        artifact_id=art_id,
        db=test_db,
    )

    serialized = snapshot.model_dump(mode="json")
    verify_no_prohibited_keys(serialized)

    serialized_str = str(serialized)
    assert "CONFIDENTIAL INTERNAL JOB DESCRIPTION" not in serialized_str
    assert "job_description_text" not in serialized_str
    assert "raw_text" not in serialized_str
    assert "storage_path" not in serialized_str
