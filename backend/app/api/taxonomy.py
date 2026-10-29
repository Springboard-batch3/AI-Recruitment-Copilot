import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.auth import AuthenticatedContext, require_role
from app.database import get_db
from app.models.auth import UserRole
from app.models.taxonomy import (
    OccupationAlias,
    OccupationConcept,
    OccupationSkillLink,
    SkillAlias,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.schemas.taxonomy import (
    OccupationSearchItemResponse,
    SkillSearchItemResponse,
)
from app.services.taxonomy_normalization import normalize_search_query

logger = logging.getLogger("ai_hiring.api.taxonomy")

router = APIRouter(
    prefix="/api/v1/taxonomy",
    tags=["taxonomy"],
)

require_recruiter_or_admin = require_role({UserRole.RECRUITER, UserRole.ADMIN})


def _escape_like_pattern(text: str) -> str:
    """Escapes SQL LIKE wildcards (% and _) safely."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get(
    "/occupations",
    response_model=List[OccupationSearchItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Search Occupations and Job Titles",
    description=(
        "Searches active O*NET occupation concepts and alternate job titles using deterministic 7-tier ranking. "
        "Accessible only to RECRUITER and ADMIN."
    ),
)
async def search_occupations(
    q: Optional[str] = Query(default=None, min_length=1, max_length=100, description="Search query string"),
    query: Optional[str] = Query(default=None, min_length=1, max_length=100, description="Alternative search query parameter"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of results to return"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[OccupationSearchItemResponse]:
    raw_query = q or query
    cleaned_q = raw_query.strip() if raw_query else ""
    if not cleaned_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_SEARCH_QUERY", "message": "Search query cannot be empty or whitespace."},
        )

    norm_q = normalize_search_query(cleaned_q)
    escaped_norm_q = _escape_like_pattern(norm_q)


    # 1. Fetch active snapshot
    stmt_snap = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    snapshot = db.execute(stmt_snap).scalar_one_or_none()
    if snapshot is None:
        return []

    # 2. Query matching concepts and aliases
    # We query occupations joined with aliases on active snapshot
    stmt = (
        select(OccupationConcept)
        .options(selectinload(OccupationConcept.aliases))
        .where(
            OccupationConcept.snapshot_id == snapshot.snapshot_id,
            or_(
                OccupationConcept.normalized_title.like(f"%{escaped_norm_q}%", escape="\\"),
                OccupationConcept.aliases.any(
                    OccupationAlias.normalized_alias.like(f"%{escaped_norm_q}%", escape="\\")
                ),
            ),
        )
    )
    matching_occupations = db.execute(stmt).scalars().all()

    # 3. Deterministic 7-Tier Ranking:
    # Tier 1: Exact canonical match (normalized_title == norm_q)
    # Tier 2: Exact alias match (alias.normalized_alias == norm_q)
    # Tier 3: Prefix canonical match (normalized_title.startswith(norm_q))
    # Tier 4: Prefix alias match (alias.normalized_alias.startswith(norm_q))
    # Tier 5: Substring canonical match (norm_q in normalized_title)
    # Tier 6: Substring alias match (norm_q in alias.normalized_alias)
    # Tie-breaker: (tier, canonical_title.lower(), concept_id)

    scored_items = []
    for occ in matching_occupations:
        tier = 7
        matched_alias_text = None

        if occ.normalized_title == norm_q:
            tier = 1
        else:
            # Check exact alias match
            exact_alias = next((a for a in occ.aliases if a.normalized_alias == norm_q), None)
            if exact_alias:
                tier = 2
                matched_alias_text = exact_alias.job_title_alias
            elif occ.normalized_title.startswith(norm_q):
                tier = 3
            else:
                # Check prefix alias match
                prefix_alias = next((a for a in occ.aliases if a.normalized_alias.startswith(norm_q)), None)
                if prefix_alias:
                    tier = 4
                    matched_alias_text = prefix_alias.job_title_alias
                elif norm_q in occ.normalized_title:
                    tier = 5
                else:
                    # Check substring alias match
                    sub_alias = next((a for a in occ.aliases if norm_q in a.normalized_alias), None)
                    if sub_alias:
                        tier = 6
                        matched_alias_text = sub_alias.job_title_alias

        scored_items.append((
            tier,
            occ.canonical_title.lower(),
            occ.concept_id,
            occ,
            matched_alias_text,
        ))

    scored_items.sort(key=lambda x: (x[0], x[1], str(x[2])))

    results = [
        OccupationSearchItemResponse(
            concept_id=item[3].concept_id,
            onet_soc_code=item[3].onet_soc_code,
            canonical_title=item[3].canonical_title,
            matched_alias=item[4],
            source=item[3].source_provenance,
            source_version=snapshot.source_version,
            entity_type="occupation",
            dataset_version=snapshot.source_version,
        )
        for item in scored_items[:limit]
    ]

    return results


@router.get(
    "/skills",
    response_model=List[SkillSearchItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Search Skills across Essential, Transferable, Software and Controlled Aliases",
    description=(
        "Searches active skill concepts and controlled aliases using deterministic 7-tier ranking. "
        "Accessible only to RECRUITER and ADMIN."
    ),
)
async def search_skills(
    q: Optional[str] = Query(default=None, min_length=1, max_length=100, description="Search query string"),
    query: Optional[str] = Query(default=None, min_length=1, max_length=100, description="Alternative search query parameter"),
    limit: int = Query(default=10, ge=1, le=50, description="Maximum number of results to return"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
) -> List[SkillSearchItemResponse]:
    raw_query = q or query
    cleaned_q = raw_query.strip() if raw_query else ""
    if not cleaned_q:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EMPTY_SEARCH_QUERY", "message": "Search query cannot be empty or whitespace."},
        )

    norm_q = normalize_search_query(cleaned_q)
    escaped_norm_q = _escape_like_pattern(norm_q)

    # 1. Fetch active snapshot
    stmt_snap = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    snapshot = db.execute(stmt_snap).scalar_one_or_none()
    if snapshot is None:
        return []

    # 2. Query skills matching query in canonical label or aliases
    stmt = (
        select(SkillConcept)
        .options(selectinload(SkillConcept.aliases))
        .where(
            SkillConcept.snapshot_id == snapshot.snapshot_id,
            or_(
                SkillConcept.normalized_label.like(f"%{escaped_norm_q}%", escape="\\"),
                SkillConcept.aliases.any(
                    SkillAlias.normalized_alias.like(f"%{escaped_norm_q}%", escape="\\")
                ),
            ),
        )
    )
    matching_skills = db.execute(stmt).scalars().all()

    # 3. Deterministic 7-Tier Ranking
    scored_items = []
    for sk in matching_skills:
        tier = 7
        matched_alias_text = None

        if sk.normalized_label == norm_q:
            tier = 1
        else:
            exact_alias = next((a for a in sk.aliases if a.normalized_alias == norm_q), None)
            if exact_alias:
                tier = 2
                matched_alias_text = exact_alias.alias_text
            elif sk.normalized_label.startswith(norm_q):
                tier = 3
            else:
                prefix_alias = next((a for a in sk.aliases if a.normalized_alias.startswith(norm_q)), None)
                if prefix_alias:
                    tier = 4
                    matched_alias_text = prefix_alias.alias_text
                elif norm_q in sk.normalized_label:
                    tier = 5
                else:
                    sub_alias = next((a for a in sk.aliases if norm_q in a.normalized_alias), None)
                    if sub_alias:
                        tier = 6
                        matched_alias_text = sub_alias.alias_text

        scored_items.append((
            tier,
            sk.canonical_label.lower(),
            sk.concept_id,
            sk,
            matched_alias_text,
        ))

    scored_items.sort(key=lambda x: (x[0], x[1], str(x[2])))

    results = [
        SkillSearchItemResponse(
            concept_id=item[3].concept_id,
            canonical_label=item[3].canonical_label,
            matched_alias=item[4],
            category=item[3].category,
            source=item[3].source,
            source_family=getattr(item[3], "source_family", "software") or "software",
            source_version=snapshot.source_version,
            external_id=item[3].external_id,
            entity_type="skill",
        )
        for item in scored_items[:limit]
    ]

    return results


@router.get(
    "/occupations/{concept_id}/skills",
    response_model=List[dict],
    status_code=status.HTTP_200_OK,
    summary="Get Associated Skill Suggestions for an Occupation",
    description=(
        "Returns skills linked to an exact O*NET occupation concept, deterministically ranked. "
        "Accessible only to RECRUITER and ADMIN."
    ),
)
async def get_occupation_skills(
    concept_id: uuid.UUID,
    limit: int = Query(default=30, ge=1, le=100, description="Maximum number of skills to return"),
    auth_ctx: AuthenticatedContext = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db),
):
    # 1. Fetch active snapshot
    stmt_snap = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
    snapshot = db.execute(stmt_snap).scalar_one_or_none()
    if snapshot is None:
        return []

    # 2. Verify occupation exists in active snapshot
    stmt_occ = select(OccupationConcept).where(
        OccupationConcept.concept_id == concept_id,
        OccupationConcept.snapshot_id == snapshot.snapshot_id,
    )
    occ = db.execute(stmt_occ).scalar_one_or_none()
    if occ is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OCCUPATION_NOT_FOUND", "message": f"Occupation '{concept_id}' not found in active snapshot."},
        )

    # 3. Fetch linked skills
    stmt_links = (
        select(OccupationSkillLink)
        .options(selectinload(OccupationSkillLink.skill))
        .where(OccupationSkillLink.occupation_concept_id == concept_id)
    )
    links = db.execute(stmt_links).scalars().all()

    # 4. Rank deterministically:
    # 1. Relevant and not suppressed (not_relevant is False, recommend_suppress is False)
    # 2. Importance rating descending (None -> -1.0)
    # 3. in_demand software flag (True > False)
    # 4. hot_technology flag (True > False)
    # 5. Official label alphabetically
    def sort_key(link: OccupationSkillLink):
        is_active_rel = (not getattr(link, "not_relevant", False)) and (not getattr(link, "recommend_suppress", False))
        importance = getattr(link, "importance_value", None) or -1.0
        in_demand = bool(getattr(link, "in_demand", False))
        hot_tech = bool(getattr(link, "hot_technology", False))
        label = link.skill.canonical_label.lower() if link.skill else ""
        return (
            0 if is_active_rel else 1,
            -importance,
            0 if in_demand else 1,
            0 if hot_tech else 1,
            label,
        )

    sorted_links = sorted(links, key=sort_key)

    results = []
    for link in sorted_links[:limit]:
        if not link.skill:
            continue
        results.append({
            "concept_id": str(link.skill_concept_id),
            "canonical_label": link.skill.canonical_label,
            "category": link.skill.category,
            "source": link.skill.source,
            "source_family": getattr(link, "source_family", getattr(link.skill, "source_family", "software")),
            "external_id": getattr(link, "element_id", link.skill.external_id),
            "hot_technology": getattr(link, "hot_technology", False),
            "in_demand": getattr(link, "in_demand", False),
            "importance_value": getattr(link, "importance_value", None),
            "level_value": getattr(link, "level_value", None),
            "recommend_suppress": getattr(link, "recommend_suppress", False),
            "not_relevant": getattr(link, "not_relevant", False),
        })

    return results

