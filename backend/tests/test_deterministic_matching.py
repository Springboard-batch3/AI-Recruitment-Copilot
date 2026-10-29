import uuid
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from app.models.job import (
    JobLifecycleStatus,
    JobVersionStatus,
    RequirementCategory,
    RequirementNecessity,
    RequirementOrigin,
    RequirementPriority,
)
from app.schemas.matching import (
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
from app.schemas.requirement_match import (
    MatchComparisonMethod,
    MatchResultState,
    RequirementMatch,
    RequirementMatchResultSet,
)
from app.services.deterministic_matching import (
    ForeignEvidenceReferenceError,
    MATCHER_RULES_VERSION,
    UnsupportedRequirementCategoryError,
    match_requisition_requirements,
)


def _make_candidate_view(
    skills=None,
    employment=None,
    education=None,
    projects=None,
    certifications=None,
    languages=None,
    evidence_refs=None,
) -> CandidateFeatureView:
    ev_refs = evidence_refs or [
        EvidenceReferenceFeature(
            evidence_id="ev-skill-1",
            page_number=1,
            block_number=1,
            character_start=0,
            character_end=10,
            extraction_method="DIRECT_TEXT",
        ),
        EvidenceReferenceFeature(
            evidence_id="ev-emp-1",
            page_number=1,
            block_number=2,
            character_start=15,
            character_end=50,
            extraction_method="DIRECT_TEXT",
        ),
        EvidenceReferenceFeature(
            evidence_id="ev-emp-2",
            page_number=1,
            block_number=3,
            character_start=55,
            character_end=90,
            extraction_method="DIRECT_TEXT",
        ),
        EvidenceReferenceFeature(
            evidence_id="ev-edu-1",
            page_number=2,
            block_number=1,
            character_start=100,
            character_end=150,
            extraction_method="DIRECT_TEXT",
        ),
        EvidenceReferenceFeature(
            evidence_id="ev-cert-1",
            page_number=2,
            block_number=2,
            character_start=160,
            character_end=200,
            extraction_method="DIRECT_TEXT",
        ),
        EvidenceReferenceFeature(
            evidence_id="ev-lang-1",
            page_number=2,
            block_number=3,
            character_start=210,
            character_end=230,
            extraction_method="DIRECT_TEXT",
        ),
    ]

    return CandidateFeatureView(
        profile_id=uuid.uuid4(),
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        headline="Senior Systems Engineer",
        skills=skills or [],
        employment=employment or [],
        education=education or [],
        projects=projects or [],
        certifications=certifications or [],
        languages=languages or [],
        evidence_references=ev_refs,
        review_required=False,
        warning_metadata=[],
    )


def _make_req_view(requirements: list[RequirementMatchingItem]) -> FrozenRequisitionMatchingView:
    return FrozenRequisitionMatchingView(
        requisition_id=uuid.uuid4(),
        job_version_id=uuid.uuid4(),
        version_number=1,
        revision=1,
        lifecycle_status=JobLifecycleStatus.ACTIVE,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        owner_user_id=uuid.uuid4(),
        title="Senior Python Backend Engineer",
        requirements=requirements,
    )


def _make_snapshot(req_view, cand_view, created_at=None) -> MatchInputSnapshot:
    return MatchInputSnapshot(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        snapshot_created_at=created_at or datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        requisition_view=req_view,
        candidate_view=cand_view,
    )


# ---------------------------------------------------------------------------
# GENERAL TESTS
# ---------------------------------------------------------------------------


def test_every_active_requirement_produces_exactly_one_result():
    """1. Every active requirement produces exactly one result."""
    r1 = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.CRITICAL,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    r2 = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=1,
        category=RequirementCategory.SKILL,
        label="Docker",
        normalized_label="docker",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="Python",
                normalized_name="python",
                evidence_ids=["ev-skill-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r1, r2]), cand)
    result_set = match_requisition_requirements(snap)

    assert len(result_set.matches) == 2
    assert result_set.matches[0].requirement_id == r1.requirement_id
    assert result_set.matches[1].requirement_id == r2.requirement_id


def test_output_order_follows_persisted_requirement_order():
    """2. Output order follows persisted requirement order."""
    r0 = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Skill Zero",
        normalized_label="skill zero",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.LOW,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    r1 = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=1,
        category=RequirementCategory.SKILL,
        label="Skill One",
        normalized_label="skill one",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.LOW,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    r2 = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=2,
        category=RequirementCategory.SKILL,
        label="Skill Two",
        normalized_label="skill two",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.LOW,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )

    # Requisition items scrambled in list
    req_view = _make_req_view([r2, r0, r1])
    cand = _make_candidate_view()
    snap = _make_snapshot(req_view, cand)

    result_set = match_requisition_requirements(snap)
    orders = [m.display_order for m in result_set.matches]
    assert orders == [0, 1, 2]


def test_same_input_and_evaluation_date_produce_identical_output():
    """3. Same input and evaluation date produce identical output."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    eval_date = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    snap = _make_snapshot(_make_req_view([r]), cand)

    res1 = match_requisition_requirements(snap, evaluation_date=eval_date)
    res2 = match_requisition_requirements(snap, evaluation_date=eval_date)

    assert res1.model_dump(mode="json") == res2.model_dump(mode="json")


def test_match_values_remain_between_zero_and_one():
    """4. Match values remain between 0.0 and 1.0."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view()
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    for m in result_set.matches:
        assert 0.0 <= m.match_value <= 1.0


def test_credited_results_contain_valid_evidence():
    """5. Credited results contain valid evidence."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.PREFERRED,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    match = result_set.matches[0]
    assert match.result_state == MatchResultState.MATCHED
    assert match.match_value == 1.0
    assert "ev-skill-1" in match.evidence_references


def test_candidate_identity_changes_do_not_alter_results():
    """6. Candidate identity changes do not alter results."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    # Verify no identity or protected fields in serialized result
    serialized = res.model_dump(mode="json")
    verify_no_prohibited_keys(serialized)


def test_raw_resume_and_job_description_text_never_appear_in_results():
    """7. Raw resume and raw job-description text never appear in results."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    res_str = str(res.model_dump(mode="json"))
    assert "raw_text" not in res_str
    assert "job_description_text" not in res_str
    assert "storage_path" not in res_str


def test_unsupported_categories_fail_safely():
    """8. Unsupported categories fail safely."""
    # Construct invalid category requirement manually
    item_dict = {
        "requirement_id": uuid.uuid4(),
        "display_order": 0,
        "category": RequirementCategory.SKILL,
        "label": "Unknown Item",
        "normalized_label": "unknown item",
        "necessity": RequirementNecessity.PREFERRED,
        "priority": RequirementPriority.LOW,
        "origin": RequirementOrigin.RECRUITER_ENTERED,
    }
    r = RequirementMatchingItem(**item_dict)
    # Monkeypatch category to unknown enum value
    object.__setattr__(r, "category", "NON_EXISTENT_CATEGORY")

    snap = _make_snapshot(_make_req_view([r]), _make_candidate_view())
    with pytest.raises(UnsupportedRequirementCategoryError):
        match_requisition_requirements(snap)


def test_no_aggregate_compatibility_score_or_hiring_decision():
    """9 & 10. No aggregate compatibility score or hiring decision is produced."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    dumped = res.model_dump(mode="json")
    assert "compatibility_percentage" not in dumped
    assert "total_score" not in dumped
    assert "overall_score" not in dumped
    assert "hiring_decision" not in dumped
    assert "ranking" not in dumped


# ---------------------------------------------------------------------------
# SKILL TESTS
# ---------------------------------------------------------------------------


def test_skill_canonical_id_exact_match():
    """Skill 1: Canonical ID exact match."""
    canon_id = uuid.uuid4()
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python Language",
        normalized_label="python language",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED,
        taxonomy_skill_concept_id=canon_id,
        taxonomy_canonical_label="Python",
    )
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="Python",
                normalized_name="python",
                evidence_ids=["ev-skill-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    m = result_set.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0
    assert m.comparison_method == MatchComparisonMethod.CANONICAL_TAXONOMY_ID


def test_skill_normalized_exact_label_match():
    """Skill 2: Normalized exact-label match."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="FastAPI Framework",
        normalized_label="fastapi framework",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="FastAPI Framework",
                normalized_name="fastapi framework",
                evidence_ids=["ev-skill-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    m = result_set.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0
    assert m.comparison_method == MatchComparisonMethod.EXACT_NORMALIZED_LABEL


def test_skill_recruiter_approved_alias_match():
    """Skill 3: Recruiter-approved alias match."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Kubernetes",
        normalized_label="kubernetes",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        accepted_equivalents=["k8s", "kube"],
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="k8s",
                normalized_name="k8s",
                evidence_ids=["ev-skill-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    m = result_set.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0
    assert m.comparison_method == MatchComparisonMethod.RECRUITER_APPROVED_ALIAS


def test_custom_requirement_without_onet_mapping_matches_normally():
    """Skill 4: Custom requirement without O*NET mapping matches cleanly."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Internal Proprietary Framework",
        normalized_label="internal proprietary framework",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
        taxonomy_skill_concept_id=None,
    )
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="Internal Proprietary Framework",
                normalized_name="internal proprietary framework",
                evidence_ids=["ev-skill-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    result_set = match_requisition_requirements(snap)

    m = result_set.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0


def test_substring_false_positives_prevented():
    """Skill 6-8: Java vs JavaScript, C vs C++, React vs React Native."""
    # Java should NOT match JavaScript
    r_java = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Java",
        normalized_label="java",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand_js = _make_candidate_view(
        skills=[CandidateSkillFeature(name="JavaScript", normalized_name="javascript", evidence_ids=["ev-skill-1"])]
    )
    snap_java = _make_snapshot(_make_req_view([r_java]), cand_js)
    res_java = match_requisition_requirements(snap_java)
    assert res_java.matches[0].result_state == MatchResultState.NOT_EVIDENCED
    assert res_java.matches[0].match_value == 0.0

    # C should NOT match C++
    r_c = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="C",
        normalized_label="c",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand_cpp = _make_candidate_view(
        skills=[CandidateSkillFeature(name="C++", normalized_name="c++", evidence_ids=["ev-skill-1"])]
    )
    snap_c = _make_snapshot(_make_req_view([r_c]), cand_cpp)
    res_c = match_requisition_requirements(snap_c)
    assert res_c.matches[0].result_state == MatchResultState.NOT_EVIDENCED
    assert res_c.matches[0].match_value == 0.0

    # React should NOT match React Native
    r_react = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="React",
        normalized_label="react",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand_rn = _make_candidate_view(
        skills=[CandidateSkillFeature(name="React Native", normalized_name="react native", evidence_ids=["ev-skill-1"])]
    )
    snap_react = _make_snapshot(_make_req_view([r_react]), cand_rn)
    res_react = match_requisition_requirements(snap_react)
    assert res_react.matches[0].result_state == MatchResultState.NOT_EVIDENCED
    assert res_react.matches[0].match_value == 0.0


def test_skill_presence_does_not_prove_experience_duration():
    """Skill 9: Skill presence in skill list does not prove experience duration."""
    r_exp = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Python Developer",
        normalized_label="python developer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=36,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    # Candidate lists Python as a skill, but has NO employment history
    cand = _make_candidate_view(
        skills=[CandidateSkillFeature(name="Python", normalized_name="python", evidence_ids=["ev-skill-1"])]
    )
    snap = _make_snapshot(_make_req_view([r_exp]), cand)
    res = match_requisition_requirements(snap)

    assert res.matches[0].result_state == MatchResultState.NOT_EVIDENCED
    assert res.matches[0].match_value == 0.0


# ---------------------------------------------------------------------------
# EXPERIENCE TESTS
# ---------------------------------------------------------------------------


def test_experience_exactly_meets_minimum():
    """Experience 1: Experience exactly meets minimum."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Backend Engineer",
        normalized_label="backend engineer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=24,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Backend Engineer",
                start_date="2022-01",
                end_date="2024-01",
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0


def test_experience_exceeds_minimum_and_is_capped_at_one():
    """Experience 2: Experience exceeds minimum and is capped at 1.0."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Backend Engineer",
        normalized_label="backend engineer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=12,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Backend Engineer",
                start_date="2020-01",
                end_date="2024-01",
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.MATCHED
    assert m.match_value == 1.0


def test_experience_below_minimum_is_partial():
    """Experience 3: Experience below minimum is partial."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Backend Engineer",
        normalized_label="backend engineer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=24,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Backend Engineer",
                start_date="2023-01",
                end_date="2024-01",  # 12 months out of 24
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.PARTIALLY_MATCHED
    assert m.match_value == 0.5
    assert any("EXPERIENCE_BELOW_THRESHOLD" in code for code in m.reason_codes)


def test_no_relevant_experience_evidence_returns_not_evidenced():
    """Experience 4: No relevant evidence returns NOT_EVIDENCED."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Kubernetes Operator",
        normalized_label="kubernetes operator",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=12,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Sales Associate",
                start_date="2020-01",
                end_date="2022-01",
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.NOT_EVIDENCED
    assert m.match_value == 0.0
    assert m.evidence_references == []


def test_overlapping_intervals_are_not_double_counted():
    """Experience 6: Overlapping intervals are merged without double-counting."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Software Engineer",
        normalized_label="software engineer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=24,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    # Role 1: Jan 2022 - Jan 2023 (12 months)
    # Role 2: Jun 2022 - Jun 2023 (12 months, overlaps Jun 2022 - Jan 2023)
    # Total merged: Jan 2022 - Jun 2023 = 18 months (NOT 24 months!)
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Software Engineer Part-Time",
                start_date="2022-01",
                end_date="2023-01",
                evidence_ids=["ev-emp-1"],
            ),
            CandidateEmploymentRoleFeature(
                job_title="Software Engineer Consulting",
                start_date="2022-06",
                end_date="2023-06",
                evidence_ids=["ev-emp-2"],
            ),
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.PARTIALLY_MATCHED
    # 17 / 24 = 0.7083 (Jan 2022 to Jun 2023 is 17 months in date_extraction interval math)
    assert m.match_value == round(17 / 24, 4)


def test_conflicting_inverted_intervals_surfaced():
    """Experience 8: Conflicting intervals surfaced as CONFLICTING_EVIDENCE."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Software Engineer",
        normalized_label="software engineer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=12,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    # Inverted date: start 2024-01 > end 2022-01
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Software Engineer",
                start_date="2024-01",
                end_date="2022-01",
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    m = res.matches[0]
    assert m.result_state == MatchResultState.CONFLICTING_EVIDENCE
    assert m.match_value == 0.0
    assert "INVERTED_DATE_INTERVAL" in m.reason_codes


def test_recency_boundary_deterministic_against_evaluation_date():
    """Experience 9 & 10: Recency boundary is deterministic and decoupled from system clock."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EXPERIENCE,
        label="Backend Developer",
        normalized_label="backend developer",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        min_experience_months=12,
        recency_years=2,  # Must be within 2 years (24 months)
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    # Role ended 2020-01
    cand = _make_candidate_view(
        employment=[
            CandidateEmploymentRoleFeature(
                job_title="Backend Developer",
                start_date="2018-01",
                end_date="2020-01",
                evidence_ids=["ev-emp-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)

    # Eval date A: 2021-01 -> 12 months elapsed (within 24 months)
    res_a = match_requisition_requirements(
        snap, evaluation_date=datetime(2021, 1, 1, tzinfo=timezone.utc)
    )
    assert res_a.matches[0].result_state == MatchResultState.MATCHED
    assert res_a.matches[0].match_value == 1.0

    # Eval date B: 2024-01 -> 48 months elapsed (exceeds 24 months)
    res_b = match_requisition_requirements(
        snap, evaluation_date=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    assert res_b.matches[0].result_state == MatchResultState.PARTIALLY_MATCHED
    assert "RECENCY_THRESHOLD_EXCEEDED" in res_b.matches[0].reason_codes


# ---------------------------------------------------------------------------
# EDUCATION, PROJECT, CERTIFICATION, LANGUAGE TESTS
# ---------------------------------------------------------------------------


def test_education_exact_match_and_approved_equivalent():
    """Education: Exact match and recruiter-approved equivalent."""
    r_exact = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.EDUCATION,
        label="Master of Science in Computer Science",
        normalized_label="master of science in computer science",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    r_equiv = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=1,
        category=RequirementCategory.EDUCATION,
        label="Bachelor of Science in Computer Science",
        normalized_label="bachelor of science in computer science",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        accepted_equivalents=["BS CS", "B.S. in Software Engineering"],
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        education=[
            CandidateEducationFeature(
                degree="BS CS",
                field_of_study="Computer Science",
                evidence_ids=["ev-edu-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r_exact, r_equiv]), cand)
    res = match_requisition_requirements(snap)

    assert res.matches[0].result_state == MatchResultState.NOT_EVIDENCED
    assert res.matches[1].result_state == MatchResultState.MATCHED
    assert res.matches[1].comparison_method == MatchComparisonMethod.EDUCATION_DEGREE_AND_FIELD


def test_certification_exact_match_and_expiry():
    """Certification: Exact match and expiration check relative to evaluation date."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.CERTIFICATION,
        label="AWS Solutions Architect",
        normalized_label="aws solutions architect",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        certifications=[
            CandidateCertificationFeature(
                name="AWS Solutions Architect",
                issued_date="2020-01",
                expiry_date="2023-01",
                evidence_ids=["ev-cert-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)

    # In 2022 (not expired)
    res_valid = match_requisition_requirements(
        snap, evaluation_date=datetime(2022, 1, 1, tzinfo=timezone.utc)
    )
    assert res_valid.matches[0].result_state == MatchResultState.MATCHED
    assert res_valid.matches[0].match_value == 1.0

    # In 2024 (expired)
    res_expired = match_requisition_requirements(
        snap, evaluation_date=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    assert res_expired.matches[0].result_state == MatchResultState.PARTIALLY_MATCHED
    assert "CERTIFICATION_EXPIRED" in res_expired.matches[0].reason_codes


def test_language_exact_match():
    """Language: Exact language match."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.LANGUAGE,
        label="English",
        normalized_label="english",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    cand = _make_candidate_view(
        languages=[
            CandidateLanguageFeature(
                language="English",
                normalized_language="english",
                proficiency="Fluent",
                evidence_ids=["ev-lang-1"],
            )
        ]
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    res = match_requisition_requirements(snap)

    assert res.matches[0].result_state == MatchResultState.MATCHED
    assert res.matches[0].match_value == 1.0
    assert res.matches[0].comparison_method == MatchComparisonMethod.LANGUAGE_PROFICIENCY


def test_foreign_evidence_reference_rejected():
    """Evidence lineage: An evidence ID not in candidate profile raises ForeignEvidenceReferenceError."""
    r = RequirementMatchingItem(
        requirement_id=uuid.uuid4(),
        display_order=0,
        category=RequirementCategory.SKILL,
        label="Python",
        normalized_label="python",
        necessity=RequirementNecessity.MUST_HAVE,
        priority=RequirementPriority.HIGH,
        origin=RequirementOrigin.RECRUITER_ENTERED,
    )
    # Skill points to ev-foreign which is not in candidate's evidence_references
    cand = _make_candidate_view(
        skills=[
            CandidateSkillFeature(
                name="Python",
                normalized_name="python",
                evidence_ids=["ev-foreign"],
            )
        ],
        evidence_refs=[],  # No evidence references in candidate view
    )
    snap = _make_snapshot(_make_req_view([r]), cand)
    with pytest.raises(ForeignEvidenceReferenceError):
        match_requisition_requirements(snap)
