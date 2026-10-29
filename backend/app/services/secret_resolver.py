import os
from typing import Dict, Optional

from pydantic import SecretStr

from app.schemas.phase3_protocols import SecretResolver as SecretResolverProtocol


class SecretResolutionError(Exception):
    """Raised when a secret reference cannot be resolved or is invalid."""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class EnvironmentSecretResolver(SecretResolverProtocol):
    """Production implementation of SecretResolver for environment variables.
    
    Accepts only references in the controlled format: 'env:<VAR_NAME>'.
    Never stores or returns plaintext secrets in logs, representations, or serialization.
    """

    def resolve_secret_ref(self, secret_ref: Optional[str]) -> Optional[SecretStr]:
        """Resolves a controlled secret reference string to a Pydantic SecretStr."""
        if not secret_ref:
            return None

        cleaned = secret_ref.strip()
        if not cleaned.startswith("env:"):
            raise SecretResolutionError(
                code="INVALID_SECRET_REF_FORMAT",
                message="Secret reference must begin with 'env:' prefix.",
            )

        var_name = cleaned.split("env:", 1)[1].strip()
        if not var_name or not var_name.isidentifier():
            raise SecretResolutionError(
                code="INVALID_SECRET_VAR_NAME",
                message=f"Environment variable name '{var_name}' is invalid.",
            )

        val = os.environ.get(var_name)
        if not val or not val.strip():
            raise SecretResolutionError(
                code="SECRET_NOT_FOUND",
                message=f"Configured environment secret '{var_name}' is not set or is empty.",
            )

        return SecretStr(val.strip())

    def get_provider_secret(self, secret_ref: str) -> Optional[str]:
        """Protocol method returning plain secret value in short-lived local scope."""
        secret_obj = self.resolve_secret_ref(secret_ref)
        return secret_obj.get_secret_value() if secret_obj else None

    def get_ats_credential(self, connector_id: str) -> Optional[Dict[str, str]]:
        """Resolves ATS integration credentials from environment."""
        var_name = f"ATS_CREDENTIAL_{connector_id.upper()}"
        val = os.environ.get(var_name)
        if not val:
            return None
        return {"api_key": val.strip()}
