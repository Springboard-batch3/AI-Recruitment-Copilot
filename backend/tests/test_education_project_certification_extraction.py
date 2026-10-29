import uuid
from decimal import Decimal
import pytest

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.candidate import ConfidenceBand, ReviewStatus
from app.schemas.education_project_certification import (
    CertificationWarningCode,
    DegreeLevel,
    EducationWarningCode,
    LanguageWarningCode,
    ProjectWarningCode,
)
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import SectionType
from app.schemas.skill_and_summary import ExtractedSkillItem, SkillResolutionStatus
from app.services.education_project_certification_extraction import (
    EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION,
    extract_certification_records,
    extract_education_projects_certifications_languages,
    extract_education_records,
    extract_language_records,
    extract_project_records,
)
from app.services.education_project_certification_rules import (
    EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION,
)
from app.services.section_detection import detect_sections_from_blocks


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
# 1. EDUCATION TESTS
# =============================================================================

def test_standard_education_record():
    """1. Standard degree, field, institution and date range."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Bachelor of Technology in Computer Science\nStanford University\nAugust 2018 - May 2022\nGPA: 3.9/4.0")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    assert res.parser_version == EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION
    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.degree.value == "Bachelor of Technology"
    assert rec.normalized_degree_level == DegreeLevel.BACHELORS
    assert rec.field_of_study is not None
    assert rec.field_of_study.value == "Computer Science"
    assert rec.institution.value == "Stanford University"
    assert rec.start_date.value == "August 2018"
    assert rec.end_date.value == "May 2022"
    assert rec.grade.value == "3.9/4.0"
    assert len(rec.warnings) == 0


def test_multiple_education_records():
    """2. Multiple education records."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Master of Science in Artificial Intelligence\nMassachusetts Institute of Technology\n2022 - 2024")
    b2 = _create_block(2, "B.Tech in Information Technology\nDelhi Technological University\n2018 - 2022")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    assert len(res.records) == 2
    assert res.records[0].degree.value == "Master of Science"
    assert res.records[0].normalized_degree_level == DegreeLevel.MASTERS
    assert res.records[1].degree.value == "B.Tech"
    assert res.records[1].normalized_degree_level == DegreeLevel.BACHELORS


def test_degree_alias_normalization():
    """3. Degree alias normalization."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Ph.D. in Computer Science\nHarvard University\n2019 - 2023")
    b2 = _create_block(2, "BCA in Software Development\nNational College\n2016 - 2019")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    assert res.records[0].normalized_degree_level == DegreeLevel.DOCTORATE
    assert res.records[1].normalized_degree_level == DegreeLevel.BACHELORS


def test_year_only_precision():
    """4. Year-only precision."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "B.Sc. in Physics\nOxford University\n2015 - 2018")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.start_date.value == "2015"
    assert rec.end_date.value == "2018"
    assert rec.date_interval is not None
    assert rec.date_interval.duration_months is None  # Year-only does not guess months


def test_present_ongoing_education():
    """5. Present/ongoing education."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "MBA in Marketing\nColumbia Business School\nSeptember 2023 - Present")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.end_date.value == "Present"
    assert rec.date_interval is not None
    assert rec.date_interval.is_current is True


def test_missing_institution():
    """6. Missing institution."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "B.Tech in Computer Engineering\n2018 - 2022")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.institution.value is None
    assert any(w.code == EducationWarningCode.EDUCATION_INSTITUTION_MISSING for w in rec.warnings)


def test_ambiguous_degree_and_institution():
    """7. Ambiguous degree/institution association."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Some Unknown Study Program\n2020 - 2021")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.degree.value is None
    assert any(w.code == EducationWarningCode.EDUCATION_DEGREE_MISSING for w in rec.warnings)


def test_certification_course_not_classified_as_education():
    """8. Certification is not classified as education."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Udemy: Complete Python Bootcamp - Certificate of Completion\n2023")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    assert len(res.records) == 0


# =============================================================================
# 2. PROJECT TESTS
# =============================================================================

def test_multiple_projects_remain_separate():
    """9. Multiple projects remain separate."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Projects", BlockType.HEADING)
    b1 = _create_block(1, "AI Resume Screener\nBuilt automated pipeline using FastAPI and PostgreSQL.")
    b2 = _create_block(2, "E-Commerce Mobile App\nReact Native application for online retail store.")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id)

    assert len(res.records) == 2
    assert res.records[0].name.value == "AI Resume Screener"
    assert res.records[1].name.value == "E-Commerce Mobile App"


def test_project_descriptions_stop_at_next_project():
    """10. Project descriptions stop at the next project."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Projects", BlockType.HEADING)
    b1 = _create_block(1, "Project Alpha\n• Feature 1\n• Feature 2")
    b2 = _create_block(2, "Project Beta\n• Feature 3\n• Feature 4")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id)

    assert "Feature 3" not in (res.records[0].description.value or "")
    assert "Feature 1" not in (res.records[1].description.value or "")


def test_skills_associated_only_within_project_evidence_range():
    """11. Skills are associated only within the project evidence range."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Projects", BlockType.HEADING)
    b1 = _create_block(1, "Distributed Data Engine\nEngineered streaming service using Kafka and Go.")
    b2 = _create_block(2, "Web Dashboard\nCreated frontend UI using React and TypeScript.")

    skill_go = ExtractedSkillItem(
        canonical_value="Go",
        raw_values=["Go"],
        resolution_status=SkillResolutionStatus.RESOLVED_EXACT,
        confidence=1.0,
    )
    skill_react = ExtractedSkillItem(
        canonical_value="React",
        raw_values=["React"],
        resolution_status=SkillResolutionStatus.RESOLVED_EXACT,
        confidence=1.0,
    )

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id, skill_occurrences=[skill_go, skill_react])

    p1_techs = [t.normalized_value for t in res.records[0].technologies]
    p2_techs = [t.normalized_value for t in res.records[1].technologies]

    assert "Go" in p1_techs
    assert "React" not in p1_techs

    assert "React" in p2_techs
    assert "Go" not in p2_techs


def test_general_resume_skill_not_attached_to_every_project():
    """12. A general resume skill is not attached to every project."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Projects", BlockType.HEADING)
    b1 = _create_block(1, "Portfolio Website\nBuilt static portfolio using HTML and CSS.")

    skill_docker = ExtractedSkillItem(
        canonical_value="Docker",
        raw_values=["Docker"],
        resolution_status=SkillResolutionStatus.RESOLVED_EXACT,
        confidence=1.0,
    )

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id, skill_occurrences=[skill_docker])

    p_techs = [t.normalized_value for t in res.records[0].technologies]
    assert "Docker" not in p_techs


def test_missing_project_name():
    """13. Missing project name."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Projects", BlockType.HEADING)
    b1 = _create_block(1, "• Implemented OAuth2 authentication flow for customer portal.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id)

    assert len(res.records) == 1
    assert any(w.code == ProjectWarningCode.PROJECT_NAME_MISSING for w in res.records[0].warnings)


def test_employment_description_is_not_treated_as_project():
    """14. Employment description is not treated as a project."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Software Engineer | Google\nDeveloped search index features.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_project_records(blocks, sec_res, art_id)

    # PROJECTS section does not exist, zero project records extracted
    assert len(res.records) == 0


# =============================================================================
# 3. CERTIFICATION TESTS
# =============================================================================

def test_certification_name_issuer_and_date():
    """15. Name, issuer and issue date."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "AWS Certified Solutions Architect – Associate\nIssued by: Amazon Web Services\nIssued: March 2023")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_certification_records(blocks, sec_res, art_id)

    assert len(res.records) == 1
    rec = res.records[0]
    assert rec.name.value == "AWS Certified Solutions Architect – Associate"
    assert rec.issuer.value == "Amazon Web Services"
    assert rec.issued_date.value == "March 2023"


def test_certification_credential_id_and_url():
    """16. Credential ID and URL."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "Certified Kubernetes Administrator (CKA)\nIssuer: CNCF\nCredential ID: CKA-987654\nCredential URL: https://www.credly.com/badges/sample-id")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_certification_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.credential_id is not None
    assert rec.credential_id.value == "CKA-987654"
    assert rec.credential_url.value == "https://www.credly.com/badges/sample-id"


def test_missing_certification_issuer():
    """17. Missing issuer."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "Custom Internal Agile Certificate\nIssued: 2022")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_certification_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.issuer.value is None
    assert any(w.code == CertificationWarningCode.CERTIFICATION_ISSUER_AMBIGUOUS for w in rec.warnings)


def test_issue_and_expiry_dates():
    """18. Issue and expiry dates."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "HashiCorp Certified: Terraform Associate\nIssuer: HashiCorp\nIssued: January 2022\nExpires: January 2024")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_certification_records(blocks, sec_res, art_id)

    rec = res.records[0]
    assert rec.issued_date.value == "January 2022"
    assert rec.expiry_date is not None
    assert rec.expiry_date.value == "January 2024"


def test_url_extracted_never_fetched():
    """19. URL is extracted but never fetched."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "Google Cloud Professional Data Engineer\nCredential URL: https://invalid.domain.that.does.not.exist.fake/badge/123")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    # Execution must be completely deterministic and synchronous with no network calls
    res = extract_certification_records(blocks, sec_res, art_id)

    assert res.records[0].credential_url.value == "https://invalid.domain.that.does.not.exist.fake/badge/123"


def test_training_text_not_automatically_verified_certification():
    """20. Training text is not automatically a verified certification."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Certifications", BlockType.HEADING)
    b1 = _create_block(1, "Attended 2-day internal team workshop on microservices.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_certification_records(blocks, sec_res, art_id)

    # Issuer is missing -> flagged as ambiguous issuer warning
    assert any(w.code == CertificationWarningCode.CERTIFICATION_ISSUER_AMBIGUOUS for w in res.records[0].warnings)


# =============================================================================
# 4. LANGUAGE TESTS
# =============================================================================

def test_language_with_explicit_proficiency():
    """21. Language with explicit proficiency."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "English: Native\nSpanish: Professional working proficiency\nFrench: Intermediate")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    assert len(res.languages) == 3
    assert res.languages[0].language.value == "English"
    assert res.languages[0].normalized_proficiency == "NATIVE"
    assert res.languages[1].language.value == "Spanish"
    assert res.languages[1].normalized_proficiency == "PROFESSIONAL_WORKING"
    assert res.languages[2].language.value == "French"
    assert res.languages[2].normalized_proficiency == "INTERMEDIATE"


def test_language_without_proficiency():
    """22. Language without proficiency."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "English, Hindi, German")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    assert len(res.languages) == 3
    assert res.languages[0].language.value == "English"
    assert res.languages[0].proficiency is None
    assert res.languages[1].language.value == "Hindi"
    assert res.languages[1].proficiency is None


def test_multiple_languages():
    """23. Multiple languages."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "• Japanese (Fluent)\n• Mandarin (Basic)")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    assert len(res.languages) == 2
    assert res.languages[0].language.value == "Japanese"
    assert res.languages[0].normalized_proficiency == "FLUENT"
    assert res.languages[1].language.value == "Mandarin"
    assert res.languages[1].normalized_proficiency == "ELEMENTARY"


def test_programming_languages_rejected_from_languages():
    """24. Programming languages are not natural languages."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "Python, Java, C++, TypeScript, English (Fluent)")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    # Only English should be extracted
    assert len(res.languages) == 1
    assert res.languages[0].language.value == "English"
    assert any(w.code == LanguageWarningCode.NON_NATURAL_LANGUAGE_REJECTED for w in res.warnings)


def test_resume_language_does_not_imply_proficiency():
    """25. Resume language does not imply proficiency."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "English")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    assert res.languages[0].language.value == "English"
    assert res.languages[0].proficiency is None
    assert res.languages[0].normalized_proficiency is None


def test_unknown_proficiency_wording_retained_for_review():
    """26. Unknown proficiency wording is retained for review."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "German: Level B2 Passed")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_language_records(blocks, sec_res, art_id)

    assert res.languages[0].language.value == "German"
    assert res.languages[0].proficiency is not None
    assert "Level B2 Passed" in res.languages[0].proficiency.value
    assert res.languages[0].normalized_proficiency is None
    assert any(w.code == LanguageWarningCode.LANGUAGE_PROFICIENCY_UNKNOWN for w in res.languages[0].warnings)


# =============================================================================
# 5. GENERAL TESTS
# =============================================================================

def test_valid_evidence_ids_and_offsets():
    """27. Valid evidence IDs and offsets."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Bachelor of Science in Mathematics\nOxford University\n2018 - 2021")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    for span in res.evidence_spans:
        assert span.character_start >= 0
        assert span.character_end > span.character_start
        assert span.original_text == b1.raw_text[span.character_start:span.character_end]


def test_stable_repeated_output():
    """28. Stable repeated output."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Languages", BlockType.HEADING)
    b1 = _create_block(1, "English (Fluent)\nFrench (Intermediate)")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res1 = extract_language_records(blocks, sec_res, art_id)
    res2 = extract_language_records(blocks, sec_res, art_id)

    assert len(res1.languages) == len(res2.languages)
    assert res1.languages[0].language.value == res2.languages[0].language.value
    assert res1.languages[0].normalized_proficiency == res2.languages[0].normalized_proficiency


def test_no_extracted_values_in_warnings():
    """29. No extracted values in warnings or logs."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "Secret Private College\n2020 - 2022")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_records(blocks, sec_res, art_id)

    for warn in res.warnings:
        # Check warning attributes do not leak candidate raw text
        assert "Secret Private College" not in str(warn.code.value)
        assert warn.field_name in ("degree", "institution", None)


def test_phase_1d4_master_orchestration():
    """30. Phase 1D.4 master orchestration combining all four."""
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Education", BlockType.HEADING)
    b1 = _create_block(1, "B.Tech in Computer Science\nMIT\n2018 - 2022")
    b2 = _create_block(2, "Projects", BlockType.HEADING)
    b3 = _create_block(3, "Search Optimizer\nDeveloped search tool in Python.")
    b4 = _create_block(4, "Certifications", BlockType.HEADING)
    b5 = _create_block(5, "AWS Certified Developer\nIssuer: Amazon Web Services\nIssued: 2023")
    b6 = _create_block(6, "Languages", BlockType.HEADING)
    b7 = _create_block(7, "English: Native")

    blocks = [b0, b1, b2, b3, b4, b5, b6, b7]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_education_projects_certifications_languages(blocks, sec_res, art_id)

    assert res.parser_version == EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION
    assert res.rules_version == EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION
    assert len(res.education.records) == 1
    assert len(res.projects.records) == 1
    assert len(res.certifications.records) == 1
    assert len(res.languages.languages) == 1
    assert len(res.evidence_spans) > 0
