import io
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.models import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    Base,
    SecurityAuditEventRecord,
    SessionRevokeReason,
    UserRecord,
    UserRole,
    UserSessionRecord,
)
from app.schemas.auth import (
    AuthErrorDetail,
    BootstrapAdminInput,
    SecurityAuditEventResponse,
    UserResponse,
    UserSessionResponse,
)
from app.services.security import (
    benchmark_scrypt,
    canonicalize_email,
    hash_password,
    verify_password,
)
from app.cli.bootstrap_admin import bootstrap_administrator, run_bootstrap_cli


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# 1. All valid roles and rejection of invalid roles
def test_user_roles_valid_and_invalid(auth_db):
    for valid_role in [UserRole.ADMIN, UserRole.RECRUITER, UserRole.REVIEWER, UserRole.CANDIDATE]:
        user = UserRecord(
            user_id=uuid.uuid4(),
            email=f"{valid_role.value.lower()}@domain.test",
            display_name=f"{valid_role.value} User",
            password_hash="scrypt$dummy",
            role=valid_role,
        )
        auth_db.add(user)
        auth_db.commit()
        assert user.role == valid_role

    table = Base.metadata.tables["users"]
    role_col = table.c.role
    assert set([e.value for e in UserRole]) == {"ADMIN", "RECRUITER", "REVIEWER", "CANDIDATE"}
    assert role_col.type.name == "ck_users_role"


# 2. Safe email canonicalization
def test_safe_email_canonicalization():
    assert canonicalize_email("  Test.User+Filter@Domain.TEST  ") == "test.user+filter@domain.test"
    assert canonicalize_email("Admin@Sub.Domain.TEST") == "admin@sub.domain.test"
    # Preserves dots and tags
    assert canonicalize_email("john.doe+tag@example.test") == "john.doe+tag@example.test"

    with pytest.raises(ValueError):
        canonicalize_email("")
    with pytest.raises(ValueError):
        canonicalize_email("   ")
    with pytest.raises(ValueError):
        canonicalize_email("not-an-email")
    with pytest.raises(ValueError):
        canonicalize_email("user@")


# 3. Duplicate canonical emails rejection
def test_duplicate_canonical_email_rejection(auth_db):
    email_1 = canonicalize_email("  HR.Admin@Company.TEST ")
    user1 = UserRecord(
        user_id=uuid.uuid4(),
        email=email_1,
        display_name="HR Admin",
        password_hash="scrypt$dummy",
        role=UserRole.ADMIN,
    )
    auth_db.add(user1)
    auth_db.commit()

    email_2 = canonicalize_email("hr.admin@company.test")
    user2 = UserRecord(
        user_id=uuid.uuid4(),
        email=email_2,
        display_name="HR Admin Duplicate",
        password_hash="scrypt$dummy",
        role=UserRole.ADMIN,
    )
    auth_db.add(user2)
    with pytest.raises(IntegrityError):
        auth_db.commit()
    auth_db.rollback()


# 4. Display-name and field validation
def test_display_name_and_field_validation():
    valid = BootstrapAdminInput(
        email="admin@company.test",
        display_name="Lead Administrator",
        password="ValidPassword123!",
    )
    assert valid.display_name == "Lead Administrator"

    with pytest.raises(ValidationError):
        BootstrapAdminInput(
            email="admin@company.test",
            display_name="   ",
            password="ValidPassword123!",
        )

    with pytest.raises(ValidationError):
        BootstrapAdminInput(
            email="admin@company.test",
            display_name="Admin",
            password="short",
        )


# 5. Default active/must-change/failed-count state
def test_user_default_state(auth_db):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="recruiter@company.test",
        display_name="Recruiter One",
        password_hash="scrypt$dummy",
        role=UserRole.RECRUITER,
    )
    auth_db.add(user)
    auth_db.commit()
    auth_db.refresh(user)

    assert user.is_active is True
    assert user.must_change_password is False
    assert user.failed_login_count == 0
    assert user.locked_until is None
    assert user.last_login_at is None
    assert user.created_at is not None
    assert user.updated_at is not None


# 6. Non-negative failed_login_count constraint
def test_user_failed_login_count_constraint(auth_db):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="reviewer@company.test",
        display_name="Reviewer One",
        password_hash="scrypt$dummy",
        role=UserRole.REVIEWER,
        failed_login_count=-1,
    )
    auth_db.add(user)
    with pytest.raises(IntegrityError):
        auth_db.commit()
    auth_db.rollback()


# 7. Valid and invalid session timestamp relationships
def test_session_timestamp_relationships(auth_db):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="sess_user@company.test",
        display_name="Session User",
        password_hash="scrypt$dummy",
        role=UserRole.RECRUITER,
    )
    auth_db.add(user)
    auth_db.commit()

    now = datetime.now(timezone.utc)
    # Valid: idle <= absolute
    valid_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash="a" * 64,
        csrf_token_hash="b" * 64,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(valid_session)
    auth_db.commit()

    # Invalid: idle > absolute
    invalid_session = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash="c" * 64,
        csrf_token_hash="d" * 64,
        idle_expires_at=now + timedelta(hours=13),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(invalid_session)
    with pytest.raises(IntegrityError):
        auth_db.commit()
    auth_db.rollback()


# 8. Unique session token hashes
def test_session_token_hash_uniqueness(auth_db):
    user = UserRecord(
        user_id=uuid.uuid4(),
        email="sess_user2@company.test",
        display_name="Session User 2",
        password_hash="scrypt$dummy",
        role=UserRole.RECRUITER,
    )
    auth_db.add(user)
    auth_db.commit()

    now = datetime.now(timezone.utc)
    sess1 = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash="f" * 64,
        csrf_token_hash="1" * 64,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(sess1)
    auth_db.commit()

    sess2 = UserSessionRecord(
        session_id=uuid.uuid4(),
        user_id=user.user_id,
        token_hash="f" * 64,  # Duplicate
        csrf_token_hash="2" * 64,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=12),
    )
    auth_db.add(sess2)
    with pytest.raises(IntegrityError):
        auth_db.commit()
    auth_db.rollback()


# 9. Nullable audit actor for unknown attempts
def test_audit_event_nullable_actor(auth_db):
    audit = SecurityAuditEventRecord(
        event_id=uuid.uuid4(),
        actor_user_id=None,
        event_type=AuditEventType.LOGIN_ATTEMPT,
        result=AuditResult.FAILURE,
        reason_code=AuditReasonCode.CREDENTIALS_INVALID,
        safe_metadata={"attempted_email": "unknown@domain.test"},
    )
    auth_db.add(audit)
    auth_db.commit()
    auth_db.refresh(audit)

    assert audit.actor_user_id is None
    assert audit.event_type == AuditEventType.LOGIN_ATTEMPT
    assert audit.result == AuditResult.FAILURE


# 10. Invalid audit result/event/reason values
def test_audit_event_enum_constraints():
    table = Base.metadata.tables["security_audit_events"]
    assert table.c.event_type.type.name == "ck_security_audit_events_event_type"
    assert table.c.result.type.name == "ck_security_audit_events_result"
    assert table.c.reason_code.type.name == "ck_security_audit_events_reason_code"

    assert set([e.value for e in AuditEventType]) == {
        "LOGIN_ATTEMPT",
        "LOGOUT",
        "SESSION_REVOKED",
        "PASSWORD_CHANGED",
        "USER_BOOTSTRAPPED",
        "ACCOUNT_LOCKED",
        "ACCOUNT_UNLOCKED",
        "PROVIDER_PROFILE_CREATED",
        "PROVIDER_PROFILE_UPDATED",
        "PROVIDER_PROFILE_ACTIVATED",
        "PROVIDER_PROFILE_DISABLED",
        "PROVIDER_CONFIG_REJECTED",
        "PROVIDER_COMPATIBILITY_TESTED",
        "ACCESS_DENIED",
        "CANDIDATE_INVITATION_CREATED",
        "CANDIDATE_INVITATION_REVOKED",
        "CANDIDATE_ACTIVATED",
        "CANDIDATE_BINDING_UPDATED",
        "CANDIDATE_APPLICATION_SUBMITTED",
        "CANDIDATE_APPLICATION_WITHDRAWN",
        "CANDIDATE_REVIEW_STATE_UPDATED",
        "CANDIDATE_INTERVIEW_RELEASED",
        "CANDIDATE_REGISTERED",
    }
    assert set([e.value for e in AuditResult]) == {"SUCCESS", "FAILURE", "DENIED"}
    assert set([e.value for e in AuditReasonCode]) == {
        "CREDENTIALS_INVALID",
        "ACCOUNT_LOCKED",
        "SESSION_EXPIRED",
        "UNAUTHORIZED",
        "SYSTEM_BOOTSTRAP",
        "SUCCESSFUL_AUTHENTICATION",
        "PASSWORD_CHANGE_REQUIRED",
        "PROVIDER_CONFIGURED",
        "PROVIDER_UPDATED",
        "PROVIDER_ACTIVATED",
        "PROVIDER_DISABLED",
        "PROVIDER_INVALID_CONFIG",
        "PROVIDER_INCOMPATIBLE",
        "PROVIDER_COMPATIBILITY_SUCCESS",
        "PROVIDER_COMPATIBILITY_FAILED",
        "ACCESS_DENIED",
        "STALE_VERSION",
        "MASS_ASSIGNMENT_ATTEMPT",
        "INVITATION_EXPIRED",
        "INVITATION_REVOKED",
        "INVITATION_CONSUMED",
        "INVITATION_INVALID",
        "BINDING_SUSPENDED",
        "INVITATION_CREATED",
        "INVITATION_REVOKED_BY_RECRUITER",
        "CANDIDATE_ACCOUNT_ACTIVATED",
        "CANDIDATE_ACCOUNT_REGISTERED",
        "APPLICATION_SUBMITTED",
        "APPLICATION_WITHDRAWN",
        "DUPLICATE_APPLICATION",
        "APPLICATION_NOT_ELIGIBLE",
        "RESOURCE_NOT_FOUND",
        "REVIEW_STATE_CHANGED",
        "INTERVIEW_RELEASED",
    }


# 11. Schemas rejecting unexpected fields
def test_schemas_extra_fields_forbidden():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        UserResponse(
            userId=uuid.uuid4(),
            email="test@domain.test",
            displayName="Test",
            role=UserRole.ADMIN,
            isActive=True,
            mustChangePassword=False,
            createdAt=now,
            updatedAt=now,
            unexpectedField="illegal",  # type: ignore
        )

    with pytest.raises(ValidationError):
        UserSessionResponse(
            sessionId=uuid.uuid4(),
            userId=uuid.uuid4(),
            createdAt=now,
            lastSeenAt=now,
            idleExpiresAt=now,
            absoluteExpiresAt=now,
            isRevoked=False,
            extraField="illegal",  # type: ignore
        )


# 12. Response serialization remaining camelCase
def test_response_serialization_camel_case():
    uid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    user_resp = UserResponse(
        user_id=uid,
        email="test@domain.test",
        display_name="Test User",
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    dump = user_resp.model_dump(by_alias=True)
    assert "userId" in dump
    assert "displayName" in dump
    assert "isActive" in dump
    assert "mustChangePassword" in dump
    assert "createdAt" in dump
    assert "updatedAt" in dump
    assert "user_id" not in dump


# 13. Sensitive fields absent from safe serialized responses
def test_sensitive_fields_absent_from_schemas():
    now = datetime.now(timezone.utc)
    user_resp = UserResponse(
        user_id=uuid.uuid4(),
        email="test@domain.test",
        display_name="Test User",
        role=UserRole.ADMIN,
        is_active=True,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    dump_keys = set(user_resp.model_dump().keys())
    forbidden_keys = {
        "password",
        "password_hash",
        "token_hash",
        "csrf_token_hash",
        "failed_login_count",
    }
    assert dump_keys.isdisjoint(forbidden_keys)


# 14. Deterministic scrypt verification with random salts
def test_scrypt_deterministic_verification():
    password = "SyntheticSecretPassword123!"
    encoded_hash = hash_password(password, n=16384, r=8, p=1, dklen=32)
    assert encoded_hash.startswith("scrypt$v=1$n=16384$r=8$p=1$")
    assert verify_password(password, encoded_hash) is True


# 15. Two hashes of the same password being different
def test_two_hashes_of_same_password_differ():
    password = "SyntheticSecretPassword123!"
    hash1 = hash_password(password)
    hash2 = hash_password(password)
    assert hash1 != hash2
    assert verify_password(password, hash1) is True
    assert verify_password(password, hash2) is True


# 16. Wrong-password rejection
def test_wrong_password_rejected():
    password = "SyntheticSecretPassword123!"
    wrong_password = "WrongSyntheticSecretPassword456!"
    encoded_hash = hash_password(password)
    assert verify_password(wrong_password, encoded_hash) is False
    assert verify_password("", encoded_hash) is False
    assert verify_password(password, "corrupted_hash_value") is False


# 17. Bootstrap success
def test_bootstrap_administrator_success(auth_db):
    status = bootstrap_administrator(
        email="Initial.Admin@Organization.TEST",
        display_name="Initial Admin",
        password="SuperSecretAdminPassword123!",
        session=auth_db,
    )
    assert status == 0

    # Verify user record
    stmt = select(UserRecord).where(UserRecord.email == "initial.admin@organization.test")
    admin = auth_db.execute(stmt).scalar_one_or_none()
    assert admin is not None
    assert admin.display_name == "Initial Admin"
    assert admin.role == UserRole.ADMIN
    assert admin.is_active is True
    assert admin.must_change_password is True
    assert admin.failed_login_count == 0
    assert verify_password("SuperSecretAdminPassword123!", admin.password_hash) is True

    # Verify audit event
    stmt_audit = select(SecurityAuditEventRecord).where(
        SecurityAuditEventRecord.actor_user_id == admin.user_id
    )
    audit = auth_db.execute(stmt_audit).scalar_one_or_none()
    assert audit is not None
    assert audit.event_type == AuditEventType.USER_BOOTSTRAPPED
    assert audit.result == AuditResult.SUCCESS
    assert audit.reason_code == AuditReasonCode.SYSTEM_BOOTSTRAP
    assert audit.safe_metadata["email"] == "initial.admin@organization.test"


# 18. Duplicate bootstrap rejection
def test_duplicate_bootstrap_rejection(auth_db):
    status1 = bootstrap_administrator(
        email="admin.duplicate@org.test",
        display_name="Admin 1",
        password="SuperSecretPassword123!",
        session=auth_db,
    )
    assert status1 == 0

    # Second bootstrap attempt with different casing / spaces
    status2 = bootstrap_administrator(
        email="  Admin.Duplicate@ORG.TEST  ",
        display_name="Admin Duplicate Attempt",
        password="AnotherPassword123!",
        session=auth_db,
    )
    assert status2 == 1


# 19. Bootstrap rollback on failure
def test_bootstrap_rollback_on_failure(auth_db, monkeypatch):
    import app.cli.bootstrap_admin as b_admin

    # Intentionally cause an error during hashing/commit
    def faulty_hash(*args, **kwargs):
        raise RuntimeError("Synthetic simulated hashing crash")

    monkeypatch.setattr(b_admin.security, "hash_password", faulty_hash)

    status = bootstrap_administrator(
        email="crash.admin@org.test",
        display_name="Crash Admin",
        password="SecretPassword123!",
        session=auth_db,
    )
    assert status == 1

    stmt = select(UserRecord).where(UserRecord.email == "crash.admin@org.test")
    assert auth_db.execute(stmt).scalar_one_or_none() is None


# 20. Password/hash absence from captured stdout, stderr, and logs
def test_password_and_hash_absence_from_outputs(auth_db, capsys):
    secret_pass = "TopSecretSynthetic12345!"
    status = bootstrap_administrator(
        email="silent.admin@org.test",
        display_name="Silent Admin",
        password=secret_pass,
        session=auth_db,
    )
    assert status == 0
    captured = capsys.readouterr()

    stmt = select(UserRecord).where(UserRecord.email == "silent.admin@org.test")
    admin = auth_db.execute(stmt).scalar_one()

    # Verify secret password and raw hash are NOT in stdout or stderr
    assert secret_pass not in captured.out
    assert secret_pass not in captured.err
    assert admin.password_hash not in captured.out
    assert admin.password_hash not in captured.err


# 21. Interactive password confirmation matching and mismatch testing
def test_interactive_bootstrap_cli_flow(auth_db, monkeypatch, capsys):
    import app.cli.bootstrap_admin as b_admin

    # Test password mismatch
    prompts = ["Password123!", "MismatchPassword123!"]
    monkeypatch.setattr("getpass.getpass", lambda prompt="": prompts.pop(0))
    status_mismatch = b_admin.run_bootstrap_cli(["--email", "cli.mismatch@org.test", "--display-name", "CLI Admin"])
    assert status_mismatch == 1
    captured_mismatch = capsys.readouterr()
    assert "Passwords do not match" in captured_mismatch.err

    # Test successful interactive confirmation
    prompts_success = ["ValidPassword123!", "ValidPassword123!"]
    monkeypatch.setattr("getpass.getpass", lambda prompt="": prompts_success.pop(0))
    monkeypatch.setattr(b_admin, "SessionLocal", lambda: auth_db)
    status_ok = b_admin.run_bootstrap_cli(["--email", "cli.success@org.test", "--display-name", "CLI Admin Success"])
    assert status_ok == 0
    captured_ok = capsys.readouterr()
    assert "Successfully bootstrapped Administrator account" in captured_ok.out
    assert "ValidPassword123!" not in captured_ok.out
    assert "ValidPassword123!" not in captured_ok.err


# 21. Migration upgrade from Milestone 1 and isolated downgrade
def test_migration_upgrade_and_downgrade_isolated(capsys):
    from alembic.config import Config
    from alembic import command
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://dummy:dummy@localhost:5432/dummy_isolated_test")

    # Verify offline upgrade SQL generation from d92e10a8b71f to e1a05c742301
    command.upgrade(alembic_cfg, "d92e10a8b71f:e1a05c742301", sql=True)
    captured_upgrade = capsys.readouterr()
    assert "CREATE TABLE users" in captured_upgrade.out
    assert "CREATE TABLE user_sessions" in captured_upgrade.out
    assert "CREATE TABLE security_audit_events" in captured_upgrade.out
    assert "ck_users_role" in captured_upgrade.out
    assert "ck_user_sessions_idle_lte_absolute_expiry" in captured_upgrade.out

    # Verify offline downgrade SQL generation from e1a05c742301 back to d92e10a8b71f
    command.downgrade(alembic_cfg, "e1a05c742301:d92e10a8b71f", sql=True)
    captured_downgrade = capsys.readouterr()
    assert "DROP TABLE security_audit_events" in captured_downgrade.out
    assert "DROP TABLE user_sessions" in captured_downgrade.out
    assert "DROP TABLE users" in captured_downgrade.out


# 22. Preservation of existing Milestone 1 schema
def test_milestone_1_schema_preservation():
    table_names = set(Base.metadata.tables.keys())
    m1_tables = {
        "resume_artifacts",
        "processing_jobs",
        "resume_extractions",
        "extraction_blocks",
        "candidate_identities",
        "candidate_profiles",
        "profile_corrections",
    }
    assert m1_tables.issubset(table_names)
    assert {"users", "user_sessions", "security_audit_events"}.issubset(table_names)


# Additional settings validation tests
def test_settings_validation():
    # Wildcard origin rejected
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql+psycopg://dummy", frontend_origin="*")

    # Idle > Absolute rejected
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://dummy",
            session_idle_lifetime_seconds=5000,
            session_absolute_lifetime_seconds=1000,
        )

    # Invalid SameSite rejected
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://dummy",
            session_cookie_samesite="invalid_samesite",
        )
