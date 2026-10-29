import copy
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.candidate import (
    CandidateIdentityRecord,
    CandidateProfileRecord,
    ProfileCorrectionRecord,
)
from app.models.extraction import ResumeExtraction
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
from app.schemas.correction import (
    CorrectionOperation,
    CorrectionOperationType,
    EvidenceProvenanceType,
    ProfileCorrectionRequest,
    ProfileCorrectionResponse,
    ProfileProvenance,
    ProfileSummaryResponse,
)
from app.schemas.profile_assembly import CandidateProfileResponse, ProfileAssemblyWarning
from app.services.date_extraction import parse_single_date

logger = logging.getLogger("ai_hiring.services.profile_correction")


class CorrectionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProfileNotFoundError(CorrectionError):
    def __init__(self, message: str = "Candidate profile not found."):
        super().__init__(code="PROFILE_NOT_FOUND", message=message, status_code=404)


class ProfileVersionConflictError(CorrectionError):
    def __init__(self, message: str = "Profile version conflict. The profile has been updated by another action."):
        super().__init__(code="PROFILE_VERSION_CONFLICT", message=message, status_code=409)


class IdempotencyConflictError(CorrectionError):
    def __init__(self, message: str = "Idempotency key already used with a different correction payload."):
        super().__init__(code="IDEMPOTENCY_CONFLICT", message=message, status_code=409)


class InvalidCorrectionOperationError(CorrectionError):
    def __init__(self, message: str):
        super().__init__(code="INVALID_CORRECTION_OPERATION", message=message, status_code=422)


class InvalidEvidenceReferenceError(CorrectionError):
    def __init__(self, message: str):
        super().__init__(code="INVALID_EVIDENCE_REFERENCE", message=message, status_code=422)


def _validate_email(email_str: str) -> None:
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    if not re.match(pattern, email_str.strip()):
        raise InvalidCorrectionOperationError(f"Invalid email address format: '{email_str}'")


def _validate_phone(phone_str: str) -> None:
    digits = re.sub(r"\D", "", phone_str)
    if len(digits) < 7 or len(digits) > 20:
        raise InvalidCorrectionOperationError(f"Invalid phone number length: '{phone_str}'")


def _validate_url(url_str: str) -> None:
    parsed = urllib.parse.urlparse(url_str.strip())
    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        if not re.match(r"^[a-zA-Z0-9.\-_/]+$", url_str.strip()):
            raise InvalidCorrectionOperationError(f"Invalid URL: '{url_str}'")


def _validate_date(date_str: Optional[str]) -> None:
    if not date_str:
        return
    norm = date_str.strip().upper()
    if norm in ("PRESENT", "CURRENT", "NOW"):
        return
    res = parse_single_date(date_str)
    if not res:
        raise InvalidCorrectionOperationError(f"Unsupported date format: '{date_str}'")


def _build_corrected_extracted_field(
    field_name: str,
    value: Optional[str],
    provenance_type: EvidenceProvenanceType,
    selected_evidence_ids: List[str],
    evidence_span_map: Dict[str, EvidenceSpan],
    artifact_id: str,
) -> ExtractedField:
    if value is None or value.strip() == "":
        return ExtractedField(
            field_name=field_name,
            value=None,
            normalized_value=None,
            confidence=None,
            confidence_band=ConfidenceBand.UNKNOWN,
            evidence_ids=[],
            extraction_method=None,
            review_status=ReviewStatus.CORRECTED,
        )

    val = value.strip()

    if provenance_type == EvidenceProvenanceType.HUMAN_CORRECTED_WITH_EVIDENCE:
        valid_ev_ids = []
        for ev_id in selected_evidence_ids:
            if ev_id not in evidence_span_map:
                raise InvalidEvidenceReferenceError(f"Evidence ID '{ev_id}' not found in profile evidence spans.")
            span = evidence_span_map[ev_id]
            if span.resume_artifact_id != artifact_id:
                raise InvalidEvidenceReferenceError(f"Evidence ID '{ev_id}' belongs to a different artifact.")
            valid_ev_ids.append(ev_id)

        return ExtractedField(
            field_name=field_name,
            value=val,
            normalized_value=val,
            confidence=0.95,
            confidence_band=ConfidenceBand.HIGH,
            evidence_ids=valid_ev_ids,
            extraction_method="HUMAN_CORRECTED_WITH_EVIDENCE",
            review_status=ReviewStatus.CORRECTED,
        )
    else:
        # HUMAN_PROVIDED
        return ExtractedField(
            field_name=field_name,
            value=val,
            normalized_value=val,
            confidence=None,
            confidence_band=ConfidenceBand.UNKNOWN,
            evidence_ids=[],
            extraction_method="HUMAN_PROVIDED",
            review_status=ReviewStatus.CORRECTED,
        )


def apply_profile_correction(
    session: Session,
    artifact_id: UUID,
    correction_req: ProfileCorrectionRequest,
    actor_context: str = "LOCAL_DEVELOPMENT_REVIEWER",
) -> ProfileCorrectionResponse:
    """
    Applies an audited batch of correction operations to a CandidateProfile atomically.
    - Strictly enforces base profile version equality.
    - Idempotent: Repeated identical key & payload returns existing result; changed payload conflicts.
    - Creates a new immutable CandidateProfileRecord with incremented version.
    - Creates a new CandidateIdentityRecord if identity fields are modified.
    - Records an append-only ProfileCorrectionRecord audit row.
    """
    # 1. Check Artifact exists
    stmt_art = select(ResumeArtifact).where(ResumeArtifact.artifact_id == artifact_id)
    artifact = session.execute(stmt_art).scalar_one_or_none()
    if artifact is None:
        raise CorrectionError(code="RESUME_NOT_FOUND", message="Resume artifact not found.", status_code=404)

    # 2. Check Idempotency Key
    stmt_idem = select(ProfileCorrectionRecord).where(
        ProfileCorrectionRecord.artifact_id == artifact_id,
        ProfileCorrectionRecord.idempotency_key == correction_req.idempotency_key,
    )
    existing_corr = session.execute(stmt_idem).scalar_one_or_none()

    if existing_corr is not None:
        # Verify identical payload
        payload_dict = correction_req.model_dump(mode="json", by_alias=True)
        req_ops_json = payload_dict["operations"]
        if (
            str(existing_corr.base_profile_id) == str(correction_req.base_profile_id)
            and existing_corr.base_profile_version == correction_req.base_profile_version
            and existing_corr.reason_category == correction_req.reason_category.value
            and existing_corr.reviewer_note == correction_req.reviewer_note
            and existing_corr.operations_payload == req_ops_json
        ):
            # Return existing new profile
            stmt_new_prof = (
                select(CandidateProfileRecord)
                .options(selectinload(CandidateProfileRecord.identity))
                .where(CandidateProfileRecord.profile_id == existing_corr.new_profile_id)
            )
            existing_new_prof = session.execute(stmt_new_prof).scalar_one_or_none()
            if existing_new_prof:
                return _build_correction_response(existing_corr, existing_new_prof)
        # Payload differed for same idempotency key
        raise IdempotencyConflictError()

    # 3. Check Latest Profile and Version Conflict
    stmt_latest = (
        select(CandidateProfileRecord)
        .options(selectinload(CandidateProfileRecord.identity))
        .where(CandidateProfileRecord.artifact_id == artifact_id)
        .order_by(CandidateProfileRecord.profile_version.desc())
    )
    latest_profile = session.execute(stmt_latest).scalars().first()

    if latest_profile is None:
        raise ProfileNotFoundError("No existing profile found for artifact.")

    if (
        latest_profile.profile_id != correction_req.base_profile_id
        or latest_profile.profile_version != correction_req.base_profile_version
    ):
        logger.warning(
            "Profile version conflict on correction",
            extra={
                "artifact_id": str(artifact_id),
                "expected_base_version": correction_req.base_profile_version,
                "actual_latest_version": latest_profile.profile_version,
            },
        )
        raise ProfileVersionConflictError()

    # 4. Prepare working copies of identity and professional profile
    valid_id_base = CandidateIdentity.model_validate(latest_profile.identity.identity_payload)
    identity_dict = valid_id_base.model_dump(mode="json", by_alias=True)

    valid_prof_base = CandidateProfessionalProfile.model_validate(latest_profile.professional_profile)
    prof_dict = valid_prof_base.model_dump(mode="json", by_alias=True)

    evidence_span_map: Dict[str, EvidenceSpan] = {}
    for span in latest_profile.evidence_spans:
        ev_obj = EvidenceSpan.model_validate(span)
        evidence_span_map[ev_obj.evidence_id] = ev_obj

    artifact_id_str = str(artifact_id)
    identity_changed = False

    # 5. Apply correction operations
    for op in correction_req.operations:
        sec = op.target_section.strip().lower()

        # --- IDENTITY CORRECTIONS ---
        if sec in ("identity.full_name", "full_name", "name"):
            val = op.value.strip() if op.value else ""
            if not val:
                raise InvalidCorrectionOperationError("Full name cannot be empty.")
            fld = _build_corrected_extracted_field(
                "fullName", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            identity_dict["fullName"] = fld.model_dump(mode="json", by_alias=True)
            identity_changed = True

        elif sec in ("identity.primary_email", "identity.email", "email", "emails"):
            if op.operation_type == CorrectionOperationType.REPLACE_SCALAR:
                val = op.value.strip() if op.value else ""
                if val:
                    _validate_email(val)
                    fld = _build_corrected_extracted_field(
                        "primary_email", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                    )
                    identity_dict["emails"] = [fld.model_dump(mode="json", by_alias=True)]
                else:
                    identity_dict["emails"] = []
                identity_changed = True
            elif op.operation_type == CorrectionOperationType.ADD_SET_ITEM:
                val = op.value.strip() if op.value else ""
                _validate_email(val)
                existing_emails = [e.get("value", "").lower() for e in identity_dict.get("emails", [])]
                if val.lower() in existing_emails:
                    raise InvalidCorrectionOperationError(f"Email '{val}' is already present.")
                fld = _build_corrected_extracted_field(
                    "email", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                if "emails" not in identity_dict:
                    identity_dict["emails"] = []
                identity_dict["emails"].append(fld.model_dump(mode="json", by_alias=True))
                identity_changed = True
            elif op.operation_type == CorrectionOperationType.REMOVE_SET_ITEM:
                val = op.value.strip().lower() if op.value else ""
                current_emails = identity_dict.get("emails", [])
                identity_dict["emails"] = [e for e in current_emails if e.get("value", "").strip().lower() != val]
                identity_changed = True

        elif sec in ("identity.primary_phone", "identity.phone", "phone", "phone_numbers"):
            if op.operation_type == CorrectionOperationType.REPLACE_SCALAR:
                val = op.value.strip() if op.value else ""
                if val:
                    _validate_phone(val)
                    fld = _build_corrected_extracted_field(
                        "primary_phone", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                    )
                    identity_dict["phoneNumbers"] = [fld.model_dump(mode="json", by_alias=True)]
                else:
                    identity_dict["phoneNumbers"] = []
                identity_changed = True
            elif op.operation_type == CorrectionOperationType.ADD_SET_ITEM:
                val = op.value.strip() if op.value else ""
                _validate_phone(val)
                fld = _build_corrected_extracted_field(
                    "phone", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                if "phoneNumbers" not in identity_dict:
                    identity_dict["phoneNumbers"] = []
                identity_dict["phoneNumbers"].append(fld.model_dump(mode="json", by_alias=True))
                identity_changed = True
            elif op.operation_type == CorrectionOperationType.REMOVE_SET_ITEM:
                val = op.value.strip() if op.value else ""
                digits = re.sub(r"\D", "", val)
                current_phones = identity_dict.get("phoneNumbers", [])
                identity_dict["phoneNumbers"] = [
                    p for p in current_phones if re.sub(r"\D", "", p.get("value", "")) != digits
                ]
                identity_changed = True

        elif sec in ("identity.location", "location"):
            val = op.value.strip() if op.value else None
            fld = _build_corrected_extracted_field(
                "location", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            identity_dict["location"] = fld.model_dump(mode="json", by_alias=True)
            identity_changed = True

        elif sec in ("identity.linkedin", "linkedin"):
            val = op.value.strip() if op.value else None
            if val:
                _validate_url(val)
            fld = _build_corrected_extracted_field(
                "linkedin", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            identity_dict["linkedin"] = fld.model_dump(mode="json", by_alias=True)
            identity_changed = True

        elif sec in ("identity.github", "github"):
            val = op.value.strip() if op.value else None
            if val:
                _validate_url(val)
            fld = _build_corrected_extracted_field(
                "github", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            identity_dict["github"] = fld.model_dump(mode="json", by_alias=True)
            identity_changed = True

        elif sec in ("identity.portfolio", "portfolio"):
            val = op.value.strip() if op.value else None
            if val:
                _validate_url(val)
            fld = _build_corrected_extracted_field(
                "portfolio", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            identity_dict["portfolio"] = fld.model_dump(mode="json", by_alias=True)
            identity_changed = True

        # --- PROFESSIONAL PROFILE CORRECTIONS ---
        elif sec in ("headline", "professional_profile.headline"):
            val = op.value.strip() if op.value else None
            fld = _build_corrected_extracted_field(
                "headline", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            prof_dict["headline"] = fld.model_dump(mode="json", by_alias=True)

        elif sec in ("summary", "professional_profile.summary"):
            val = op.value.strip() if op.value else None
            fld = _build_corrected_extracted_field(
                "summary", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
            )
            prof_dict["summary"] = fld.model_dump(mode="json", by_alias=True)

        elif sec in ("skills", "professional_profile.skills"):
            current_skills = prof_dict.get("skills", [])
            if op.operation_type == CorrectionOperationType.ADD_SET_ITEM:
                val = op.value.strip() if op.value else ""
                if not val:
                    raise InvalidCorrectionOperationError("Skill value cannot be empty.")
                # Check duplicates
                existing_names = [
                    (s.get("normalizedValue") or s.get("value") or "").strip().lower() for s in current_skills
                ]
                if val.lower() in existing_names:
                    raise InvalidCorrectionOperationError(f"Skill '{val}' is already present.")
                fld = _build_corrected_extracted_field(
                    "skill", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_skills.append(fld.model_dump(mode="json", by_alias=True))
                prof_dict["skills"] = current_skills
            elif op.operation_type == CorrectionOperationType.REMOVE_SET_ITEM:
                val = op.value.strip().lower() if op.value else ""
                prof_dict["skills"] = [
                    s
                    for s in current_skills
                    if (s.get("normalizedValue") or s.get("value") or "").strip().lower() != val
                ]
            else:
                raise InvalidCorrectionOperationError(f"Operation '{op.operation_type}' not supported for skills.")

        elif sec in ("employment", "experience", "professional_profile.employment"):
            current_emp = prof_dict.get("employment", [])
            if op.operation_type == CorrectionOperationType.ADD_RECORD:
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for adding employment record.")
                rec = _validate_and_build_experience_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_emp.append(rec.model_dump(mode="json", by_alias=True))
                prof_dict["employment"] = current_emp
            elif op.operation_type == CorrectionOperationType.UPDATE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_emp):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for employment records (total: {len(current_emp)})."
                    )
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for updating employment record.")
                rec = _validate_and_build_experience_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_emp[op.target_index] = rec.model_dump(mode="json", by_alias=True)
                prof_dict["employment"] = current_emp
            elif op.operation_type == CorrectionOperationType.REMOVE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_emp):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for employment records (total: {len(current_emp)})."
                    )
                current_emp.pop(op.target_index)
                prof_dict["employment"] = current_emp

        elif sec in ("education", "professional_profile.education"):
            current_edu = prof_dict.get("education", [])
            if op.operation_type == CorrectionOperationType.ADD_RECORD:
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for adding education record.")
                rec = _validate_and_build_education_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_edu.append(rec.model_dump(mode="json", by_alias=True))
                prof_dict["education"] = current_edu
            elif op.operation_type == CorrectionOperationType.UPDATE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_edu):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for education records (total: {len(current_edu)})."
                    )
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for updating education record.")
                rec = _validate_and_build_education_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_edu[op.target_index] = rec.model_dump(mode="json", by_alias=True)
                prof_dict["education"] = current_edu
            elif op.operation_type == CorrectionOperationType.REMOVE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_edu):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for education records (total: {len(current_edu)})."
                    )
                current_edu.pop(op.target_index)
                prof_dict["education"] = current_edu

        elif sec in ("projects", "professional_profile.projects"):
            current_proj = prof_dict.get("projects", [])
            if op.operation_type == CorrectionOperationType.ADD_RECORD:
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for adding project record.")
                rec = _validate_and_build_project_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_proj.append(rec.model_dump(mode="json", by_alias=True))
                prof_dict["projects"] = current_proj
            elif op.operation_type == CorrectionOperationType.UPDATE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_proj):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for project records (total: {len(current_proj)})."
                    )
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for updating project record.")
                rec = _validate_and_build_project_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_proj[op.target_index] = rec.model_dump(mode="json", by_alias=True)
                prof_dict["projects"] = current_proj
            elif op.operation_type == CorrectionOperationType.REMOVE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_proj):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for project records (total: {len(current_proj)})."
                    )
                current_proj.pop(op.target_index)
                prof_dict["projects"] = current_proj

        elif sec in ("certifications", "professional_profile.certifications"):
            current_cert = prof_dict.get("certifications", [])
            if op.operation_type == CorrectionOperationType.ADD_RECORD:
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for adding certification record.")
                rec = _validate_and_build_certification_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_cert.append(rec.model_dump(mode="json", by_alias=True))
                prof_dict["certifications"] = current_cert
            elif op.operation_type == CorrectionOperationType.UPDATE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_cert):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for certification records (total: {len(current_cert)})."
                    )
                if not op.record_payload:
                    raise InvalidCorrectionOperationError("Missing record_payload for updating certification record.")
                rec = _validate_and_build_certification_record(
                    op.record_payload, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_cert[op.target_index] = rec.model_dump(mode="json", by_alias=True)
                prof_dict["certifications"] = current_cert
            elif op.operation_type == CorrectionOperationType.REMOVE_RECORD:
                if op.target_index is None or op.target_index < 0 or op.target_index >= len(current_cert):
                    raise InvalidCorrectionOperationError(
                        f"Target index {op.target_index} is out of bounds for certification records (total: {len(current_cert)})."
                    )
                current_cert.pop(op.target_index)
                prof_dict["certifications"] = current_cert

        elif sec in ("languages", "professional_profile.languages"):
            current_lang = prof_dict.get("languages", [])
            if op.operation_type == CorrectionOperationType.ADD_SET_ITEM:
                val = op.value.strip() if op.value else ""
                if not val:
                    raise InvalidCorrectionOperationError("Language name cannot be empty.")
                fld = _build_corrected_extracted_field(
                    "language", val, op.provenance_type, op.selected_evidence_ids, evidence_span_map, artifact_id_str
                )
                current_lang.append(fld.model_dump(mode="json", by_alias=True))
                prof_dict["languages"] = current_lang
            elif op.operation_type == CorrectionOperationType.REMOVE_SET_ITEM:
                val = op.value.strip().lower() if op.value else ""
                prof_dict["languages"] = [
                    l for l in current_lang if l.get("value", "").strip().lower() != val
                ]
            else:
                raise InvalidCorrectionOperationError(f"Operation '{op.operation_type}' not supported for languages.")

        else:
            raise InvalidCorrectionOperationError(f"Unknown target section '{op.target_section}'.")

    # 6. Validate complete schemas
    valid_identity = CandidateIdentity.model_validate(identity_dict)
    valid_prof = CandidateProfessionalProfile.model_validate(prof_dict)

    # 7. Persistence: Create new identity record if changed, else reuse existing identity_id
    if identity_changed:
        full_name_val = valid_identity.full_name.value if valid_identity.full_name else None
        email_val = valid_identity.emails[0].value if valid_identity.emails else None
        phone_val = valid_identity.phone_numbers[0].value if valid_identity.phone_numbers else None
        identity_rec = CandidateIdentityRecord(
            identity_id=uuid4(),
            artifact_id=artifact_id,
            full_name=full_name_val,
            primary_email=email_val,
            primary_phone=phone_val,
            identity_payload=valid_identity.model_dump(mode="json", by_alias=True),
        )
        session.add(identity_rec)
        session.flush()
        identity_id_to_use = identity_rec.identity_id
    else:
        identity_id_to_use = latest_profile.identity_id

    # 8. Create new CandidateProfileRecord
    new_version = latest_profile.profile_version + 1
    new_profile_id = uuid4()
    correction_batch_id = uuid4()

    new_profile_rec = CandidateProfileRecord(
        profile_id=new_profile_id,
        profile_version=new_version,
        artifact_id=artifact_id,
        extraction_id=latest_profile.extraction_id,
        identity_id=identity_id_to_use,
        parent_profile_id=latest_profile.profile_id,
        parent_profile_version=latest_profile.profile_version,
        origin_profile_id=latest_profile.origin_profile_id or latest_profile.profile_id,
        provenance=ProfileProvenance.HUMAN_CORRECTED.value,
        correction_batch_id=correction_batch_id,
        schema_version=latest_profile.schema_version,
        parser_pipeline_version=latest_profile.parser_pipeline_version,
        professional_profile=valid_prof.model_dump(mode="json", by_alias=True),
        evidence_spans=latest_profile.evidence_spans,
        review_required=False,
        warning_metadata=[],
    )
    session.add(new_profile_rec)
    session.flush()

    # 9. Create ProfileCorrectionRecord audit row
    corr_record = ProfileCorrectionRecord(
        correction_id=correction_batch_id,
        artifact_id=artifact_id,
        base_profile_id=latest_profile.profile_id,
        base_profile_version=latest_profile.profile_version,
        new_profile_id=new_profile_id,
        new_profile_version=new_version,
        idempotency_key=correction_req.idempotency_key,
        reason_category=correction_req.reason_category.value,
        reviewer_note=correction_req.reviewer_note,
        operations_payload=[op.model_dump(mode="json", by_alias=True) for op in correction_req.operations],
        actor_context=actor_context,
    )
    session.add(corr_record)
    session.flush()

    # Reload with identity for response
    stmt_reload = (
        select(CandidateProfileRecord)
        .options(selectinload(CandidateProfileRecord.identity))
        .where(CandidateProfileRecord.profile_id == new_profile_id)
    )
    reloaded_new_prof = session.execute(stmt_reload).scalar_one()

    logger.info(
        "Successfully applied profile correction",
        extra={
            "artifact_id": str(artifact_id),
            "base_profile_version": latest_profile.profile_version,
            "new_profile_version": new_version,
            "correction_id": str(correction_batch_id),
        },
    )

    return _build_correction_response(corr_record, reloaded_new_prof)


def _validate_and_build_experience_record(
    payload: Dict[str, Any],
    prov_type: EvidenceProvenanceType,
    ev_ids: List[str],
    evidence_span_map: Dict[str, EvidenceSpan],
    artifact_id: str,
) -> ExperienceRecord:
    job_title = payload.get("jobTitle") or payload.get("job_title") or ""
    org = payload.get("organization") or ""
    start_date = payload.get("startDate") or payload.get("start_date") or ""
    end_date = payload.get("endDate") or payload.get("end_date") or ""

    if not job_title.strip():
        raise InvalidCorrectionOperationError("Employment record must have a job title.")
    if not org.strip():
        raise InvalidCorrectionOperationError("Employment record must have an organization.")

    _validate_date(start_date)
    _validate_date(end_date)

    descriptions_raw = payload.get("descriptions") or []
    desc_fields = []
    for d in descriptions_raw:
        d_val = d if isinstance(d, str) else d.get("value", "")
        if d_val.strip():
            desc_fields.append(
                _build_corrected_extracted_field("description", d_val, prov_type, ev_ids, evidence_span_map, artifact_id)
            )

    return ExperienceRecord(
        job_title=_build_corrected_extracted_field("jobTitle", job_title, prov_type, ev_ids, evidence_span_map, artifact_id),
        organization=_build_corrected_extracted_field("organization", org, prov_type, ev_ids, evidence_span_map, artifact_id),
        start_date=_build_corrected_extracted_field("startDate", start_date, prov_type, ev_ids, evidence_span_map, artifact_id),
        end_date=_build_corrected_extracted_field("endDate", end_date, prov_type, ev_ids, evidence_span_map, artifact_id),
        descriptions=desc_fields,
    )


def _validate_and_build_education_record(
    payload: Dict[str, Any],
    prov_type: EvidenceProvenanceType,
    ev_ids: List[str],
    evidence_span_map: Dict[str, EvidenceSpan],
    artifact_id: str,
) -> EducationRecord:
    deg = payload.get("degree") or ""
    inst = payload.get("institution") or ""
    field_study = payload.get("fieldOfStudy") or payload.get("field_of_study")
    start_date = payload.get("startDate") or payload.get("start_date") or ""
    end_date = payload.get("endDate") or payload.get("end_date") or ""
    grade = payload.get("grade") or ""

    if not deg.strip():
        raise InvalidCorrectionOperationError("Education record must have a degree.")
    if not inst.strip():
        raise InvalidCorrectionOperationError("Education record must have an institution.")

    _validate_date(start_date)
    _validate_date(end_date)

    field_study_field = (
        _build_corrected_extracted_field("fieldOfStudy", field_study, prov_type, ev_ids, evidence_span_map, artifact_id)
        if field_study
        else None
    )

    return EducationRecord(
        degree=_build_corrected_extracted_field("degree", deg, prov_type, ev_ids, evidence_span_map, artifact_id),
        field_of_study=field_study_field,
        institution=_build_corrected_extracted_field("institution", inst, prov_type, ev_ids, evidence_span_map, artifact_id),
        start_date=_build_corrected_extracted_field("startDate", start_date, prov_type, ev_ids, evidence_span_map, artifact_id),
        end_date=_build_corrected_extracted_field("endDate", end_date, prov_type, ev_ids, evidence_span_map, artifact_id),
        grade=_build_corrected_extracted_field("grade", grade, prov_type, ev_ids, evidence_span_map, artifact_id),
    )


def _validate_and_build_project_record(
    payload: Dict[str, Any],
    prov_type: EvidenceProvenanceType,
    ev_ids: List[str],
    evidence_span_map: Dict[str, EvidenceSpan],
    artifact_id: str,
) -> ProjectRecord:
    name = payload.get("name") or ""
    desc = payload.get("description") or ""
    url = payload.get("projectUrl") or payload.get("project_url") or ""
    techs_raw = payload.get("technologies") or []

    if not name.strip():
        raise InvalidCorrectionOperationError("Project record must have a name.")

    if url.strip():
        _validate_url(url)

    tech_fields = []
    for t in techs_raw:
        t_val = t if isinstance(t, str) else t.get("value", "")
        if t_val.strip():
            tech_fields.append(
                _build_corrected_extracted_field("technology", t_val, prov_type, ev_ids, evidence_span_map, artifact_id)
            )

    return ProjectRecord(
        name=_build_corrected_extracted_field("name", name, prov_type, ev_ids, evidence_span_map, artifact_id),
        description=_build_corrected_extracted_field("description", desc, prov_type, ev_ids, evidence_span_map, artifact_id),
        technologies=tech_fields,
        project_url=_build_corrected_extracted_field("projectUrl", url, prov_type, ev_ids, evidence_span_map, artifact_id),
    )


def _validate_and_build_certification_record(
    payload: Dict[str, Any],
    prov_type: EvidenceProvenanceType,
    ev_ids: List[str],
    evidence_span_map: Dict[str, EvidenceSpan],
    artifact_id: str,
) -> CertificationRecord:
    name = payload.get("name") or ""
    issuer = payload.get("issuer") or ""
    issued_date = payload.get("issuedDate") or payload.get("issued_date") or ""
    expiry_date = payload.get("expiryDate") or payload.get("expiry_date") or ""
    cred_id = payload.get("credentialId") or payload.get("credential_id") or ""
    cred_url = payload.get("credentialUrl") or payload.get("credential_url") or ""

    if not name.strip():
        raise InvalidCorrectionOperationError("Certification record must have a name.")
    if not issuer.strip():
        raise InvalidCorrectionOperationError("Certification record must have an issuer.")

    _validate_date(issued_date)
    _validate_date(expiry_date)

    if cred_url.strip():
        _validate_url(cred_url)

    return CertificationRecord(
        name=_build_corrected_extracted_field("name", name, prov_type, ev_ids, evidence_span_map, artifact_id),
        issuer=_build_corrected_extracted_field("issuer", issuer, prov_type, ev_ids, evidence_span_map, artifact_id),
        issued_date=_build_corrected_extracted_field("issuedDate", issued_date, prov_type, ev_ids, evidence_span_map, artifact_id),
        expiry_date=_build_corrected_extracted_field("expiryDate", expiry_date, prov_type, ev_ids, evidence_span_map, artifact_id) if expiry_date else None,
        credential_id=_build_corrected_extracted_field("credentialId", cred_id, prov_type, ev_ids, evidence_span_map, artifact_id) if cred_id else None,
        credential_url=_build_corrected_extracted_field("credentialUrl", cred_url, prov_type, ev_ids, evidence_span_map, artifact_id),
    )


def _build_correction_response(
    corr_record: ProfileCorrectionRecord,
    profile_rec: CandidateProfileRecord,
) -> ProfileCorrectionResponse:
    identity_payload = profile_rec.identity.identity_payload
    profile_resp = CandidateProfileResponse(
        candidate_id=str(profile_rec.identity_id),
        artifact_id=str(profile_rec.artifact_id),
        profile_id=str(profile_rec.profile_id),
        profile_version=profile_rec.profile_version,
        schema_version=profile_rec.schema_version,
        parser_pipeline_version=profile_rec.parser_pipeline_version,
        provenance=profile_rec.provenance,
        parent_profile_id=str(profile_rec.parent_profile_id) if profile_rec.parent_profile_id else None,
        parent_profile_version=profile_rec.parent_profile_version,
        created_at=profile_rec.created_at.isoformat() if profile_rec.created_at else None,
        review_required=profile_rec.review_required,
        warnings=[ProfileAssemblyWarning.model_validate(w) for w in profile_rec.warning_metadata],
        identity=CandidateIdentity.model_validate(identity_payload),
        professional_profile=CandidateProfessionalProfile.model_validate(profile_rec.professional_profile),
        evidence=[EvidenceSpan.model_validate(ev) for ev in profile_rec.evidence_spans],
    )

    return ProfileCorrectionResponse(
        correction_id=str(corr_record.correction_id),
        previous_profile_id=str(corr_record.base_profile_id),
        previous_profile_version=corr_record.base_profile_version,
        new_profile_id=str(corr_record.new_profile_id),
        new_profile_version=corr_record.new_profile_version,
        operation_count=len(corr_record.operations_payload),
        provenance=ProfileProvenance(profile_rec.provenance),
        created_at=corr_record.created_at.isoformat() if corr_record.created_at else datetime.now(timezone.utc).isoformat(),
        profile=profile_resp,
    )
