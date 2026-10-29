from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.schemas.ats_dto import (
    ATSApplicationReferenceDTO,
    ATSAssessmentNotePreviewDTO,
    ATSCandidateReferenceDTO,
    ATSHealthResultDTO,
    ATSJobReferenceDTO,
    ATSPaginatedResultDTO,
    ATSSyncCheckpointDTO,
    ATSWriteResultDTO,
)
from app.schemas.interview_blueprint import (
    BlueprintInputSnapshot,
    InterviewBlueprint,
    InterviewQuestion,
    InterviewRubric,
)
from app.schemas.provider_capability import (
    ProviderCapabilityRequest,
    ProviderCapabilityResponse,
)
from app.schemas.simulation_assessment import (
    CandidateSimulationSession,
    FinalRubricEvaluation,
    ProvisionalRubricEvaluation,
    QuestionResponseRecord,
)


class InterviewBlueprintBuilder(ABC):
    """Abstract interface for constructing deterministic interview blueprints."""
    @abstractmethod
    def build_blueprint(
        self,
        snapshot: BlueprintInputSnapshot,
        db_session: Any,
    ) -> InterviewBlueprint:
        """Assembles a structured interview blueprint from immutable snapshot records."""
        pass


class InterviewPolicyValidator(ABC):
    """Abstract interface for validating interview blueprints against safety policy."""
    @abstractmethod
    def validate_blueprint(
        self,
        blueprint: InterviewBlueprint,
    ) -> list[str]:
        """Validates blueprint against safety policy and returns any violation descriptions."""
        pass


class QuestionWordingProvider(ABC):
    """Abstract interface for generating or rewording question phrasing."""
    @abstractmethod
    async def reword_question(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        """Executes question rewording via capability provider or deterministic fallback."""
        pass


class DeterministicWordingProvider(ABC):
    """Abstract interface for pure deterministic question and rubric template generation."""
    @abstractmethod
    def generate_common_core_question(
        self,
        requirement_id: UUID,
        requirement_label: str,
        category: str,
    ) -> InterviewQuestion:
        """Generates a deterministic question from requirement metadata."""
        pass

    @abstractmethod
    def generate_supplemental_question(
        self,
        requirement_id: UUID,
        gap_state: str,
        recruiter_wording: str,
        source_report_id: UUID,
    ) -> InterviewQuestion:
        """Generates a deterministic supplemental question targeting an unverified/missing gap."""
        pass


class ModelGateway(ABC):
    """Abstract interface for executing provider-neutral model capabilities."""
    @abstractmethod
    async def execute_capability(
        self,
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        """Dispatches a strictly validated capability request to the configured backend provider."""
        pass


class ModelOutputValidator(ABC):
    """Abstract interface for validating and sanitizing model capability responses."""
    @abstractmethod
    def validate_output(
        self,
        raw_response: Dict[str, Any],
        request: ProviderCapabilityRequest,
    ) -> ProviderCapabilityResponse:
        """Validates schema, intent preservation, and topic safety of model responses."""
        pass


class AssessmentAssistant(ABC):
    """Abstract interface for structuring provisional rubric observations."""
    @abstractmethod
    async def generate_provisional_assessment(
        self,
        session: CandidateSimulationSession,
        question: InterviewQuestion,
        response_record: QuestionResponseRecord,
    ) -> ProvisionalRubricEvaluation:
        """Structures provisional rubric observations without altering scores or making decisions."""
        pass


class ATSAdapter(ABC):
    """Abstract interface for external ATS synchronization and export."""
    @abstractmethod
    async def check_health(self) -> ATSHealthResultDTO:
        """Checks connection health and authentication with target ATS."""
        pass

    @abstractmethod
    async def export_assessment_note(
        self,
        preview: ATSAssessmentNotePreviewDTO,
    ) -> ATSWriteResultDTO:
        """Transmits an approved recruiter note to the ATS application record."""
        pass


class SecretResolver(ABC):
    """Abstract interface for securely resolving provider or integration secrets."""
    @abstractmethod
    def get_provider_secret(self, provider_id: str) -> Optional[str]:
        """Resolves provider API key or bearer token from secure environment."""
        pass

    @abstractmethod
    def get_ats_credential(self, connector_id: str) -> Optional[Dict[str, str]]:
        """Resolves ATS integration credentials from secure environment."""
        pass


class DestinationPolicy(ABC):
    """Abstract interface for outbound URL validation and SSRF protection."""
    @abstractmethod
    def validate_destination_url(self, target_url: str) -> bool:
        """Validates outbound URL against strict protocol and domain whitelist."""
        pass
