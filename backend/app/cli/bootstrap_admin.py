import argparse
import getpass
import os
import sys
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models.auth import (
    AuditEventType,
    AuditReasonCode,
    AuditResult,
    SecurityAuditEventRecord,
    UserRecord,
    UserRole,
)
from app.schemas.auth import BootstrapAdminInput
from app.services import security
from app.services.security import canonicalize_email


def bootstrap_administrator(
    email: str,
    display_name: str,
    password: Optional[str] = None,
    session: Optional[Session] = None,
) -> int:
    """
    Creates the first Administrator account atomically.

    Rules:
    - Canonicalizes email
    - Hashes password using versioned scrypt
    - Assigns role=ADMIN, is_active=True, must_change_password=True
    - Logs a SecurityAuditEventRecord
    - Rejects duplicate email safely
    - Rolls back on error
    - Never logs or prints password/hash
    - Returns 0 on success, 1 on failure
    """
    settings = get_settings()

    # If password is not passed directly, check environment variable or prompt interactively
    if not password:
        env_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
        if env_password:
            password = env_password
        else:
            try:
                password = getpass.getpass("Enter initial Administrator password: ")
                confirm = getpass.getpass("Confirm Administrator password: ")
                if password != confirm:
                    sys.stderr.write("Error: Passwords do not match.\n")
                    return 1
            except (EOFError, KeyboardInterrupt):
                sys.stderr.write("\nOperation cancelled.\n")
                return 1

    # Validate input schema
    try:
        validated_input = BootstrapAdminInput(
            email=email,
            display_name=display_name,
            password=password,
        )
    except Exception as e:
        sys.stderr.write(f"Validation Error: {str(e)}\n")
        return 1

    owns_session = False
    if session is None:
        session = SessionLocal()
        owns_session = True

    try:
        # Check for existing canonical email
        stmt = select(UserRecord).where(UserRecord.email == validated_input.email)
        existing_user = session.execute(stmt).scalar_one_or_none()
        if existing_user is not None:
            sys.stderr.write(
                f"Bootstrap Error: A user account with email '{validated_input.email}' already exists.\n"
            )
            return 1

        # Hash password via versioned scrypt
        encoded_hash = security.hash_password(
            plaintext_password=validated_input.password,
            n=settings.scrypt_n,
            r=settings.scrypt_r,
            p=settings.scrypt_p,
            dklen=settings.scrypt_dklen,
        )

        admin_user = UserRecord(
            user_id=uuid.uuid4(),
            email=validated_input.email,
            display_name=validated_input.display_name,
            password_hash=encoded_hash,
            role=UserRole.ADMIN,
            is_active=True,
            must_change_password=True,
            failed_login_count=0,
        )
        session.add(admin_user)

        audit_event = SecurityAuditEventRecord(
            event_id=uuid.uuid4(),
            actor_user_id=admin_user.user_id,
            event_type=AuditEventType.USER_BOOTSTRAPPED,
            resource_type="user",
            resource_id=str(admin_user.user_id),
            result=AuditResult.SUCCESS,
            reason_code=AuditReasonCode.SYSTEM_BOOTSTRAP,
            safe_metadata={
                "role": UserRole.ADMIN.value,
                "email": validated_input.email,
            },
        )
        session.add(audit_event)

        session.commit()
        # Safe success output
        sys.stdout.write(
            f"Successfully bootstrapped Administrator account for '{validated_input.email}' (User ID: {admin_user.user_id}).\n"
        )
        return 0

    except Exception as e:
        session.rollback()
        sys.stderr.write(f"Bootstrap Failed: An error occurred during account creation: {type(e).__name__}\n")
        return 1
    finally:
        if owns_session:
            session.close()


def run_bootstrap_cli(args=None) -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap the initial Administrator user for the AI-Driven Smart Hiring Platform."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Administrator canonical email address.",
    )
    parser.add_argument(
        "--display-name",
        required=True,
        help="Administrator display name.",
    )

    parsed_args = parser.parse_args(args)
    return bootstrap_administrator(
        email=parsed_args.email,
        display_name=parsed_args.display_name,
    )


if __name__ == "__main__":
    sys.exit(run_bootstrap_cli())
