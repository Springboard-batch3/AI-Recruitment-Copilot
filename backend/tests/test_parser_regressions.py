import hashlib
from pathlib import Path
from uuid import uuid4
import pytest

from app.config import Settings
from app.models.resume import ArtifactFormat, ArtifactValidationStatus, ResumeArtifact
from app.services.docx_text_extraction import extract_docx_text
from app.services.education_project_certification_extraction import (
    extract_certification_records,
    extract_education_projects_certifications_languages,
    extract_education_records,
    extract_language_records,
    extract_project_records,
)
from app.services.employment_extraction import (
    _is_table_header_text,
    extract_employment_records,
)
from app.services.identity_extraction import extract_identity_and_contacts
from app.services.pdf_text_extraction import extract_pdf_direct_text
from app.services.section_detection import detect_sections_from_blocks, match_heading_candidate
from app.services.skill_summary_extraction import (
    _is_unresolved_candidate_defensible,
    extract_skills_and_summary,
)

EVALUATION_DOCS_DIR = Path(__file__).resolve().parent / "fixtures" / "evaluation" / "documents"


@pytest.fixture
def settings():
    return Settings()


from app.schemas.extraction import ExtractionResult
from app.services.text_normalization import normalize_extraction_result


def _load_and_extract_docx(file_name: str, settings: Settings):
    src = EVALUATION_DOCS_DIR / file_name
    content = src.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    art_id = uuid4()
    target = settings.get_quarantine_path() / "docx" / f"{art_id}.docx"
    target.write_bytes(content)
    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename=file_name,
        size_bytes=src.stat().st_size,
        sha256_hash=sha,
        declared_mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        quarantine_key=f"docx/{art_id}.docx",
        detected_format=ArtifactFormat.docx,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = extract_docx_text(art, settings)
    extraction_res = ExtractionResult(
        extractor_version=ext.extractor_version,
        blocks=ext.blocks,
    )
    norm = normalize_extraction_result(extraction_res)
    return norm.extraction_result, str(art_id)


def _load_and_extract_pdf(file_name: str, settings: Settings):
    src = EVALUATION_DOCS_DIR / file_name
    content = src.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    art_id = uuid4()
    target = settings.get_quarantine_path() / "pdf" / f"{art_id}.pdf"
    target.write_bytes(content)
    art = ResumeArtifact(
        artifact_id=art_id,
        original_filename=file_name,
        size_bytes=src.stat().st_size,
        sha256_hash=sha,
        declared_mime_type="application/pdf",
        quarantine_key=f"pdf/{art_id}.pdf",
        detected_format=ArtifactFormat.pdf,
        validation_status=ArtifactValidationStatus.VALIDATED,
    )
    ext = extract_pdf_direct_text(art, settings)
    extraction_res = ExtractionResult(
        extractor_version=ext.extractor_version,
        blocks=ext.blocks,
    )
    norm = normalize_extraction_result(extraction_res)
    return norm.extraction_result, str(art_id)


def test_section_heading_singular_and_plural_aliases():
    """Verify singular and plural section aliases are detected deterministically."""
    assert match_heading_candidate("PROJECT") is not None
    assert match_heading_candidate("PROJECTS") is not None
    assert match_heading_candidate("SELECTED PROJECT") is not None
    assert match_heading_candidate("SELECTED PROJECTS") is not None
    assert match_heading_candidate("CERTIFICATION") is not None
    assert match_heading_candidate("CERTIFICATIONS") is not None
    assert match_heading_candidate("LANGUAGE") is not None
    assert match_heading_candidate("LANGUAGES") is not None
    assert match_heading_candidate("TECHNICAL TOOLKIT") is not None


def test_table_header_detection():
    """Verify structural table-header rows are identified and skipped."""
    assert _is_table_header_text("Role and organization") is True
    assert _is_table_header_text("Dates") is True
    assert _is_table_header_text("Responsibilities and outcomes") is True
    assert _is_table_header_text("Data Engineer") is False
    assert _is_table_header_text("Northstar Transit Labs") is False


def test_skill_boundary_rejects_headings():
    """Verify heading aliases and project titles are rejected from defensible skill candidates."""
    assert _is_unresolved_candidate_defensible("SELECTED PROJECT") is False
    assert _is_unresolved_candidate_defensible("CERTIFICATION") is False
    assert _is_unresolved_candidate_defensible("TECHNICAL TOOLKIT") is False
    assert _is_unresolved_candidate_defensible("dbt") is True


def test_table_reordered_docx_integrity_fixture_regression(settings):
    """Verify complete, exact profile extraction on Phase_1E2_Integrity_Table_Reordered_Resume.docx."""
    ext, art_id = _load_and_extract_docx("Phase_1E2_Integrity_Table_Reordered_Resume.docx", settings)
    sec = detect_sections_from_blocks(ext.blocks)
    id_res = extract_identity_and_contacts(ext.blocks, sec, art_id)
    sk_res = extract_skills_and_summary(ext.blocks, sec, art_id, candidate_name=id_res.identity.full_name.value)
    emp_res = extract_employment_records(ext.blocks, sec, art_id)
    other_res = extract_education_projects_certifications_languages(ext.blocks, sec, art_id, sk_res.skills)

    # 1. Identity & Location
    assert id_res.identity.full_name.value == "AARAV MEHTA"
    assert id_res.identity.location.value == "Los Angeles, CA"
    assert len(id_res.identity.emails) == 1
    assert id_res.identity.emails[0].value == "aarav.mehta@example.org"
    assert len(id_res.identity.phone_numbers) == 1
    assert id_res.identity.phone_numbers[0].value == "+1 202-555-0114"
    assert len(id_res.warnings) == 0

    # 2. Headline & Summary & Skills
    assert sk_res.headline.headline_field.value == "Data Engineer"
    assert sk_res.summary.summary_field.value is not None
    assert len(sk_res.summary.summary_field.value) > 50

    canonical_skills = [s.canonical_value for s in sk_res.skills]
    assert len(canonical_skills) == 8
    for expected_skill in ["Python", "SQL", "PostgreSQL", "Apache Spark", "Apache Airflow", "Docker", "AWS", "dbt"]:
        assert expected_skill in canonical_skills

    # 3. Employment (Table headers skipped, exactly 2 records)
    assert len(emp_res.records) == 2
    assert emp_res.records[0].job_title.value == "Data Engineer"
    assert emp_res.records[0].organization.value == "Northstar Transit Labs"
    assert emp_res.records[0].start_date.value == "June 2024"
    assert emp_res.records[0].end_date.value == "Present"

    assert emp_res.records[1].job_title.value == "Junior Data Engineer"
    assert emp_res.records[1].organization.value == "BluePeak Analytics"
    assert emp_res.records[1].start_date.value == "July 2022"
    assert emp_res.records[1].end_date.value == "May 2024"

    # 4. Education (Combined line parsed)
    assert len(other_res.education.records) == 1
    edu = other_res.education.records[0]
    assert edu.degree.value == "Bachelor of Science"
    assert edu.field_of_study is not None and edu.field_of_study.value == "Computer Science"
    assert edu.institution.value == "Western Coast University"
    assert edu.start_date is not None and edu.start_date.value == "2018"
    assert edu.end_date is not None and edu.end_date.value == "2022"
    assert len(other_res.education.warnings) == 0

    # 5. Project (SELECTED PROJECT parsed)
    assert len(other_res.projects.records) == 1
    proj = other_res.projects.records[0]
    assert proj.name.value == "Transit Reliability Warehouse"
    assert "dbt-based warehouse" in (proj.description.value or "")
    assert len(other_res.projects.warnings) == 0

    # 6. Certification (Pipe-separated line parsed)
    assert len(other_res.certifications.records) == 1
    cert = other_res.certifications.records[0]
    assert cert.name.value == "AWS Certified Data Engineer – Associate"
    assert cert.issuer is not None and cert.issuer.value == "Amazon Web Services"
    assert cert.issued_date is not None and "March 2025" in cert.issued_date.value
    assert len(other_res.certifications.warnings) == 0

    # 7. Languages (Pipe split & proficiency mapped)
    assert len(other_res.languages.languages) == 2
    lang_map = {l.language.value: (l.proficiency.value if l.proficiency else None) for l in other_res.languages.languages}
    assert "English" in lang_map
    assert "Hindi" in lang_map
    assert lang_map["English"] == "Professional proficiency"
    assert lang_map["Hindi"] == "Native proficiency"
    assert len(other_res.languages.warnings) == 0


    # 8. Verify zero UNRESOLVED_SKILL_CANDIDATE warnings on DOCX fixture
    unresolved_warnings = [w for w in sk_res.warnings if w.code.value == "UNRESOLVED_SKILL_CANDIDATE"]
    assert len(unresolved_warnings) == 0


def test_two_column_pdf_integrity_fixture_regression(settings):
    """Verify complete, exact profile extraction on Phase_1E2_Integrity_Two_Column_Resume.pdf."""
    ext, art_id = _load_and_extract_pdf("Phase_1E2_Integrity_Two_Column_Resume.pdf", settings)
    sec = detect_sections_from_blocks(ext.blocks)
    id_res = extract_identity_and_contacts(ext.blocks, sec, art_id)
    sk_res = extract_skills_and_summary(ext.blocks, sec, art_id, candidate_name=id_res.identity.full_name.value)
    emp_res = extract_employment_records(ext.blocks, sec, art_id)
    other_res = extract_education_projects_certifications_languages(ext.blocks, sec, art_id, sk_res.skills)

    # 1. Identity & Location
    assert id_res.identity.full_name.value == "NISHA RAO"
    assert id_res.identity.location.value == "Seattle, WA"
    assert len(id_res.identity.emails) == 1
    assert id_res.identity.emails[0].value == "nisha.rao@example.org"
    assert len(id_res.identity.phone_numbers) == 1
    assert id_res.identity.phone_numbers[0].value == "+1 202-555-0176"
    assert len(id_res.warnings) == 0

    # 2. Headline & Summary & Skills
    assert sk_res.headline.headline_field.value == "Frontend Developer"
    assert sk_res.summary.summary_field.value is not None
    assert len(sk_res.summary.summary_field.value) > 50

    canonical_skills = [s.canonical_value for s in sk_res.skills]
    assert len(canonical_skills) == 9
    for expected_skill in ["JavaScript", "TypeScript", "React", "Redux", "HTML", "CSS", "Jest", "Vite", "Git"]:
        assert expected_skill in canonical_skills

    # 3. Employment (Exactly 2 records)
    assert len(emp_res.records) == 2
    assert emp_res.records[0].job_title.value == "Senior Frontend Developer"
    assert emp_res.records[0].organization.value == "Harbor Civic Systems"
    assert emp_res.records[0].start_date.value == "January 2024"
    assert emp_res.records[0].end_date.value == "Present"

    assert emp_res.records[1].job_title.value == "Frontend Developer"
    assert emp_res.records[1].organization.value == "Cedar Commerce Studio"
    assert emp_res.records[1].start_date.value == "August 2021"
    assert emp_res.records[1].end_date.value == "December 2023"

    # 4. Education (Bachelor of Technology at Coastal Institute of Technology)
    assert len(other_res.education.records) == 1
    edu = other_res.education.records[0]
    assert edu.degree.value == "Bachelor of Technology"
    assert edu.field_of_study is not None and edu.field_of_study.value == "Information Technology"
    assert edu.institution.value == "Coastal Institute of Technology"
    assert edu.start_date is not None and edu.start_date.value == "2017"
    assert edu.end_date is not None and edu.end_date.value == "2021"
    assert len(other_res.education.warnings) == 0

    # 5. Projects (2 projects, continuations attached)
    assert len(other_res.projects.records) == 2
    proj_names = [p.name.value for p in other_res.projects.records]
    assert "Accessible Service Portal" in proj_names
    assert "Retail Performance Console" in proj_names
    assert len(other_res.projects.warnings) == 0

    # 6. Certification (Meta Front-End Developer Professional Certificate issued by Meta)
    assert len(other_res.certifications.records) == 1
    cert = other_res.certifications.records[0]
    assert cert.name.value == "Meta Front-End Developer Professional Certificate"
    assert cert.issuer is not None and cert.issuer.value == "Meta"
    assert cert.issued_date is not None and "November 2024" in cert.issued_date.value
    assert len(other_res.certifications.warnings) == 0

    # 7. Languages (English & Telugu with proficiency)
    assert len(other_res.languages.languages) == 2
    lang_map = {l.language.value: (l.proficiency.value if l.proficiency else None) for l in other_res.languages.languages}
    assert "English" in lang_map
    assert "Telugu" in lang_map
    assert lang_map["English"] == "Professional"
    assert lang_map["Telugu"] == "Native"
    assert len(other_res.languages.warnings) == 0

    # 8. Verify zero UNRESOLVED_SKILL_CANDIDATE warnings on PDF fixture
    unresolved_warnings = [w for w in sk_res.warnings if w.code.value == "UNRESOLVED_SKILL_CANDIDATE"]
    assert len(unresolved_warnings) == 0


def test_skill_catalog_mappings_and_aliases():
    """Verify Redux, Jest, Vite, Spark, Airflow, dbt, Power BI and their approved aliases in skill catalog."""
    from app.services.skill_catalog import DEFAULT_SKILL_CATALOG, SKILL_CATALOG_VERSION

    assert SKILL_CATALOG_VERSION == "skill-catalog-v1.3"

    # Redux
    lookup = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Redux")
    assert lookup is not None
    assert lookup[2] == "Redux"
    assert lookup[1] is False  # exact

    lookup_alias = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("ReduxJS")
    assert lookup_alias is not None
    assert lookup_alias[2] == "Redux"
    assert lookup_alias[1] is True  # alias

    # Jest
    lookup_jest = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Jest")
    assert lookup_jest is not None
    assert lookup_jest[2] == "Jest"
    assert lookup_jest[1] is False

    # Vite
    lookup_vite = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Vite")
    assert lookup_vite is not None
    assert lookup_vite[2] == "Vite"
    assert lookup_vite[1] is False

    # Power BI
    lookup_pbi = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Power BI")
    assert lookup_pbi is not None
    assert lookup_pbi[2] == "Power BI"
    assert lookup_pbi[1] is False

    lookup_pbi_alias = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("PowerBI")
    assert lookup_pbi_alias is not None
    assert lookup_pbi_alias[2] == "Power BI"

    lookup_pbi_alias2 = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Microsoft Power BI")
    assert lookup_pbi_alias2 is not None
    assert lookup_pbi_alias2[2] == "Power BI"

    # Apache Spark
    lookup_spark = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Apache Spark")
    assert lookup_spark is not None
    assert lookup_spark[2] == "Apache Spark"
    assert lookup_spark[1] is False

    lookup_spark_alias = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("pyspark")
    assert lookup_spark_alias is not None
    assert lookup_spark_alias[2] == "Apache Spark"

    # Apache Airflow
    lookup_airflow = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Apache Airflow")
    assert lookup_airflow is not None
    assert lookup_airflow[2] == "Apache Airflow"
    assert lookup_airflow[1] is False

    lookup_airflow_alias = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("airflow")
    assert lookup_airflow_alias is not None
    assert lookup_airflow_alias[2] == "Apache Airflow"

    # dbt
    lookup_dbt = DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("dbt")
    assert lookup_dbt is not None
    assert lookup_dbt[2] == "dbt"
    assert lookup_dbt[1] is False


def test_unresolved_skill_candidate_preservation_and_confidence():
    """Verify an unknown skill like ZorbLang is preserved with evidence and doesn't trigger extraction review."""
    from uuid import uuid4
    from app.models.extraction import BlockType, ExtractionMethod
    from app.schemas.extraction import ExtractionBlockData
    from app.schemas.section_and_identity import DetectedSection, SectionDetectionResult, SectionType
    from app.services.skill_summary_extraction import extract_skills_and_summary, DEFAULT_SKILL_CATALOG
    from app.services.profile_assembly import assemble_candidate_profile
    from app.schemas.skill_and_summary import SkillResolutionStatus

    art_id = str(uuid4())
    blocks = [
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            structural_location="col_0/para/0",
            block_type=BlockType.HEADING,
            raw_text="TECHNICAL SKILLS",
            normalized_text="TECHNICAL SKILLS",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
        ExtractionBlockData(
            sequence_index=1,
            page_number=1,
            structural_location="col_0/para/1",
            block_type=BlockType.PARAGRAPH,
            raw_text="Python | ZorbLang | PostgreSQL",
            normalized_text="Python | ZorbLang | PostgreSQL",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
    ]
    sec_res = SectionDetectionResult(
        parser_version="section-detect-v1.2",
        sections=[DetectedSection(section_type=SectionType.SKILLS, start_sequence_index=0, end_sequence_index=1)],
    )

    sk_res = extract_skills_and_summary(blocks, sec_res, art_id)
    assert len(sk_res.skills) == 3

    # Check ZorbLang
    zorb = next(s for s in sk_res.skills if s.canonical_value == "ZorbLang")
    assert zorb.resolution_status == SkillResolutionStatus.UNRESOLVED
    assert zorb.confidence == 0.95  # Explicit extraction confidence
    assert len(zorb.evidence_ids) == 1

    # Check that ZorbLang is NOT in the default catalog
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("ZorbLang") is None

    # Check that warning is produced
    assert any(w.code.value == "UNRESOLVED_SKILL_CANDIDATE" for w in sk_res.warnings)

    # Check profile assembly does not flag generic parser review_required
    profile_res = assemble_candidate_profile(blocks, art_id)
    assert profile_res.review_required is False


def test_current_role_deterministic_duration_reference():
    """Verify deterministic as_of_date calculates current role duration without live clock."""
    from uuid import uuid4
    from app.models.extraction import BlockType, ExtractionMethod
    from app.schemas.extraction import ExtractionBlockData
    from app.schemas.section_and_identity import DetectedSection, SectionDetectionResult, SectionType
    from app.services.employment_extraction import extract_employment_records
    from app.services.profile_assembly import assemble_candidate_profile

    art_id = str(uuid4())
    blocks = [
        ExtractionBlockData(
            sequence_index=0,
            page_number=1,
            structural_location="p/0",
            block_type=BlockType.HEADING,
            raw_text="EXPERIENCE",
            normalized_text="EXPERIENCE",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
        ExtractionBlockData(
            sequence_index=1,
            page_number=1,
            structural_location="p/1",
            block_type=BlockType.PARAGRAPH,
            raw_text="Senior Engineer\nAcme Corp\nJanuary 2024 – Present",
            normalized_text="Senior Engineer\nAcme Corp\nJanuary 2024 – Present",
            extraction_method=ExtractionMethod.PDF_TEXT,
        ),
    ]
    sec_res = SectionDetectionResult(
        parser_version="section-detect-v1.2",
        sections=[DetectedSection(section_type=SectionType.EXPERIENCE, start_sequence_index=1, end_sequence_index=1)],
    )

    # 1. With as_of_date "2024-06" -> 5 months (Jan 2024 to June 2024)
    emp_res = extract_employment_records(blocks, sec_res, art_id, as_of_date="2024-06")
    assert len(emp_res.records) == 1
    assert emp_res.records[0].is_current is True
    assert emp_res.records[0].duration_months == 5
    assert emp_res.as_of_date == "2024-06"
    assert not any(w.code.value == "CURRENT_ROLE_REQUIRES_AS_OF_DATE" for w in emp_res.warnings)

    # 2. Assembled profile preserves exact records and idempotency across repeated runs
    prof1 = assemble_candidate_profile(blocks, art_id, as_of_date="2024-06")
    prof2 = assemble_candidate_profile(blocks, art_id, as_of_date="2024-06")
    assert len(prof1.professional_profile.employment) == 1
    assert prof1.professional_profile.employment[0].job_title.value == "Senior Engineer"
    assert prof1.professional_profile.employment[0].start_date.normalized_value == "2024-01"
    assert prof1.professional_profile.employment[0].end_date.value == "Present"
    assert prof1.parser_pipeline_version == prof2.parser_pipeline_version


def test_meera_shah_structure_and_logical_assembly_regression():
    """Verify Meera Shah structure extraction, compound line parsing, and certification assembly."""
    from uuid import uuid4
    from app.models.extraction import BlockType, ExtractionMethod
    from app.schemas.extraction import ExtractionBlockData
    from app.services.profile_assembly import assemble_candidate_profile

    art_id = str(uuid4())
    # 43 extraction blocks mirroring Meera Shah DOCX structure
    raw_texts = [
        ("HEADER", BlockType.PARAGRAPH, "Meera Shah"),
        ("HEADER", BlockType.PARAGRAPH, "meera.shah@example.com | +1 415-555-0186 | linkedin.com/in/meerashah-data"),
        ("HEADER", BlockType.PARAGRAPH, "Senior Data Analyst"),
        ("SUMMARY_HD", BlockType.HEADING, "PROFESSIONAL SUMMARY"),
        ("SUMMARY_BODY", BlockType.PARAGRAPH, "Senior Data Analyst with 5+ years of experience leading analytics initiatives across retail and logistics."),
        ("SKILLS_HD", BlockType.HEADING, "TECHNICAL SKILLS"),
        ("SKILLS_BODY", BlockType.PARAGRAPH, "Python, SQL, PostgreSQL, pandas, NumPy, scikit-learn, Power BI, Docker, Git"),
        ("EXP_HD", BlockType.HEADING, "EXPERIENCE"),
        ("EXP_1_HDR", BlockType.PARAGRAPH, "Northstar Retail Analytics - Senior Data Analyst"),
        ("EXP_1_DATE", BlockType.PARAGRAPH, "April 2023 - Present"),
        ("EXP_1_B1", BlockType.PARAGRAPH, "• Built automated anomaly detection pipelines for daily transaction data using pandas and SQL."),
        ("EXP_1_B2", BlockType.PARAGRAPH, "• Partnered with marketing leadership to redesign core customer KPI reporting in Power BI."),
        ("EXP_2_HDR", BlockType.PARAGRAPH, "Harbor Logistics - Data Analyst"),
        ("EXP_2_DATE", BlockType.PARAGRAPH, "July 2020 - March 2023"),
        ("EXP_2_B1", BlockType.PARAGRAPH, "• Analyzed shipment turnaround data across 14 hubs using PostgreSQL and Python."),
        ("EXP_2_B2", BlockType.PARAGRAPH, "• Maintained scheduled ETL jobs and created dashboards for operations leadership."),
        ("EDU_HD", BlockType.HEADING, "EDUCATION"),
        ("EDU_1_DEG", BlockType.PARAGRAPH, "Master of Science in Data Analytics"),
        ("EDU_1_INST", BlockType.PARAGRAPH, "University of California, Berkeley | 2018 - 2020"),
        ("EDU_2_DEG", BlockType.PARAGRAPH, "Bachelor of Science in Statistics"),
        ("EDU_2_INST", BlockType.PARAGRAPH, "San Jose State University | 2014 - 2018"),
        ("PROJ_HD", BlockType.HEADING, "PROJECTS"),
        ("PROJ_1_TITLE", BlockType.PARAGRAPH, "Delivery Delay Forecast"),
        ("PROJ_1_DESC", BlockType.PARAGRAPH, "Trained scikit-learn classification models to predict late deliveries, achieving 87% accuracy."),
        ("PROJ_2_TITLE", BlockType.PARAGRAPH, "Retail KPI Monitor"),
        ("PROJ_2_DESC", BlockType.PARAGRAPH, "Designed real-time sales and inventory tracking dashboards in Power BI."),
        ("CERT_HD", BlockType.HEADING, "CERTIFICATIONS"),
        ("CERT_1_TITLE", BlockType.PARAGRAPH, "Microsoft Certified: Power BI Data Analyst Associate"),
        ("CERT_1_META", BlockType.PARAGRAPH, "Issuer: Microsoft | Issued: September 2024 | Credential ID: PL300-EXAMPLE"),
        ("CERT_2_TITLE", BlockType.PARAGRAPH, "Google Advanced Data Analytics Professional Certificate"),
        ("CERT_2_META", BlockType.PARAGRAPH, "Issuer: Google | Issued: June 2023"),
        ("LANG_HD", BlockType.HEADING, "LANGUAGES"),
        ("LANG_BODY", BlockType.PARAGRAPH, "English - Fluent | Hindi - Professional working proficiency | Gujarati - Native"),
        ("FOOTER", BlockType.FOOTER, "Synthetic Candidate Data - For Evaluation and Testing Purposes Only"),
    ]

    blocks = []
    for idx, (label, b_type, text) in enumerate(raw_texts):
        loc = "section/0/footer/default/paragraph/0" if b_type == BlockType.FOOTER else f"body/p/{idx}"
        blocks.append(
            ExtractionBlockData(
                sequence_index=idx,
                page_number=1,
                structural_location=loc,
                block_type=b_type,
                raw_text=text,
                normalized_text=text,
                extraction_method=ExtractionMethod.DOCX_TEXT,
            )
        )

    profile_res = assemble_candidate_profile(blocks, art_id, as_of_date="2026-03")

    # 1. Identity & Location
    assert profile_res.identity.full_name.value == "Meera Shah"
    assert profile_res.identity.location.value is None
    assert profile_res.identity.emails[0].value == "meera.shah@example.com"
    assert profile_res.identity.phone_numbers[0].value == "+1 415-555-0186"

    # 2. Headline & Summary & Skills
    prof = profile_res.professional_profile
    assert prof.headline.value == "Senior Data Analyst"
    assert "Senior Data Analyst with 5+ years" in prof.summary.value
    assert len(prof.skills) == 9
    skill_names = [s.normalized_value for s in prof.skills]
    for expected in ["Python", "SQL", "PostgreSQL", "pandas", "NumPy", "scikit-learn", "Power BI", "Docker", "Git"]:
        assert expected in skill_names

    # 3. Employment (2 records, exact role & org separated by ASCII hyphen)
    assert len(prof.employment) == 2
    e1 = prof.employment[0]
    assert e1.job_title.value == "Senior Data Analyst"
    assert e1.organization.value == "Northstar Retail Analytics"
    assert e1.end_date.value == "Present"

    e2 = prof.employment[1]
    assert e2.job_title.value == "Data Analyst"
    assert e2.organization.value == "Harbor Logistics"
    assert "2023" in e2.end_date.value

    # 4. Education (2 records)
    assert len(prof.education) == 2
    assert prof.education[0].degree.value == "Master of Science"
    assert prof.education[0].field_of_study.value == "Data Analytics"
    assert prof.education[1].degree.value == "Bachelor of Science"
    assert prof.education[1].field_of_study.value == "Statistics"

    # 5. Projects (2 records)
    assert len(prof.projects) == 2
    p_names = [p.name.value for p in prof.projects]
    assert "Delivery Delay Forecast" in p_names
    assert "Retail KPI Monitor" in p_names

    # 6. Certifications (2 records with issuer and metadata attached)
    assert len(prof.certifications) == 2
    c1 = prof.certifications[0]
    assert c1.name.value == "Microsoft Certified: Power BI Data Analyst Associate"
    assert c1.issuer.value == "Microsoft"
    assert c1.credential_id.value == "PL300-EXAMPLE"

    c2 = prof.certifications[1]
    assert c2.name.value == "Google Advanced Data Analytics Professional Certificate"
    assert c2.issuer.value == "Google"

    # 7. Languages (3 records)
    assert len(prof.languages) == 3
    l_names = {l.value: l.normalized_value for l in prof.languages}
    assert "English" in l_names
    assert "Hindi" in l_names
    assert "Gujarati" in l_names

    # 8. Warnings & Review Required
    assert len(profile_res.warnings) == 0
    assert profile_res.review_required is False
