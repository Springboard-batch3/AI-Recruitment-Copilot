from typing import Dict, List, Optional
from pydantic import Field

from app.schemas.matching import FrozenSchema
from app.schemas.phase3_enums import QuestionType

QUESTION_CATALOGUE_VERSION: str = "1.0.0"
RUBRIC_CATALOGUE_VERSION: str = "1.0.0"


class QuestionTemplateEntry(FrozenSchema):
    """Immutable curated catalogue question template entry."""

    template_id: str = Field(min_length=3, max_length=100)
    version: str = Field(default=QUESTION_CATALOGUE_VERSION)
    competency_key: str = Field(min_length=2, max_length=100)
    question_type: QuestionType
    text_template: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    estimated_duration_minutes: int = Field(default=5, ge=1, le=30)
    allowed_probes: List[str] = Field(default_factory=list, max_length=2)
    linked_rubric_template_id: str = Field(min_length=3, max_length=100)
    linked_rubric_version: str = Field(default=RUBRIC_CATALOGUE_VERSION)
    is_active: bool = True
    catalogue_order: int = Field(default=0, ge=0)


class RubricTemplateEntry(FrozenSchema):
    """Immutable curated catalogue rubric template entry with anchored 1-5 scale."""

    rubric_template_id: str = Field(min_length=3, max_length=100)
    version: str = Field(default=RUBRIC_CATALOGUE_VERSION)
    competency_definition: str = Field(min_length=5, max_length=1000)
    question_intent: str = Field(min_length=5, max_length=1000)
    observable_indicators: List[str] = Field(min_length=1)
    rating_anchors: Dict[int, str] = Field(
        description="Rating anchors for evaluation scale points (must include at least 1, 3, and 5)."
    )
    sufficient_evidence_guidance: str = Field(min_length=5, max_length=2000)
    insufficient_evidence_guidance: str = Field(min_length=5, max_length=2000)
    incomplete_answer_guidance: Optional[str] = Field(default=None, max_length=2000)
    prohibited_inferences: List[str] = Field(default_factory=list)
    subject_matter_review_status: str = Field(default="REVIEWED", max_length=50)
    is_active: bool = True


# =========================================================================
# Curated Standard Rubric Catalogues
# =========================================================================

CURATED_RUBRIC_CATALOGUE: Dict[str, RubricTemplateEntry] = {
    "RUB-POSTGRESQL-TUNING-01": RubricTemplateEntry(
        rubric_template_id="RUB-POSTGRESQL-TUNING-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Demonstrated ability to diagnose, profile, and optimize relational database query execution plans, indexing strategies, and locking overhead in PostgreSQL.",
        question_intent="Evaluate practical expertise with PostgreSQL query optimization tools (EXPLAIN ANALYZE, vacuuming, indexing patterns) and systematic bottleneck diagnosis.",
        observable_indicators=[
            "Explains use of EXPLAIN / EXPLAIN (ANALYZE, BUFFERS) to isolate sequential scans and buffer bottlenecks.",
            "Discusses specific indexing strategies (e.g., B-Tree, partial, composite, BRIN, GIN) and their maintenance overhead.",
            "Considers concurrency issues including table/row locks, deadlocks, and connection pooling.",
            "Articulates measurable before/after performance improvements or regression analysis.",
        ],
        rating_anchors={
            1: "Mentions generic tuning advice (e.g., 'add an index') without articulating EXPLAIN plan inspection, buffer analysis, or lock evaluation.",
            3: "Accurately describes using EXPLAIN ANALYZE to identify slow query execution steps and discusses appropriate indexing or configuration parameter adjustments.",
            5: "Demonstrates deep mastery of execution plans, memory buffers (shared_buffers, work_mem), vacuum tuning, locking mechanisms, and measurable latency reduction in high-concurrency production environments.",
        },
        sufficient_evidence_guidance="Candidate cites concrete diagnostic steps using query plans, explains index/query trade-offs, and validates performance with measurable latency or throughput metrics.",
        insufficient_evidence_guidance="Candidate provides theoretical or vague answers without mentioning diagnostic tools (EXPLAIN ANALYZE), specific query patterns, or execution plan details.",
        incomplete_answer_guidance="If the candidate only mentions adding indexes, probe on how they evaluate index overhead and determine plan choices.",
        prohibited_inferences=[
            "Do not infer expertise or lack thereof from candidate speaking pace or dialect.",
            "Do not evaluate general intelligence or prestige of previous employers; focus strictly on demonstrated PostgreSQL optimization reasoning.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-DISTRIBUTED-SYSTEMS-01": RubricTemplateEntry(
        rubric_template_id="RUB-DISTRIBUTED-SYSTEMS-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Demonstrated understanding of distributed system design, consistency models, partition tolerance, failover recovery, and asynchronous messaging.",
        question_intent="Evaluate architecture trade-off analysis between strong and eventual consistency, fault tolerance, and network partition resiliency.",
        observable_indicators=[
            "Identifies CAP/PACELC trade-offs and consistency constraints clearly.",
            "Outlines fault-handling strategies (circuit breakers, idempotency keys, retries with exponential backoff, dead-letter queues).",
            "Discusses distributed data synchronization or event-driven integration mechanisms (outbox pattern, saga, consensus).",
            "Evaluates system failure modes and degradation behavior under network partitions.",
        ],
        rating_anchors={
            1: "Fails to identify consistency trade-offs or assumes network calls never fail; offers no strategy for handling partial network partitions or duplicates.",
            3: "Clearly explains consistency vs availability trade-offs and prescribes standard fault-tolerant patterns (idempotency, retries, queues) with sound engineering rationale.",
            5: "Expertly details failure domains, distributed transaction alternatives (e.g. Sagas, transactional outbox), consensus mechanics, and operational observability under degraded conditions.",
        },
        sufficient_evidence_guidance="Candidate specifies clear fault boundaries, details how state inconsistencies are reconciled, and justifies architectural trade-offs.",
        insufficient_evidence_guidance="Candidate treats network operations as reliable local calls or cannot articulate how systems recover from dropped packets or failed nodes.",
        incomplete_answer_guidance="If the candidate describes a happy-path architecture, probe for network failure handling and data inconsistency reconciliation.",
        prohibited_inferences=[
            "Do not penalize candidate for using different architectural terminology if core distributed consensus/fault concepts are correctly communicated.",
            "Do not infer candidate culture fit or enthusiasm.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-PYTHON-CORE-01": RubricTemplateEntry(
        rubric_template_id="RUB-PYTHON-CORE-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Proficiency in core Python software engineering, asynchronous programming (asyncio), data structures, typing, memory control, and idiomatic maintainability.",
        question_intent="Evaluate mastery of Python runtime internals, concurrency models (threads, multiprocessing, asyncio), and production code quality standards.",
        observable_indicators=[
            "Explains asynchronous I/O execution loops vs multi-threading/multiprocessing and GIL constraints.",
            "Employs modern typing, execution contexts, yield generators, and data hiding.",
            "Identifies memory profiling tools, reference cycles, or stream processing.",
            "Demonstrates clean separation of concerns and robust exception handling.",
        ],
        rating_anchors={
            1: "Demonstrates basic syntax familiarity but lacks understanding of Python concurrency, generators, type annotations, or runtime execution constraints.",
            3: "Correctly explains the Python concurrency model (asyncio vs threading vs multiprocessing), writes structured modular code with type hints, and handles errors cleanly.",
            5: "Shows deep expertise in Python execution internals (bytecode, GIL, memory reclamation, async event loop mechanics), profiling techniques, and high-performance library design.",
        },
        sufficient_evidence_guidance="Candidate provides clear explanations of Python idioms, concurrency choices, and data structure performance characteristics.",
        insufficient_evidence_guidance="Candidate provides surface-level answers without awareness of Python concurrency mechanics or performance implications.",
        incomplete_answer_guidance="Probe on event loop blocking and how CPU-bound vs I/O-bound tasks should be isolated in Python.",
        prohibited_inferences=[
            "Do not judge coding style preference (e.g. functional vs object-oriented) unless relevant to project maintainability.",
            "Do not infer personality traits.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-API-DESIGN-01": RubricTemplateEntry(
        rubric_template_id="RUB-API-DESIGN-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Competence in RESTful/HTTP API architecture, contract design, idempotency, versioning, pagination, and error contract standards.",
        question_intent="Evaluate ability to architect secure, scalable, and intuitive API interfaces with robust contract guarantees.",
        observable_indicators=[
            "Designs consistent HTTP status codes, structured error payloads, and idempotent mutation contracts.",
            "Handles cursor-based vs offset-based result splitting and payload size constraints.",
            "Defines clear API versioning and backward-compatibility strategies.",
            "Considers rate-limiting, authentication context, and tenant isolation.",
        ],
        rating_anchors={
            1: "Designs inconsistent endpoints without structured error envelopes, idempotency mechanisms, or pagination considerations.",
            3: "Produces clear RESTful endpoints with appropriate HTTP methods, status codes, cursor/offset pagination, and structured error schemas.",
            5: "Architects comprehensive API contracts with deterministic idempotency keys, robust backward-compatibility guarantees, OpenAPI schema integration, and defensive security controls.",
        },
        sufficient_evidence_guidance="Candidate explains endpoint schemas, status code decisions, idempotency keys for mutations, and pagination trade-offs.",
        insufficient_evidence_guidance="Candidate does not distinguish HTTP verbs, ignores failure response schemas, or fails to address concurrency/idempotency.",
        incomplete_answer_guidance="Probe on idempotency handling for non-idempotent HTTP POST operations.",
        prohibited_inferences=[
            "Do not evaluate personal tone or confidence level.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-INCIDENT-RESPONSE-01": RubricTemplateEntry(
        rubric_template_id="RUB-INCIDENT-RESPONSE-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Systematic troubleshooting, incident resolution, root cause analysis (RCA), blameless postmortems, and preventative remediation in production environments.",
        question_intent="Evaluate structured problem-solving under pressure, telemetry utilization (metrics, logs, spans), and proactive mitigation strategies.",
        observable_indicators=[
            "Executes a structured incident workflow: mitigate customer impact first, then isolate root cause.",
            "Utilizes observability signals (distributed spans, structured logs, APM metrics) to isolate faults.",
            "Conducts blameless postmortems and identifies systemic preventative guardrails (canary deployments, alerting, automated rollbacks).",
            "Relays incident status clearly to stakeholders during active resolution.",
        ],
        rating_anchors={
            1: "Describes unstructured trial-and-error debugging without telemetry, prioritization, or root cause prevention.",
            3: "Demonstrates a methodical workflow: establishes blast radius, rolls back or mitigates impact, isolates root cause using telemetry, and documents remediations.",
            5: "Exemplifies senior incident leadership: orchestrates rapid mitigation, diagnoses complex edge cases across distributed telemetry, and implements resilient architectural mitigations to prevent recurrence.",
        },
        sufficient_evidence_guidance="Candidate clearly separates immediate mitigation from long-term root cause analysis and cites specific observability techniques.",
        insufficient_evidence_guidance="Candidate describes fixing code directly in production or cannot articulate how they locate issues without guesswork.",
        incomplete_answer_guidance="Probe on how they verify that a rollback or mitigation actually resolved customer impact before proceeding to RCA.",
        prohibited_inferences=[
            "Do not evaluate emotional state or stress perception during storytelling.",
            "Do not infer character or blame.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-CODE-QUALITY-TESTING-01": RubricTemplateEntry(
        rubric_template_id="RUB-CODE-QUALITY-TESTING-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Application of test automation pyramids (unit, integration, contract, regression), CI/CD quality gates, static analysis, and maintainable refactoring.",
        question_intent="Evaluate commitment to test reliability, deterministic test environments, mock isolation, and prevention of regression bugs.",
        observable_indicators=[
            "Differentiates unit, integration, and end-to-end testing boundaries and cost/speed trade-offs.",
            "Avoids brittle tests by testing observable behavior and contract invariants rather than internal implementation details.",
            "Utilizes static analysis, linting, type checkers, and automated CI quality gates.",
            "Employs deterministic test fixtures and isolated test databases.",
        ],
        rating_anchors={
            1: "Relies solely on manual testing or writes brittle tests that break upon refactoring without verifying critical business invariants.",
            3: "Designs comprehensive automated test suites balancing unit and integration layers, uses fixtures effectively, and enforces automated CI checks.",
            5: "Champions robust quality engineering: establishes contract testing, mutation or property-based testing, deterministic hermetic fixtures, and comprehensive regression prevention strategies.",
        },
        sufficient_evidence_guidance="Candidate explains how they test complex edge cases, structure mock boundaries, and maintain test execution speed in CI.",
        insufficient_evidence_guidance="Candidate views testing as an afterthought or only writes happy-path tests.",
        incomplete_answer_guidance="Probe on strategies for testing asynchronous operations or third-party external failure modes.",
        prohibited_inferences=[
            "Do not evaluate candidate personality or educational pedigree.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-DATA-MODELING-01": RubricTemplateEntry(
        rubric_template_id="RUB-DATA-MODELING-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Relational database schema normalization, integrity constraints, migration workflows, and indexing design for scalable transactional workloads.",
        question_intent="Evaluate ability to structure clean relational data models with strict referential integrity and safe zero-downtime migration plans.",
        observable_indicators=[
            "Applies 3NF normalization rules where appropriate and justifies deliberate denormalization.",
            "Defines foreign keys, check constraints, unique constraints, and non-nullable guarantees.",
            "Plans backward-compatible, multi-step schema migrations (e.g. expand-and-contract pattern).",
            "Selects optimal primary keys (UUID vs BIGINT) and composite indexes matching query access patterns.",
        ],
        rating_anchors={
            1: "Proposes unconstrained tables without foreign keys or indexes; lacks awareness of data integrity risks or migration downtime.",
            3: "Designs normalized relational schemas with appropriate constraints and indexes, and explains a safe phased migration strategy for schema changes.",
            5: "Masterfully structures scalable domain models with rigorous constraints, optimized composite indexes, partitioning strategies, and zero-downtime expand/contract schema evolutions.",
        },
        sufficient_evidence_guidance="Candidate specifies explicit schema constraints, foreign key cascades/restrictions, index rationale, and zero-downtime migration steps.",
        insufficient_evidence_guidance="Candidate relies purely on application-level checks without database constraints or neglects data integrity edge cases.",
        incomplete_answer_guidance="Probe on how they execute schema column renames or type conversions without production downtime.",
        prohibited_inferences=[
            "Do not evaluate personal accent or word fluency.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-SECURITY-AUTH-01": RubricTemplateEntry(
        rubric_template_id="RUB-SECURITY-AUTH-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Implementation of authentication, role-based/attribute-based access control (RBAC/ABAC), cryptographic best practices, and defense-in-depth against OWASP vulnerabilities.",
        question_intent="Evaluate rigorous security mindset in authentication token lifecycles, tenant isolation, SQL/injection prevention, and credential protection.",
        observable_indicators=[
            "Enforces server-side authorization checks on all resource mutations (never trusts client assertions).",
            "Explains secure credential handling (Argon2/bcrypt hashing, constant-time comparisons, short-lived tokens with rotation).",
            "Applies OWASP defensive patterns against injection, SSRF, CSRF, and broken object-level authorization (BOLA).",
            "Maintains safe audit logging that strictly redacts sensitive credentials and PII.",
        ],
        rating_anchors={
            1: "Suggests insecure practices (e.g. client-side role checks, plain-text token handling, unparameterized queries) with little awareness of attack vectors.",
            3: "Implements standard secure authentication, strict server-side RBAC/ABAC checks, parameterized queries, and safe audit trails.",
            5: "Exhibits deep security engineering expertise: designs defense-in-depth architectures with cryptographic token rotation, tenant-scoped data isolation, automated vulnerability scanning, and proactive abuse mitigations.",
        },
        sufficient_evidence_guidance="Candidate describes specific security controls (server-side authorization, input sanitization, safe error handling, token rotation) and potential exploit vectors.",
        insufficient_evidence_guidance="Candidate overlooks server-side authorization or downplays credential protection.",
        incomplete_answer_guidance="Probe on multi-tenant access control and how they prevent broken object level authorization (BOLA).",
        prohibited_inferences=[
            "Do not evaluate candidate personality or cultural background.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
    "RUB-COLLABORATION-LEADERSHIP-01": RubricTemplateEntry(
        rubric_template_id="RUB-COLLABORATION-LEADERSHIP-01",
        version=RUBRIC_CATALOGUE_VERSION,
        competency_definition="Cross-functional alignment, technical decision documentation (RFCs/ADRs), constructive code reviews, mentorship, and resolving engineering disagreements.",
        question_intent="Evaluate structured communication, stakeholder negotiation, evidence-based technical decision-making, and fostering team velocity.",
        observable_indicators=[
            "Uses structured decision frameworks (Architecture Decision Records, trade-off matrices) to align team members.",
            "Conducts constructive, educational code reviews focused on architecture and maintainability.",
            "Navigates architectural conflicts with empathy and objective data/prototypes.",
            "Mentors junior engineers and shares domain knowledge systematically.",
        ],
        rating_anchors={
            1: "Displays unwillingness to consider alternative perspectives or struggles to articulate technical rationale to non-technical stakeholders.",
            3: "Communicates technical concepts clearly, uses ADRs/prototypes to resolve technical disputes collaboratively, and conducts helpful code reviews.",
            5: "Exemplifies senior technical leadership: aligns cross-functional stakeholders on complex multi-quarter technical initiatives, builds consensus through objective trade-off evaluation, and elevates team engineering standards.",
        },
        sufficient_evidence_guidance="Candidate gives concrete examples of navigating technical trade-offs with teammates, using written RFCs or prototypes, and reaching alignment collaboratively.",
        insufficient_evidence_guidance="Candidate blames others for project friction or cannot describe how they resolve conflicting technical viewpoints.",
        incomplete_answer_guidance="Probe on how they handled a situation where their preferred technical proposal was not adopted.",
        prohibited_inferences=[
            "Do not evaluate extroversion or charisma as a proxy for technical leadership.",
            "Do not evaluate culture fit or sociability.",
        ],
        subject_matter_review_status="REVIEWED",
        is_active=True,
    ),
}


# =========================================================================
# Curated Standard Question Catalogues
# =========================================================================

CURATED_QUESTION_CATALOGUE: List[QuestionTemplateEntry] = [
    # 1. PostgreSQL Performance Tuning
    QuestionTemplateEntry(
        template_id="Q-POSTGRESQL-TUNING-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="POSTGRESQL_PERFORMANCE_TUNING",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Can you describe a situation where a critical database query or transaction was causing severe performance degradation, and walk through how you diagnosed and resolved the issue?",
        question_intent="Evaluate the candidate's systematic diagnostic process using query plans and database telemetry to optimize database workloads under pressure.",
        estimated_duration_minutes=6,
        allowed_probes=[
            "What specific metrics or execution plan nodes alerted you to the core bottleneck?",
            "What trade-offs did you evaluate before applying the chosen indexing or query change?",
        ],
        linked_rubric_template_id="RUB-POSTGRESQL-TUNING-01",
        catalogue_order=1,
    ),
    QuestionTemplateEntry(
        template_id="Q-POSTGRESQL-TUNING-TECH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="POSTGRESQL_PERFORMANCE_TUNING",
        question_type=QuestionType.TECHNICAL,
        text_template="When analyzing an EXPLAIN (ANALYZE, BUFFERS) output for a slow query in PostgreSQL, what key indicators do you examine, and how do you decide between indexing adjustments versus query restructuring?",
        question_intent="Assess concrete technical knowledge of PostgreSQL query engine mechanics, memory buffers, and index scanning strategies.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "How do you evaluate whether a partial or composite index is justified versus a standard B-Tree?",
            "How do you account for table bloat and autovacuum behavior when optimizing high-write tables?",
        ],
        linked_rubric_template_id="RUB-POSTGRESQL-TUNING-01",
        catalogue_order=2,
    ),
    # 2. Distributed Systems Architecture
    QuestionTemplateEntry(
        template_id="Q-DISTRIBUTED-SYSTEMS-SIT-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="DISTRIBUTED_SYSTEMS",
        question_type=QuestionType.SITUATIONAL,
        text_template="Suppose you are designing an asynchronous order processing pipeline where dependent downstream microservices may experience temporary network partitions. How would you ensure data consistency and prevent duplicate processing?",
        question_intent="Evaluate architecture principles for consistency, idempotency, retries, and failure recovery in distributed environments.",
        estimated_duration_minutes=6,
        allowed_probes=[
            "How would you implement idempotency keys across service boundaries to prevent duplicate execution?",
            "What dead-letter queue and reconciliation mechanisms would you put in place for unrecoverable failures?",
        ],
        linked_rubric_template_id="RUB-DISTRIBUTED-SYSTEMS-01",
        catalogue_order=3,
    ),
    QuestionTemplateEntry(
        template_id="Q-DISTRIBUTED-SYSTEMS-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="DISTRIBUTED_SYSTEMS",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Tell me about a complex distributed system you designed or maintained. What were the most significant reliability trade-offs you faced, and how did you validate system resilience?",
        question_intent="Assess real-world architectural decision-making, failure domain isolation, and disaster recovery validation.",
        estimated_duration_minutes=6,
        allowed_probes=[
            "How did you monitor and measure latency and error rates across asynchronous service boundaries?",
            "What occurred during your system's first major production partial disruption?",
        ],
        linked_rubric_template_id="RUB-DISTRIBUTED-SYSTEMS-01",
        catalogue_order=4,
    ),
    # 3. Python Core Engineering
    QuestionTemplateEntry(
        template_id="Q-PYTHON-CORE-TECH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="PYTHON_CORE",
        question_type=QuestionType.TECHNICAL,
        text_template="How does the Python asyncio event loop operate under the hood, and how do you ensure that CPU-bound operations do not block the event loop in high-throughput backend services?",
        question_intent="Assess depth of understanding in Python concurrency models, asyncio runtime constraints, and thread/process offloading.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "When would you choose a ProcessPoolExecutor over an ThreadPoolExecutor for background task execution?",
            "How do you handle resource clean-up and cancellation tokens in long-running async coroutines?",
        ],
        linked_rubric_template_id="RUB-PYTHON-CORE-01",
        catalogue_order=5,
    ),
    QuestionTemplateEntry(
        template_id="Q-PYTHON-CORE-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="PYTHON_CORE",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Can you share an experience where you refactored or optimized a performance-critical Python codebase? What profiling tools did you use and what concrete improvements resulted?",
        question_intent="Evaluate real-world code quality, profiling proficiency (cProfile, memory_profiler), and optimization methodology in Python.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "What specific memory or CPU bottleneck did the profiler uncover?",
            "How did you ensure regression testing remained solid during the refactor?",
        ],
        linked_rubric_template_id="RUB-PYTHON-CORE-01",
        catalogue_order=6,
    ),
    # 4. API Design and Contract Standards
    QuestionTemplateEntry(
        template_id="Q-API-DESIGN-TECH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="API_DESIGN",
        question_type=QuestionType.TECHNICAL,
        text_template="When designing a public REST API that must evolve over several years, how do you handle versioning, backward compatibility, structured error envelopes, and cursor pagination?",
        question_intent="Evaluate API design discipline, schema stability, and developer experience considerations.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "Why might cursor-based pagination be preferable to offset-based pagination in dynamic datasets?",
            "How do you deprecate and sunset older API fields without breaking existing mobile or enterprise clients?",
        ],
        linked_rubric_template_id="RUB-API-DESIGN-01",
        catalogue_order=7,
    ),
    # 5. Production Incident Response and Reliability
    QuestionTemplateEntry(
        template_id="Q-INCIDENT-RESPONSE-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="INCIDENT_RESPONSE",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Walk me through a severe, high-priority production system disruption that you helped investigate. How did you stabilize the system, isolate the root cause, and ensure it could not recur?",
        question_intent="Evaluate troubleshooting discipline under stress, telemetry utilization, blameless post-mortems, and long-term remediation guardrails.",
        estimated_duration_minutes=6,
        allowed_probes=[
            "How did you balance the urgency of restoring customer service against preserving telemetry for root-cause analysis?",
            "What specific automated guardrail or alert was introduced as a result of the postmortem?",
        ],
        linked_rubric_template_id="RUB-INCIDENT-RESPONSE-01",
        catalogue_order=8,
    ),
    # 6. Relational Data Modeling and Schema Evolution
    QuestionTemplateEntry(
        template_id="Q-DATA-MODELING-TECH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="DATA_MODELING",
        question_type=QuestionType.TECHNICAL,
        text_template="How do you approach designing a normalized transactional relational schema with strict integrity constraints, and how do you safely perform schema migrations on large tables without downtime?",
        question_intent="Assess database normalization skills, foreign key integrity rules, and safe zero-downtime schema evolution.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "How do you control locking overhead when adding a NOT NULL column with a default value to a multi-million-row table?",
            "Under what specific circumstances would you deliberately denormalize data in a relational model?",
        ],
        linked_rubric_template_id="RUB-DATA-MODELING-01",
        catalogue_order=9,
    ),
    # 7. Application Security and Authorization
    QuestionTemplateEntry(
        template_id="Q-SECURITY-AUTH-TECH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="APPLICATION_SECURITY",
        question_type=QuestionType.TECHNICAL,
        text_template="How do you architect multi-tenant authorization controls to prevent Broken Object Level Authorization (BOLA/IDOR) and ensure sensitive data is never leaked via API responses or logs?",
        question_intent="Evaluate rigorous security principles, tenant isolation, authentication token verification, and data masking/redaction discipline.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "How do you enforce server-side tenant scoping at the database query layer?",
            "What automated testing patterns do you use to verify that cross-tenant access is strictly denied?",
        ],
        linked_rubric_template_id="RUB-SECURITY-AUTH-01",
        catalogue_order=10,
    ),
    # 8. Testing and Quality Engineering
    QuestionTemplateEntry(
        template_id="Q-TESTING-QUALITY-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="TESTING_QUALITY",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Describe how you structured the automated testing strategy for a complex backend system with multiple external dependencies and databases. How did you maintain high test reliability and fast CI execution?",
        question_intent="Assess testing architecture, mock/fixture boundaries, hermetic test environment design, and flaky test prevention.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "How did you decide what to test in memory versus against real containerized dependencies in CI?",
            "How did you prevent tests from becoming brittle and breaking on internal implementation refactorings?",
        ],
        linked_rubric_template_id="RUB-CODE-QUALITY-TESTING-01",
        catalogue_order=11,
    ),
    # 9. Engineering Collaboration and Technical Leadership
    QuestionTemplateEntry(
        template_id="Q-COLLABORATION-BEH-01",
        version=QUESTION_CATALOGUE_VERSION,
        competency_key="TECHNICAL_COLLABORATION",
        question_type=QuestionType.BEHAVIORAL,
        text_template="Can you share an example of a significant architectural disagreement within your team? How did you navigate the discussion, evaluate the technical trade-offs, and reach a constructive consensus?",
        question_intent="Evaluate objective technical trade-off evaluation, communication clarity, RFC documentation, and fostering collaborative team velocity.",
        estimated_duration_minutes=5,
        allowed_probes=[
            "What objective evidence or proof-of-concept did you use to compare the competing proposals?",
            "How did you ensure all team members remained aligned and committed once the decision was finalized?",
        ],
        linked_rubric_template_id="RUB-COLLABORATION-LEADERSHIP-01",
        catalogue_order=12,
    ),
]


# =========================================================================
# Competency Key Aliases & Mappings
# =========================================================================

# Mapping normalized requirement keywords/labels to stable competency keys
COMPETENCY_KEY_MAPPINGS: Dict[str, str] = {
    # PostgreSQL
    "postgresql": "POSTGRESQL_PERFORMANCE_TUNING",
    "postgres": "POSTGRESQL_PERFORMANCE_TUNING",
    "postgresql performance tuning": "POSTGRESQL_PERFORMANCE_TUNING",
    "database tuning": "POSTGRESQL_PERFORMANCE_TUNING",
    "query optimization": "POSTGRESQL_PERFORMANCE_TUNING",
    "sql performance": "POSTGRESQL_PERFORMANCE_TUNING",
    
    # Distributed Systems
    "distributed systems": "DISTRIBUTED_SYSTEMS",
    "distributed architecture": "DISTRIBUTED_SYSTEMS",
    "microservices": "DISTRIBUTED_SYSTEMS",
    "async messaging": "DISTRIBUTED_SYSTEMS",
    "event-driven": "DISTRIBUTED_SYSTEMS",
    "event driven": "DISTRIBUTED_SYSTEMS",
    "kafka": "DISTRIBUTED_SYSTEMS",
    "rabbitmq": "DISTRIBUTED_SYSTEMS",
    
    # Python
    "python": "PYTHON_CORE",
    "fastapi": "PYTHON_CORE",
    "asyncio": "PYTHON_CORE",
    "django": "PYTHON_CORE",
    "backend development": "PYTHON_CORE",
    "python programming": "PYTHON_CORE",
    
    # API Design
    "api design": "API_DESIGN",
    "rest api": "API_DESIGN",
    "restful": "API_DESIGN",
    "http": "API_DESIGN",
    "graphql": "API_DESIGN",
    "contract design": "API_DESIGN",
    
    # Incident Response
    "incident response": "INCIDENT_RESPONSE",
    "production support": "INCIDENT_RESPONSE",
    "troubleshooting": "INCIDENT_RESPONSE",
    "observability": "INCIDENT_RESPONSE",
    "monitoring": "INCIDENT_RESPONSE",
    "sre": "INCIDENT_RESPONSE",
    "reliability": "INCIDENT_RESPONSE",
    
    # Data Modeling
    "data modeling": "DATA_MODELING",
    "database design": "DATA_MODELING",
    "schema design": "DATA_MODELING",
    "relational databases": "DATA_MODELING",
    "sql": "DATA_MODELING",
    "alembic": "DATA_MODELING",
    "sqlalchemy": "DATA_MODELING",
    
    # Application Security
    "security": "APPLICATION_SECURITY",
    "authentication": "APPLICATION_SECURITY",
    "authorization": "APPLICATION_SECURITY",
    "oauth": "APPLICATION_SECURITY",
    "rbac": "APPLICATION_SECURITY",
    "appsec": "APPLICATION_SECURITY",
    "owasp": "APPLICATION_SECURITY",
    
    # Testing & Quality
    "testing": "TESTING_QUALITY",
    "pytest": "TESTING_QUALITY",
    "unit testing": "TESTING_QUALITY",
    "integration testing": "TESTING_QUALITY",
    "test automation": "TESTING_QUALITY",
    "quality assurance": "TESTING_QUALITY",
    "ci/cd": "TESTING_QUALITY",
    
    # Technical Collaboration
    "collaboration": "TECHNICAL_COLLABORATION",
    "technical leadership": "TECHNICAL_COLLABORATION",
    "code review": "TECHNICAL_COLLABORATION",
    "mentorship": "TECHNICAL_COLLABORATION",
    "communication": "TECHNICAL_COLLABORATION",
    "system architecture": "DISTRIBUTED_SYSTEMS",
}


def get_catalogue_questions_for_competency(competency_key: str) -> List[QuestionTemplateEntry]:
    """Returns all active question templates for a given competency key in deterministic catalogue order."""
    matched = [
        q for q in CURATED_QUESTION_CATALOGUE
        if q.competency_key == competency_key and q.is_active
    ]
    return sorted(matched, key=lambda q: (q.catalogue_order, q.template_id))


def get_catalogue_rubric(rubric_template_id: str) -> Optional[RubricTemplateEntry]:
    """Retrieves an active rubric template entry by its ID."""
    rubric = CURATED_RUBRIC_CATALOGUE.get(rubric_template_id)
    if rubric and rubric.is_active:
        return rubric
    return None


# =========================================================================
# Supplemental Evidence Clarification Question Catalogue
# =========================================================================

SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION: str = "1.0.0"


class SupplementalQuestionTemplateEntry(FrozenSchema):
    """Immutable curated catalogue template entry for non-scored candidate supplemental evidence questions."""

    template_id: str = Field(min_length=3, max_length=100)
    version: str = Field(default=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION)
    match_state: str = Field(min_length=3, max_length=50)
    text_template: str = Field(min_length=10, max_length=2000)
    question_intent: str = Field(min_length=5, max_length=1000)
    clarification_purpose: str = Field(min_length=5, max_length=1000)
    facts_to_confirm: List[str] = Field(default_factory=list)
    prohibited_inferences: List[str] = Field(default_factory=list)
    estimated_duration_minutes: int = Field(default=4, ge=1, le=15)
    is_active: bool = True
    catalogue_order: int = Field(default=0, ge=0)


CURATED_SUPPLEMENTAL_CATALOGUE: Dict[str, SupplementalQuestionTemplateEntry] = {
    "CONFLICTING_EVIDENCE": SupplementalQuestionTemplateEntry(
        template_id="SUPP-Q-CONFLICT-01",
        version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        match_state="CONFLICTING_EVIDENCE",
        text_template="We found differing details related to {label}. Could you clarify the relevant context, dates or responsibilities?",
        question_intent="Request factual reconciliation without accusation.",
        clarification_purpose="Reconcile timeline, context, or differing detail points neutrally.",
        facts_to_confirm=[
            "Timeline and project dates",
            "Specific roles and responsibilities",
            "Contextual technical environment",
        ],
        prohibited_inferences=[
            "Do not imply dishonesty or falsehood.",
            "Do not use accusatory phrasing.",
        ],
        estimated_duration_minutes=4,
        catalogue_order=1,
    ),
    "INSUFFICIENT_EVIDENCE": SupplementalQuestionTemplateEntry(
        template_id="SUPP-Q-INSUFFICIENT-01",
        version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        match_state="INSUFFICIENT_EVIDENCE",
        text_template="Could you provide a concrete example that demonstrates your experience with {label}, including your actions and the result?",
        question_intent="Request a clearer example or additional context.",
        clarification_purpose="Obtain specific contextual evidence and concrete technical actions.",
        facts_to_confirm=[
            "Concrete technical actions taken",
            "Problem context and tooling used",
            "Verification or outcome achieved",
        ],
        prohibited_inferences=[
            "Do not convert automated extraction confidence into a negative conclusion.",
            "Do not infer candidate character or communication style.",
        ],
        estimated_duration_minutes=4,
        catalogue_order=2,
    ),
    "NOT_EVIDENCED": SupplementalQuestionTemplateEntry(
        template_id="SUPP-Q-NOT-EVIDENCED-01",
        version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        match_state="NOT_EVIDENCED",
        text_template="The reviewed material did not provide enough information about {label}. Would you like to describe any relevant experience or transferable work in this area?",
        question_intent="Give the candidate a neutral opportunity to provide relevant experience not present in the reviewed material.",
        clarification_purpose="Provide neutral opportunity to present relevant unlisted or transferable experience.",
        facts_to_confirm=[
            "Relevant projects or prior experience",
            "Transferable skills or related technology use",
            "Practical application context",
        ],
        prohibited_inferences=[
            "Do not treat missing resume evidence as proof of missing skill.",
            "Do not imply negligence or lack of qualification.",
        ],
        estimated_duration_minutes=4,
        catalogue_order=3,
    ),
    "PARTIALLY_MATCHED": SupplementalQuestionTemplateEntry(
        template_id="SUPP-Q-PARTIAL-01",
        version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        match_state="PARTIALLY_MATCHED",
        text_template="Please describe your experience with {label}, including the scope of your work and the responsibilities you personally handled.",
        question_intent="Clarify scope, duration, complexity or a missing requirement dimension.",
        clarification_purpose="Clarify scope, technical depth, and specific responsibilities handled.",
        facts_to_confirm=[
            "Technical scope and complexity",
            "Direct responsibilities vs team contributions",
            "Duration and recent practical application",
        ],
        prohibited_inferences=[
            "Do not assume missing resume detail proves lack of technical ability.",
            "Do not use defensive or evaluative tone.",
        ],
        estimated_duration_minutes=4,
        catalogue_order=4,
    ),
    "MATCHED": SupplementalQuestionTemplateEntry(
        template_id="SUPP-Q-MATCHED-01",
        version=SUPPLEMENTAL_QUESTION_CATALOGUE_VERSION,
        match_state="MATCHED",
        text_template="Please describe a specific situation in which you applied {label}. What was your personal contribution, and what was the outcome?",
        question_intent="Verify depth, ownership, context, trade-offs and result.",
        clarification_purpose="Verify depth of practical ownership, technical context, and measurable outcomes.",
        facts_to_confirm=[
            "Specific role and technical ownership",
            "Design constraints and trade-offs considered",
            "Demonstrated project result or metric",
        ],
        prohibited_inferences=[
            "Do not infer future performance from self-reported project scope alone.",
            "Do not evaluate personality traits or presentation style.",
        ],
        estimated_duration_minutes=4,
        catalogue_order=5,
    ),
}


def get_catalogue_supplemental_template(match_state: str) -> Optional[SupplementalQuestionTemplateEntry]:
    """Retrieves an active supplemental question template by match state string."""
    state_key = match_state.value if hasattr(match_state, "value") else str(match_state)
    tmpl = CURATED_SUPPLEMENTAL_CATALOGUE.get(state_key)
    if tmpl and tmpl.is_active:
        return tmpl
    return None
