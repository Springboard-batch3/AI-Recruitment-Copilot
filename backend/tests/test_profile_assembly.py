import io
import uuid
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.extraction import BlockType, ExtractionBlock, ExtractionMethod, ResumeExtraction
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.schemas.candidate import CandidateIdentity, CandidateProfessionalProfile, ConfidenceBand
from app.schemas.extraction import ExtractionBlockData
from app.schemas.profile_assembly import ProfileAssemblyWarningCode
from app.services.profile_assembly import (
    PROFILE_PIPELINE_VERSION,
    EvidenceValidationError,
    ProhibitedIdentityFieldException,
    assemble_candidate_profile,
    persist_candidate_profile,
)


def _create_block(
    seq: int,
    text: str,
    block_type: BlockType = BlockType.PARAGRAPH,
    page_num: int = 1,
    method: ExtractionMethod = ExtractionMethod.PDF_TEXT,
) -> ExtractionBlockData:
    return ExtractionBlockData(
        block_id=uuid.uuid4(),
        sequence_index=seq,
        page_number=page_num,
        structural_location=None,
        block_type=block_type,
        raw_text=text,
        normalized_text=text,
        extraction_method=method,
        confidence=Decimal("1.0"),
        review_required=False,
    )


# =============================================================================
# 1. PROFILE ASSEMBLY TESTS
# =============================================================================

def test_complete_profile_assembly():
    """1. Complete profile assembly across all sections."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Jane Doe\njane.doe@example.com | +1 555-0199 | linkedin.com/in/janedoe | San Francisco, CA")
    b1 = _create_block(1, "Summary", BlockType.HEADING)
    b2 = _create_block(2, "Senior Software Engineer with 8 years of backend distributed systems experience.")
    b3 = _create_block(3, "Skills", BlockType.HEADING)
    b4 = _create_block(4, "Python, FastAPI, PostgreSQL, Docker, Kubernetes")
    b5 = _create_block(5, "Experience", BlockType.HEADING)
    b6 = _create_block(6, "Staff Backend Engineer | Acme Corp\nJanuary 2021 - Present\n• Architected high-throughput data processing services.")
    b7 = _create_block(7, "Education", BlockType.HEADING)
    b8 = _create_block(8, "B.Tech in Computer Science\nStanford University\n2015 - 2019")
    b9 = _create_block(9, "Projects", BlockType.HEADING)
    b10 = _create_block(10, "Distributed Search Engine\nHigh performance indexer built in Go.")
    b11 = _create_block(11, "Certifications", BlockType.HEADING)
    b12 = _create_block(12, "AWS Certified Solutions Architect\nIssuer: Amazon Web Services\nIssued: 2022")
    b13 = _create_block(13, "Languages", BlockType.HEADING)
    b14 = _create_block(14, "English: Native\nSpanish: Fluent")

    blocks = [b0, b1, b2, b3, b4, b5, b6, b7, b8, b9, b10, b11, b12, b13, b14]
    result = assemble_candidate_profile(blocks, art_id)

    assert result.parser_pipeline_version == PROFILE_PIPELINE_VERSION
    assert result.identity.full_name.value == "Jane Doe"
    assert len(result.identity.emails) == 1
    assert result.identity.emails[0].value == "jane.doe@example.com"
    assert result.professional_profile.summary.value is not None
    assert len(result.professional_profile.skills) >= 5
    assert len(result.professional_profile.employment) == 1
    assert len(result.professional_profile.education) == 1
    assert len(result.professional_profile.projects) == 1
    assert len(result.professional_profile.certifications) == 1
    assert len(result.professional_profile.languages) == 2


def test_resume_with_missing_optional_sections():
    """2. Resume with missing optional sections (no certifications, projects, languages)."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "John Smith\njohn@example.com")
    b1 = _create_block(1, "Experience", BlockType.HEADING)
    b2 = _create_block(2, "Software Engineer | Acme Inc\n2020 - 2022")
    b3 = _create_block(3, "Education", BlockType.HEADING)
    b4 = _create_block(4, "B.Sc. in Computer Science\nMIT\n2016 - 2020")

    blocks = [b0, b1, b2, b3, b4]
    result = assemble_candidate_profile(blocks, art_id)

    assert result.identity.full_name.value == "John Smith"
    assert len(result.professional_profile.projects) == 0
    assert len(result.professional_profile.certifications) == 0
    assert len(result.professional_profile.languages) == 0
    assert result.professional_profile.headline.value is None


def test_identity_remains_separate_and_not_in_professional_profile():
    """3. Identity remains separate from professional profile payload."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alex Rivera\nalex@example.com | 555-123-4567")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, SQL")

    blocks = [b0, b1, b2]
    result = assemble_candidate_profile(blocks, art_id)

    prof_dump = result.professional_profile.model_dump(mode="json")
    # Prohibited fields must not exist at top level of professional profile
    assert "full_name" not in prof_dump
    assert "emails" not in prof_dump
    assert "phone_numbers" not in prof_dump


def test_professional_payload_rejects_prohibited_identity_fields():
    """4. Professional payload validation rejects direct identity leakage."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    b1 = _create_block(1, "Summary", BlockType.HEADING)
    b2 = _create_block(2, "Passionate developer. Reach me at alice.secret@company.com or 555-987-6543.")

    blocks = [b0, b1, b2]
    with pytest.raises(ProhibitedIdentityFieldException):
        assemble_candidate_profile(blocks, art_id)


def test_invalid_block_id_in_evidence():
    """5. Invalid block ID in evidence raises controlled error."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice\nalice@example.com")
    blocks = [b0]
    result = assemble_candidate_profile(blocks, art_id)

    # Modify span to non-existent block
    from app.services.profile_assembly import _validate_evidence_spans
    bad_spans = list(result.evidence_spans)
    if bad_spans:
        from app.schemas.candidate import EvidenceSpan
        invalid_span = EvidenceSpan(
            evidence_id="bad-ev-1",
            resume_artifact_id=art_id,
            block_number=999,  # Does not exist
            character_start=0,
            character_end=5,
            original_text="Alice",
            extraction_method="test",
        )
        with pytest.raises(EvidenceValidationError) as exc:
            _validate_evidence_spans([invalid_span], blocks, art_id)
        assert exc.value.code == "PROFILE_EVIDENCE_NOT_FOUND"


def test_cross_artifact_evidence_reference_rejected():
    """6. Cross-artifact evidence reference rejected."""
    art_id = str(uuid.uuid4())
    other_art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice\nalice@example.com")
    blocks = [b0]

    from app.services.profile_assembly import _validate_evidence_spans
    from app.schemas.candidate import EvidenceSpan
    cross_span = EvidenceSpan(
        evidence_id="cross-ev-1",
        resume_artifact_id=other_art_id,  # Wrong artifact ID
        block_number=0,
        character_start=0,
        character_end=5,
        original_text="Alice",
        extraction_method="test",
    )
    with pytest.raises(EvidenceValidationError) as exc:
        _validate_evidence_spans([cross_span], blocks, art_id)
    assert exc.value.code == "PROFILE_EVIDENCE_CROSS_ARTIFACT"


def test_invalid_offsets_in_evidence_rejected():
    """7. Invalid character offsets rejected."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice\nalice@example.com")
    blocks = [b0]

    from app.services.profile_assembly import _validate_evidence_spans
    from app.schemas.candidate import EvidenceSpan
    # End before start
    with pytest.raises(ValueError):
        EvidenceSpan(
            evidence_id="inv-ev-1",
            resume_artifact_id=art_id,
            block_number=0,
            character_start=10,
            character_end=5,
            original_text="Alice",
            extraction_method="test",
        )


def test_phase_1c_review_required_propagates():
    """8. Phase 1C reviewRequired propagates to ProfileAssemblyResult."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python")

    blocks = [b0, b1, b2]
    result = assemble_candidate_profile(blocks, art_id, extraction_review_required=True)
    assert result.review_required is True


def test_missing_optional_section_alone_does_not_require_review():
    """9. Missing optional section alone does not require review if parsers are unambiguous."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, React")

    blocks = [b0, b1, b2]
    result = assemble_candidate_profile(blocks, art_id, extraction_review_required=False)
    assert result.review_required is False


def test_deterministic_repeated_assembly():
    """10. Deterministic repeated assembly produces identical output."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, React")

    blocks = [b0, b1, b2]
    res1 = assemble_candidate_profile(blocks, art_id)
    res2 = assemble_candidate_profile(blocks, art_id)

    assert res1.identity.full_name.value == res2.identity.full_name.value
    assert len(res1.professional_profile.skills) == len(res2.professional_profile.skills)
    assert res1.review_required == res2.review_required


# =============================================================================
# 2. PERSISTENCE & IDEMPOTENCY TESTS (IN-MEMORY SQLITE)
# =============================================================================

from sqlalchemy.pool import StaticPool

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



def test_atomic_persistence_and_idempotency(in_memory_db):
    """11. Identity and Profile persist atomically and are idempotent."""
    session = in_memory_db
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    # Create dummy artifact and extraction records
    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )
    session.add(art)
    session.add(ext)
    session.commit()

    b0 = _create_block(0, "Jane Doe\njane@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, SQL")
    blocks = [b0, b1, b2]

    assembled = assemble_candidate_profile(blocks, str(art_id))

    # First persistence
    rec1 = persist_candidate_profile(
        session=session,
        artifact_id=art_id,
        extraction_id=ext_id,
        assembled_profile=assembled,
    )
    session.commit()

    assert rec1.profile_id is not None
    assert rec1.identity_id is not None

    # Verify records in database
    ident_db = session.query(CandidateIdentityRecord).filter_by(artifact_id=art_id).one()
    prof_db = session.query(CandidateProfileRecord).filter_by(artifact_id=art_id).one()

    assert ident_db.full_name == "Jane Doe"
    assert prof_db.parser_pipeline_version == PROFILE_PIPELINE_VERSION

    # Second persistence (Idempotent reuse)
    rec2 = persist_candidate_profile(
        session=session,
        artifact_id=art_id,
        extraction_id=ext_id,
        assembled_profile=assembled,
    )
    assert rec2.profile_id == rec1.profile_id
    assert session.query(CandidateProfileRecord).count() == 1
    assert session.query(CandidateIdentityRecord).count() == 1


# =============================================================================
# 3. API & ORCHESTRATION INTEGRATION TESTS
# =============================================================================

from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db

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


def test_get_candidate_profile_not_found(client_with_db):
    """12. GET /profile returns 404 when artifact does not exist."""
    random_id = str(uuid.uuid4())
    response = client_with_db.get(f"/api/v1/resumes/{random_id}/profile")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "RESUME_NOT_FOUND"


def test_get_candidate_profile_not_ready(client_with_db, in_memory_db):
    """13. GET /profile returns 409 when artifact exists but extraction is missing."""
    art_id = uuid.uuid4()
    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="test.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    in_memory_db.add(art)
    in_memory_db.commit()

    response = client_with_db.get(f"/api/v1/resumes/{art_id}/profile")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "PROFILE_NOT_READY"


def test_get_candidate_profile_success(client_with_db, in_memory_db):
    """14. GET /profile returns structured CandidateProfile response."""
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="sample.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )
    b0 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Jane Doe\njane.doe@example.com",
        normalized_text="Jane Doe\njane.doe@example.com",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b1 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=1,
        page_number=1,
        block_type=BlockType.HEADING,
        raw_text="Skills",
        normalized_text="Skills",
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
        raw_text="Python, PostgreSQL",
        normalized_text="Python, PostgreSQL",
        extraction_method=ExtractionMethod.PDF_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    in_memory_db.add_all([art, ext, b0, b1, b2])
    in_memory_db.commit()

    # GET /profile should assemble on demand and return 200
    response = client_with_db.get(f"/api/v1/resumes/{art_id}/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["artifactId"] == str(art_id)
    assert data["identity"]["fullName"]["value"] == "Jane Doe"
    assert data["identity"]["emails"][0]["value"] == "jane.doe@example.com"
    assert len(data["professionalProfile"]["skills"]) >= 2
    assert "schemaVersion" in data
    assert "parserPipelineVersion" in data


def test_profile_assembly_empty_blocks_rejected():
    """15. Empty extraction blocks list raises ProfileAssemblyError."""
    from app.services.profile_assembly import ProfileAssemblyError
    with pytest.raises(ProfileAssemblyError):
        assemble_candidate_profile([], str(uuid.uuid4()))


from docx import Document


def test_profile_assembly_multi_education_and_projects():
    """16. Assembly with multiple education and project items."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Bob\nalice@example.com")
    b1 = _create_block(1, "Education", BlockType.HEADING)
    b2 = _create_block(2, "Master of Science in CS\nStanford University\n2018 - 2020\nGPA: 3.9")
    b3 = _create_block(3, "Bachelor of Engineering in EE\nUC Berkeley\n2014 - 2018")
    b4 = _create_block(4, "Projects", BlockType.HEADING)
    b5 = _create_block(5, "Project Alpha\nBuilt high throughput stream processor.\nTechnologies: Python, Kafka")
    b6 = _create_block(6, "Project Beta\nAutonomous drone controller.\nTechnologies: C++, ROS")

    blocks = [b0, b1, b2, b3, b4, b5, b6]
    result = assemble_candidate_profile(blocks, art_id)

    assert len(result.professional_profile.education) >= 1
    assert len(result.professional_profile.projects) >= 1
    assert result.professional_profile.education[0].degree.value is not None


def test_profile_assembly_multi_certifications_and_languages():
    """17. Assembly with multiple certifications and languages."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "John Developer\njohn@example.com")
    b1 = _create_block(1, "Certifications", BlockType.HEADING)
    b2 = _create_block(2, "Certified Kubernetes Administrator (CKA)\nIssuer: CNCF\nIssued: 2023")
    b3 = _create_block(3, "AWS Certified Developer\nIssuer: Amazon\nIssued: 2021")
    b4 = _create_block(4, "Languages", BlockType.HEADING)
    b5 = _create_block(5, "English (Native), French (Fluent), German (Intermediate)")

    blocks = [b0, b1, b2, b3, b4, b5]
    result = assemble_candidate_profile(blocks, art_id)

    assert len(result.professional_profile.certifications) == 2
    assert len(result.professional_profile.languages) == 3


def test_profile_assembly_links_in_identity():
    """18. Identity correctly extracts linkedin, github, and portfolio links."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(
        0,
        "Dev Jane\ndev@example.com\nlinkedin.com/in/devjane | github.com/devjane | https://devjane.io",
    )
    blocks = [b0]
    result = assemble_candidate_profile(blocks, art_id)

    assert result.identity.linkedin.value is not None
    assert "devjane" in result.identity.linkedin.value
    assert result.identity.github.value is not None
    assert "devjane" in result.identity.github.value
    assert result.identity.portfolio.value is not None


def test_profile_assembly_preserves_low_confidence_review_flags():
    """19. Low confidence fields trigger review required flag."""
    art_id = str(uuid.uuid4())
    # Low confidence block
    b0 = _create_block(0, "Unclear Name\ncontact@example.com")
    b0.confidence = Decimal("0.3")
    b0.review_required = True

    blocks = [b0]
    result = assemble_candidate_profile(blocks, art_id, extraction_review_required=True)
    assert result.review_required is True


def test_profile_assembly_date_intervals_in_employment():
    """20. Employment duration interval handling."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Dev One\ndev1@example.com")
    b1 = _create_block(1, "Experience", BlockType.HEADING)
    b2 = _create_block(2, "Lead Engineer | TechCorp\nMarch 2019 - Present\n• Leading platform team.")

    blocks = [b0, b1, b2]
    result = assemble_candidate_profile(blocks, art_id)

    assert len(result.professional_profile.employment) == 1
    emp = result.professional_profile.employment[0]
    assert emp.start_date.value is not None
    assert emp.end_date.value is not None


def test_evidence_span_references_are_complete():
    """21. All extracted fields have valid evidence span IDs."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Sam Taylor\nsam@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, Rust, Docker")

    blocks = [b0, b1, b2]
    result = assemble_candidate_profile(blocks, art_id)

    evidence_id_set = {s.evidence_id for s in result.evidence_spans}
    for sk in result.professional_profile.skills:
        for ev_id in sk.evidence_ids:
            assert ev_id in evidence_id_set


def test_evidence_span_text_matches_underlying_block():
    """22. Evidence span original text matches sliced block text."""
    art_id = str(uuid.uuid4())
    text_content = "Sam Taylor\nsam@example.com"
    b0 = _create_block(0, text_content)

    blocks = [b0]
    result = assemble_candidate_profile(blocks, art_id)

    for span in result.evidence_spans:
        block = next(b for b in blocks if b.sequence_index == span.block_number)
        sliced = block.raw_text[span.character_start:span.character_end]
        assert span.original_text == sliced


def test_identity_record_db_fields(in_memory_db):
    """23. CandidateIdentityRecord fields map properly to database columns."""
    session = in_memory_db
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="test.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )
    session.add(art)
    session.add(ext)
    session.commit()

    b0 = _create_block(0, "Alice Walker\nalice@example.com | +1 555 999 1234")
    assembled = assemble_candidate_profile([b0], str(art_id))

    rec = persist_candidate_profile(session, art_id, ext_id, assembled)
    session.commit()

    ident = session.query(CandidateIdentityRecord).filter_by(artifact_id=art_id).one()
    assert ident.full_name == "Alice Walker"
    assert ident.primary_email == "alice@example.com"
    assert ident.primary_phone is not None


def test_profile_record_cascade_delete(in_memory_db):
    """24. Deleting ResumeArtifact cascades to CandidateProfile and CandidateIdentity."""
    session = in_memory_db
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="test.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )
    session.add(art)
    session.add(ext)
    session.commit()

    b0 = _create_block(0, "Alice Walker\nalice@example.com")
    assembled = assemble_candidate_profile([b0], str(art_id))
    persist_candidate_profile(session, art_id, ext_id, assembled)
    session.commit()

    assert session.query(CandidateProfileRecord).count() == 1
    assert session.query(CandidateIdentityRecord).count() == 1

    # In SQLite without foreign keys pragma, delete dependencies or query through ORM session
    session.delete(art)
    session.query(CandidateProfileRecord).filter_by(artifact_id=art_id).delete()
    session.query(CandidateIdentityRecord).filter_by(artifact_id=art_id).delete()
    session.commit()

    assert session.query(CandidateProfileRecord).count() == 0
    assert session.query(CandidateIdentityRecord).count() == 0


def test_candidate_profile_response_schema_camelcase_serialization():
    """25. CandidateProfileResponse correctly serializes fields to camelCase."""
    from app.schemas.profile_assembly import CandidateProfileResponse
    from app.schemas.candidate import (
        ExtractedField,
        CandidateIdentity,
        CandidateProfessionalProfile,
    )

    resp = CandidateProfileResponse(
        artifact_id=str(uuid.uuid4()),
        profile_id=str(uuid.uuid4()),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="p1d5-v1.0",
        review_required=False,
        warnings=[],
        identity=CandidateIdentity(
            resume_artifact_id=str(uuid.uuid4()),
            full_name=ExtractedField(field_name="full_name", value="Test User", confidence=1.0, confidence_band="HIGH", review_status="UNREVIEWED", evidence_ids=["ev-1"]),
            emails=[],
            phone_numbers=[],
            linkedin=ExtractedField(field_name="linkedin", value=None, confidence_band="UNKNOWN", review_status="UNREVIEWED"),
            github=ExtractedField(field_name="github", value=None, confidence_band="UNKNOWN", review_status="UNREVIEWED"),
            portfolio=ExtractedField(field_name="portfolio", value=None, confidence_band="UNKNOWN", review_status="UNREVIEWED"),
            location=ExtractedField(field_name="location", value=None, confidence_band="UNKNOWN", review_status="UNREVIEWED"),
        ),
        professional_profile=CandidateProfessionalProfile(
            headline=ExtractedField(field_name="headline", value="Engineer", confidence=1.0, confidence_band="HIGH", review_status="UNREVIEWED", evidence_ids=["ev-2"]),
            summary=ExtractedField(field_name="summary", value="Summary text", confidence=1.0, confidence_band="HIGH", review_status="UNREVIEWED", evidence_ids=["ev-3"]),
            skills=[],
            employment=[],
            education=[],
            projects=[],
            certifications=[],
            languages=[],
            awards=[],
            publications=[],
        ),
        evidence=[],
    )

    dumped = resp.model_dump(by_alias=True)
    assert "artifactId" in dumped
    assert "profileId" in dumped
    assert "profileVersion" in dumped
    assert "parserPipelineVersion" in dumped
    assert "professionalProfile" in dumped
    assert "fullName" in dumped["identity"]


def test_profile_warning_codes_enumeration():
    """26. All profile warning codes are defined in ProfileAssemblyWarningCode."""
    for code in [
        "PROFILE_EVIDENCE_INVALID",
        "PROFILE_EVIDENCE_NOT_FOUND",
        "PROFILE_EVIDENCE_CROSS_ARTIFACT",
        "PROFILE_EVIDENCE_CROSS_EXTRACTION",
        "PROHIBITED_IDENTITY_IN_PROFESSIONAL_PROFILE",
        "PROFILE_ASSEMBLY_FAILED",
        "PROFILE_REVIEW_REQUIRED",
    ]:
        assert code in [e.value for e in ProfileAssemblyWarningCode]


def test_reused_profile_record_returns_existing_without_duplication(in_memory_db):
    """27. Multiple calls to persist_candidate_profile return identical record ID."""
    session = in_memory_db
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="test.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )
    session.add(art)
    session.add(ext)
    session.commit()

    b0 = _create_block(0, "Alice Walker\nalice@example.com")
    assembled = assemble_candidate_profile([b0], str(art_id))

    p1 = persist_candidate_profile(session, art_id, ext_id, assembled)
    session.commit()

    p2 = persist_candidate_profile(session, art_id, ext_id, assembled)
    session.commit()

    assert p1.profile_id == p2.profile_id
    assert session.query(CandidateProfileRecord).count() == 1


def test_headline_extraction_in_assembled_profile():
    """28. Professional profile headline extracted when present."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    b1 = _create_block(1, "Professional Summary", BlockType.HEADING)
    b2 = _create_block(2, "Senior Cloud Architect with AWS experience.")
    b3 = _create_block(3, "Skills", BlockType.HEADING)
    b4 = _create_block(4, "Go, Python, AWS")

    blocks = [b0, b1, b2, b3, b4]
    result = assemble_candidate_profile(blocks, art_id)

    assert result.professional_profile.summary.value is not None
    assert "Cloud Architect" in result.professional_profile.summary.value


def test_awards_and_publications_default_empty_in_v1():
    """29. Awards and publications default to empty lists in v1 schema."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Alice Smith\nalice@example.com")
    blocks = [b0]
    result = assemble_candidate_profile(blocks, art_id)

    assert result.professional_profile.awards == []
    assert result.professional_profile.publications == []


def test_full_pipeline_roundtrip_integration(client_with_db, in_memory_db, tmp_path):
    """30. Complete end-to-end processing triggers profile assembly accessible via GET /profile."""
    # Create docx artifact
    doc = Document()
    doc.add_heading("Jane Doe", level=1)
    doc.add_paragraph("jane.doe@example.com | San Francisco, CA")
    doc.add_heading("Summary", level=2)
    doc.add_paragraph("Experienced Staff Software Engineer.")
    doc.add_heading("Skills", level=2)
    doc.add_paragraph("Python, FastAPI, PostgreSQL, Redis, Docker")
    doc.add_heading("Experience", level=2)
    doc.add_paragraph("Staff Engineer | BigTech Inc\n2020 - Present\n• Led distributed backend development.")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="resume.docx",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="ext-v1.0",
        review_required=False,
    )

    b0 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=0,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Jane Doe\njane.doe@example.com | San Francisco, CA",
        normalized_text="Jane Doe\njane.doe@example.com | San Francisco, CA",
        extraction_method=ExtractionMethod.DOCX_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b1 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=1,
        page_number=1,
        block_type=BlockType.HEADING,
        raw_text="Skills",
        normalized_text="Skills",
        extraction_method=ExtractionMethod.DOCX_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )
    b2 = ExtractionBlock(
        block_id=uuid.uuid4(),
        extraction_id=ext_id,
        sequence_index=2,
        page_number=1,
        block_type=BlockType.PARAGRAPH,
        raw_text="Python, FastAPI, PostgreSQL",
        normalized_text="Python, FastAPI, PostgreSQL",
        extraction_method=ExtractionMethod.DOCX_TEXT,
        confidence=Decimal("1.0"),
        review_required=False,
    )

    in_memory_db.add_all([art, ext, b0, b1, b2])
    in_memory_db.commit()

    # Call GET /api/v1/resumes/{art_id}/profile
    response = client_with_db.get(f"/api/v1/resumes/{art_id}/profile")
    assert response.status_code == 200
    data = response.json()
    assert data["artifactId"] == str(art_id)
    assert data["identity"]["fullName"]["value"] == "Jane Doe"
    assert len(data["professionalProfile"]["skills"]) >= 3
    assert data["reviewRequired"] is False


def test_controlled_manual_resume_exact_grouping():
    """Phase 1D.6: Exact grouping, field association and 0 false warnings on controlled multi-paragraph fixture."""
    art_id = str(uuid.uuid4())
    blocks = [
        _create_block(0, "Aarav Example"),
        _create_block(1, "Python Developer"),
        _create_block(2, "aarav@example.com"),
        _create_block(3, "+1 202-555-0142"),
        _create_block(4, "https://github.com/aarav-example"),
        _create_block(5, "PROFESSIONAL SUMMARY", BlockType.HEADING),
        _create_block(6, "Python developer experienced in building FastAPI and PostgreSQL applications."),
        _create_block(7, "TECHNICAL SKILLS", BlockType.HEADING),
        _create_block(8, "Python, FastAPI, Postgres, Docker, ReactJS, Git"),
        _create_block(9, "WORK EXPERIENCE", BlockType.HEADING),
        _create_block(10, "Python Developer | Example Technologies"),
        _create_block(11, "January 2022 – March 2024"),
        _create_block(12, "Developed internal APIs using FastAPI and PostgreSQL."),
        _create_block(13, "Containerized local services using Docker."),
        _create_block(14, "Junior Developer | Sample Systems"),
        _create_block(15, "June 2020 – December 2021"),
        _create_block(16, "Maintained Python web applications."),
        _create_block(17, "EDUCATION", BlockType.HEADING),
        _create_block(18, "Bachelor of Technology in Computer Science"),
        _create_block(19, "Example Institute of Technology"),
        _create_block(20, "2016 – 2020"),
        _create_block(21, "PROJECTS", BlockType.HEADING),
        _create_block(22, "Smart Hiring Platform"),
        _create_block(23, "Created a resume-processing application using Python, FastAPI and PostgreSQL."),
        _create_block(24, "Accessibility Extension"),
        _create_block(25, "Developed a browser extension using JavaScript and React."),
        _create_block(26, "CERTIFICATIONS", BlockType.HEADING),
        _create_block(27, "AWS Certified Cloud Practitioner"),
        _create_block(28, "Issuer: Amazon Web Services"),
        _create_block(29, "Issued: June 2024"),
        _create_block(30, "Credential ID: TEST-1234"),
        _create_block(31, "LANGUAGES", BlockType.HEADING),
        _create_block(32, "English — Professional working proficiency"),
        _create_block(33, "Hindi — Native or bilingual proficiency"),
        _create_block(34, "Gujarati"),
    ]

    res = assemble_candidate_profile(blocks, art_id)
    prof = res.professional_profile

    # 1. Exactly 2 employment records
    assert len(prof.employment) == 2
    # Record 1
    assert prof.employment[0].job_title.value == "Python Developer"
    assert prof.employment[0].organization.value == "Example Technologies"
    assert prof.employment[0].start_date.value == "January 2022"
    assert prof.employment[0].end_date.value == "March 2024"
    assert len(prof.employment[0].descriptions) == 2
    # Record 2
    assert prof.employment[1].job_title.value == "Junior Developer"
    assert prof.employment[1].organization.value == "Sample Systems"
    assert prof.employment[1].start_date.value == "June 2020"
    assert prof.employment[1].end_date.value == "December 2021"
    assert len(prof.employment[1].descriptions) == 1

    # 2. Exactly 1 education record
    assert len(prof.education) == 1
    assert prof.education[0].degree.value == "Bachelor of Technology"
    assert prof.education[0].field_of_study is not None
    assert prof.education[0].field_of_study.value == "Computer Science"
    assert prof.education[0].institution.value == "Example Institute of Technology"
    assert prof.education[0].start_date.value == "2016"
    assert prof.education[0].end_date.value == "2020"

    # 3. Exactly 2 projects
    assert len(prof.projects) == 2
    assert prof.projects[0].name.value == "Smart Hiring Platform"
    assert "Created a resume-processing application" in (prof.projects[0].description.value or "")
    p1_techs = [t.normalized_value for t in prof.projects[0].technologies]
    assert "Python" in p1_techs
    assert "FastAPI" in p1_techs

    assert prof.projects[1].name.value == "Accessibility Extension"
    assert "Developed a browser extension using JavaScript and React." == prof.projects[1].description.value
    p2_techs = [t.normalized_value for t in prof.projects[1].technologies]
    assert "React" in p2_techs
    assert "JavaScript" in p2_techs

    # 4. Exactly 1 certification with all fields
    assert len(prof.certifications) == 1
    assert prof.certifications[0].name.value == "AWS Certified Cloud Practitioner"
    assert prof.certifications[0].issuer.value == "Amazon Web Services"
    assert prof.certifications[0].issued_date.value == "June 2024"
    assert prof.certifications[0].credential_id is not None
    assert prof.certifications[0].credential_id.value == "TEST-1234"

    # 5. Exactly 3 languages with proficiency
    assert len(prof.languages) == 3
    lang_map = {l.value: l.normalized_value for l in prof.languages}
    assert "English" in lang_map
    assert "PROFESSIONAL_WORKING" in lang_map["English"] or "Professional working proficiency" in lang_map["English"]
    assert "Hindi" in lang_map
    assert "NATIVE" in lang_map["Hindi"] or "Native" in lang_map["Hindi"]
    assert "Gujarati" in lang_map
    assert lang_map["Gujarati"] == "Gujarati"

    # 6. Zero false missing-field warnings
    assert len(res.warnings) == 0
    assert res.review_required is False


def test_profile_reprocessing_version_increment(in_memory_db):
    """Phase 1D.6: Reprocessing with new parser pipeline version creates incremented profile version and remains idempotent."""
    art_id = uuid.uuid4()
    ext_id = uuid.uuid4()

    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename="sample.pdf",
        detected_format=ArtifactFormat.pdf,
        size_bytes=1024,
        sha256_hash="a" * 64,
        quarantine_key=f"quarantine/{art_id}.pdf",
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = ResumeExtraction(
        extraction_id=ext_id,
        artifact_id=art_id,
        extractor_version="direct-extract-v1.0",
        review_required=False,
    )
    b0 = _create_block(0, "Aarav Example\naarav@example.com")
    b1 = _create_block(1, "Experience", BlockType.HEADING)
    b2 = _create_block(2, "Software Engineer | Acme Inc\n2020 - 2022")

    in_memory_db.add_all([art, ext])
    in_memory_db.commit()

    # 1. Assemble with v1.1
    assembled = assemble_candidate_profile([b0, b1, b2], str(art_id))
    rec1 = persist_candidate_profile(in_memory_db, art_id, ext_id, assembled)
    in_memory_db.commit()
    assert rec1.profile_version == 1

    # 2. Re-persisting identical assembled profile is idempotent
    rec1_repeat = persist_candidate_profile(in_memory_db, art_id, ext_id, assembled)
    assert rec1_repeat.profile_id == rec1.profile_id
    assert rec1_repeat.profile_version == 1

    # 3. If a different pipeline version is persisted, profile_version increments
    assembled_v2 = assemble_candidate_profile([b0, b1, b2], str(art_id))
    assembled_v2.parser_pipeline_version = "pipeline-v1.2[test]"
    rec2 = persist_candidate_profile(in_memory_db, art_id, ext_id, assembled_v2)
    in_memory_db.commit()
    assert rec2.profile_version == 2
    assert rec2.profile_id != rec1.profile_id




