import uuid
from decimal import Decimal
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.job import (
    JobLifecycleStatus,
    JobVersionStatus,
    JobRequisition,
    JobVersion,
    JobRequirement,
    RequirementCategory,
    RequirementNecessity,
    RequirementPriority,
    RequirementOrigin,
)
from app.models.candidate import CandidateProfileRecord
from app.models.matching import MatchRunRecord, MatchResultRecord
from app.models.gap_report import GapReportRecord, GapReportItemRecord
from app.services.deterministic_matching import match_requisition_requirements
from app.services.scoring import calculate_compatibility_score
from app.schemas.matching import (
    CandidateFeatureView,
    CandidateSkillFeature,
    CandidateEmploymentRoleFeature,
    CandidateProjectFeature,
    EvidenceReferenceFeature,
    FrozenRequisitionMatchingView,
    RequirementMatchingItem,
)
from app.services.canonical_summary import compute_canonical_evidence_summary

client = TestClient(app)


def test_controlled_job_and_fixtures_matching_validation():
    """Phase 2E.2: Controlled End-to-End Validation of Junior Python API Developer

    against Alex Morgan, Priya Shah, and Daniel Lee.
    """
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Create Controlled Ready Job Requisition & Job Version
    job_id = uuid.uuid4()
    version_id = uuid.uuid4()

    req_items = [
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=0,
            label="Python",
            normalized_label="python",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.CRITICAL,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=[],
        ),
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=1,
            label="REST API development",
            normalized_label="rest api development",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=["REST API", "RESTful API", "API development"],
        ),
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=2,
            label="PostgreSQL",
            normalized_label="postgresql",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.MUST_HAVE,
            priority=RequirementPriority.HIGH,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=["Postgres", "PostgreSQL database"],
        ),
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=3,
            label="FastAPI",
            normalized_label="fastapi",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.MEDIUM,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=[],
        ),
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=4,
            label="Git",
            normalized_label="git",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.MEDIUM,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=["Git version control", "GitHub", "GitLab"],
        ),
        RequirementMatchingItem(
            requirement_id=uuid.uuid4(),
            display_order=5,
            label="Docker",
            normalized_label="docker",
            category=RequirementCategory.SKILL,
            necessity=RequirementNecessity.PREFERRED,
            priority=RequirementPriority.LOW,
            approved_weight=1.0,
            origin=RequirementOrigin.RECRUITER_ENTERED,
            accepted_equivalents=["Docker containerization", "Containers"],
        ),
    ]

    owner_id = uuid.uuid4()
    job_view = FrozenRequisitionMatchingView(
        requisition_id=job_id,
        job_version_id=version_id,
        version_number=1,
        revision=1,
        lifecycle_status=JobLifecycleStatus.ACTIVE,
        version_status=JobVersionStatus.READY_FOR_MATCHING,
        owner_user_id=owner_id,
        title="Junior Python API Developer",
        requirements=req_items,
    )

    # 2. Build 3 Candidate Feature Views
    # Alex Morgan: All 3 required, all 3 preferred evidenced
    alex_id = uuid.uuid4()
    alex_view = CandidateFeatureView(
        profile_id=alex_id,
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        skills=[
            CandidateSkillFeature(name="Python", normalized_name="python", confidence=0.95, evidence_ids=["alex-ev-1"]),
            CandidateSkillFeature(name="REST API", normalized_name="rest api", confidence=0.95, evidence_ids=["alex-ev-2"]),
            CandidateSkillFeature(name="PostgreSQL", normalized_name="postgresql", confidence=0.95, evidence_ids=["alex-ev-3"]),
            CandidateSkillFeature(name="FastAPI", normalized_name="fastapi", confidence=0.95, evidence_ids=["alex-ev-4"]),
            CandidateSkillFeature(name="Git", normalized_name="git", confidence=0.95, evidence_ids=["alex-ev-5"]),
            CandidateSkillFeature(name="Docker", normalized_name="docker", confidence=0.95, evidence_ids=["alex-ev-6"]),
        ],
        employment=[],
        education=[],
        projects=[],
        certifications=[],
        languages=[],
        evidence_references=[
            EvidenceReferenceFeature(evidence_id=f"alex-ev-{i}", page_number=1, block_number=i, character_start=0, character_end=10, extraction_method="DIRECT_TEXT")
            for i in range(1, 7)
        ],
    )

    # Priya Shah: All 3 required, 1 preferred (Git). Has Flask (must not satisfy FastAPI), no Docker.
    priya_id = uuid.uuid4()
    priya_view = CandidateFeatureView(
        profile_id=priya_id,
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        skills=[
            CandidateSkillFeature(name="Python", normalized_name="python", confidence=0.95, evidence_ids=["priya-ev-1"]),
            CandidateSkillFeature(name="RESTful API", normalized_name="restful api", confidence=0.95, evidence_ids=["priya-ev-2"]),
            CandidateSkillFeature(name="Postgres", normalized_name="postgres", confidence=0.95, evidence_ids=["priya-ev-3"]),
            CandidateSkillFeature(name="Flask", normalized_name="flask", confidence=0.95, evidence_ids=["priya-ev-4"]),
            CandidateSkillFeature(name="Git", normalized_name="git", confidence=0.95, evidence_ids=["priya-ev-5"]),
        ],
        employment=[],
        education=[],
        projects=[],
        certifications=[],
        languages=[],
        evidence_references=[
            EvidenceReferenceFeature(evidence_id=f"priya-ev-{i}", page_number=1, block_number=i, character_start=0, character_end=10, extraction_method="DIRECT_TEXT")
            for i in range(1, 6)
        ],
    )

    # Daniel Lee: 0 required (Java, MySQL), 1 preferred (Git). Maven & JUnit must not satisfy Docker.
    daniel_id = uuid.uuid4()
    daniel_view = CandidateFeatureView(
        profile_id=daniel_id,
        profile_version=1,
        schema_version="1.0",
        parser_pipeline_version="1.0.0",
        provenance="PARSER_EXTRACTED",
        skills=[
            CandidateSkillFeature(name="Java", normalized_name="java", confidence=0.95, evidence_ids=["daniel-ev-1"]),
            CandidateSkillFeature(name="MySQL", normalized_name="mysql", confidence=0.95, evidence_ids=["daniel-ev-2"]),
            CandidateSkillFeature(name="Git", normalized_name="git", confidence=0.95, evidence_ids=["daniel-ev-3"]),
            CandidateSkillFeature(name="Maven", normalized_name="maven", confidence=0.95, evidence_ids=["daniel-ev-4"]),
            CandidateSkillFeature(name="JUnit", normalized_name="junit", confidence=0.95, evidence_ids=["daniel-ev-5"]),
        ],
        employment=[],
        education=[],
        projects=[],
        certifications=[],
        languages=[],
        evidence_references=[
            EvidenceReferenceFeature(evidence_id=f"daniel-ev-{i}", page_number=1, block_number=i, character_start=0, character_end=10, extraction_method="DIRECT_TEXT")
            for i in range(1, 6)
        ],
    )

    from app.schemas.matching import MatchInputSnapshot

    # 3. Match each candidate against the 6 requirements
    alex_snap = MatchInputSnapshot(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        snapshot_created_at=now,
        requisition_view=job_view,
        candidate_view=alex_view,
    )
    priya_snap = MatchInputSnapshot(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        snapshot_created_at=now,
        requisition_view=job_view,
        candidate_view=priya_view,
    )
    daniel_snap = MatchInputSnapshot(
        match_input_schema_version="1.0.0",
        matcher_algorithm_version="1.0.0",
        snapshot_created_at=now,
        requisition_view=job_view,
        candidate_view=daniel_view,
    )

    alex_matches = match_requisition_requirements(alex_snap, evaluation_date=now)
    priya_matches = match_requisition_requirements(priya_snap, evaluation_date=now)
    daniel_matches = match_requisition_requirements(daniel_snap, evaluation_date=now)

    assert len(alex_matches.matches) == 6
    assert len(priya_matches.matches) == 6
    assert len(daniel_matches.matches) == 6

    # Verify total requirement results = exactly 18 across 3 candidates
    total_req_results = len(alex_matches.matches) + len(priya_matches.matches) + len(daniel_matches.matches)
    assert total_req_results == 18

    # 4. Verify Alex Morgan Evidence
    alex_matched_reqs = [m for m in alex_matches.matches if m.result_state.value == "MATCHED"]
    assert len(alex_matched_reqs) == 6  # 3 required + 3 preferred

    # 5. Verify Priya Shah Evidence and Negative Matching
    # Flask must not satisfy FastAPI
    priya_fastapi_match = next(m for m in priya_matches.matches if m.requirement_name == "FastAPI")
    assert priya_fastapi_match.result_state.value == "NOT_EVIDENCED"
    assert priya_fastapi_match.match_value == 0.0

    priya_docker_match = next(m for m in priya_matches.matches if m.requirement_name == "Docker")
    assert priya_docker_match.result_state.value == "NOT_EVIDENCED"

    priya_req_matched = [m for m in priya_matches.matches if m.necessity == RequirementNecessity.MUST_HAVE and m.result_state.value == "MATCHED"]
    assert len(priya_req_matched) == 3  # Python, REST API, PostgreSQL

    priya_pref_matched = [m for m in priya_matches.matches if m.necessity == RequirementNecessity.PREFERRED and m.result_state.value == "MATCHED"]
    assert len(priya_pref_matched) == 1
    assert priya_pref_matched[0].requirement_name == "Git"

    # 6. Verify Daniel Lee Evidence and Negative Matching
    # Java must not satisfy Python
    daniel_python_match = next(m for m in daniel_matches.matches if m.requirement_name == "Python")
    assert daniel_python_match.result_state.value == "NOT_EVIDENCED"

    # MySQL must not satisfy PostgreSQL
    daniel_postgres_match = next(m for m in daniel_matches.matches if m.requirement_name == "PostgreSQL")
    assert daniel_postgres_match.result_state.value == "NOT_EVIDENCED"

    # Maven or JUnit must not satisfy Docker
    daniel_docker_match = next(m for m in daniel_matches.matches if m.requirement_name == "Docker")
    assert daniel_docker_match.result_state.value == "NOT_EVIDENCED"

    daniel_req_matched = [m for m in daniel_matches.matches if m.necessity == RequirementNecessity.MUST_HAVE and m.result_state.value == "MATCHED"]
    assert len(daniel_req_matched) == 0  # 0 of 3 required

    daniel_pref_matched = [m for m in daniel_matches.matches if m.necessity == RequirementNecessity.PREFERRED and m.result_state.value == "MATCHED"]
    assert len(daniel_pref_matched) == 1
    assert daniel_pref_matched[0].requirement_name == "Git"

    # 7. Calculate Compatibility Scores
    alex_score = calculate_compatibility_score(alex_matches)
    priya_score = calculate_compatibility_score(priya_matches)
    daniel_score = calculate_compatibility_score(daniel_matches)

    # 8. Verify Expected Rank Order: Alex > Priya > Daniel
    assert alex_score.raw_compatibility_score > priya_score.raw_compatibility_score
    assert priya_score.raw_compatibility_score > daniel_score.raw_compatibility_score

    # 9. Verify Evidence Summary Invariants
    alex_summary = compute_canonical_evidence_summary(alex_score.requirement_contributions, "HIGH")
    assert alex_summary.required_total == 3
    assert alex_summary.required_evidence_found == 3
    assert alex_summary.preferred_total == 3
    assert alex_summary.preferred_evidence_found == 3

    priya_summary = compute_canonical_evidence_summary(priya_score.requirement_contributions, "HIGH")
    assert priya_summary.required_total == 3
    assert priya_summary.required_evidence_found == 3
    assert priya_summary.preferred_total == 3
    assert priya_summary.preferred_evidence_found == 1

    daniel_summary = compute_canonical_evidence_summary(daniel_score.requirement_contributions, "LOW")
    assert daniel_summary.required_total == 3
    assert daniel_summary.required_evidence_found == 0
    assert daniel_summary.preferred_total == 3
    assert daniel_summary.preferred_evidence_found == 1


def test_controlled_gap_reports_persistence_invariants_and_idempotency():
    """Verify exactly 3 match results, 18 requirement results, 3 gap reports,

    18 gap records, summary invariants, and idempotent repeated creation.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.auth import Base, UserRecord, UserRole
    from app.models.job import JobRequisition, JobVersion, JobRequirement
    from app.models.resume import ResumeArtifact, ArtifactFormat, ArtifactValidationStatus
    from app.models.candidate import CandidateProfileRecord, CandidateIdentityRecord
    from app.models.matching import MatchRunRecord, MatchResultRecord, MatchRunLifecycleStatus
    from app.models.gap_report import GapReportRecord, GapReportItemRecord
    from app.services.gap_report_service import create_gap_report_for_actor

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        # Seed Recruiter
        recruiter = UserRecord(
            user_id=uuid.uuid4(),
            email="recruiter.e2e@example.com",
            display_name="E2E Recruiter",
            password_hash="mock_hash",
            role=UserRole.RECRUITER,
            is_active=True,
        )
        db.add(recruiter)
        db.flush()

        # Seed Job & Version
        job = JobRequisition(
            job_id=uuid.uuid4(),
            created_by_user_id=recruiter.user_id,
            status=JobLifecycleStatus.ACTIVE,
        )
        db.add(job)
        db.flush()

        ver = JobVersion(
            job_version_id=uuid.uuid4(),
            job_id=job.job_id,
            version_number=1,
            revision=1,
            version_status=JobVersionStatus.READY_FOR_MATCHING,
            title="Junior Python API Developer",
            created_by_user_id=recruiter.user_id,
        )
        db.add(ver)
        db.flush()

        req_names = [
            ("Python", RequirementNecessity.MUST_HAVE, RequirementPriority.CRITICAL),
            ("REST API development", RequirementNecessity.MUST_HAVE, RequirementPriority.HIGH),
            ("PostgreSQL", RequirementNecessity.MUST_HAVE, RequirementPriority.HIGH),
            ("FastAPI", RequirementNecessity.PREFERRED, RequirementPriority.MEDIUM),
            ("Git", RequirementNecessity.PREFERRED, RequirementPriority.MEDIUM),
            ("Docker", RequirementNecessity.PREFERRED, RequirementPriority.LOW),
        ]
        job_reqs = []
        for idx, (name, nec, pri) in enumerate(req_names):
            r = JobRequirement(
                requirement_id=uuid.uuid4(),
                job_version_id=ver.job_version_id,
                display_order=idx,
                category=RequirementCategory.SKILL,
                necessity=nec,
                priority=pri,
                origin=RequirementOrigin.RECRUITER_ENTERED,
                label=name,
                normalized_label=name.lower(),
                rationale=f"Rationale for {name}",
            )
            job_reqs.append(r)
        db.add_all(job_reqs)
        db.flush()

        # Seed 3 Candidates (Alex, Priya, Daniel)
        candidate_data = [
            ("Alex Morgan", [("Python", "MATCHED"), ("REST API development", "MATCHED"), ("PostgreSQL", "MATCHED"), ("FastAPI", "MATCHED"), ("Git", "MATCHED"), ("Docker", "MATCHED")], 95.0, 1),
            ("Priya Shah", [("Python", "MATCHED"), ("REST API development", "MATCHED"), ("PostgreSQL", "MATCHED"), ("FastAPI", "NOT_EVIDENCED"), ("Git", "MATCHED"), ("Docker", "NOT_EVIDENCED")], 80.0, 2),
            ("Daniel Lee", [("Python", "NOT_EVIDENCED"), ("REST API development", "NOT_EVIDENCED"), ("PostgreSQL", "NOT_EVIDENCED"), ("FastAPI", "NOT_EVIDENCED"), ("Git", "MATCHED"), ("Docker", "NOT_EVIDENCED")], 30.0, 3),
        ]

        # Seed Match Run
        run = MatchRunRecord(
            run_id=uuid.uuid4(),
            recruiter_id=recruiter.user_id,
            job_id=job.job_id,
            job_version_id=ver.job_version_id,
            revision=1,
            evaluation_date=datetime.now(timezone.utc),
            candidate_count=3,
            matcher_algorithm_version="1.0.0",
            matching_rule_version="1.0.0",
            scoring_policy_version="1.0.0",
            match_input_schema_version="1.0.0",
            input_fingerprint="fp-test",
            candidate_input_fingerprint="cfp-test",
            idempotency_key="idemp-e2e-1",
            lifecycle_status=MatchRunLifecycleStatus.SUCCEEDED,
        )
        db.add(run)
        db.flush()

        match_results = []
        for cand_name, req_states, score, rank in candidate_data:
            art = ResumeArtifact(
                artifact_id=uuid.uuid4(),
                original_filename=f"{cand_name.replace(' ', '_')}.pdf",
                detected_format=ArtifactFormat.pdf,
                validation_status=ArtifactValidationStatus.VALIDATED,
                sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                size_bytes=1000,
            )
            db.add(art)
            db.flush()

            ident = CandidateIdentityRecord(
                identity_id=uuid.uuid4(),
                artifact_id=art.artifact_id,
                full_name=cand_name,
                primary_email=f"{cand_name.lower().replace(' ', '.')}@example.com",
                identity_payload={"fullName": {"value": cand_name}},
            )
            db.add(ident)
            db.flush()

            prof = CandidateProfileRecord(
                profile_id=uuid.uuid4(),
                artifact_id=art.artifact_id,
                profile_version=1,
                extraction_id=uuid.uuid4(),
                identity_id=ident.identity_id,
                schema_version="1.0",
                parser_pipeline_version="1.0.0",
                provenance="PARSER_EXTRACTED",
                professional_profile={
                    "skills": [{"name": k} for k, s in req_states if s == "MATCHED"],
                },
            )
            db.add(prof)
            db.flush()

            # Build contributions
            contribs = []
            for idx, (r_obj, (r_name, r_state)) in enumerate(zip(job_reqs, req_states)):
                contribs.append({
                    "requirement_id": str(r_obj.requirement_id),
                    "requirement_name": r_name,
                    "display_order": idx,
                    "category": "SKILL",
                    "necessity": "MUST_HAVE" if r_obj.necessity == RequirementNecessity.MUST_HAVE else "PREFERRED",
                    "priority": r_obj.priority.value,
                    "match_state": r_state,
                    "match_value": 1.0 if r_state == "MATCHED" else 0.0,
                    "weighted_contribution": 16.6 if r_state == "MATCHED" else 0.0,
                    "evidence_references": [f"ev-{r_name}-1"] if r_state == "MATCHED" else [],
                    "comparison_method": "EXACT_NORMALIZED_LABEL",
                    "reason_codes": [],
                    "review_recommended": False,
                })

            res = MatchResultRecord(
                result_id=uuid.uuid4(),
                run_id=run.run_id,
                profile_id=prof.profile_id,
                profile_version=1,
                rank=rank,
                is_tied=False,
                raw_compatibility_score=Decimal(str(score)),
                display_compatibility_score=Decimal(str(score)),
                confidence_tier="HIGH" if score > 70 else "LOW",
                score_decomposition={"requirement_contributions": contribs},
            )
            db.add(res)
            match_results.append(res)

        db.commit()

        # Exactly 3 candidate match results
        assert len(match_results) == 3

        # Create Gap Report for each result
        created_reports = []
        for res in match_results:
            rep1, reused1 = create_gap_report_for_actor(
                match_result_id=res.result_id,
                actor=recruiter,
                idempotency_key=f"idemp-rep-{res.result_id}",
                db=db,
            )
            assert reused1 is False
            created_reports.append(rep1)

            # Idempotency check: Repeated request must reuse existing report
            rep2, reused2 = create_gap_report_for_actor(
                match_result_id=res.result_id,
                actor=recruiter,
                idempotency_key=f"idemp-rep-repeat-{res.result_id}",
                db=db,
            )
            assert reused2 is True
            assert rep2.report_id == rep1.report_id

        # Exactly 3 gap reports created
        assert len(created_reports) == 3

        # Exactly 18 corresponding gap records across the 3 reports
        total_items = db.query(GapReportItemRecord).count()
        assert total_items == 18

        # Summary invariants
        for rep in created_reports:
            assert rep.total_requirements == 6
            if "Alex" in str(rep.profile_id):
                assert rep.covered_count == 6
            assert rep.match_run_id == run.run_id

    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

