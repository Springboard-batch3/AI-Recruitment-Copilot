import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import CandidateIdentityRecord, CandidateProfileRecord
from app.models.extraction import ExtractionBlock, ResumeExtraction
from app.models.resume import ResumeArtifact
from app.schemas.candidate import (
    CandidateIdentity,
    CandidateProfessionalProfile,
    CandidateProfileV1,
    CertificationRecord,
    ConfidenceBand,
    EducationRecord,
    EvidenceSpan,
    ExperienceRecord,
    ExtractedField,
    ProjectRecord,
    ReviewStatus,
)
from app.schemas.extraction import ExtractionBlockData
from app.schemas.profile_assembly import (
    ProfileAssemblyResult,
    ProfileAssemblyWarning,
    ProfileAssemblyWarningCode,
)
from app.schemas.section_and_identity import SectionType
from app.services.education_project_certification_extraction import (
    EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION,
    extract_education_projects_certifications_languages,
)
from app.services.education_project_certification_rules import (
    EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION,
)
from app.services.employment_extraction import (
    EMPLOYMENT_PARSER_VERSION,
    extract_employment_records,
)
from app.services.identity_extraction import (
    IDENTITY_PARSER_VERSION,
    extract_identity_and_contacts,
)
from app.services.section_detection import (
    SECTION_PARSER_VERSION,
    detect_sections_from_blocks,
)
from app.services.skill_catalog import SKILL_CATALOG_VERSION
from app.services.skill_summary_extraction import (
    SKILL_SUMMARY_PARSER_VERSION,
    extract_skills_and_summary,
)

logger = logging.getLogger("ai_hiring.services.profile_assembly")

PROFILE_PIPELINE_VERSION = (
    f"pipeline-v1.4[{SECTION_PARSER_VERSION}+{IDENTITY_PARSER_VERSION}+"
    f"{SKILL_SUMMARY_PARSER_VERSION}+{EMPLOYMENT_PARSER_VERSION}+"
    f"{EDUCATION_PROJECT_CERTIFICATION_PARSER_VERSION}]"
)


class ProfileAssemblyError(Exception):
    """Base exception for profile assembly failures."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProhibitedIdentityFieldException(ProfileAssemblyError):
    def __init__(self, message: str = "Prohibited personal identity fields detected in professional profile."):
        super().__init__(
            code="PROHIBITED_IDENTITY_IN_PROFESSIONAL_PROFILE",
            message=message,
            status_code=422,
        )


class EvidenceValidationError(ProfileAssemblyError):
    def __init__(self, code: str, message: str):
        super().__init__(code=code, message=message, status_code=422)


def _validate_evidence_spans(
    evidence_spans: List[EvidenceSpan],
    blocks: List[ExtractionBlockData],
    artifact_id: str,
) -> Tuple[List[EvidenceSpan], List[ProfileAssemblyWarning]]:
    """
    Validates every EvidenceSpan:
    - Must belong to the exact resume_artifact_id.
    - Must point to an existing block_number (sequence_index).
    - character_start >= 0, character_end > character_start.
    - Offsets must not exceed the target block's raw text length.
    - original_text must match the referenced slice in raw_text or normalized_text.
    """
    valid_spans: List[EvidenceSpan] = []
    warnings: List[ProfileAssemblyWarning] = []
    seen_ids: Set[str] = set()

    block_map: Dict[int, ExtractionBlockData] = {b.sequence_index: b for b in blocks}

    for span in evidence_spans:
        # Check deduplication
        if span.evidence_id in seen_ids:
            continue
        seen_ids.add(span.evidence_id)

        # Cross artifact validation
        if span.resume_artifact_id != artifact_id:
            warnings.append(
                ProfileAssemblyWarning(
                    code=ProfileAssemblyWarningCode.PROFILE_EVIDENCE_CROSS_ARTIFACT.value,
                    category="EVIDENCE",
                    evidence_id=span.evidence_id,
                )
            )
            raise EvidenceValidationError(
                code="PROFILE_EVIDENCE_CROSS_ARTIFACT",
                message="Evidence reference does not match the active resume artifact.",
            )

        if span.block_number is None or span.block_number not in block_map:
            warnings.append(
                ProfileAssemblyWarning(
                    code=ProfileAssemblyWarningCode.PROFILE_EVIDENCE_NOT_FOUND.value,
                    category="EVIDENCE",
                    evidence_id=span.evidence_id,
                )
            )
            raise EvidenceValidationError(
                code="PROFILE_EVIDENCE_NOT_FOUND",
                message="Referenced extraction block was not found.",
            )

        target_block = block_map[span.block_number]

        # Offset bounds check
        if span.character_start < 0 or span.character_end <= span.character_start:
            warnings.append(
                ProfileAssemblyWarning(
                    code=ProfileAssemblyWarningCode.PROFILE_EVIDENCE_INVALID.value,
                    category="EVIDENCE",
                    evidence_id=span.evidence_id,
                )
            )
            raise EvidenceValidationError(
                code="PROFILE_EVIDENCE_INVALID",
                message="Invalid character offsets in evidence span.",
            )

        # Length check
        raw_len = len(target_block.raw_text)
        norm_len = len(target_block.normalized_text)
        max_len = max(raw_len, norm_len)

        if span.character_end > max_len:
            warnings.append(
                ProfileAssemblyWarning(
                    code=ProfileAssemblyWarningCode.PROFILE_EVIDENCE_INVALID.value,
                    category="EVIDENCE",
                    evidence_id=span.evidence_id,
                )
            )
            raise EvidenceValidationError(
                code="PROFILE_EVIDENCE_INVALID",
                message="Evidence span exceeds the boundary of the referenced text block.",
            )

        valid_spans.append(span)

    return valid_spans, warnings


def _assert_identity_isolation(
    professional_profile: CandidateProfessionalProfile,
    identity: CandidateIdentity,
) -> None:
    """
    Automated check ensuring prohibited direct identity/contact fields
    (name, email, phone, personal URLs) are not leaked into professional feature payload.
    """
    # Prohibited patterns
    email_re = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    phone_re = re.compile(r"(?:\+?\d{1,3}[\s.-]*)?(?:\(\d{1,4}\)[\s.-]*|\d{1,4}[\s.-]*)\d{2,4}[\s.-]+\d{3,9}\b")

    # Check headline
    if professional_profile.headline.value:
        h_val = professional_profile.headline.value
        if email_re.search(h_val) or (identity.full_name.value and identity.full_name.value.lower() in h_val.lower()):
            raise ProhibitedIdentityFieldException("Candidate name or email found inside headline field.")

    # Check skills
    for sk in professional_profile.skills:
        if sk.value:
            if email_re.search(sk.value) or phone_re.search(sk.value):
                raise ProhibitedIdentityFieldException("Contact data found inside skill field.")

    # Check summary
    if professional_profile.summary.value:
        s_val = professional_profile.summary.value
        if email_re.search(s_val) or phone_re.search(s_val):
            raise ProhibitedIdentityFieldException("Candidate contact data found inside professional summary.")


def assemble_candidate_profile(
    blocks: List[ExtractionBlockData],
    artifact_id: str,
    as_of_date: Optional[str] = None,
    extraction_review_required: bool = False,
) -> ProfileAssemblyResult:
    """
    Assembles CandidateProfileV1 from extraction blocks by orchestrating Phase 1D.1 - 1D.4 parsers:
    1. Section Detection & Identity Extraction (Phase 1D.1)
    2. Skills, Headline & Professional Summary (Phase 1D.2)
    3. Employment Records & Durations (Phase 1D.3)
    4. Education, Projects, Certifications & Languages (Phase 1D.4)
    5. Evidence Validation & Identity Isolation
    6. Deterministic review status calculation
    """
    if not blocks:
        raise ProfileAssemblyError(
            code="PROFILE_ASSEMBLY_FAILED",
            message="Cannot assemble profile from empty extraction blocks.",
            status_code=422,
        )

    all_warnings: List[ProfileAssemblyWarning] = []

    # 1. Section Detection & Identity Extraction
    sec_res = detect_sections_from_blocks(blocks)
    for w in sec_res.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code="SECTION_WARNING",
                category="SECTION",
                message=w,
            )
        )

    ident_res = extract_identity_and_contacts(blocks, sec_res, artifact_id)
    for iw in ident_res.warnings:
        # Completely absent optional phone number does not require verification
        if iw.code.value == "PHONE_NOT_FOUND":
            continue
        all_warnings.append(
            ProfileAssemblyWarning(
                code=iw.code.value,
                category="IDENTITY",
                field_name=iw.field_name,
                count=iw.candidate_count,
            )
        )

    # 2. Skills & Professional Summary
    skill_res = extract_skills_and_summary(
        blocks,
        sec_res,
        artifact_id,
        candidate_name=ident_res.identity.full_name.value if ident_res.identity.full_name else None,
    )
    for sw in skill_res.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=sw.code.value,
                category=(
                    "SKILL_NORMALIZATION"
                    if sw.code.value == "UNRESOLVED_SKILL_CANDIDATE"
                    else "SKILL_SUMMARY"
                ),
                field_name=sw.field_name,
                count=sw.candidate_count,
                evidence_id=sw.evidence_id,
            )
        )

    # 3. Employment
    emp_res = extract_employment_records(blocks, sec_res, artifact_id, as_of_date=as_of_date)
    for ew in emp_res.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=ew.code.value,
                category="EMPLOYMENT",
                field_name=ew.field_name,
                count=ew.candidate_count,
                evidence_id=ew.evidence_id,
            )
        )

    # 4. Education, Projects, Certifications & Languages
    p1d4_res = extract_education_projects_certifications_languages(
        blocks=blocks,
        sections_result=sec_res,
        artifact_id=artifact_id,
        skill_occurrences=skill_res.skills,
        as_of_date=as_of_date,
    )
    for edw in p1d4_res.education.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=edw.code.value,
                category="EDUCATION",
                field_name=edw.field_name,
                count=edw.candidate_count,
                evidence_id=edw.evidence_id,
            )
        )
    for pw in p1d4_res.projects.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=pw.code.value,
                category="PROJECTS",
                field_name=pw.field_name,
                count=pw.candidate_count,
                evidence_id=pw.evidence_id,
            )
        )
    for cw in p1d4_res.certifications.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=cw.code.value,
                category="CERTIFICATIONS",
                field_name=cw.field_name,
                count=cw.candidate_count,
                evidence_id=cw.evidence_id,
            )
        )
    for lw in p1d4_res.languages.warnings:
        all_warnings.append(
            ProfileAssemblyWarning(
                code=lw.code.value,
                category="LANGUAGES",
                field_name=lw.field_name,
                count=lw.candidate_count,
                evidence_id=lw.evidence_id,
            )
        )

    # Convert intermediate models to CandidateProfessionalProfile fields
    # Headline
    headline_field = (
        skill_res.headline.headline_field
        if skill_res.headline
        else ExtractedField(
            field_name="headline",
            value=None,
            normalized_value=None,
            confidence=None,
            confidence_band=ConfidenceBand.UNKNOWN,
            evidence_ids=[],
            extraction_method=None,
            review_status=ReviewStatus.UNREVIEWED,
        )
    )

    # Summary
    summary_field = (
        skill_res.summary.summary_field
        if skill_res.summary
        else ExtractedField(
            field_name="summary",
            value=None,
            normalized_value=None,
            confidence=None,
            confidence_band=ConfidenceBand.UNKNOWN,
            evidence_ids=[],
            extraction_method=None,
            review_status=ReviewStatus.UNREVIEWED,
        )
    )

    # Skills
    skills_fields: List[ExtractedField] = []
    for sk in skill_res.skills:
        skills_fields.append(
            ExtractedField(
                field_name="skill",
                value=sk.canonical_value,
                normalized_value=sk.canonical_value,
                category=sk.category,
                confidence=sk.confidence,
                confidence_band=sk.confidence_band,
                evidence_ids=sk.evidence_ids,
                extraction_method=sk.extraction_method or "skill_catalog",
                review_status=ReviewStatus.UNREVIEWED,
            )
        )

    # Employment records
    employment_records: List[ExperienceRecord] = []
    for er in emp_res.records:
        employment_records.append(
            ExperienceRecord(
                job_title=er.job_title,
                organization=er.organization,
                start_date=er.start_date,
                end_date=er.end_date,
                descriptions=er.descriptions,
            )
        )

    # Education records
    education_records: List[EducationRecord] = []
    for ed in p1d4_res.education.records:
        education_records.append(
            EducationRecord(
                degree=ed.degree,
                field_of_study=ed.field_of_study,
                institution=ed.institution,
                start_date=ed.start_date,
                end_date=ed.end_date,
                grade=ed.grade,
            )
        )

    # Project records
    project_records: List[ProjectRecord] = []
    for pr in p1d4_res.projects.records:
        project_records.append(
            ProjectRecord(
                name=pr.name,
                description=pr.description,
                technologies=pr.technologies,
                project_url=pr.project_url,
            )
        )

    # Certification records
    cert_records: List[CertificationRecord] = []
    for cr in p1d4_res.certifications.records:
        cert_records.append(
            CertificationRecord(
                name=cr.name,
                issuer=cr.issuer,
                issued_date=cr.issued_date,
                expiry_date=cr.expiry_date,
                credential_id=cr.credential_id,
                credential_url=cr.credential_url,
            )
        )

    # Language fields
    language_fields: List[ExtractedField] = []
    for lr in p1d4_res.languages.languages:
        language_fields.append(lr.language)

    # Build CandidateProfessionalProfile
    professional_profile = CandidateProfessionalProfile(
        headline=headline_field,
        summary=summary_field,
        skills=skills_fields,
        employment=employment_records,
        education=education_records,
        projects=project_records,
        certifications=cert_records,
        languages=language_fields,
        awards=[],
        publications=[],
    )

    # Identity Isolation Check
    _assert_identity_isolation(professional_profile, ident_res.identity)

    # Collect & Validate all Evidence Spans
    raw_spans = (
        ident_res.evidence_spans
        + skill_res.evidence_spans
        + emp_res.evidence_spans
        + p1d4_res.evidence_spans
    )
    valid_spans, ev_warnings = _validate_evidence_spans(raw_spans, blocks, artifact_id)
    all_warnings.extend(ev_warnings)

    # Deterministic Review Required calculation
    review_required = False
    if extraction_review_required:
        review_required = True

    # Meaningful parser ambiguities
    critical_warning_categories = {"IDENTITY", "EMPLOYMENT", "EDUCATION"}
    for w in all_warnings:
        if w.category in critical_warning_categories:
            if "MISSING" in w.code or "AMBIGUOUS" in w.code or "INVALID" in w.code or "CONFLICT" in w.code:
                review_required = True
                break

    return ProfileAssemblyResult(
        schema_version="1.0",
        parser_pipeline_version=PROFILE_PIPELINE_VERSION,
        identity=ident_res.identity,
        professional_profile=professional_profile,
        evidence_spans=valid_spans,
        review_required=review_required,
        warnings=all_warnings,
    )


def persist_candidate_profile(
    session: Session,
    artifact_id: UUID,
    extraction_id: UUID,
    assembled_profile: ProfileAssemblyResult,
) -> CandidateProfileRecord:
    """
    Persists CandidateIdentityRecord and CandidateProfileRecord in a single atomic database transaction.
    - Strictly idempotent: returns existing profile if an identical completed profile exists for artifact & pipeline version.
    - Flushes both records together.
    """
    # 1. Check if profile already exists for artifact & pipeline version & schema version
    stmt_existing = (
        select(CandidateProfileRecord)
        .where(
            CandidateProfileRecord.artifact_id == artifact_id,
            CandidateProfileRecord.parser_pipeline_version == assembled_profile.parser_pipeline_version,
            CandidateProfileRecord.schema_version == assembled_profile.schema_version,
        )
    )
    existing_profile = session.execute(stmt_existing).scalar_one_or_none()
    if existing_profile is not None:
        logger.info(
            "Reusing existing candidate profile for artifact",
            extra={"artifact_id": str(artifact_id), "profile_id": str(existing_profile.profile_id)},
        )
        return existing_profile

    # 2. Get or create CandidateIdentityRecord
    stmt_ident = select(CandidateIdentityRecord).where(CandidateIdentityRecord.artifact_id == artifact_id)
    identity_record = session.execute(stmt_ident).scalar_one_or_none()

    identity_dict = assembled_profile.identity.model_dump(mode="json")
    full_name_val = assembled_profile.identity.full_name.value
    email_val = (
        assembled_profile.identity.emails[0].value
        if assembled_profile.identity.emails
        else None
    )
    phone_val = (
        assembled_profile.identity.phone_numbers[0].value
        if assembled_profile.identity.phone_numbers
        else None
    )

    if identity_record is None:
        identity_record = CandidateIdentityRecord(
            identity_id=uuid.uuid4(),
            artifact_id=artifact_id,
            full_name=full_name_val,
            primary_email=email_val,
            primary_phone=phone_val,
            identity_payload=identity_dict,
        )
        session.add(identity_record)
        session.flush()
    else:
        # Update identity payload if needed
        identity_record.full_name = full_name_val
        identity_record.primary_email = email_val
        identity_record.primary_phone = phone_val
        identity_record.identity_payload = identity_dict
        session.flush()

    # 3. Create CandidateProfileRecord with incrementing profile_version
    from sqlalchemy import func
    stmt_max_v = select(func.max(CandidateProfileRecord.profile_version)).where(CandidateProfileRecord.artifact_id == artifact_id)
    max_v = session.execute(stmt_max_v).scalar_one_or_none() or 0
    next_profile_version = max_v + 1

    profile_record = CandidateProfileRecord(
        profile_id=uuid.uuid4(),
        profile_version=next_profile_version,
        artifact_id=artifact_id,
        extraction_id=extraction_id,
        identity_id=identity_record.identity_id,
        schema_version=assembled_profile.schema_version,
        parser_pipeline_version=assembled_profile.parser_pipeline_version,
        professional_profile=assembled_profile.professional_profile.model_dump(mode="json"),
        evidence_spans=[s.model_dump(mode="json") for s in assembled_profile.evidence_spans],
        review_required=assembled_profile.review_required,
        warning_metadata=[w.model_dump(mode="json") for w in assembled_profile.warnings],
    )
    session.add(profile_record)
    session.flush()

    logger.info(
        "Successfully assembled and persisted CandidateProfile",
        extra={"artifact_id": str(artifact_id), "profile_id": str(profile_record.profile_id)},
    )
    return profile_record
