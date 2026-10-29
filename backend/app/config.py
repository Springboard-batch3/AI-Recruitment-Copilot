from functools import lru_cache
from pathlib import Path
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


class Settings(BaseSettings):
    database_url: str
    max_resume_upload_bytes: int = 10485760
    max_resume_pages: int = 5
    max_docx_expanded_bytes: int = 52428800
    max_docx_archive_entries: int = 1000
    max_docx_compression_ratio: int = 100
    resume_intake_directory: str = "backend/runtime/resume_intake"
    resume_quarantine_directory: str = "backend/runtime/quarantine"
    ats_local_drop_root: str = "local_ats_demo"
    min_pdf_direct_text_chars: int = 30
    max_pdf_extracted_chars: int = 500000
    max_docx_extracted_chars: int = 500000
    # Authentication and Session Configuration
    session_cookie_name: str = "ai_hiring_session"
    candidate_session_cookie_name: str = "ai_hiring_candidate_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_idle_lifetime_seconds: int = 1800
    session_absolute_lifetime_seconds: int = 43200
    login_failure_threshold: int = 5
    login_cooldown_seconds: int = 900
    frontend_origin: str = "http://localhost:5173"
    allowed_frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://admin.localhost:5173,http://recruiter.localhost:5173,http://candidate.localhost:5173,http://localhost:3000,http://127.0.0.1:3000"
    scrypt_n: int = 16384
    scrypt_r: int = 8
    scrypt_p: int = 1
    scrypt_dklen: int = 32

    # Model Provider & LLM Configuration (Disabled by default)
    llm_enabled: bool = False
    llm_provider: str = "disabled"
    llm_profile_name: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_mode: str = "chat_completions"
    llm_api_key_secret_ref: str = "env:LLM_API_KEY"
    llm_allow_remote: bool = False
    llm_remote_allowed_hosts: str = ""
    llm_connect_timeout_seconds: float = 5.0
    llm_request_timeout_seconds: float = 30.0
    llm_max_input_tokens: int = 4096
    llm_max_output_tokens: int = 600
    llm_max_response_bytes: int = 262144
    llm_max_concurrency: int = 1
    llm_retry_limit: int = 1
    # Local Speech Processing Configuration (Phase 4D.3)
    audio_mock_mode: bool = True
    whisper_cpp_bin: str = ""
    whisper_cpp_model: str = ""
    piper_bin: str = ""
    piper_model: str = ""
    ffmpeg_bin: str = ""
    audio_max_seconds: int = 120
    audio_max_upload_bytes: int = 10485760
    audio_processing_timeout_seconds: float = 30.0
    audio_tts_cache_dir: str = "backend/runtime/audio_tts_cache"
    audio_ephemeral_temp_dir: str = "backend/runtime/audio_temp"

    # Streamlit Dashboard Configuration (Phase 4E.2)
    dashboard_token_secret: str = "dashboard-local-secure-token-secret-change-in-prod"
    dashboard_ticket_ttl_seconds: int = 120
    dashboard_session_ttl_minutes: int = 30
    dashboard_public_url: str = "http://localhost:8501"

    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        extra="ignore",
    )

    @field_validator("frontend_origin")
    @classmethod
    def validate_frontend_origin(cls, v: str) -> str:
        origin = v.strip()
        if not origin:
            raise ValueError("frontend_origin cannot be empty.")
        if origin == "*":
            raise ValueError("Wildcard frontend_origin ('*') is not allowed when credentials/sessions are enabled.")
        return origin

    @field_validator("allowed_frontend_origins")
    @classmethod
    def validate_allowed_frontend_origins(cls, v: str) -> str:
        if not v or not v.strip():
            return ""
        origins = [o.strip() for o in v.split(",") if o.strip()]
        for origin in origins:
            if origin == "*":
                raise ValueError("Wildcard origins ('*') are not allowed when credentials/sessions are enabled.")
        return v

    @field_validator("session_cookie_samesite")
    @classmethod
    def validate_samesite(cls, v: str) -> str:
        val = v.strip().lower()
        if val not in {"lax", "strict", "none"}:
            raise ValueError("session_cookie_samesite must be one of 'lax', 'strict', or 'none'.")
        return val

    @field_validator(
        "session_idle_lifetime_seconds",
        "session_absolute_lifetime_seconds",
        "login_failure_threshold",
        "login_cooldown_seconds",
        "scrypt_n",
        "scrypt_r",
        "scrypt_p",
        "scrypt_dklen",
        "llm_max_input_tokens",
        "llm_max_output_tokens",
        "llm_max_response_bytes",
        "llm_max_concurrency",
    )
    @classmethod
    def validate_positive_ints(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Configuration values must be positive integers.")
        return v

    @field_validator("llm_retry_limit")
    @classmethod
    def validate_retry_limit(cls, v: int) -> int:
        if v < 0 or v > 3:
            raise ValueError("llm_retry_limit must be between 0 and 3.")
        return v

    @field_validator("llm_connect_timeout_seconds", "llm_request_timeout_seconds")
    @classmethod
    def validate_timeouts(cls, v: float) -> float:
        if v <= 0.0 or v > 300.0:
            raise ValueError("Timeouts must be positive floats between 0.1 and 300.0 seconds.")
        return v

    @model_validator(mode="after")
    def validate_session_expiry_lifetimes(self) -> "Settings":
        if self.session_idle_lifetime_seconds > self.session_absolute_lifetime_seconds:
            raise ValueError("session_idle_lifetime_seconds cannot exceed session_absolute_lifetime_seconds.")
        return self

    def get_allowed_frontend_origins(self) -> list[str]:
        origins = []
        if self.frontend_origin and self.frontend_origin.strip():
            origins.append(self.frontend_origin.strip().rstrip("/"))
        if self.allowed_frontend_origins:
            for item in self.allowed_frontend_origins.split(","):
                norm = item.strip().rstrip("/")
                if norm and norm not in origins:
                    origins.append(norm)
        return origins

    def get_remote_allowed_hosts_list(self) -> list[str]:
        if not self.llm_remote_allowed_hosts:
            return []
        return [h.strip().lower() for h in self.llm_remote_allowed_hosts.split(",") if h.strip()]

    def get_intake_path(self) -> Path:
        raw_path = Path(self.resume_intake_directory)
        if raw_path.is_absolute():
            return raw_path.resolve()
        return (REPO_ROOT / raw_path).resolve()

    def get_quarantine_path(self) -> Path:
        raw_path = Path(self.resume_quarantine_directory)
        if raw_path.is_absolute():
            return raw_path.resolve()
        return (REPO_ROOT / raw_path).resolve()

    def get_ats_local_drop_root(self) -> Path:
        raw_path = Path(self.ats_local_drop_root)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (REPO_ROOT / raw_path).resolve()
        return resolved

    def get_audio_tts_cache_path(self) -> Path:
        raw_path = Path(self.audio_tts_cache_dir)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (REPO_ROOT / raw_path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def get_audio_temp_path(self) -> Path:
        raw_path = Path(self.audio_ephemeral_temp_dir)
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            resolved = (REPO_ROOT / raw_path).resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved


@lru_cache
def get_settings() -> Settings:
    return Settings()

