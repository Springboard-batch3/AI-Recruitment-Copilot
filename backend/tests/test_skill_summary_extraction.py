import uuid
from decimal import Decimal
import pytest

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.candidate import ConfidenceBand, ReviewStatus
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import SectionType
from app.schemas.skill_and_summary import (
    SkillCategory,
    SkillResolutionStatus,
    SkillSummaryWarningCode,
)
from app.services.section_detection import detect_sections_from_blocks
from app.services.skill_catalog import (
    DEFAULT_SKILL_CATALOG,
    SKILL_CATALOG_VERSION,
    SkillCatalog,
)
from app.services.skill_summary_extraction import (
    SKILL_SUMMARY_PARSER_VERSION,
    extract_skills_and_summary,
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


# =========================================================================
# 1. Exact skills in an explicit SKILLS section
# =========================================================================

def test_exact_skills_in_skills_section():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Python, Java, TypeScript, Docker, Kubernetes")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    assert res.parser_version == SKILL_SUMMARY_PARSER_VERSION
    assert res.catalog_version == SKILL_CATALOG_VERSION

    skill_names = [s.canonical_value for s in res.skills]
    assert "Python" in skill_names
    assert "Java" in skill_names
    assert "TypeScript" in skill_names
    assert "Docker" in skill_names
    assert "Kubernetes" in skill_names

    py_skill = next(s for s in res.skills if s.canonical_value == "Python")
    assert py_skill.resolution_status == SkillResolutionStatus.RESOLVED_EXACT
    assert py_skill.category == SkillCategory.PROGRAMMING_LANGUAGE
    assert py_skill.confidence_band == ConfidenceBand.HIGH
    assert not py_skill.review_required


# =========================================================================
# 2. Alias normalization (Postgres -> PostgreSQL, ReactJS -> React, sklearn -> scikit-learn)
# =========================================================================

def test_alias_normalization():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Technical Skills", BlockType.HEADING)
    b1 = _create_block(1, "Postgres, ReactJS, sklearn, dotnet, GCP, Amazon Web Services")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_map = {s.canonical_value: s for s in res.skills}

    assert "PostgreSQL" in skill_map
    assert skill_map["PostgreSQL"].resolution_status == SkillResolutionStatus.RESOLVED_ALIAS
    assert "Postgres" in skill_map["PostgreSQL"].raw_values

    assert "React" in skill_map
    assert skill_map["React"].resolution_status == SkillResolutionStatus.RESOLVED_ALIAS
    assert "ReactJS" in skill_map["React"].raw_values

    assert "scikit-learn" in skill_map
    assert skill_map["scikit-learn"].resolution_status == SkillResolutionStatus.RESOLVED_ALIAS
    assert "sklearn" in skill_map["scikit-learn"].raw_values

    assert ".NET" in skill_map
    assert skill_map[".NET"].resolution_status == SkillResolutionStatus.RESOLVED_ALIAS
    assert "dotnet" in skill_map[".NET"].raw_values

    assert "Google Cloud" in skill_map
    assert "AWS" in skill_map


# =========================================================================
# 3. Punctuation-sensitive skills (.NET, C++, C#, Node.js, scikit-learn)
# =========================================================================

def test_punctuation_sensitive_skills():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "C++, C#, .NET, Node.js, scikit-learn")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_map = {s.canonical_value: s for s in res.skills}

    assert "C++" in skill_map
    assert "C#" in skill_map
    assert ".NET" in skill_map
    assert "Node.js" in skill_map
    assert "scikit-learn" in skill_map


# =========================================================================
# 4. Java does not match inside JavaScript
# =========================================================================

def test_java_does_not_match_inside_javascript():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "JavaScript, TypeScript")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_names = [s.canonical_value for s in res.skills]
    assert "JavaScript" in skill_names
    assert "Java" not in skill_names


# =========================================================================
# 5. Short ambiguous terms (Go, R, C) rejected in ordinary prose
# =========================================================================

def test_short_ambiguous_terms_rejected_in_prose():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(
        1,
        "Ready to go to market with product C while maintaining section R compliance. Built microservices with Python.",
    )

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_names = [s.canonical_value for s in res.skills]
    assert "Python" in skill_names
    assert "Go" not in skill_names
    assert "C" not in skill_names
    assert "R" not in skill_names


# =========================================================================
# 6. Defensible short terms accepted in explicit Skills list
# =========================================================================

def test_defensible_short_terms_accepted_in_skills_list():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Python, Go, R, C, SQL")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_names = [s.canonical_value for s in res.skills]
    assert "Go" in skill_names
    assert "R" in skill_names
    assert "C" in skill_names
    assert "Python" in skill_names
    assert "SQL" in skill_names


# =========================================================================
# 7. Skills found in EXPERIENCE and PROJECTS retain context
# =========================================================================

def test_skills_found_in_experience_and_projects_retain_context():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Experience", BlockType.HEADING)
    b1 = _create_block(1, "Developed high throughput APIs using FastAPI and PostgreSQL on AWS.")
    b2 = _create_block(2, "Projects", BlockType.HEADING)
    b3 = _create_block(3, "Created frontend dashboard using React and Redis cache.")

    blocks = [b0, b1, b2, b3]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_map = {s.canonical_value: s for s in res.skills}

    assert "FastAPI" in skill_map
    assert "EXPERIENCE" in skill_map["FastAPI"].sections

    assert "React" in skill_map
    assert "PROJECTS" in skill_map["React"].sections


# =========================================================================
# 8. Duplicate aliases merge into one canonical skill with multiple evidence references
# =========================================================================

def test_duplicate_aliases_merge_with_multiple_evidences():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Postgres, ReactJS")
    b2 = _create_block(2, "Experience", BlockType.HEADING)
    b3 = _create_block(3, "Used PostgreSQL and React for enterprise customer portal.")

    blocks = [b0, b1, b2, b3]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_map = {s.canonical_value: s for s in res.skills}

    assert len(res.skills) == 2

    pg_skill = skill_map["PostgreSQL"]
    assert pg_skill.canonical_value == "PostgreSQL"
    assert "Postgres" in pg_skill.raw_values
    assert "PostgreSQL" in pg_skill.raw_values
    assert len(pg_skill.evidence_ids) == 2
    assert "SKILLS" in pg_skill.sections
    assert "EXPERIENCE" in pg_skill.sections
    assert pg_skill.resolution_status == SkillResolutionStatus.RESOLVED_EXACT  # Upgraded from alias because exact appeared

    react_skill = skill_map["React"]
    assert "ReactJS" in react_skill.raw_values
    assert "React" in react_skill.raw_values
    assert len(react_skill.evidence_ids) == 2


# =========================================================================
# 9. Unknown explicit Skills items become UNRESOLVED
# =========================================================================

def test_unknown_explicit_skills_become_unresolved():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Python, CustomProprietaryFramework, FastEngineXYZ, Docker")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_map = {s.canonical_value: s for s in res.skills}

    assert "CustomProprietaryFramework" in skill_map
    unresolved_item = skill_map["CustomProprietaryFramework"]
    assert unresolved_item.resolution_status == SkillResolutionStatus.UNRESOLVED
    assert unresolved_item.review_required is True

    unresolved_warnings = [
        w for w in res.warnings if w.code == SkillSummaryWarningCode.UNRESOLVED_SKILL_CANDIDATE
    ]
    assert len(unresolved_warnings) >= 2


# =========================================================================
# 10. Unknown words in summary prose are not automatically skills
# =========================================================================

def test_unknown_words_in_summary_prose_are_not_skills():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Professional Summary", BlockType.HEADING)
    b1 = _create_block(
        1,
        "Visionary and results-driven engineering leader with extensive expertise in agile synergy and team motivation.",
    )

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    # None of the random prose words (visionary, synergy, motivation) should become skills
    assert len(res.skills) == 0


# =========================================================================
# 11. Contact values are not skills
# =========================================================================

def test_contact_values_are_not_skills():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(
        1,
        "Python, user@example.com, https://github.com/janedoe, +1-555-0199, Docker",
    )

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_names = [s.canonical_value for s in res.skills]
    assert "Python" in skill_names
    assert "Docker" in skill_names
    assert "user@example.com" not in skill_names
    assert "https://github.com/janedoe" not in skill_names
    assert "+1-555-0199" not in skill_names


# =========================================================================
# 12. Section headings are not skills
# =========================================================================

def test_section_headings_are_not_skills():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Technical Skills:\nPython, Java")
    b1 = _create_block(1, "Experience:\nWorked at Acme Corp.")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    skill_names = [s.canonical_value for s in res.skills]
    assert "Technical Skills" not in skill_names
    assert "Experience" not in skill_names
    assert "Python" in skill_names
    assert "Java" in skill_names


# =========================================================================
# 13. Stable ordering across repeated runs
# =========================================================================

def test_determinism_and_stable_ordering():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Docker, Python, AWS, React, Kubernetes, PostgreSQL")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)

    res1 = extract_skills_and_summary(blocks, sec_res, art_id)
    res2 = extract_skills_and_summary(blocks, sec_res, art_id)

    assert res1.model_dump() == res2.model_dump()
    order1 = [s.canonical_value for s in res1.skills]
    order2 = [s.canonical_value for s in res2.skills]
    assert order1 == order2


# =========================================================================
# 14. Valid evidence block IDs and offsets
# =========================================================================

def test_valid_evidence_block_ids_and_offsets():
    art_id = str(uuid.uuid4())
    raw_skills_text = "Skills: Python, TypeScript, Docker"
    b0 = _create_block(0, raw_skills_text)

    blocks = [b0]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    assert len(res.evidence_spans) > 0
    for span in res.evidence_spans:
        assert span.resume_artifact_id == art_id
        assert span.block_number == 0
        assert span.character_start >= 0
        assert span.character_end <= len(raw_skills_text)
        assert span.character_end > span.character_start
        assert raw_skills_text[span.character_start : span.character_end] == span.original_text


# =========================================================================
# 15. Missing SUMMARY returns a valid empty/null result
# =========================================================================

def test_missing_summary_returns_valid_null():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, "Python, SQL")

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    assert res.summary is not None
    assert res.summary.summary_field.value is None
    assert res.summary.summary_field.confidence is None
    assert res.summary.normalized_text is None
    assert any(w.code == SkillSummaryWarningCode.SUMMARY_NOT_FOUND for w in res.warnings)


# =========================================================================
# 16. Multiple summary blocks preserve order
# =========================================================================

def test_multiple_summary_blocks_preserve_order():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Professional Summary", BlockType.HEADING)
    b1 = _create_block(1, "Paragraph 1: Experienced software engineer with backend focus.")
    b2 = _create_block(2, "Paragraph 2: Passionate about cloud architecture and distributed systems.")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    assert res.summary is not None
    assert res.summary.summary_field.value is not None
    assert "Paragraph 1:" in res.summary.summary_field.value
    assert "Paragraph 2:" in res.summary.summary_field.value
    assert res.summary.summary_field.value.index("Paragraph 1:") < res.summary.summary_field.value.index("Paragraph 2:")
    assert len(res.summary.evidence_ids) == 2


# =========================================================================
# 17. Professional summary text is not rewritten
# =========================================================================

def test_professional_summary_text_not_rewritten():
    art_id = str(uuid.uuid4())
    summary_text = "Experienced Python & C++ architect with 10+ years in distributed systems."
    b0 = _create_block(0, "Summary", BlockType.HEADING)
    b1 = _create_block(1, summary_text)

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    assert res.summary.summary_field.value == summary_text
    assert res.summary.raw_text == summary_text
    assert res.summary.normalized_text == summary_text


# =========================================================================
# 18. Defensible headline extraction
# =========================================================================

def test_defensible_headline_extraction():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Jane Doe\nSenior Python Developer\njane@example.com")
    b1 = _create_block(1, "Skills", BlockType.HEADING)
    b2 = _create_block(2, "Python, Django")

    blocks = [b0, b1, b2]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id, candidate_name="Jane Doe")

    assert res.headline is not None
    assert res.headline.headline_field.value == "Senior Python Developer"
    assert res.headline.confidence == 0.90
    assert len(res.headline.evidence_ids) == 1


# =========================================================================
# 19. Ambiguous headline produces a review warning
# =========================================================================

def test_ambiguous_headline_produces_warning():
    art_id = str(uuid.uuid4())
    b0 = _create_block(0, "Jane Doe\nFull-stack Developer\nMachine Learning Engineer")

    blocks = [b0]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id, candidate_name="Jane Doe")

    assert res.headline.headline_field.value is None
    warning_codes = [w.code for w in res.warnings]
    assert SkillSummaryWarningCode.HEADLINE_AMBIGUOUS in warning_codes


# =========================================================================
# 20. Warnings/logs do not contain fixture summary or skill-bearing sentences
# =========================================================================

def test_warnings_do_not_leak_sentences():
    art_id = str(uuid.uuid4())
    secret_skill_sentence = "SecretSpecialCandidateProseThatMustNotBeInWarningOutput"
    b0 = _create_block(0, "Skills", BlockType.HEADING)
    b1 = _create_block(1, secret_skill_sentence)

    blocks = [b0, b1]
    sec_res = detect_sections_from_blocks(blocks)
    res = extract_skills_and_summary(blocks, sec_res, art_id)

    for warning in res.warnings:
        warn_dump = str(warning.model_dump())
        assert secret_skill_sentence not in warn_dump
