


import uuid
import pytest
from sqlalchemy import select

from app.models.extraction import BlockType, ExtractionMethod
from app.schemas.extraction import ExtractionBlockData
from app.schemas.section_and_identity import SectionType
from app.services.section_detection import detect_sections_from_blocks
from app.services.employment_extraction import extract_employment_records
from app.services.education_project_certification_extraction import extract_education_projects_certifications_languages
from app.services.skill_summary_extraction import extract_skills_and_summary
from app.services.profile_assembly import assemble_candidate_profile
from app.services.skill_catalog import DEFAULT_SKILL_CATALOG, load_active_onet_catalog


KABIR_SETHI_BLOCKS = [
    (0, "Kabir Sethi", BlockType.PARAGRAPH),
    (1, "Operations Analyst", BlockType.PARAGRAPH),
    (2, "Jaipur, Rajasthan | kabir.sethi@example.com | linkedin.com/in/kabir-sethi-demo", BlockType.PARAGRAPH),
    (3, "PROFESSIONAL SUMMARY", BlockType.HEADING),
    (
        4,
        "Operations analyst with 3 years of experience reviewing procedures, tracking service metrics, preparing reports, and\n"
        "coordinating daily issue follow-up. Demonstrates critical thinking, reading comprehension, writing, active listening, and\n"
        "operational monitoring. Offers transferable analytical experience but limited formal system analysis and limited\n"
        "ownership of complex technical problem solving.",
        BlockType.PARAGRAPH,
    ),
    (
        5,
        "CAPABILITY PROFILE\n"
        "STRONG: Reading comprehension, writing, active listening, coordination, routine monitoring\n"
        "TRANSFERABLE: Critical thinking, judgment and decision making, active learning\n"
        "LIMITED EVIDENCE: Formal system analysis, software requirements modelling, complex multi-system problem solving",
        BlockType.PARAGRAPH,
    ),
    (6, "PROFESSIONAL EXPERIENCE", BlockType.HEADING),
    (
        7,
        "Operations Analyst | CedarLine Logistics | September 2023 - Present\n"
        "Review daily service reports and shipment exceptions, verify source records, and prepare concise summaries for\n"
        "operations managers.\n"
        "Maintain Excel and Power BI trackers for turnaround time, backlog, and error rates; flagged a recurring handoff delay\n"
        "that improved on-time processing by 9% after a procedure change.\n"
        "Listen to branch-user concerns, clarify facts, and coordinate follow-up with customer service, finance, and warehouse\n"
        "teams.\n"
        "Compare routine response options using urgency, customer impact, and available capacity; escalate higher-risk\n"
        "decisions to the operations manager.\n"
        "Write standard operating procedure updates and short training notes after reviewing policy and process changes.",
        BlockType.PARAGRAPH,
    ),
    (
        8,
        "Operations Coordinator | SilverRoute Services | July 2022 - August 2023\n"
        "Tracked work queues and prepared weekly performance summaries for a 25-person service unit.\n"
        "Coordinated schedules and issue ownership across three teams, following open actions through completion.\n"
        "Supported a ticketing-system rollout by attending training, testing standard workflows, and compiling user feedback;\n"
        "did not design system requirements or architecture.",
        BlockType.PARAGRAPH,
    ),
    (
        9,
        "EDUCATION AND DEVELOPMENT\n"
        "B.Com in Business Administration, University of Rajasthan, 2022\n"
        "Professional development: Advanced Excel for Operations, Power BI Essentials, Fundamentals of Process Mapping",
        BlockType.PARAGRAPH,
    ),
    (
        10,
        "TOOLS\n"
        "Microsoft Excel, Power BI, Microsoft Teams, SharePoint, basic SQL, ticketing and workflow systems",
        BlockType.PARAGRAPH,
    ),
]


def _build_blocks():
    block_datas = []
    for seq, text, b_type in KABIR_SETHI_BLOCKS:
        block_datas.append(
            ExtractionBlockData(
                block_id=uuid.uuid4(),
                sequence_index=seq,
                page_number=1,
                structural_location=f"/page/1/block/{seq}",
                block_type=b_type,
                raw_text=text,
                normalized_text=text,
                extraction_method=ExtractionMethod.PDF_TEXT,
            )
        )
    return block_datas


def test_section_detection_boundaries():
    blocks = _build_blocks()
    sec_res = detect_sections_from_blocks(blocks)

    sec_map = {s.section_type: s for s in sec_res.sections}
    assert SectionType.SUMMARY in sec_map
    assert SectionType.SKILLS in [s.section_type for s in sec_res.sections]
    assert SectionType.EXPERIENCE in sec_map
    assert SectionType.EDUCATION in sec_map

    # Capability profile begins a capability/skill section
    cap_sec = next(s for s in sec_res.sections if s.heading and "capability" in s.heading.normalized_heading_text)
    assert cap_sec.section_type == SectionType.SKILLS
    assert cap_sec.start_sequence_index == 5
    assert cap_sec.end_sequence_index == 5

    # Professional summary ends before capability profile
    sum_sec = sec_map[SectionType.SUMMARY]
    assert sum_sec.end_sequence_index == 4

    # Experience starts at 6 and ends at 8 (before EDUCATION AND DEVELOPMENT at 9)
    exp_sec = sec_map[SectionType.EXPERIENCE]
    assert exp_sec.start_sequence_index == 6
    assert exp_sec.end_sequence_index == 8

    # Education is at 9
    edu_sec = sec_map[SectionType.EDUCATION]
    assert edu_sec.start_sequence_index == 9
    assert edu_sec.end_sequence_index == 9

    # Tools is a skills section at 10
    tools_sec = next(s for s in sec_res.sections if s.heading and "tools" in s.heading.normalized_heading_text)
    assert tools_sec.section_type == SectionType.SKILLS
    assert tools_sec.start_sequence_index == 10


def test_employment_extraction_exactly_two_records():
    blocks = _build_blocks()
    sec_res = detect_sections_from_blocks(blocks)
    emp_res = extract_employment_records(blocks, sec_res, str(uuid.uuid4()))

    assert len(emp_res.records) == 2

    # Record 1: CedarLine Logistics
    rec0 = emp_res.records[0]
    assert rec0.job_title.value == "Operations Analyst"
    assert rec0.organization.value == "CedarLine Logistics"
    assert "September 2023" in (rec0.start_date.value or "")
    assert rec0.end_date.value == "Present"

    # Record 2: SilverRoute Services
    rec1 = emp_res.records[1]
    assert rec1.job_title.value == "Operations Coordinator"
    assert rec1.organization.value == "SilverRoute Services"
    assert "July 2022" in (rec1.start_date.value or "")
    assert "August 2023" in (rec1.end_date.value or "")

    # No missing organization warning
    warning_codes = [w.code.value for w in emp_res.warnings]
    assert "EMPLOYMENT_ORGANIZATION_MISSING" not in warning_codes

    # Education and tools text not leaked into employment
    all_descriptions = " ".join([d.value or "" for d in rec0.descriptions + rec1.descriptions])
    assert "University of Rajasthan" not in all_descriptions
    assert "B.Com" not in all_descriptions
    assert "Microsoft Teams" not in all_descriptions


def test_education_extraction():
    blocks = _build_blocks()
    sec_res = detect_sections_from_blocks(blocks)
    ed_res = extract_education_projects_certifications_languages(blocks, sec_res, str(uuid.uuid4()))

    assert len(ed_res.education.records) == 1
    rec = ed_res.education.records[0]
    assert rec.degree.value == "B.Com"
    assert rec.field_of_study is not None and rec.field_of_study.value == "Business Administration"
    assert rec.institution.value == "University of Rajasthan"
    assert rec.end_date.value == "2022"

    # Professional development courses are not misclassified as certifications or projects
    assert len(ed_res.certifications.records) == 0
    assert len(ed_res.projects.records) == 0


def test_skills_extraction_and_categorization():
    blocks = _build_blocks()
    sec_res = detect_sections_from_blocks(blocks)
    sk_res = extract_skills_and_summary(blocks, sec_res, str(uuid.uuid4()))

    skills_by_name = {s.canonical_value: s for s in sk_res.skills}

    # Expected Positive Essential Skills
    essential = [
        "Reading Comprehension",
        "Writing",
        "Active Listening",
        "Critical Thinking",
        "Active Learning",
        "Monitoring",
    ]
    for es in essential:
        assert es in skills_by_name, f"Missing essential skill: {es}"
        assert skills_by_name[es].category == "Essential Skills"

    # Expected Positive Transferable Skills
    transferable = [
        "Coordination",
        "Judgment and Decision Making",
    ]
    for ts in transferable:
        assert ts in skills_by_name, f"Missing transferable skill: {ts}"
        assert skills_by_name[ts].category == "Transferable Skills"

    # Expected Positive Software Skills
    software_canonical = ["Microsoft Excel", "Microsoft Teams", "Microsoft SharePoint"]
    for sw in software_canonical:
        assert sw in skills_by_name, f"Missing software skill: {sw}"
        assert skills_by_name[sw].category in (
            "Software and Tools",
            "Spreadsheet software",
            "Project management software",
            "Document management software",
        )

    # Power BI must be present and categorized as Software and Tools (NEVER Programming Languages)
    assert "Power BI" in skills_by_name or "Microsoft Power BI" in skills_by_name
    pbi = skills_by_name.get("Power BI") or skills_by_name.get("Microsoft Power BI")
    assert pbi.category == "Software and Tools" or pbi.category != "PROGRAMMING_LANGUAGE"

    # SQL extracted from basic SQL
    assert "SQL" in skills_by_name or "Structured query language SQL" in skills_by_name
    sql_item = skills_by_name.get("SQL") or skills_by_name.get("Structured query language SQL")
    assert "basic SQL" in sql_item.raw_values
    assert sql_item.confidence <= 0.95

    # Custom domain skills
    assert "Process Mapping" in skills_by_name
    assert "Ticketing/workflow systems" in skills_by_name

    # Disqualified / Negated skills must NOT be present
    disqualified = [
        "Systems Analysis",
        "Software Requirements Modelling",
        "Complex Problem Solving",
        "System Architecture",
    ]
    for dq in disqualified:
        assert dq not in skills_by_name, f"Disqualified skill found: {dq}"


def test_deduplication_and_multiple_evidence_spans():
    blocks = _build_blocks()
    sec_res = detect_sections_from_blocks(blocks)
    sk_res = extract_skills_and_summary(blocks, sec_res, str(uuid.uuid4()))

    # Microsoft Excel is mentioned in experience, education, and tools -> deduplicated with multiple spans
    excel_skill = next(s for s in sk_res.skills if "excel" in s.canonical_value.lower())
    assert len(excel_skill.evidence_ids) >= 2
    assert "SKILLS" in excel_skill.sections
    assert "EXPERIENCE" in excel_skill.sections

    # Power BI is mentioned in experience, education, and tools -> deduplicated
    pbi_skill = next(s for s in sk_res.skills if "power bi" in s.canonical_value.lower())
    assert len(pbi_skill.evidence_ids) >= 2


def test_legacy_catalog_fallback():
    # Calling lookup_exact_or_alias on DEFAULT_SKILL_CATALOG works offline
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("C++") is not None
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("C#") is not None
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias(".NET") is not None
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Node.js") is not None
    assert DEFAULT_SKILL_CATALOG.lookup_exact_or_alias("Python") is not None


def test_end_to_end_profile_assembly():
    blocks = _build_blocks()
    artifact_id = str(uuid.uuid4())
    prof_res = assemble_candidate_profile(blocks, artifact_id)

    # 1. Pipeline version updated
    assert "pipeline-v1.4" in prof_res.parser_pipeline_version

    # 2. Exactly 2 employment records
    assert len(prof_res.professional_profile.employment) == 2

    # 3. Exactly 1 education record
    assert len(prof_res.professional_profile.education) == 1

    # 4. No phone number verification warning
    warnings = prof_res.warnings
    assert not any(w.field_name == "phone_numbers" and "VERIF" in w.code for w in warnings)
    assert not any(w.code == "PHONE_NOT_FOUND" for w in warnings)

    # 5. Review required is False
    assert prof_res.review_required is False

    # 6. Evidence references attached to every extracted field
    for sk in prof_res.professional_profile.skills:
        assert len(sk.evidence_ids) > 0
        assert sk.category is not None

    for emp in prof_res.professional_profile.employment:
        assert len(emp.job_title.evidence_ids) > 0
        assert len(emp.organization.evidence_ids) > 0

    edu = prof_res.professional_profile.education[0]
    assert len(edu.degree.evidence_ids) > 0
    assert len(edu.institution.evidence_ids) > 0
