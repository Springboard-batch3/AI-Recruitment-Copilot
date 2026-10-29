import enum
from typing import Any


class InterviewMode(str, enum.Enum):
    """Execution and delivery mode for an AI question set and interview."""
    TEXT = "TEXT"
    AUDIO = "AUDIO"


class InterviewLayer(str, enum.Enum):
    """Structural layer of an interview question."""
    COMMON_CORE = "COMMON_CORE"
    SUPPLEMENTAL_EVIDENCE = "SUPPLEMENTAL_EVIDENCE"


class QuestionType(str, enum.Enum):
    """Categorical question taxonomy."""
    BEHAVIORAL = "BEHAVIORAL"
    SITUATIONAL = "SITUATIONAL"
    TECHNICAL = "TECHNICAL"
    EVIDENCE_CLARIFICATION = "EVIDENCE_CLARIFICATION"


class QuestionFormat(str, enum.Enum):
    """Format discriminator for interview questions."""
    CONVERSATION = "CONVERSATION"
    MCQ_SINGLE_ANSWER = "MCQ_SINGLE_ANSWER"


class QuestionDifficulty(str, enum.Enum):
    """Controlled difficulty level for practice questions."""
    FOUNDATIONAL = "FOUNDATIONAL"
    INTERMEDIATE = "INTERMEDIATE"
    ADVANCED = "ADVANCED"


class PracticeReleaseStatus(str, enum.Enum):
    """State of practice assignment released to a candidate."""
    RELEASED = "RELEASED"
    REVOKED = "REVOKED"


class AuthoringOrigin(str, enum.Enum):
    """Provenance of the question text or rubric."""
    # Canonical Phase 3G.1 Origins
    LOCAL_LLM_GENERATED = "LOCAL_LLM_GENERATED"
    LOCAL_LLM_GENERATED_RECRUITER_EDITED = "LOCAL_LLM_GENERATED_RECRUITER_EDITED"
    LEGACY_DETERMINISTIC = "LEGACY_DETERMINISTIC"
    LEGACY_MANUAL = "LEGACY_MANUAL"

    # Historic / legacy compatibility aliases
    DETERMINISTIC = "DETERMINISTIC"
    AI_ASSISTED = "AI_ASSISTED"
    AI_GENERATED = "AI_GENERATED"
    RECRUITER_EDITED = "RECRUITER_EDITED"
    RECRUITER_AUTHORED = "RECRUITER_AUTHORED"

    @classmethod
    def is_eligible_for_release(cls, origin: Any) -> bool:
        """Evaluates whether an authoring origin is eligible for Phase 3G practice release."""
        val = origin.value if hasattr(origin, "value") else str(origin or "")
        return val in (cls.LOCAL_LLM_GENERATED.value, cls.LOCAL_LLM_GENERATED_RECRUITER_EDITED.value)

    @classmethod
    def normalize_to_canonical(cls, origin: Any) -> "AuthoringOrigin":
        """Maps historic/legacy origins to canonical equivalents."""
        val = origin.value if hasattr(origin, "value") else str(origin or "")
        if val in (cls.LOCAL_LLM_GENERATED.value, cls.AI_GENERATED.value):
            return cls.LOCAL_LLM_GENERATED
        if val in (cls.LOCAL_LLM_GENERATED_RECRUITER_EDITED.value, cls.RECRUITER_EDITED.value):
            return cls.LOCAL_LLM_GENERATED_RECRUITER_EDITED
        if val in (cls.LEGACY_MANUAL.value, cls.RECRUITER_AUTHORED.value):
            return cls.LEGACY_MANUAL
        return cls.LEGACY_DETERMINISTIC


class ReleaseIneligibilityReasonCode(str, enum.Enum):
    """Authoritative domain reason codes for practice release ineligibility."""
    NOT_APPROVED = "NOT_APPROVED"
    STALE_JOB_REVISION = "STALE_JOB_REVISION"
    WRONG_SET_VERSION = "WRONG_SET_VERSION"
    LEGACY_DETERMINISTIC_PRESENT = "LEGACY_DETERMINISTIC_PRESENT"
    LEGACY_MANUAL_PRESENT = "LEGACY_MANUAL_PRESENT"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    GENERATION_FAILED = "GENERATION_FAILED"
    EMPTY_SET = "EMPTY_SET"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_DISABLED = "PROVIDER_DISABLED"



class ValidationState(str, enum.Enum):
    """Quality and safety validation state for interview items."""
    PENDING = "PENDING"
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class PlanLifecycle(str, enum.Enum):
    """Lifecycle states for interview plans/blueprints."""
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    IN_USE = "IN_USE"
    SUPERSEDED = "SUPERSEDED"


class SimulationLifecycle(str, enum.Enum):
    """Lifecycle states for candidate simulation sessions."""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    PROVISIONALLY_ASSESSED = "PROVISIONALLY_ASSESSED"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"
    FINALIZED = "FINALIZED"


class ATSExportLifecycle(str, enum.Enum):
    """Lifecycle states for ATS export jobs."""
    DRAFT = "DRAFT"
    PREVIEWED = "PREVIEWED"
    CONFIRMED = "CONFIRMED"
    SENDING = "SENDING"
    SENT = "SENT"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ProviderType(str, enum.Enum):
    """Supported model provider adapter types."""
    DISABLED = "DISABLED"
    LOCAL_OPENAI_COMPATIBLE = "LOCAL_OPENAI_COMPATIBLE"
    REMOTE_OPENAI_COMPATIBLE = "REMOTE_OPENAI_COMPATIBLE"
    CUSTOM_ADAPTER = "CUSTOM_ADAPTER"


class ProviderApiMode(str, enum.Enum):
    """API mode supported by the provider endpoint."""
    CHAT_COMPLETIONS = "CHAT_COMPLETIONS"
    RESPONSES = "RESPONSES"
    CUSTOM = "CUSTOM"


class ProviderEgressClass(str, enum.Enum):
    """Network egress scope and isolation classification."""
    LOCAL_ONLY = "LOCAL_ONLY"
    APPROVED_REMOTE = "APPROVED_REMOTE"


class ProviderCompatibilityStatus(str, enum.Enum):
    """Health and compatibility test status of a provider profile."""
    NOT_TESTED = "NOT_TESTED"
    HEALTHY = "HEALTHY"
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    DISABLED = "DISABLED"


class PrivacyReviewStatus(str, enum.Enum):
    """Data protection and privacy review clearance state."""
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PracticeSessionLifecycle(str, enum.Enum):
    """Lifecycle states for candidate practice interview sessions."""
    IN_PROGRESS = "IN_PROGRESS"
    QUESTION_GENERATING = "QUESTION_GENERATING"
    AWAITING_ANSWER = "AWAITING_ANSWER"
    ANSWER_SAVING = "ANSWER_SAVING"
    QUESTION_GENERATION_FAILED_RETRYABLE = "QUESTION_GENERATION_FAILED_RETRYABLE"
    FINAL_ANSWER_SAVING = "FINAL_ANSWER_SAVING"
    FINAL_REVIEW_GENERATING = "FINAL_REVIEW_GENERATING"
    FINAL_REVIEW_FAILED_RETRYABLE = "FINAL_REVIEW_FAILED_RETRYABLE"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class PracticeSection(str, enum.Enum):
    """Current section within an active candidate practice session."""
    CONVERSATION = "CONVERSATION"
    MCQ = "MCQ"
    COMPLETE = "COMPLETE"


class PracticeMessageRole(str, enum.Enum):
    """Author role for practice conversation messages."""
    INTERVIEWER = "INTERVIEWER"
    CANDIDATE = "CANDIDATE"


class PracticeMessageKind(str, enum.Enum):
    """Semantic category of conversation turn messages."""
    GREETING = "GREETING"
    PRIMARY_QUESTION = "PRIMARY_QUESTION"
    ANSWER = "ANSWER"
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    FOLLOW_UP = "FOLLOW_UP"


class ProviderOperation(str, enum.Enum):
    """Strictly allowlisted model capability operations.
    
    Prohibits generic operations (e.g., CHAT, RUN_PROMPT, COMPLETE_TEXT).
    """
    REWORD_QUESTION = "REWORD_QUESTION"
    CREATE_BOUNDED_VARIANTS = "CREATE_BOUNDED_VARIANTS"
    STRUCTURE_PROVISIONAL_ASSESSMENT = "STRUCTURE_PROVISIONAL_ASSESSMENT"
    SUMMARIZE_SELECTED_EVIDENCE = "SUMMARIZE_SELECTED_EVIDENCE"
    PRACTICE_CONVERSATION_FOLLOW_UP = "PRACTICE_CONVERSATION_FOLLOW_UP"
    GENERATE_LIVE_INTERVIEW_QUESTION = "GENERATE_LIVE_INTERVIEW_QUESTION"
    GENERATE_FINAL_INTERVIEW_REVIEW = "GENERATE_FINAL_INTERVIEW_REVIEW"


class AuditRetentionClass(str, enum.Enum):
    """Data retention and governance classifications for Milestone 3 audit records."""
    IMMUTABLE_HIRING_EVIDENCE = "IMMUTABLE_HIRING_EVIDENCE"
    DRAFT_CONTENT = "DRAFT_CONTENT"
    MODEL_PROVENANCE = "MODEL_PROVENANCE"
    SIMULATION_CONTENT = "SIMULATION_CONTENT"
    INTEGRATION_AUDIT = "INTEGRATION_AUDIT"
    OPERATIONAL_TRACE = "OPERATIONAL_TRACE"


class ValidationErrorCode(str, enum.Enum):
    """Machine-readable validation and policy failure codes."""
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    UNSUPPORTED_REFERENCE = "UNSUPPORTED_REFERENCE"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    PROHIBITED_TOPIC = "PROHIBITED_TOPIC"
    PROTECTED_ATTRIBUTE = "PROTECTED_ATTRIBUTE"
    INTENT_CHANGED = "INTENT_CHANGED"
    DUPLICATE_OUTPUT = "DUPLICATE_OUTPUT"
    UNSAFE_CONTENT = "UNSAFE_CONTENT"
    STALE_SOURCE_VERSION = "STALE_SOURCE_VERSION"

    # Phase 3C.3 Typed Semantic Validation Codes
    RESPONSE_INTENT_CHANGED = "RESPONSE_INTENT_CHANGED"
    RESPONSE_COMPETENCY_CHANGED = "RESPONSE_COMPETENCY_CHANGED"
    RESPONSE_MULTIPLE_QUESTIONS = "RESPONSE_MULTIPLE_QUESTIONS"
    RESPONSE_UNSUPPORTED_CLAIM = "RESPONSE_UNSUPPORTED_CLAIM"
    RESPONSE_PROHIBITED_TOPIC = "RESPONSE_PROHIBITED_TOPIC"
    RESPONSE_RECOMMENDATION_BLOCKED = "RESPONSE_RECOMMENDATION_BLOCKED"
    RESPONSE_RUBRIC_LEAKAGE = "RESPONSE_RUBRIC_LEAKAGE"
    RESPONSE_REFERENCE_INVALID = "RESPONSE_REFERENCE_INVALID"
    RESPONSE_DUPLICATE = "RESPONSE_DUPLICATE"
    RESPONSE_UNSAFE_CONTENT = "RESPONSE_UNSAFE_CONTENT"
    RESPONSE_LENGTH_INVALID = "RESPONSE_LENGTH_INVALID"
    RESPONSE_VALIDATION_INCONCLUSIVE = "RESPONSE_VALIDATION_INCONCLUSIVE"


class ProviderErrorCode(str, enum.Enum):
    """Machine-readable provider failure codes."""
    PROVIDER_DISABLED = "PROVIDER_DISABLED"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
    PROVIDER_POLICY_REJECTED = "PROVIDER_POLICY_REJECTED"
    PROVIDER_RESPONSE_TOO_LARGE = "PROVIDER_RESPONSE_TOO_LARGE"
    PROFILE_INCOMPATIBLE = "PROFILE_INCOMPATIBLE"
    DESTINATION_DENIED = "DESTINATION_DENIED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    # Phase 3C Response Classification and Mode Codes
    RESPONSE_CONTENT_EMPTY = "RESPONSE_CONTENT_EMPTY"
    RESPONSE_TRUNCATED = "RESPONSE_TRUNCATED"
    RESPONSE_SCHEMA_INVALID = "RESPONSE_SCHEMA_INVALID"
    PROFILE_MODE_INCOMPATIBLE = "PROFILE_MODE_INCOMPATIBLE"

