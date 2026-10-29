import dataclasses
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from app.schemas.skill_and_summary import SkillCategory

logger = logging.getLogger("ai_hiring.services.skill_catalog")

SKILL_CATALOG_VERSION = "skill-catalog-v1.3"


@dataclasses.dataclass(frozen=True)
class SkillDefinition:
    canonical_name: str
    category: Union[SkillCategory, str]
    aliases: Tuple[str, ...] = ()
    # If True, this term is short/ambiguous (e.g. C, R, Go, JS) and should only be matched
    # in explicit skill list contexts or strictly bounded expressions, never arbitrary prose.
    is_ambiguous_short: bool = False
    source: str = "legacy"
    source_family: str = "software"
    external_id: Optional[str] = None


# Canonical skill definitions
SKILL_DEFINITIONS: List[SkillDefinition] = [
    # Programming Languages
    SkillDefinition(
        canonical_name="Python",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("python3", "python 3", "python2", "python 2"),
    ),
    SkillDefinition(
        canonical_name="Java",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("java8", "java 8", "java11", "java 11", "java17", "java 17", "java21"),
    ),
    SkillDefinition(
        canonical_name="JavaScript",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("javascript", "js", "ecmascript", "es6", "es6+"),
    ),
    SkillDefinition(
        canonical_name="TypeScript",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("typescript", "ts"),
    ),
    SkillDefinition(
        canonical_name="C",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
        is_ambiguous_short=True,
    ),
    SkillDefinition(
        canonical_name="C++",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("cpp", "c plus plus"),
    ),
    SkillDefinition(
        canonical_name="C#",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("csharp", "c sharp"),
    ),
    SkillDefinition(
        canonical_name="Go",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("golang",),
        is_ambiguous_short=True,
    ),
    SkillDefinition(
        canonical_name="R",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
        is_ambiguous_short=True,
    ),
    SkillDefinition(
        canonical_name="SQL",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("t-sql", "tsql", "pl/sql", "plsql"),
    ),
    SkillDefinition(
        canonical_name="HTML",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("html5",),
    ),
    SkillDefinition(
        canonical_name="CSS",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("css3",),
    ),
    SkillDefinition(
        canonical_name="Rust",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="Kotlin",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="Swift",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="PHP",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=("php7", "php8"),
    ),
    SkillDefinition(
        canonical_name="Ruby",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="Scala",
        category=SkillCategory.PROGRAMMING_LANGUAGE,
        aliases=(),
    ),

    # Frameworks & Libraries
    SkillDefinition(
        canonical_name="React",
        category=SkillCategory.FRAMEWORK,
        aliases=("reactjs", "react.js", "react js"),
    ),
    SkillDefinition(
        canonical_name="Redux",
        category=SkillCategory.FRAMEWORK,
        aliases=("reduxjs", "redux.js", "redux js"),
    ),
    SkillDefinition(
        canonical_name="Node.js",
        category=SkillCategory.FRAMEWORK,
        aliases=("nodejs", "node.js", "node js"),
    ),
    SkillDefinition(
        canonical_name="FastAPI",
        category=SkillCategory.FRAMEWORK,
        aliases=("fast-api", "fastapi"),
    ),
    SkillDefinition(
        canonical_name="Django",
        category=SkillCategory.FRAMEWORK,
        aliases=("django rest framework", "drf"),
    ),
    SkillDefinition(
        canonical_name="Flask",
        category=SkillCategory.FRAMEWORK,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name=".NET",
        category=SkillCategory.FRAMEWORK,
        aliases=(".net core", "dotnet", "dotnet core", "asp.net", "asp.net core", ".net framework"),
    ),
    SkillDefinition(
        canonical_name="Spring Boot",
        category=SkillCategory.FRAMEWORK,
        aliases=("springboot", "spring-boot", "spring framework"),
    ),
    SkillDefinition(
        canonical_name="scikit-learn",
        category=SkillCategory.FRAMEWORK,
        aliases=("sklearn", "scikit learn"),
    ),
    SkillDefinition(
        canonical_name="pandas",
        category=SkillCategory.FRAMEWORK,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="NumPy",
        category=SkillCategory.FRAMEWORK,
        aliases=("numpy",),
    ),
    SkillDefinition(
        canonical_name="TensorFlow",
        category=SkillCategory.FRAMEWORK,
        aliases=("tensorflow", "tf"),
    ),
    SkillDefinition(
        canonical_name="PyTorch",
        category=SkillCategory.FRAMEWORK,
        aliases=("pytorch", "torch"),
    ),
    SkillDefinition(
        canonical_name="Vue.js",
        category=SkillCategory.FRAMEWORK,
        aliases=("vue", "vuejs", "vue.js", "vue 3"),
    ),
    SkillDefinition(
        canonical_name="Angular",
        category=SkillCategory.FRAMEWORK,
        aliases=("angularjs", "angular 2+"),
    ),
    SkillDefinition(
        canonical_name="Next.js",
        category=SkillCategory.FRAMEWORK,
        aliases=("nextjs", "next.js"),
    ),
    SkillDefinition(
        canonical_name="Express.js",
        category=SkillCategory.FRAMEWORK,
        aliases=("express", "expressjs", "express.js"),
    ),
    SkillDefinition(
        canonical_name="Jest",
        category=SkillCategory.FRAMEWORK,
        aliases=("jestjs", "jest.js"),
    ),
    SkillDefinition(
        canonical_name="GraphQL",
        category=SkillCategory.FRAMEWORK,
        aliases=(),
    ),

    # Databases
    SkillDefinition(
        canonical_name="PostgreSQL",
        category=SkillCategory.DATABASE,
        aliases=("postgres", "postgresql", "psql"),
    ),
    SkillDefinition(
        canonical_name="MySQL",
        category=SkillCategory.DATABASE,
        aliases=("mysql",),
    ),
    SkillDefinition(
        canonical_name="MongoDB",
        category=SkillCategory.DATABASE,
        aliases=("mongodb", "mongo"),
    ),
    SkillDefinition(
        canonical_name="SQLite",
        category=SkillCategory.DATABASE,
        aliases=("sqlite3",),
    ),
    SkillDefinition(
        canonical_name="Redis",
        category=SkillCategory.DATABASE,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="Elasticsearch",
        category=SkillCategory.DATABASE,
        aliases=("elastic search", "opensearch"),
    ),
    SkillDefinition(
        canonical_name="DynamoDB",
        category=SkillCategory.DATABASE,
        aliases=("dynamo db", "amazon dynamodb"),
    ),
    SkillDefinition(
        canonical_name="Oracle Database",
        category=SkillCategory.DATABASE,
        aliases=("oracle db", "oracle sql"),
    ),

    # Tools & Platforms
    SkillDefinition(
        canonical_name="Git",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("git version control",),
    ),
    SkillDefinition(
        canonical_name="Vite",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("vitejs", "vite.js"),
    ),
    SkillDefinition(
        canonical_name="Docker",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("docker container", "docker compose"),
    ),
    SkillDefinition(
        canonical_name="Kubernetes",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("k8s",),
    ),
    SkillDefinition(
        canonical_name="Linux",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("ubuntu", "debian", "centos", "redhat", "unix"),
    ),
    SkillDefinition(
        canonical_name="AWS",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("amazon web services", "aws cloud"),
    ),
    SkillDefinition(
        canonical_name="Azure",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("microsoft azure", "azure cloud"),
    ),
    SkillDefinition(
        canonical_name="Google Cloud",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("gcp", "google cloud platform"),
    ),
    SkillDefinition(
        canonical_name="CI/CD",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("cicd", "continuous integration", "github actions", "gitlab ci", "jenkins"),
    ),
    SkillDefinition(
        canonical_name="Terraform",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=(),
    ),
    SkillDefinition(
        canonical_name="Kafka",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("apache kafka",),
    ),
    SkillDefinition(
        canonical_name="Apache Spark",
        category=SkillCategory.FRAMEWORK,
        aliases=("spark", "pyspark", "apache-spark"),
    ),
    SkillDefinition(
        canonical_name="Apache Airflow",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("airflow", "apache-airflow"),
    ),
    SkillDefinition(
        canonical_name="dbt",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("data build tool", "dbt core", "dbt cloud"),
    ),
    SkillDefinition(
        canonical_name="Power BI",
        category=SkillCategory.TOOL_OR_PLATFORM,
        aliases=("powerbi", "power-bi", "microsoft power bi"),
    ),
]


ONET_APPROVED_ALIASES: Dict[str, Tuple[str, ...]] = {
    "Microsoft Power BI": ("power bi", "powerbi", "power-bi"),
    "Microsoft Excel": ("excel", "ms excel"),
    "Structured query language SQL": ("sql", "structured query language"),
    "SQL": ("structured query language",),
    "Microsoft SharePoint": ("sharepoint",),
    "Microsoft Teams": ("teams", "ms teams"),
    "Monitoring": ("operational monitoring", "routine monitoring"),
    "Operations Monitoring": ("routine monitoring", "operational monitoring"),
}

CUSTOM_DOMAIN_SKILLS: List[SkillDefinition] = [
    SkillDefinition(
        canonical_name="Process Mapping",
        category="Domain/Custom Skills",
        aliases=("process mapping", "fundamentals of process mapping"),
        source="custom",
        source_family="custom",
    ),
    SkillDefinition(
        canonical_name="Ticketing/workflow systems",
        category="Domain/Custom Skills",
        aliases=(
            "ticketing/workflow systems",
            "ticketing and workflow systems",
            "ticketing systems",
            "workflow systems",
            "ticketing system",
            "workflow system",
            "ticketing-system",
        ),
        source="custom",
        source_family="custom",
    ),
]


class SkillCatalog:
    """Deterministic, indexed lookup for canonical skills and conservative aliases."""

    def __init__(self, definitions: List[SkillDefinition] = SKILL_DEFINITIONS):
        self.version = SKILL_CATALOG_VERSION
        self._definitions = definitions
        self._exact_map: Dict[str, SkillDefinition] = {}
        self._alias_map: Dict[str, Tuple[SkillDefinition, str]] = {}
        self._ambiguous_shorts: Set[str] = set()

        for defn in self._definitions:
            canon_lower = defn.canonical_name.lower().strip()
            # If not yet in exact map, insert it (preserves precedence)
            if canon_lower not in self._exact_map:
                self._exact_map[canon_lower] = defn
            if defn.is_ambiguous_short:
                self._ambiguous_shorts.add(canon_lower)

            for alias in defn.aliases:
                alias_lower = alias.lower().strip()
                if alias_lower not in self._alias_map:
                    self._alias_map[alias_lower] = (defn, alias)
                if len(alias_lower) <= 2:
                    self._ambiguous_shorts.add(alias_lower)

    def lookup_exact_or_alias(self, text: str) -> Optional[Tuple[SkillDefinition, bool, str]]:
        """
        Looks up normalized token/phrase.
        Returns: (SkillDefinition, is_alias: bool, matched_canonical_name: str) or None
        """
        cleaned = text.lower().strip()
        if not cleaned:
            return None

        if cleaned in self._exact_map:
            defn = self._exact_map[cleaned]
            return defn, False, defn.canonical_name

        if cleaned in self._alias_map:
            defn, _ = self._alias_map[cleaned]
            return defn, True, defn.canonical_name

        return None

    def is_ambiguous_short(self, text: str) -> bool:
        return text.lower().strip() in self._ambiguous_shorts

    def get_all_canonical_patterns(self) -> List[Tuple[str, SkillDefinition, bool]]:
        """
        Returns all lookup strings ordered by decreasing length (to match longer phrases first).
        Format: (lookup_lower, SkillDefinition, is_alias)
        """
        patterns = []
        for defn in self._definitions:
            patterns.append((defn.canonical_name.lower(), defn, False))
            for alias in defn.aliases:
                patterns.append((alias.lower(), defn, True))

        # Sort by length descending, then alphabetically for determinism
        patterns.sort(key=lambda x: (-len(x[0]), x[0]))
        return patterns


DEFAULT_SKILL_CATALOG = SkillCatalog()

_ACTIVE_ONET_CATALOG_CACHE: Optional[Tuple[str, SkillCatalog]] = None


def load_active_onet_catalog(session: Optional[Any] = None) -> SkillCatalog:
    """
    Loads a SkillCatalog enriched with the active O*NET 31.0 snapshot.
    Safe precedence:
    1. Exact active O*NET canonical label
    2. Approved active O*NET alias
    3. Existing legacy-catalogue canonical label or alias
    4. Explicit custom/domain skill
    Gracefully falls back to DEFAULT_SKILL_CATALOG if database is unreachable,
    tables are missing, or no active snapshot exists.
    """
    global _ACTIVE_ONET_CATALOG_CACHE

    db = session
    should_close = False
    if db is None:
        try:
            from app.database import SessionLocal
            db = SessionLocal()
            should_close = True
        except Exception as e:
            logger.debug(f"Database unavailable for O*NET catalog loading: {e}")
            return DEFAULT_SKILL_CATALOG

    try:
        from app.models.taxonomy import TaxonomySnapshot, TaxonomySnapshotState, SkillConcept, SkillAlias
        from sqlalchemy import select

        snap = db.execute(
            select(TaxonomySnapshot).where(
                TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE,
                TaxonomySnapshot.source == "ONET",
            )
        ).scalar_one_or_none()

        if snap is None:
            return DEFAULT_SKILL_CATALOG

        snap_key = str(snap.snapshot_id)
        if _ACTIVE_ONET_CATALOG_CACHE is not None and _ACTIVE_ONET_CATALOG_CACHE[0] == snap_key:
            return _ACTIVE_ONET_CATALOG_CACHE[1]

        # Load skills from DB for active snapshot
        skills = db.execute(
            select(SkillConcept).where(SkillConcept.snapshot_id == snap.snapshot_id)
        ).scalars().all()

        aliases = db.execute(select(SkillAlias)).scalars().all()
        alias_map_by_concept_id: Dict[Any, List[str]] = {}
        for al in aliases:
            alias_map_by_concept_id.setdefault(al.skill_concept_id, []).append(al.alias_text)

        legacy_by_canon: Dict[str, SkillDefinition] = {
            d.canonical_name.lower(): d for d in SKILL_DEFINITIONS
        }
        definitions: List[SkillDefinition] = []
        seen_canonical_lower: Set[str] = set()

        for sk in skills:
            c_name = sk.canonical_label.strip()
            c_lower = c_name.lower()
            if not c_name or c_lower in seen_canonical_lower:
                continue
            seen_canonical_lower.add(c_lower)

            fam = (sk.source_family or "").lower()
            if c_lower in legacy_by_canon:
                legacy_def = legacy_by_canon[c_lower]
                cat = legacy_def.category
                fam = legacy_def.source_family
            elif fam == "essential":
                cat = "Essential Skills"
            elif fam == "transferable":
                cat = "Transferable Skills"
            elif fam == "software":
                cat = sk.category or "Software and Tools"
            else:
                cat = "Domain/Custom Skills"

            sk_aliases: List[str] = list(alias_map_by_concept_id.get(sk.concept_id, []))
            if c_name in ONET_APPROVED_ALIASES:
                for app_al in ONET_APPROVED_ALIASES[c_name]:
                    if app_al not in sk_aliases:
                        sk_aliases.append(app_al)

            is_ambig = (
                len(c_name) <= 2
                or c_lower in {"c", "r", "go", "js", "ts", "c++", "c#", ".net", "science", "monitoring"}
                or (fam in ("essential", "transferable") and len(c_name.split()) == 1)
            )

            definitions.append(
                SkillDefinition(
                    canonical_name=c_name,
                    category=cat,
                    aliases=tuple(sk_aliases),
                    is_ambiguous_short=is_ambig,
                    source=f"ONET_{snap.source_version}",
                    source_family=fam,
                    external_id=sk.external_id,
                )
            )

        # Append legacy definitions (precedence fallback)
        for legacy_def in SKILL_DEFINITIONS:
            if legacy_def.canonical_name.lower() not in seen_canonical_lower:
                definitions.append(legacy_def)
                seen_canonical_lower.add(legacy_def.canonical_name.lower())

        # Append custom domain skills
        for custom_def in CUSTOM_DOMAIN_SKILLS:
            if custom_def.canonical_name.lower() not in seen_canonical_lower:
                definitions.append(custom_def)
                seen_canonical_lower.add(custom_def.canonical_name.lower())

        catalog = SkillCatalog(definitions=definitions)
        _ACTIVE_ONET_CATALOG_CACHE = (snap_key, catalog)
        return catalog

    except Exception as e:
        logger.warning(f"Error loading active O*NET catalog, falling back to default: {e}")
        return DEFAULT_SKILL_CATALOG
    finally:
        if should_close and db is not None:
            db.close()
