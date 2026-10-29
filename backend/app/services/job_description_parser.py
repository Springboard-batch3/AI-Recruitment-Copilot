import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.job import (
    EmploymentType,
    RequirementCategory,
    RequirementEvidenceType,
    RequirementNecessity,
    RequirementPriority,
    WorkplaceType,
)
from app.models.taxonomy import (
    SkillAlias,
    SkillConcept,
    TaxonomySnapshot,
    TaxonomySnapshotState,
)
from app.services.requirement_validation import SENSITIVE_PATTERNS
from app.services.taxonomy_normalization import normalize_search_query

logger = logging.getLogger("ai_hiring.services.job_description_parser")

PARSER_VERSION = "1.0.0"

# Common heading patterns
HEADING_PATTERNS = {
    "SUMMARY": re.compile(
        r"^(?:#+\s*)?(?:about\s+(?:the\s+)?(?:role|job|position|company|us)|role\s+summary|job\s+summary|position\s+(?:summary|overview)|role\s+overview|job\s+overview|overview)[:\s]*$",
        re.IGNORECASE,
    ),
    "RESPONSIBILITIES": re.compile(
        r"^(?:#+\s*)?(?:responsibilities|duties|what\s+you(?:'ll|\s+will)\s+do|key\s+responsibilities|primary\s+(?:responsibilities|duties)|core\s+responsibilities|day\s+in\s+the\s+life|accountabilities)[:\s]*$",
        re.IGNORECASE,
    ),
    "REQUIRED_QUALIFICATIONS": re.compile(
        r"^(?:#+\s*)?(?:required\s+(?:qualifications|skills|requirements)|must\s+have(?:s)?|minimum\s+(?:qualifications|requirements)|basic\s+qualifications|what\s+you\s+need|mandatory\s+requirements|essential\s+(?:requirements|qualifications))[:\s]*$",
        re.IGNORECASE,
    ),
    "PREFERRED_QUALIFICATIONS": re.compile(
        r"^(?:#+\s*)?(?:preferred\s+(?:qualifications|skills|requirements)|nice\s+to\s+have(?:s)?|desired\s+(?:qualifications|skills)|bonus\s+points|preferred\s+experience|good\s+to\s+have|additional\s+qualifications)[:\s]*$",
        re.IGNORECASE,
    ),
    "GENERAL_QUALIFICATIONS": re.compile(
        r"^(?:#+\s*)?(?:qualifications|requirements|requirements\s+&\s+qualifications|skills\s+&\s+experience|who\s+you\s+are|what\s+we(?:'re|\s+are)\s+looking\s+for|candidate\s+profile)[:\s]*$",
        re.IGNORECASE,
    ),
}

# Explicit field patterns
TITLE_PREFIX_PATTERN = re.compile(r"^(?:job\s+title|title|position|role)\s*:\s*(.+)$", re.IGNORECASE)
DEPT_PREFIX_PATTERN = re.compile(r"^(?:department|team|org|division)\s*:\s*(.+)$", re.IGNORECASE)
LOCATION_PREFIX_PATTERN = re.compile(r"^(?:location|work\s+location)\s*:\s*(.+)$", re.IGNORECASE)
OPENINGS_PREFIX_PATTERN = re.compile(r"^(?:openings|number\s+of\s+openings|headcount|positions)\s*:\s*(\d+)$", re.IGNORECASE)

# Employment type keywords
EMPLOYMENT_TYPES = [
    (re.compile(r"\bfull[- ]time\b", re.IGNORECASE), EmploymentType.FULL_TIME),
    (re.compile(r"\bpart[- ]time\b", re.IGNORECASE), EmploymentType.PART_TIME),
    (re.compile(r"\bcontract(?:or)?\b", re.IGNORECASE), EmploymentType.CONTRACT),
    (re.compile(r"\bintern(?:ship)?\b", re.IGNORECASE), EmploymentType.INTERNSHIP),
    (re.compile(r"\btemporary\b", re.IGNORECASE), EmploymentType.TEMPORARY),
]

# Workplace type keywords
WORKPLACE_TYPES = [
    (re.compile(r"\bremote\b", re.IGNORECASE), WorkplaceType.REMOTE),
    (re.compile(r"\bhybrid\b", re.IGNORECASE), WorkplaceType.HYBRID),
    (re.compile(r"\bon[- ]?site\b", re.IGNORECASE), WorkplaceType.ON_SITE),
]

# Experience numeric regex (e.g. "3 years", "minimum 5 years", "2+ years", "3-5 years")
EXPERIENCE_NUMERIC_PATTERN = re.compile(
    r"(?:(?:minimum|at\s+least)\s+)?(\d+)\+?\s*(?:to|-|\s+to\s+)?\s*(?:(\d+)\+?)?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+experience|\s+professional\s+experience)?",
    re.IGNORECASE,
)

# Education indicators
EDUCATION_PATTERN = re.compile(
    r"\b(?:bachelor(?:'s)?|master(?:'s)?|ph\.?d\.?|doctorate|b\.?s\.?|m\.?s\.?|b\.?a\.?|m\.?a\.?|degree\s+in|diploma\s+in)\b",
    re.IGNORECASE,
)

# Certification indicators
CERTIFICATION_PATTERN = re.compile(
    r"\b(?:certif(?:ied|ication)|aws\s+certif\w*|pmp|cissp|cisa|cka|ckad|gcp\s+certif\w*|azure\s+certif\w*|itil|cpa)\b",
    re.IGNORECASE,
)

# Explicit must have vs preferred keywords in line
MUST_HAVE_KEYWORDS = re.compile(r"\b(?:must\s+have|must-have|required|mandatory|essential|minimum(?:\s+of)?)\b", re.IGNORECASE)
PREFERRED_KEYWORDS = re.compile(r"\b(?:preferred|nice\s+to\s+have|nice-to-have|plus|advantage|desirable|bonus|ideally)\b", re.IGNORECASE)


class JobDescriptionParser:
    def __init__(self, db: Session):
        self.db = db
        self.active_snapshot = self._get_active_snapshot()
        self.skill_cache = self._load_skill_cache() if self.active_snapshot else {}

    def _get_active_snapshot(self) -> Optional[TaxonomySnapshot]:
        stmt = select(TaxonomySnapshot).where(TaxonomySnapshot.state == TaxonomySnapshotState.ACTIVE)
        return self.db.execute(stmt).scalar_one_or_none()

    def _load_skill_cache(self) -> Dict[str, Tuple[uuid.UUID, str, str]]:
        """Preloads active skill concepts and aliases for fast deterministic phrase matching."""
        if not self.active_snapshot:
            return {}
        
        stmt = (
            select(SkillConcept)
            .options(selectinload(SkillConcept.aliases))
            .where(SkillConcept.snapshot_id == self.active_snapshot.snapshot_id)
        )
        concepts = self.db.execute(stmt).scalars().all()
        cache: Dict[str, Tuple[uuid.UUID, str, str]] = {}
        for c in concepts:
            norm = c.normalized_label
            cache[norm] = (c.concept_id, c.canonical_label, c.source)
            for a in c.aliases:
                norm_alias = a.normalized_alias
                if norm_alias not in cache:
                    cache[norm_alias] = (c.concept_id, c.canonical_label, a.alias_source)
        return cache

    def parse(self, raw_text: str) -> Dict[str, Any]:
        """
        Conservatively parses raw job description text and returns structured suggestions.
        """
        suggestions: List[Dict[str, Any]] = []
        warnings: List[Dict[str, Any]] = []

        cleaned_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = cleaned_text.split("\n")

        # 1. Section Segmentation
        sections = self._segment_sections(cleaned_text, lines)

        # 2. Job Metadata Extraction
        self._extract_job_metadata(cleaned_text, lines, sections, suggestions)

        # 3. Role Summary Extraction
        self._extract_summary(cleaned_text, sections, suggestions)

        # 4. Responsibilities Extraction
        self._extract_responsibilities(cleaned_text, sections, suggestions)

        # 5. Requirements Extraction
        self._extract_requirements(cleaned_text, sections, suggestions, warnings)

        return {
            "parser_version": PARSER_VERSION,
            "taxonomy_version": self.active_snapshot.source_version if self.active_snapshot else None,
            "suggestions": suggestions,
            "warnings": warnings,
        }

    def _segment_sections(self, raw_text: str, lines: List[str]) -> List[Dict[str, Any]]:
        """Segments lines into classified sections based on detected headings."""
        sections: List[Dict[str, Any]] = []
        current_section_type = "HEADER_OR_UNKNOWN"
        current_lines: List[Tuple[int, str, int, int]] = [] # (line_idx, text, start_offset, end_offset)

        current_offset = 0
        for idx, line in enumerate(lines):
            line_len = len(line)
            start_off = current_offset
            end_off = current_offset + line_len
            current_offset += line_len + 1  # for the \n

            stripped = line.strip()
            if not stripped:
                continue

            # Check if this line is a heading
            matched_type = None
            for s_type, pattern in HEADING_PATTERNS.items():
                if pattern.match(stripped):
                    matched_type = s_type
                    break

            if matched_type:
                if current_lines:
                    sections.append({
                        "type": current_section_type,
                        "lines": current_lines,
                    })
                current_section_type = matched_type
                current_lines = []
            else:
                current_lines.append((idx, line, start_off, end_off))

        if current_lines:
            sections.append({
                "type": current_section_type,
                "lines": current_lines,
            })

        return sections

    def _extract_job_metadata(
        self,
        raw_text: str,
        lines: List[str],
        sections: List[Dict[str, Any]],
        suggestions: List[Dict[str, Any]],
    ) -> None:
        # Check first 5 lines for explicit prefixes or title
        first_section_lines = sections[0]["lines"] if sections else []
        candidate_header_lines = first_section_lines[:6]

        title_found = False
        for idx, line, start_off, end_off in candidate_header_lines:
            stripped = line.strip()
            # Explicit title prefix
            m_title = TITLE_PREFIX_PATTERN.match(stripped)
            if m_title:
                val = m_title.group(1).strip()
                if val and not title_found:
                    suggestions.append({
                        "suggestion_id": str(uuid.uuid4()),
                        "target_field": "TITLE",
                        "proposed_value": val,
                        "source_excerpt": stripped,
                        "start_offset": start_off,
                        "end_offset": end_off,
                        "extraction_method": "PATTERN_MATCH",
                        "confidence": "HIGH",
                        "review_status": "PENDING",
                    })
                    title_found = True
                    continue

            # Explicit department
            m_dept = DEPT_PREFIX_PATTERN.match(stripped)
            if m_dept:
                suggestions.append({
                    "suggestion_id": str(uuid.uuid4()),
                    "target_field": "DEPARTMENT",
                    "proposed_value": m_dept.group(1).strip(),
                    "source_excerpt": stripped,
                    "start_offset": start_off,
                    "end_offset": end_off,
                    "extraction_method": "PATTERN_MATCH",
                    "confidence": "HIGH",
                    "review_status": "PENDING",
                })
                continue

            # Explicit location
            m_loc = LOCATION_PREFIX_PATTERN.match(stripped)
            if m_loc:
                suggestions.append({
                    "suggestion_id": str(uuid.uuid4()),
                    "target_field": "LOCATION",
                    "proposed_value": m_loc.group(1).strip(),
                    "source_excerpt": stripped,
                    "start_offset": start_off,
                    "end_offset": end_off,
                    "extraction_method": "PATTERN_MATCH",
                    "confidence": "HIGH",
                    "review_status": "PENDING",
                })
                continue

            # Explicit openings
            m_open = OPENINGS_PREFIX_PATTERN.match(stripped)
            if m_open:
                try:
                    val_int = int(m_open.group(1))
                    if val_int > 0:
                        suggestions.append({
                            "suggestion_id": str(uuid.uuid4()),
                            "target_field": "OPENINGS_COUNT",
                            "proposed_value": val_int,
                            "source_excerpt": stripped,
                            "start_offset": start_off,
                            "end_offset": end_off,
                            "extraction_method": "PATTERN_MATCH",
                            "confidence": "HIGH",
                            "review_status": "PENDING",
                        })
                except ValueError:
                    pass

        # If no explicit title found, check first non-empty line if it looks like a clean title
        if not title_found and candidate_header_lines:
            first_idx, first_line, first_start, first_end = candidate_header_lines[0]
            clean_first = first_line.strip().lstrip("#").strip()
            words = clean_first.split()
            if 1 <= len(words) <= 7 and not clean_first.endswith(":") and len(clean_first) < 80:
                # Conservative title suggestion
                suggestions.append({
                    "suggestion_id": str(uuid.uuid4()),
                    "target_field": "TITLE",
                    "proposed_value": clean_first,
                    "source_excerpt": first_line.strip(),
                    "start_offset": first_start,
                    "end_offset": first_end,
                    "extraction_method": "HEADING_BOUNDED",
                    "confidence": "MEDIUM",
                    "review_status": "PENDING",
                })

        # Scan full text for explicit Employment Type & Workplace Type
        for pattern, emp_type in EMPLOYMENT_TYPES:
            m = pattern.search(raw_text)
            if m:
                suggestions.append({
                    "suggestion_id": str(uuid.uuid4()),
                    "target_field": "EMPLOYMENT_TYPE",
                    "proposed_value": emp_type.value,
                    "source_excerpt": m.group(0),
                    "start_offset": m.start(),
                    "end_offset": m.end(),
                    "extraction_method": "PATTERN_MATCH",
                    "confidence": "HIGH",
                    "review_status": "PENDING",
                })
                break

        for pattern, wp_type in WORKPLACE_TYPES:
            m = pattern.search(raw_text)
            if m:
                suggestions.append({
                    "suggestion_id": str(uuid.uuid4()),
                    "target_field": "WORKPLACE_TYPE",
                    "proposed_value": wp_type.value,
                    "source_excerpt": m.group(0),
                    "start_offset": m.start(),
                    "end_offset": m.end(),
                    "extraction_method": "PATTERN_MATCH",
                    "confidence": "HIGH",
                    "review_status": "PENDING",
                })
                break

    def _extract_summary(
        self,
        raw_text: str,
        sections: List[Dict[str, Any]],
        suggestions: List[Dict[str, Any]],
    ) -> None:
        for s in sections:
            if s["type"] == "SUMMARY":
                lines_text = [l[1].strip() for l in s["lines"] if l[1].strip()]
                if lines_text:
                    summary_text = " ".join(lines_text)
                    start_off = s["lines"][0][2]
                    end_off = s["lines"][-1][3]
                    suggestions.append({
                        "suggestion_id": str(uuid.uuid4()),
                        "target_field": "SUMMARY",
                        "proposed_value": summary_text[:10000],
                        "source_excerpt": summary_text[:300],
                        "start_offset": start_off,
                        "end_offset": end_off,
                        "extraction_method": "HEADING_BOUNDED",
                        "confidence": "HIGH",
                        "review_status": "PENDING",
                    })

    def _extract_responsibilities(
        self,
        raw_text: str,
        sections: List[Dict[str, Any]],
        suggestions: List[Dict[str, Any]],
    ) -> None:
        for s in sections:
            if s["type"] == "RESPONSIBILITIES":
                for idx, line_text, start_off, end_off in s["lines"]:
                    cleaned = self._clean_bullet_item(line_text)
                    if cleaned and len(cleaned) >= 5:
                        suggestions.append({
                            "suggestion_id": str(uuid.uuid4()),
                            "target_field": "RESPONSIBILITY",
                            "proposed_value": cleaned,
                            "source_excerpt": line_text.strip(),
                            "start_offset": start_off,
                            "end_offset": end_off,
                            "extraction_method": "BULLET_ITEM",
                            "confidence": "HIGH",
                            "review_status": "PENDING",
                        })

    def _extract_requirements(
        self,
        raw_text: str,
        sections: List[Dict[str, Any]],
        suggestions: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
    ) -> None:
        req_sections = [
            s for s in sections
            if s["type"] in ("REQUIRED_QUALIFICATIONS", "PREFERRED_QUALIFICATIONS", "GENERAL_QUALIFICATIONS")
        ]

        for s in req_sections:
            section_type = s["type"]
            for idx, line_text, start_off, end_off in s["lines"]:
                cleaned_item = self._clean_bullet_item(line_text)
                if not cleaned_item or len(cleaned_item) < 3:
                    continue

                # 1. Protected / Sensitive information detection
                sensitive_found = False
                for pattern_str, bias_code in SENSITIVE_PATTERNS:
                    sensitive_match = re.search(pattern_str, cleaned_item, re.IGNORECASE)
                    if sensitive_match:
                        warnings.append({
                            "code": "POTENTIALLY_SENSITIVE_CRITERIA",
                            "rule": bias_code,
                            "matched_term": sensitive_match.group(0),
                            "message": f"Detected potentially sensitive term '{sensitive_match.group(0)}' in source text. It has been excluded from structured requirements.",
                            "source_excerpt": line_text.strip(),
                            "start_offset": start_off,
                            "end_offset": end_off,
                        })
                        sensitive_found = True
                        break
                if sensitive_found:
                    # Do not automatically convert to structured requirement
                    continue

                # 2. Determine Necessity (MUST_HAVE, PREFERRED, or UNDECIDED)
                necessity: Optional[RequirementNecessity] = None
                if section_type == "REQUIRED_QUALIFICATIONS" or MUST_HAVE_KEYWORDS.search(cleaned_item):
                    necessity = RequirementNecessity.MUST_HAVE
                elif section_type == "PREFERRED_QUALIFICATIONS" or PREFERRED_KEYWORDS.search(cleaned_item):
                    necessity = RequirementNecessity.PREFERRED
                else:
                    # General qualifications without explicit indicator -> Leave undecided (recruiter must choose)
                    necessity = None

                # 3. Numeric Experience extraction
                min_exp_months = None
                exp_match = EXPERIENCE_NUMERIC_PATTERN.search(cleaned_item)
                if exp_match:
                    try:
                        years_val = int(exp_match.group(1))
                        if years_val > 0:
                            min_exp_months = years_val * 12
                    except ValueError:
                        pass

                # 4. Category Classification
                category = RequirementCategory.OTHER
                if EDUCATION_PATTERN.search(cleaned_item):
                    category = RequirementCategory.EDUCATION
                elif CERTIFICATION_PATTERN.search(cleaned_item):
                    category = RequirementCategory.CERTIFICATION
                elif min_exp_months and not self._contains_known_skill(cleaned_item):
                    category = RequirementCategory.EXPERIENCE
                else:
                    # Check if there are technical skill phrases
                    matched_skills = self._match_skills(cleaned_item)
                    if matched_skills:
                        category = RequirementCategory.SKILL
                    else:
                        category = RequirementCategory.SKILL if len(cleaned_item) < 50 else RequirementCategory.OTHER

                # 5. Build Suggestion(s)
                # If multiple discrete skills are listed in one bullet (e.g. "Experience with Python and PostgreSQL"),
                # generate distinct structured skill requirements when clearly matched.
                matched_skills = self._match_skills(cleaned_item) if category == RequirementCategory.SKILL else []

                if matched_skills and len(matched_skills) > 1 and not min_exp_months:
                    # Multi-skill bullet
                    for sk_concept_id, sk_label, sk_source in matched_skills:
                        suggestions.append({
                            "suggestion_id": str(uuid.uuid4()),
                            "target_field": "REQUIREMENT",
                            "proposed_value": {
                                "label": sk_label,
                                "category": RequirementCategory.SKILL.value,
                                "necessity": necessity.value if necessity else None,
                                "priority": RequirementPriority.HIGH.value if necessity == RequirementNecessity.MUST_HAVE else RequirementPriority.MEDIUM.value,
                                "minExperienceMonths": None,
                                "taxonomySkillConceptId": str(sk_concept_id) if sk_concept_id else None,
                                "taxonomyLabel": sk_label,
                                "taxonomySource": sk_source,
                                "origin": "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED" if sk_concept_id else "RECRUITER_ENTERED",
                                "acceptedEvidenceTypes": [RequirementEvidenceType.EMPLOYMENT.value, RequirementEvidenceType.PROJECT.value],
                                "acceptedEquivalents": [],
                                "rationale": None,  # NEVER invent rationale
                            },
                            "source_excerpt": line_text.strip(),
                            "start_offset": start_off,
                            "end_offset": end_off,
                            "extraction_method": "TAXONOMY_MATCH",
                            "confidence": "HIGH",
                            "review_status": "PENDING",
                        })
                else:
                    # Single requirement suggestion
                    top_skill = matched_skills[0] if matched_skills else None
                    label_to_use = top_skill[1] if (top_skill and len(cleaned_item) < 40) else cleaned_item

                    suggestions.append({
                        "suggestion_id": str(uuid.uuid4()),
                        "target_field": "REQUIREMENT",
                        "proposed_value": {
                            "label": label_to_use,
                            "category": category.value,
                            "necessity": necessity.value if necessity else None,
                            "priority": RequirementPriority.HIGH.value if necessity == RequirementNecessity.MUST_HAVE else RequirementPriority.MEDIUM.value,
                            "minExperienceMonths": min_exp_months,
                            "taxonomySkillConceptId": str(top_skill[0]) if top_skill else None,
                            "taxonomyLabel": top_skill[1] if top_skill else None,
                            "taxonomySource": top_skill[2] if top_skill else None,
                            "origin": "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED" if top_skill else "RECRUITER_ENTERED",
                            "acceptedEvidenceTypes": [RequirementEvidenceType.EMPLOYMENT.value, RequirementEvidenceType.PROJECT.value],
                            "acceptedEquivalents": [],
                            "rationale": None,  # NEVER invent rationale
                        },
                        "source_excerpt": line_text.strip(),
                        "start_offset": start_off,
                        "end_offset": end_off,
                        "extraction_method": "TAXONOMY_MATCH" if top_skill else "BULLET_ITEM",
                        "confidence": "HIGH" if top_skill else "MEDIUM",
                        "review_status": "PENDING",
                    })

    def _clean_bullet_item(self, line: str) -> str:
        """Strips leading bullet characters (-, *, •, numbers) and whitespace."""
        s = line.strip()
        s = re.sub(r"^[\s\-\*\•\–\—\>\#\+\d\.\)\(\]]+\s*", "", s).strip()
        return s

    def _contains_known_skill(self, text: str) -> bool:
        return bool(self._match_skills(text))

    def _match_skills(self, text: str) -> List[Tuple[uuid.UUID, str, str]]:
        """Matches words / tokens against active O*NET skill taxonomy cache."""
        if not self.skill_cache:
            return []

        norm_text = normalize_search_query(text)
        raw_tokens = re.findall(r"[a-z0-9\+\#\.\-]+", norm_text)
        tokens = [t.strip(".,;:!?()[]{}'\"") for t in raw_tokens if t.strip(".,;:!?()[]{}'\"")]

        matched: List[Tuple[uuid.UUID, str, str]] = []
        seen_concepts: Set[uuid.UUID] = set()

        # Check 1-word, 2-word, 3-word n-grams
        n = len(tokens)
        for length in (3, 2, 1):
            for i in range(n - length + 1):
                ngram = " ".join(tokens[i : i + length])
                if ngram in self.skill_cache:
                    concept_id, canonical_label, source = self.skill_cache[ngram]
                    if concept_id not in seen_concepts:
                        # Prevent job titles from matching as skills
                        if canonical_label.lower() not in ("developer", "engineer", "manager", "architect"):
                            seen_concepts.add(concept_id)
                            matched.append((concept_id, canonical_label, source))

        return matched
