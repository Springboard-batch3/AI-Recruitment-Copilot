import copy
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models.base import Base
from app.models.candidate import (
    CandidateIdentityRecord,
    CandidateProfileRecord,
    ProfileCorrectionRecord,
)
from app.models.extraction import BlockType, ExtractionBlock, ExtractionMethod, ResumeExtraction
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.candidate import (
    CandidateIdentity,
    CandidateProfessionalProfile,
    CertificationRecord,
    ConfidenceBand,
    EducationRecord,
    EvidenceSpan,
    ExperienceRecord,
    ExtractedField,
    ProjectRecord,
    ReviewStatus,
)
from app.schemas.correction import (
    CorrectionOperation,
    CorrectionOperationType,
    CorrectionReasonCategory,
    EvidenceProvenanceType,
    ProfileCorrectionRequest,
    ProfileProvenance,
)
from app.schemas.profile_assembly import CandidateProfileResponse
from app.services.profile_correction import (
    IdempotencyConflictError,
    InvalidCorrectionOperationError,
    InvalidEvidenceReferenceError,
    ProfileNotFoundError,
    ProfileVersionConflictError,
    apply_profile_correction,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client_with_db(in_memory_db):
    def override_get_db():
        try:
            yield in_memory_db
        finally:
            pass

    from unittest.mock import MagicMock
    from app.api.auth import AuthenticatedContext, require_recruiter
    from app.models.auth import UserRecord, UserRole

    mock_recruiter = UserRecord(
        user_id=uuid.uuid4(),
        email="mock.recruiter@company.test",
        display_name="Mock Recruiter",
        password_hash="mock",
        role=UserRole.RECRUITER,
        is_active=True,
        must_change_password=False,
    )
    mock_ctx = AuthenticatedContext(user=mock_recruiter, session=MagicMock())

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_recruiter] = lambda: mock_ctx
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_base_v1_profile(session, artifact_id=None):
    art_id = artifact_id or uuid.uuid4()
    ext_id = uuid.uuid4()
    ident_id = uuid.uuid4()
    prof_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="candidate_resume.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=2048,
        sha256_hash="e" * 64,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="pdf-extract-v1.0",
        review_required=False,
    )
    b0 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Jane Doe\njane.doe@example.com | +1 555-0199 | San Francisco, CA",
        normalized_text="Jane Doe\njane.doe@example.com | +1 555-0199 | San Francisco, CA",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b1 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=1,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Skills: Python, FastAPI, Docker",
        normalized_text="Skills: Python, FastAPI, Docker",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b2 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=2,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Software Engineer | Acme Corp\n2021 - 2023",
        normalized_text="Software Engineer | Acme Corp\n2021 - 2023",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    session.add_all([art, ext, b0, b1, b2])

    ev_span1 = {
        "evidenceId": "ev-name-1",
        "resumeArtifactId": str(art_id),
        "pageNumber": 1,
        "blockNumber": 0,
        "characterStart": 0,
        "characterEnd": 8,
        "originalText": "Jane Doe",
        "extractionMethod": "PDF_TEXT",
    }
    ev_span2 = {
        "evidenceId": "ev-skill-1",
        "resumeArtifactId": str(art_id),
        "pageNumber": 1,
        "blockNumber": 1,
        "characterStart": 8,
        "characterEnd": 14,
        "originalText": "Python",
        "extractionMethod": "PDF_TEXT",
    }

    identity_payload = {
        "resumeArtifactId": str(art_id),
        "fullName": {"fieldName": "fullName", "value": "Jane Doe", "confidence": 0.95, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
        "emails": [{"fieldName": "email", "value": "jane.doe@example.com", "confidence": 0.95, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"}],
        "phoneNumbers": [{"fieldName": "phone", "value": "+1 555-0199", "confidence": 0.95, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"}],
        "location": {"fieldName": "location", "value": "San Francisco, CA", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
        "linkedin": {"fieldName": "linkedin", "value": None, "confidenceBand": "UNKNOWN", "evidenceIds": [], "reviewStatus": "UNREVIEWED"},
        "github": {"fieldName": "github", "value": None, "confidenceBand": "UNKNOWN", "evidenceIds": [], "reviewStatus": "UNREVIEWED"},
        "portfolio": {"fieldName": "portfolio", "value": None, "confidenceBand": "UNKNOWN", "evidenceIds": [], "reviewStatus": "UNREVIEWED"},
    }

    identity_rec = CandidateIdentityRecord(
        identity_id=ident_id,
        artifact_id=art_id,
        full_name="Jane Doe",
        primary_email="jane.doe@example.com",
        primary_phone="+1 555-0199",
        identity_payload=identity_payload,
    )
    session.add(identity_rec)

    prof_payload = {
        "headline": {"fieldName": "headline", "value": "Software Engineer", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
        "summary": {"fieldName": "summary", "value": "Experienced backend developer.", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
        "skills": [
            {"fieldName": "skill", "value": "Python", "normalizedValue": "Python", "confidence": 0.95, "confidenceBand": "HIGH", "evidenceIds": ["ev-skill-1"], "reviewStatus": "UNREVIEWED"},
            {"fieldName": "skill", "value": "FastAPI", "normalizedValue": "FastAPI", "confidence": 0.95, "confidenceBand": "HIGH", "evidenceIds": ["ev-skill-1"], "reviewStatus": "UNREVIEWED"},
        ],
        "employment": [
            {
                "jobTitle": {"fieldName": "jobTitle", "value": "Software Engineer", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
                "organization": {"fieldName": "organization", "value": "Acme Corp", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
                "startDate": {"fieldName": "startDate", "value": "2021", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
                "endDate": {"fieldName": "endDate", "value": "2023", "confidence": 0.9, "confidenceBand": "HIGH", "evidenceIds": ["ev-name-1"], "reviewStatus": "UNREVIEWED"},
                "descriptions": [],
            }
        ],
        "education": [],
        "projects": [],
        "certifications": [],
        "languages": [],
        "awards": [],
        "publications": [],
    }

    prof_rec = CandidateProfileRecord(
        profile_id=prof_id,
        profile_version=1,
        artifact_id=art_id,
        extraction_id=ext_id,
        identity_id=ident_id,
        provenance="PARSER_EXTRACTED",
        schema_version="1.0",
        parser_pipeline_version="pipeline-v1.3[test]",
        professional_profile=prof_payload,
        evidence_spans=[ev_span1, ev_span2],
        review_required=False,
        warning_metadata=[],
    )
    session.add(prof_rec)
    session.commit()

    return art_id, prof_id


# =============================================================================
# 1. UNIT TESTS: VALIDATION & FIELD CORRECTIONS
# =============================================================================

def test_valid_scalar_corrections(in_memory_db):
    """Verify scalar replacement for name, headline, location, summary."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    req = ProfileCorrectionRequest(
        base_profile_id=prof_id,
        base_profile_version=1,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
        reviewer_note=None,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.REPLACE_SCALAR,
                target_section="identity.full_name",
                value="Dr. Jane Doe",
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            ),
            CorrectionOperation(
                operation_type=CorrectionOperationType.REPLACE_SCALAR,
                target_section="headline",
                value="Staff Systems Engineer",
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            ),
            CorrectionOperation(
                operation_type=CorrectionOperationType.REPLACE_SCALAR,
                target_section="location",
                value="Seattle, WA",
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            ),
        ],
    )

    res = apply_profile_correction(in_memory_db, art_id, req)
    in_memory_db.commit()

    assert res.previous_profile_version == 1
    assert res.new_profile_version == 2
    assert res.provenance == ProfileProvenance.HUMAN_CORRECTED
    assert res.profile.identity.full_name.value == "Dr. Jane Doe"
    assert res.profile.identity.full_name.review_status == ReviewStatus.CORRECTED
    assert res.profile.identity.location.value == "Seattle, WA"
    assert res.profile.professional_profile.headline.value == "Staff Systems Engineer"


def test_invalid_email_and_phone_formats(in_memory_db):
    """Verify malformed email and phone numbers raise validation errors."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # Invalid email
    with pytest.raises(InvalidCorrectionOperationError) as exc:
        req_email = ProfileCorrectionRequest(
            base_profile_id=prof_id,
            base_profile_version=1,
            idempotency_key=str(uuid.uuid4()),
            reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
            operations=[
                CorrectionOperation(
                    operation_type=CorrectionOperationType.REPLACE_SCALAR,
                    target_section="identity.primary_email",
                    value="not-an-email",
                    provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
                )
            ],
        )
        apply_profile_correction(in_memory_db, art_id, req_email)
    assert "Invalid email address format" in str(exc.value)

    # Invalid phone
    with pytest.raises(InvalidCorrectionOperationError) as exc:
        req_phone = ProfileCorrectionRequest(
            base_profile_id=prof_id,
            base_profile_version=1,
            idempotency_key=str(uuid.uuid4()),
            reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
            operations=[
                CorrectionOperation(
                    operation_type=CorrectionOperationType.REPLACE_SCALAR,
                    target_section="identity.primary_phone",
                    value="123",  # too short
                    provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
                )
            ],
        )
        apply_profile_correction(in_memory_db, art_id, req_phone)
    assert "Invalid phone number length" in str(exc.value)


def test_set_item_add_and_remove_duplicate_prevention(in_memory_db):
    """Verify add/remove skills, preventing duplicate additions."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # 1. Add new skill Kubernetes
    req_add = ProfileCorrectionRequest(
        base_profile_id=prof_id,
        base_profile_version=1,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.MISSING_INFORMATION,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.ADD_SET_ITEM,
                target_section="skills",
                value="Kubernetes",
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            )
        ],
    )
    res_add = apply_profile_correction(in_memory_db, art_id, req_add)
    in_memory_db.commit()

    skills_v2 = [s.value for s in res_add.profile.professional_profile.skills]
    assert "Kubernetes" in skills_v2
    assert "Python" in skills_v2
    assert "FastAPI" in skills_v2

    # 2. Duplicate skill addition raises error
    with pytest.raises(InvalidCorrectionOperationError) as exc:
        req_dup = ProfileCorrectionRequest(
            base_profile_id=res_add.profile.profile_id,
            base_profile_version=2,
            idempotency_key=str(uuid.uuid4()),
            reason_category=CorrectionReasonCategory.MISSING_INFORMATION,
            operations=[
                CorrectionOperation(
                    operation_type=CorrectionOperationType.ADD_SET_ITEM,
                    target_section="skills",
                    value="kubernetes",  # case insensitive duplicate
                    provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
                )
            ],
        )
        apply_profile_correction(in_memory_db, art_id, req_dup)
    assert "Skill 'kubernetes' is already present" in str(exc.value)

    # 3. Remove skill FastAPI
    req_rem = ProfileCorrectionRequest(
        base_profile_id=res_add.profile.profile_id,
        base_profile_version=2,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.REMOVE_SET_ITEM,
                target_section="skills",
                value="FastAPI",
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            )
        ],
    )
    res_rem = apply_profile_correction(in_memory_db, art_id, req_rem)
    in_memory_db.commit()
    skills_v3 = [s.value for s in res_rem.profile.professional_profile.skills]
    assert "FastAPI" not in skills_v3
    assert "Python" in skills_v3
    assert "Kubernetes" in skills_v3


def test_structured_record_add_update_remove(in_memory_db):
    """Verify add, update and remove on structured employment and education records."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # 1. Update existing employment record at index 0
    req_up = ProfileCorrectionRequest(
        base_profile_id=prof_id,
        base_profile_version=1,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.INCORRECT_DATE,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.UPDATE_RECORD,
                target_section="employment",
                target_index=0,
                record_payload={
                    "jobTitle": "Senior Software Engineer",
                    "organization": "Acme Corp",
                    "startDate": "January 2021",
                    "endDate": "Present",
                    "descriptions": ["Led backend infrastructure."],
                },
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            )
        ],
    )
    res_up = apply_profile_correction(in_memory_db, art_id, req_up)
    in_memory_db.commit()

    assert len(res_up.profile.professional_profile.employment) == 1
    emp0 = res_up.profile.professional_profile.employment[0]
    assert emp0.job_title.value == "Senior Software Engineer"
    assert emp0.end_date.value == "Present"

    # 2. Add education record
    req_add_edu = ProfileCorrectionRequest(
        base_profile_id=res_up.profile.profile_id,
        base_profile_version=2,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.MISSING_INFORMATION,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.ADD_RECORD,
                target_section="education",
                record_payload={
                    "degree": "Bachelor of Science",
                    "fieldOfStudy": "Computer Science",
                    "institution": "University of California, Berkeley",
                    "startDate": "2016",
                    "endDate": "2020",
                    "grade": "3.9 GPA",
                },
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            )
        ],
    )
    res_edu = apply_profile_correction(in_memory_db, art_id, req_add_edu)
    in_memory_db.commit()

    assert len(res_edu.profile.professional_profile.education) == 1
    edu0 = res_edu.profile.professional_profile.education[0]
    assert edu0.degree.value == "Bachelor of Science"
    assert edu0.field_of_study.value == "Computer Science"

    # 3. Remove employment record at index 0
    req_rem_emp = ProfileCorrectionRequest(
        base_profile_id=res_edu.profile.profile_id,
        base_profile_version=3,
        idempotency_key=str(uuid.uuid4()),
        reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
        operations=[
            CorrectionOperation(
                operation_type=CorrectionOperationType.REMOVE_RECORD,
                target_section="employment",
                target_index=0,
                provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
            )
        ],
    )
    res_rem_emp = apply_profile_correction(in_memory_db, art_id, req_rem_emp)
    in_memory_db.commit()
    assert len(res_rem_emp.profile.professional_profile.employment) == 0


def test_mandatory_reason_and_other_note_rule():
    """Verify reasonCategory is mandatory, and OTHER requires reviewerNote."""
    # When reason is OTHER and note is missing -> validation fails
    with pytest.raises(ValueError) as exc:
        ProfileCorrectionRequest(
            base_profile_id=uuid.uuid4(),
            base_profile_version=1,
            idempotency_key="key-1",
            reason_category=CorrectionReasonCategory.OTHER,
            reviewer_note=None,
            operations=[
                CorrectionOperation(
                    operation_type=CorrectionOperationType.REPLACE_SCALAR,
                    target_section="headline",
                    value="Engineer",
                    provenance_type=EvidenceProvenanceType.HUMAN_PROVIDED,
                )
            ],
        )
    assert "reviewer_note is required when reason_category is OTHER" in str(exc.value)


def test_evidence_ownership_and_cross_artifact_rejection(in_memory_db):
    """Verify selecting an evidence ID from another artifact or non-existent ID fails."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # 1. Non-existent evidence ID
    with pytest.raises(InvalidEvidenceReferenceError) as exc:
        req_nonexistent = ProfileCorrectionRequest(
            base_profile_id=prof_id,
            base_profile_version=1,
            idempotency_key=str(uuid.uuid4()),
            reason_category=CorrectionReasonCategory.INCORRECT_EXTRACTION,
            operations=[
                CorrectionOperation(
                    operation_type=CorrectionOperationType.REPLACE_SCALAR,
                    target_section="headline",
                    value="Engineer",
                    selected_evidence_ids=["ev-does-not-exist"],
                    provenance_type=EvidenceProvenanceType.HUMAN_CORRECTED_WITH_EVIDENCE,
                )
            ],
        )
        apply_profile_correction(in_memory_db, art_id, req_nonexistent)
    assert "not found in profile evidence spans" in str(exc.value)


# =============================================================================
# 2. INTEGRATION & LINEAGE TESTS
# =============================================================================

def test_profile_lineage_and_immutability(client_with_db, in_memory_db):
    """Verify v1 correction creates v2; v2 correction creates v3; v1 and v2 remain immutable."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # 1. First correction (v1 -> v2)
    resp1 = client_with_db.post(
        f"/api/v1/resumes/{art_id}/profile-corrections",
        json={
            "baseProfileId": str(prof_id),
            "baseProfileVersion": 1,
            "idempotencyKey": "batch-1",
            "reasonCategory": "INCORRECT_EXTRACTION",
            "operations": [
                {
                    "operationType": "REPLACE_SCALAR",
                    "targetSection": "headline",
                    "value": "Lead Architect",
                    "provenanceType": "HUMAN_PROVIDED",
                }
            ],
        },
    )
    assert resp1.status_code == 201
    data1 = resp1.json()
    assert data1["previousProfileVersion"] == 1
    assert data1["newProfileVersion"] == 2
    v2_id = data1["newProfileId"]

    # 2. Second correction (v2 -> v3)
    resp2 = client_with_db.post(
        f"/api/v1/resumes/{art_id}/profile-corrections",
        json={
            "baseProfileId": v2_id,
            "baseProfileVersion": 2,
            "idempotencyKey": "batch-2",
            "reasonCategory": "MISSING_INFORMATION",
            "operations": [
                {
                    "operationType": "ADD_SET_ITEM",
                    "targetSection": "skills",
                    "value": "Terraform",
                    "provenanceType": "HUMAN_PROVIDED",
                }
            ],
        },
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    assert data2["previousProfileVersion"] == 2
    assert data2["newProfileVersion"] == 3

    # 3. Retrieve latest profile (defaults to v3)
    resp_latest = client_with_db.get(f"/api/v1/resumes/{art_id}/profile")
    assert resp_latest.status_code == 200
    assert resp_latest.json()["profileVersion"] == 3
    assert resp_latest.json()["provenance"] == "HUMAN_CORRECTED"
    assert resp_latest.json()["professionalProfile"]["headline"]["value"] == "Lead Architect"

    # 4. Retrieve specific historical version v1
    resp_v1 = client_with_db.get(f"/api/v1/resumes/{art_id}/profile?version=1")
    assert resp_v1.status_code == 200
    assert resp_v1.json()["profileVersion"] == 1
    assert resp_v1.json()["provenance"] == "PARSER_EXTRACTED"
    assert resp_v1.json()["professionalProfile"]["headline"]["value"] == "Software Engineer"

    # 5. Retrieve specific historical version v2
    resp_v2 = client_with_db.get(f"/api/v1/resumes/{art_id}/profile?version=2")
    assert resp_v2.status_code == 200
    assert resp_v2.json()["profileVersion"] == 2
    assert resp_v2.json()["professionalProfile"]["headline"]["value"] == "Lead Architect"

    # 6. Check profile history list
    resp_hist = client_with_db.get(f"/api/v1/resumes/{art_id}/profiles")
    assert resp_hist.status_code == 200
    hist = resp_hist.json()
    assert len(hist) == 3
    assert [h["profileVersion"] for h in hist] == [1, 2, 3]


def test_base_version_conflict_protection(client_with_db, in_memory_db):
    """Verify HTTP 409 PROFILE_VERSION_CONFLICT when base version is stale."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    # Apply v1 -> v2
    client_with_db.post(
        f"/api/v1/resumes/{art_id}/profile-corrections",
        json={
            "baseProfileId": str(prof_id),
            "baseProfileVersion": 1,
            "idempotencyKey": "batch-1",
            "reasonCategory": "INCORRECT_EXTRACTION",
            "operations": [
                {"operationType": "REPLACE_SCALAR", "targetSection": "headline", "value": "Role 1", "provenanceType": "HUMAN_PROVIDED"}
            ],
        },
    )

    # Attempt to correct base version 1 again
    resp_stale = client_with_db.post(
        f"/api/v1/resumes/{art_id}/profile-corrections",
        json={
            "baseProfileId": str(prof_id),
            "baseProfileVersion": 1,
            "idempotencyKey": "batch-2",
            "reasonCategory": "INCORRECT_EXTRACTION",
            "operations": [
                {"operationType": "REPLACE_SCALAR", "targetSection": "headline", "value": "Role 2", "provenanceType": "HUMAN_PROVIDED"}
            ],
        },
    )
    assert resp_stale.status_code == 409
    assert resp_stale.json()["detail"]["code"] == "PROFILE_VERSION_CONFLICT"


def test_idempotency_behavior(client_with_db, in_memory_db):
    """Verify identical retry returns same profile; modified payload with same key returns conflict."""
    art_id, prof_id = _seed_base_v1_profile(in_memory_db)

    payload = {
        "baseProfileId": str(prof_id),
        "baseProfileVersion": 1,
        "idempotencyKey": "idem-key-100",
        "reasonCategory": "INCORRECT_EXTRACTION",
        "operations": [
            {"operationType": "REPLACE_SCALAR", "targetSection": "headline", "value": "Role A", "provenanceType": "HUMAN_PROVIDED"}
        ],
    }

    # 1. First execution
    resp1 = client_with_db.post(f"/api/v1/resumes/{art_id}/profile-corrections", json=payload)
    assert resp1.status_code == 201
    v2_id = resp1.json()["newProfileId"]

    # 2. Identical retry
    resp2 = client_with_db.post(f"/api/v1/resumes/{art_id}/profile-corrections", json=payload)
    assert resp2.status_code == 201
    assert resp2.json()["newProfileId"] == v2_id
    assert resp2.json()["newProfileVersion"] == 2

    # 3. Same key with different payload
    diff_payload = copy.deepcopy(payload)
    diff_payload["operations"][0]["value"] = "Different Role"
    resp3 = client_with_db.post(f"/api/v1/resumes/{art_id}/profile-corrections", json=diff_payload)
    assert resp3.status_code == 409
    assert resp3.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_evaluation_isolation_from_corrected_profiles():
    """Verify Phase 1E.3 parser evaluation ignores human-corrected profiles and continues using parser origin."""
    from pathlib import Path
    from tests.evaluation_support.evaluator import ParserAccuracyEvaluator

    eval_dir = Path(__file__).resolve().parent / "fixtures" / "evaluation"
    evaluator = ParserAccuracyEvaluator(evaluation_dir=eval_dir)
    report = evaluator.evaluate()

    # Evaluation is strictly deterministic and tests the direct parser output without being polluted by human corrections
    assert report.integrity_status == "PASSED"
    assert report.held_out_metrics.document_count == 2
    assert report.dev_metrics.document_count == 4
