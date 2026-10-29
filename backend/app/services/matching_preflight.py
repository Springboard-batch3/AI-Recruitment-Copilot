import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import UserRecord, UserRole
from app.models.candidate import CandidateProfileRecord
from app.models.job import (
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
from app.models.taxonomy import (
    OccupationConcept,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.schemas.matching import (
    MATCH_INPUT_SCHEMA_VERSION,
    MATCHER_ALGORITHM_VERSION,
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

logger = logging.getLogger("ai_hiring.services.matching_preflight")


# Domain Error Hierarchy
class MatchPreflightError(Exception):
    """Base domain exception for matching preflight operations."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class RequisitionNotFoundError(MatchPreflightError):
    def __init__(self, message: str = "Requisition not found."):
        super().__init__(code="REQUISITION_NOT_FOUND", message=message, status_code=404)


class RequisitionNotReadyError(MatchPreflightError):
    def __init__(self, message: str = "Requisition revision is not ready for matching."):
        super().__init__(code="REQUISITION_REVISION_NOT_READY", message=message, status_code=409)


class ExplicitRevisionRequiredError(MatchPreflightError):
    def __init__(self, message: str = "Explicit requisition revision is required."):
        super().__init__(code="EXPLICIT_REVISION_REQUIRED", message=message, status_code=422)


class CandidateProfileNotFoundError(MatchPreflightError):
    def __init__(self, message: str = "Candidate profile not found."):
        super().__init__(code="CANDIDATE_PROFILE_NOT_FOUND", message=message, status_code=404)


class ExplicitProfileVersionRequiredError(MatchPreflightError):
    def __init__(self, message: str = "Explicit candidate profile version is required."):
        super().__init__(code="EXPLICIT_PROFILE_VERSION_REQUIRED", message=message, status_code=422)


class EmptyRequirementCollectionError(MatchPreflightError):
    def __init__(self, message: str = "Requisition has no structured requirements."):
        super().__init__(code="EMPTY_REQUIREMENT_COLLECTION", message=message, status_code=422)


class InvalidRequirementDataError(MatchPreflightError):
    def __init__(self, message: str = "Requirement data is invalid."):
        super().__init__(code="INVALID_REQUIREMENT_DATA", message=message, status_code=422)


class InvalidLocalTaxonomyReferenceError(MatchPreflightError):
    def __init__(self, message: str = "Invalid local taxonomy reference."):
        super().__init__(code="INVALID_LOCAL_TAXONOMY_REFERENCE", message=message, status_code=422)


class UnsupportedMatchingContractVersionError(MatchPreflightError):
    def __init__(self, message: str = "Unsupported matching contract version."):
        super().__init__(code="UNSUPPORTED_MATCHING_CONTRACT_VERSION", message=message, status_code=422)


class ProhibitedFeatureDetectedError(MatchPreflightError):
    def __init__(self, message: str = "Prohibited identity or protected feature detected."):
        super().__init__(code="PROHIBITED_FEATURE_DETECTED", message=message, status_code=422)


class UnauthorizedPreflightError(MatchPreflightError):
    def __init__(self, message: str = "Recruiter or admin authorization required."):
        super().__init__(code="UNAUTHORIZED", message=message, status_code=403)


def project_candidate_feature_view(profile: CandidateProfileRecord) -> CandidateFeatureView:
    """Projects a persisted CandidateProfileRecord into a strictly allowlisted,

    pseudonymous CandidateFeatureView. Never accesses or serializes identity records,
    raw resume text, file paths, or contact channels.
    """
    prof_data: Dict[str, Any] = profile.professional_profile or {}

    # 1. Headline (optional text only)
    headline_val: Optional[str] = None
    headline_field = prof_data.get("headline")
    if isinstance(headline_field, dict):
        headline_val = headline_field.get("value")
    elif isinstance(headline_field, str):
        headline_val = headline_field

    # 2. Skills
    skills_list: List[CandidateSkillFeature] = []
    for s in prof_data.get("skills", []):
        if not isinstance(s, dict):
            continue
        val = s.get("value")
        if not val or not str(val).strip():
            continue
        norm_val = s.get("normalized_value") or s.get("normalizedValue") or val
        conf_band = s.get("confidence_band") or s.get("confidenceBand") or "UNKNOWN"
        ev_ids = s.get("evidence_ids") or s.get("evidenceIds") or []
        rev_stat = s.get("review_status") or s.get("reviewStatus") or "UNREVIEWED"
        skills_list.append(
            CandidateSkillFeature(
                name=str(val).strip(),
                normalized_name=str(norm_val).strip().lower(),
                confidence=s.get("confidence"),
                confidence_band=conf_band,
                evidence_ids=ev_ids,
                review_status=rev_stat,
            )
        )

    # 3. Employment
    employment_list: List[CandidateEmploymentRoleFeature] = []
    for e in prof_data.get("employment", []):
        if not isinstance(e, dict):
            continue
        jt_field = e.get("job_title") or e.get("jobTitle")
        job_title = jt_field.get("value") if isinstance(jt_field, dict) else (jt_field if isinstance(jt_field, str) else None)
        if not job_title or not str(job_title).strip():
            continue

        org_field = e.get("organization")
        org = org_field.get("value") if isinstance(org_field, dict) else (org_field if isinstance(org_field, str) else None)

        sd_field = e.get("start_date") or e.get("startDate")
        sd = sd_field.get("value") if isinstance(sd_field, dict) else (sd_field if isinstance(sd_field, str) else None)

        ed_field = e.get("end_date") or e.get("endDate")
        ed = ed_field.get("value") if isinstance(ed_field, dict) else (ed_field if isinstance(ed_field, str) else None)

        descriptions: List[str] = []
        for d in e.get("descriptions", []):
            if isinstance(d, dict) and d.get("value"):
                descriptions.append(str(d["value"]).strip())
            elif isinstance(d, str) and d.strip():
                descriptions.append(d.strip())

        evidence_ids = []
        if isinstance(jt_field, dict):
            evidence_ids.extend(jt_field.get("evidence_ids") or jt_field.get("evidenceIds") or [])
        for d in e.get("descriptions", []):
            if isinstance(d, dict):
                evidence_ids.extend(d.get("evidence_ids") or d.get("evidenceIds") or [])

        conf = jt_field.get("confidence") if isinstance(jt_field, dict) else None
        band = "UNKNOWN"
        if isinstance(jt_field, dict):
            band = jt_field.get("confidence_band") or jt_field.get("confidenceBand") or "UNKNOWN"

        employment_list.append(
            CandidateEmploymentRoleFeature(
                job_title=str(job_title).strip(),
                organization=str(org).strip() if org else None,
                start_date=str(sd).strip() if sd else None,
                end_date=str(ed).strip() if ed else None,
                descriptions=descriptions,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                confidence=conf,
                confidence_band=band,
            )
        )

    # 4. Education
    education_list: List[CandidateEducationFeature] = []
    for ed in prof_data.get("education", []):
        if not isinstance(ed, dict):
            continue
        deg_field = ed.get("degree")
        deg = deg_field.get("value") if isinstance(deg_field, dict) else (deg_field if isinstance(deg_field, str) else None)

        fos_field = ed.get("field_of_study") or ed.get("fieldOfStudy")
        fos = fos_field.get("value") if isinstance(fos_field, dict) else (fos_field if isinstance(fos_field, str) else None)

        inst_field = ed.get("institution")
        inst = inst_field.get("value") if isinstance(inst_field, dict) else (inst_field if isinstance(inst_field, str) else None)

        sd_field = ed.get("start_date") or ed.get("startDate")
        sd = sd_field.get("value") if isinstance(sd_field, dict) else (sd_field if isinstance(sd_field, str) else None)

        ed_field = ed.get("end_date") or ed.get("endDate")
        end_d = ed_field.get("value") if isinstance(ed_field, dict) else (ed_field if isinstance(ed_field, str) else None)

        gr_field = ed.get("grade")
        grade = gr_field.get("value") if isinstance(gr_field, dict) else (gr_field if isinstance(gr_field, str) else None)

        evidence_ids = []
        for f in [deg_field, fos_field, inst_field]:
            if isinstance(f, dict):
                evidence_ids.extend(f.get("evidence_ids") or f.get("evidenceIds") or [])

        conf = deg_field.get("confidence") if isinstance(deg_field, dict) else None
        band = "UNKNOWN"
        if isinstance(deg_field, dict):
            band = deg_field.get("confidence_band") or deg_field.get("confidenceBand") or "UNKNOWN"

        education_list.append(
            CandidateEducationFeature(
                degree=str(deg).strip() if deg else None,
                field_of_study=str(fos).strip() if fos else None,
                institution=str(inst).strip() if inst else None,
                start_date=str(sd).strip() if sd else None,
                end_date=str(end_d).strip() if end_d else None,
                grade=str(grade).strip() if grade else None,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                confidence=conf,
                confidence_band=band,
            )
        )

    # 5. Projects (strictly excluding project_url)
    projects_list: List[CandidateProjectFeature] = []
    for p in prof_data.get("projects", []):
        if not isinstance(p, dict):
            continue
        nm_field = p.get("name")
        name = nm_field.get("value") if isinstance(nm_field, dict) else (nm_field if isinstance(nm_field, str) else None)
        if not name or not str(name).strip():
            continue

        desc_field = p.get("description")
        desc = desc_field.get("value") if isinstance(desc_field, dict) else (desc_field if isinstance(desc_field, str) else None)

        techs: List[str] = []
        for t in p.get("technologies", []):
            if isinstance(t, dict) and t.get("value"):
                techs.append(str(t["value"]).strip())
            elif isinstance(t, str) and t.strip():
                techs.append(t.strip())

        evidence_ids = []
        if isinstance(nm_field, dict):
            evidence_ids.extend(nm_field.get("evidence_ids") or nm_field.get("evidenceIds") or [])
        if isinstance(desc_field, dict):
            evidence_ids.extend(desc_field.get("evidence_ids") or desc_field.get("evidenceIds") or [])

        conf = nm_field.get("confidence") if isinstance(nm_field, dict) else None
        band = "UNKNOWN"
        if isinstance(nm_field, dict):
            band = nm_field.get("confidence_band") or nm_field.get("confidenceBand") or "UNKNOWN"

        projects_list.append(
            CandidateProjectFeature(
                name=str(name).strip(),
                description=str(desc).strip() if desc else None,
                technologies=techs,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                confidence=conf,
                confidence_band=band,
            )
        )

    # 6. Certifications (strictly excluding credential_url)
    certifications_list: List[CandidateCertificationFeature] = []
    for c in prof_data.get("certifications", []):
        if not isinstance(c, dict):
            continue
        nm_field = c.get("name")
        name = nm_field.get("value") if isinstance(nm_field, dict) else (nm_field if isinstance(nm_field, str) else None)
        if not name or not str(name).strip():
            continue

        iss_field = c.get("issuer")
        issuer = iss_field.get("value") if isinstance(iss_field, dict) else (iss_field if isinstance(iss_field, str) else None)

        isd_field = c.get("issued_date") or c.get("issuedDate")
        issued_date = isd_field.get("value") if isinstance(isd_field, dict) else (isd_field if isinstance(isd_field, str) else None)

        exd_field = c.get("expiry_date") or c.get("expiryDate")
        expiry_date = exd_field.get("value") if isinstance(exd_field, dict) else (exd_field if isinstance(exd_field, str) else None)

        crid_field = c.get("credential_id") or c.get("credentialId")
        credential_id = crid_field.get("value") if isinstance(crid_field, dict) else (crid_field if isinstance(crid_field, str) else None)

        evidence_ids = []
        if isinstance(nm_field, dict):
            evidence_ids.extend(nm_field.get("evidence_ids") or nm_field.get("evidenceIds") or [])

        conf = nm_field.get("confidence") if isinstance(nm_field, dict) else None
        band = "UNKNOWN"
        if isinstance(nm_field, dict):
            band = nm_field.get("confidence_band") or nm_field.get("confidenceBand") or "UNKNOWN"

        certifications_list.append(
            CandidateCertificationFeature(
                name=str(name).strip(),
                issuer=str(issuer).strip() if issuer else None,
                issued_date=str(issued_date).strip() if issued_date else None,
                expiry_date=str(expiry_date).strip() if expiry_date else None,
                credential_id=str(credential_id).strip() if credential_id else None,
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                confidence=conf,
                confidence_band=band,
            )
        )

    # 7. Languages
    languages_list: List[CandidateLanguageFeature] = []
    for l in prof_data.get("languages", []):
        if not isinstance(l, dict):
            continue
        val = l.get("value")
        if not val or not str(val).strip():
            continue
        norm_lang = l.get("normalized_value") or l.get("normalizedValue") or val
        conf_band = l.get("confidence_band") or l.get("confidenceBand") or "UNKNOWN"
        ev_ids = l.get("evidence_ids") or l.get("evidenceIds") or []
        languages_list.append(
            CandidateLanguageFeature(
                language=str(val).strip(),
                normalized_language=str(norm_lang).strip().lower(),
                proficiency=l.get("proficiency"),
                evidence_ids=ev_ids,
                confidence=l.get("confidence"),
                confidence_band=conf_band,
            )
        )

    # 8. Evidence references (locators only; never copies full text or artifact id)
    evidence_refs: List[EvidenceReferenceFeature] = []
    raw_spans = profile.evidence_spans or []
    for span in raw_spans:
        if not isinstance(span, dict):
            continue
        ev_id = span.get("evidence_id") or span.get("evidenceId")
        c_start = span.get("character_start") if span.get("character_start") is not None else span.get("characterStart")
        c_end = span.get("character_end") if span.get("character_end") is not None else span.get("characterEnd")
        page_num = span.get("page_number") if span.get("page_number") is not None else span.get("pageNumber")
        block_num = span.get("block_number") if span.get("block_number") is not None else span.get("blockNumber")
        ext_meth = span.get("extraction_method") or span.get("extractionMethod") or "PARSER"

        if ev_id and c_start is not None and c_end is not None and int(c_end) > int(c_start):
            evidence_refs.append(
                EvidenceReferenceFeature(
                    evidence_id=str(ev_id),
                    page_number=int(page_num) if page_num is not None else None,
                    block_number=int(block_num) if block_num is not None else None,
                    character_start=int(c_start),
                    character_end=int(c_end),
                    extraction_method=str(ext_meth),
                )
            )

    try:
        view = CandidateFeatureView(
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            schema_version=profile.schema_version,
            parser_pipeline_version=profile.parser_pipeline_version,
            provenance=profile.provenance,
            headline=headline_val,
            skills=skills_list,
            employment=employment_list,
            education=education_list,
            projects=projects_list,
            certifications=certifications_list,
            languages=languages_list,
            evidence_references=evidence_refs,
            review_required=profile.review_required,
            warning_metadata=profile.warning_metadata or [],
        )
    except ValueError as e:
        raise ProhibitedFeatureDetectedError(str(e))

    # Defensive check: ensure serialized output has no prohibited keys
    try:
        verify_no_prohibited_keys(view.model_dump(mode="json"))
    except ValueError as e:
        raise ProhibitedFeatureDetectedError(str(e))

    return view


def build_frozen_requisition_matching_view(
    req: JobRequisition,
    version: JobVersion,
    db: Session,
) -> FrozenRequisitionMatchingView:
    """Builds a frozen matching-safe view of a confirmed READY_FOR_MATCHING requisition revision."""
    if version.version_status != JobVersionStatus.READY_FOR_MATCHING:
        raise RequisitionNotReadyError(
            f"Requisition revision {version.revision} status is {version.version_status.value}, expected READY_FOR_MATCHING."
        )

    reqs = version.requirements or []
    if len(reqs) == 0:
        raise EmptyRequirementCollectionError("Requisition has no structured requirements.")

    # Sort deterministically by display_order ascending, then requirement_id ascending
    sorted_reqs = sorted(reqs, key=lambda r: (r.display_order, str(r.requirement_id)))

    # Fetch active taxonomy snapshot once for resolving references
    stmt_active_snap = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    active_snapshot = db.execute(stmt_active_snap).scalar_one_or_none()

    matched_items: List[RequirementMatchingItem] = []
    for r in sorted_reqs:
        # Validate essential requirement fields
        if not r.label or not r.label.strip():
            raise InvalidRequirementDataError(f"Requirement {r.requirement_id} has an empty label.")
        if not r.normalized_label or not r.normalized_label.strip():
            raise InvalidRequirementDataError(f"Requirement '{r.label}' has an empty normalized label.")

        # Business rationale required for MUST_HAVE or CRITICAL
        is_must_have = r.necessity == RequirementNecessity.MUST_HAVE
        is_critical = r.priority == RequirementPriority.CRITICAL
        if (is_must_have or is_critical) and (not r.rationale or not r.rationale.strip()):
            raise InvalidRequirementDataError(
                f"Requirement '{r.label}' requires a human-written job-related business rationale."
            )

        # Resolve O*NET taxonomy reference
        tax_canonical_label = None
        tax_external_id = None
        tax_source = None
        tax_version = None

        if r.taxonomy_skill_concept_id is not None:
            if active_snapshot is None:
                raise InvalidLocalTaxonomyReferenceError(
                    f"No active O*NET taxonomy snapshot found to resolve skill '{r.taxonomy_skill_concept_id}'."
                )

            stmt_skill = select(SkillConcept).where(
                SkillConcept.concept_id == r.taxonomy_skill_concept_id,
                SkillConcept.snapshot_id == active_snapshot.snapshot_id,
            )
            skill_obj = db.execute(stmt_skill).scalar_one_or_none()
            if skill_obj is None:
                # Check if concept belonged to a previously active snapshot
                stmt_prev_skill = select(SkillConcept).where(
                    SkillConcept.concept_id == r.taxonomy_skill_concept_id,
                )
                prev_skill = db.execute(stmt_prev_skill).scalar_one_or_none()
                if prev_skill is not None:
                    # Look up active snapshot equivalent by normalized label or external_id
                    stmt_active_equiv = select(SkillConcept).where(
                        SkillConcept.snapshot_id == active_snapshot.snapshot_id,
                        SkillConcept.normalized_label == prev_skill.normalized_label,
                    )
                    skill_obj = db.execute(stmt_active_equiv).scalar_one_or_none()
                    if skill_obj is None:
                        skill_obj = prev_skill
                else:
                    raise InvalidLocalTaxonomyReferenceError(
                        f"Requirement '{r.label}' references skill concept '{r.taxonomy_skill_concept_id}' not found in active O*NET snapshot."
                    )

            tax_canonical_label = skill_obj.canonical_label
            tax_external_id = skill_obj.external_id
            tax_source = active_snapshot.source
            tax_version = active_snapshot.source_version
        elif r.origin == RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED:
            raise InvalidLocalTaxonomyReferenceError(
                f"Requirement '{r.label}' claims taxonomy provenance but lacks taxonomy skill concept ID."
            )
        # Custom requirements (origin == RECRUITER_ENTERED with no taxonomy ID) remain valid with None taxonomy fields

        matched_items.append(
            RequirementMatchingItem(
                requirement_id=r.requirement_id,
                display_order=r.display_order,
                category=r.category,
                label=r.label.strip(),
                normalized_label=r.normalized_label.strip(),
                necessity=r.necessity,
                priority=r.priority,
                approved_weight=r.custom_weight_override,
                min_experience_months=r.min_experience_months,
                recency_years=r.recency_years,
                accepted_evidence_types=list(r.accepted_evidence_types or []),
                accepted_equivalents=list(r.accepted_equivalents or []),
                rationale=r.rationale.strip() if r.rationale else None,
                origin=r.origin,
                taxonomy_skill_concept_id=r.taxonomy_skill_concept_id,
                taxonomy_canonical_label=tax_canonical_label,
                taxonomy_external_id=tax_external_id,
                taxonomy_source=tax_source,
                taxonomy_source_version=tax_version,
                review_required=r.review_required,
                warning_metadata=list(r.warning_metadata or []),
            )
        )

    occ_title = None
    occ_soc = None
    if version.occupation_concept_id is not None:
        if active_snapshot is not None:
            stmt_occ = select(OccupationConcept).where(
                OccupationConcept.concept_id == version.occupation_concept_id,
                OccupationConcept.snapshot_id == active_snapshot.snapshot_id,
            )
            occ_obj = db.execute(stmt_occ).scalar_one_or_none()
            if occ_obj is None:
                stmt_prev_occ = select(OccupationConcept).where(
                    OccupationConcept.concept_id == version.occupation_concept_id,
                )
                prev_occ = db.execute(stmt_prev_occ).scalar_one_or_none()
                if prev_occ is not None:
                    stmt_active_occ_equiv = select(OccupationConcept).where(
                        OccupationConcept.snapshot_id == active_snapshot.snapshot_id,
                        OccupationConcept.onet_soc_code == prev_occ.onet_soc_code,
                    )
                    occ_obj = db.execute(stmt_active_occ_equiv).scalar_one_or_none() or prev_occ

            if occ_obj is not None:
                occ_title = occ_obj.canonical_title
                occ_soc = occ_obj.onet_soc_code

    return FrozenRequisitionMatchingView(
        requisition_id=req.job_id,
        job_version_id=version.job_version_id,
        version_number=version.version_number,
        revision=version.revision,
        lifecycle_status=req.status,
        version_status=version.version_status,
        owner_user_id=req.created_by_user_id,
        title=version.title.strip(),
        occupation_concept_id=version.occupation_concept_id,
        occupation_title=occ_title,
        occupation_soc_code=occ_soc,
        taxonomy_version=version.taxonomy_version or (active_snapshot.source_version if active_snapshot else None),
        requirements=matched_items,
    )


def prepare_match_input_snapshot(
    actor: Optional[UserRecord],
    job_id: UUID,
    expected_revision: Optional[int],
    profile_version: Optional[int],
    artifact_id: Optional[UUID] = None,
    profile_id: Optional[UUID] = None,
    db: Session = ...,
) -> MatchInputSnapshot:
    """Cohesive application operation that loads, validates, and packages exact

    ready requisition requirements and an exact candidate profile version into
    an immutable, pseudonymous MatchInputSnapshot.
    """
    # 1. Enforce Recruiter or Admin role if actor is present
    if actor is not None and actor.role not in {UserRole.RECRUITER, UserRole.ADMIN}:
        raise UnauthorizedPreflightError("Actor must possess RECRUITER or ADMIN role.")

    # 2. Enforce explicit requisition revision
    if expected_revision is None or not isinstance(expected_revision, int):
        raise ExplicitRevisionRequiredError("Explicit requisition revision integer is required.")

    # 3. Enforce explicit candidate profile version
    if profile_version is None or not isinstance(profile_version, int):
        raise ExplicitProfileVersionRequiredError("Explicit candidate profile version integer is required.")

    # 4. Load requisition and enforce ownership
    stmt_req = (
        select(JobRequisition)
        .options(
            selectinload(JobRequisition.versions).selectinload(JobVersion.requirements),
            selectinload(JobRequisition.versions).selectinload(JobVersion.occupation),
        )
        .where(JobRequisition.job_id == job_id)
    )
    req = db.execute(stmt_req).scalar_one_or_none()

    # Recruiter ownership enforcement (conceal existence on failure)
    if req is None or (actor is not None and actor.role == UserRole.RECRUITER and req.created_by_user_id != actor.user_id):
        raise RequisitionNotFoundError("Requisition not found.")

    if req.status != JobLifecycleStatus.ACTIVE:
        raise RequisitionNotFoundError("Requisition is not active.")

    # 5. Load the exact requested revision (NOT latest)
    target_version = next((v for v in req.versions if v.revision == expected_revision), None)
    if target_version is None:
        raise RequisitionNotFoundError(f"Requisition revision {expected_revision} not found.")

    # 6. Validate READY_FOR_MATCHING and build frozen requisition view
    frozen_req_view = build_frozen_requisition_matching_view(
        req=req,
        version=target_version,
        db=db,
    )

    # 7. Load exact candidate profile version (NOT latest)
    if artifact_id is None and profile_id is None:
        raise CandidateProfileNotFoundError("Either artifact_id or profile_id must be provided.")

    stmt_prof = select(CandidateProfileRecord).where(
        CandidateProfileRecord.profile_version == profile_version
    )
    if artifact_id is not None:
        stmt_prof = stmt_prof.where(CandidateProfileRecord.artifact_id == artifact_id)
    if profile_id is not None:
        stmt_prof = stmt_prof.where(CandidateProfileRecord.profile_id == profile_id)

    profile_rec = db.execute(stmt_prof).scalar_one_or_none()
    if profile_rec is None:
        raise CandidateProfileNotFoundError(f"Candidate profile version {profile_version} not found.")

    # 8. Build pseudonymous candidate feature view through positive allowlist projection
    candidate_view = project_candidate_feature_view(profile_rec)

    # 9. Return immutable MatchInputSnapshot
    now = datetime.now(timezone.utc)
    return MatchInputSnapshot(
        match_input_schema_version=MATCH_INPUT_SCHEMA_VERSION,
        matcher_algorithm_version=MATCHER_ALGORITHM_VERSION,
        snapshot_created_at=now,
        requisition_view=frozen_req_view,
        candidate_view=candidate_view,
    )
