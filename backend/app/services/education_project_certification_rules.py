import re
from typing import Dict, List, Optional, Set, Tuple

from app.schemas.education_project_certification import DegreeLevel

EDUCATION_PROJECT_CERTIFICATION_RULES_VERSION = "ed-proj-cert-lang-rules-v1.2"

# =============================================================================
# 1. EDUCATION RULES & PATTERNS
# =============================================================================

# Common degree patterns and mapping to DegreeLevel and canonical names
# Formatted as (Regex Pattern, DegreeLevel, Canonical Degree Name)
DEGREE_DEFINITIONS: List[Tuple[re.Pattern, DegreeLevel, str]] = [
    # Ph.D. / Doctor of Philosophy / Doctorate
    (
        re.compile(
            r"\b(?:Ph\.?D\.?|Doctor of Philosophy|Doctorate|D\.Phil\.?)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.DOCTORATE,
        "Doctor of Philosophy",
    ),
    # M.Tech / Master of Technology
    (
        re.compile(
            r"\b(?:M\.?Tech\.?|Master of Technology)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master of Technology",
    ),
    # M.S. / M.Sc. / Master of Science
    (
        re.compile(
            r"\b(?:M\.?Sc\.?|M\.?S\.?|Master of Science)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master of Science",
    ),
    # M.E. / Master of Engineering
    (
        re.compile(
            r"\b(?:M\.?E\.?|Master of Engineering)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master of Engineering",
    ),
    # MCA / Master of Computer Applications
    (
        re.compile(
            r"\b(?:M\.?C\.?A\.?|Master of Computer Applications)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master of Computer Applications",
    ),
    # MBA / Master of Business Administration
    (
        re.compile(
            r"\b(?:M\.?B\.?A\.?|Master of Business Administration)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master of Business Administration",
    ),
    # Master's (generic) / Masters Degree
    (
        re.compile(
            r"\b(?:Master's Degree|Master of Arts|M\.?A\.?|Master(?:'s)?)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.MASTERS,
        "Master's Degree",
    ),
    # B.Tech / Bachelor of Technology
    (
        re.compile(
            r"\b(?:B\.?Tech\.?|Bachelor of Technology)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Technology",
    ),
    # B.E. / Bachelor of Engineering
    (
        re.compile(
            r"\b(?:B\.?E\.?|Bachelor of Engineering)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Engineering",
    ),
    # B.S. / B.Sc. / Bachelor of Science
    (
        re.compile(
            r"\b(?:B\.?Sc\.?|B\.?S\.?|Bachelor of Science)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Science",
    ),
    # BCA / Bachelor of Computer Applications
    (
        re.compile(
            r"\b(?:B\.?C\.?A\.?|Bachelor of Computer Applications)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Computer Applications",
    ),
    # BBA / Bachelor of Business Administration
    (
        re.compile(
            r"\b(?:B\.?B\.?A\.?|Bachelor of Business Administration)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Business Administration",
    ),
    # B.Com / Bachelor of Commerce
    (
        re.compile(
            r"\b(?:B\.?Com(?:\.|\b)|Bachelor of Commerce)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor of Commerce",
    ),
    # Bachelor's (generic) / Bachelor of Arts
    (
        re.compile(
            r"\b(?:Bachelor's Degree|Bachelor of Arts|B\.?A\.?|Bachelor(?:'s)?)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.BACHELORS,
        "Bachelor's Degree",
    ),
    # Diploma / Polytechnic
    (
        re.compile(
            r"\b(?:Diploma|Polytechnic Diploma|Advanced Diploma|Graduate Diploma)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.DIPLOMA,
        "Diploma",
    ),
    # Associate Degree
    (
        re.compile(
            r"\b(?:Associate Degree|Associate of Arts|Associate of Science|A\.?A\.?|A\.?S\.?)\b",
            re.IGNORECASE,
        ),
        DegreeLevel.ASSOCIATE,
        "Associate Degree",
    ),
]

# Words that indicate an institution name
INSTITUTION_INDICATORS: Set[str] = {
    "university", "institute", "college", "school", "academy", "polytechnic",
    "faculty", "campus", "dept", "department", "center", "centre", "conservatory",
    "seminary",
}

# Major / Field of Study prefixes
FIELD_OF_STUDY_PREFIX_RE = re.compile(
    r"\b(?:in|major in|specialization in|specialized in|majoring in|emphasis in|field of|discipline of)\s+([A-Za-z0-9\s&,/-]+?)(?=(?:\s*[,;|—–\n]|\s+(?:from|at|with|gpa|cgpa|\([0-9]|19\d\d|20\d\d)|$))",
    re.IGNORECASE,
)

# Grade / GPA patterns
GRADE_RE = re.compile(
    r"\b(?:GPA|CGPA|Grade|Marks|Percentage|Score)?\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?\s*/\s*[0-9]+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?\s*%|(?:First Class with Distinction|First Class|Second Class|Distinction|Honors|Cum Laude|Magna Cum Laude|Summa Cum Laude))\b",
    re.IGNORECASE,
)

# Check if text looks like an online training course / certification instead of a formal education record
DISQUALIFYING_EDUCATION_KEYWORDS = {
    "udemy", "coursera", "edx", "pluralsight", "linkedin learning", "bootcamp course",
    "certificate of completion", "nanodegree", "certification", "authorized trainer",
}


# =============================================================================
# 2. PROJECT RULES & PATTERNS
# =============================================================================

# Words/Phrases that clearly indicate project boundaries or headings
PROJECT_TITLE_DISQUALIFIERS = {
    "summary", "skills", "experience", "education", "certifications", "languages",
    "awards", "publications", "contact", "references", "profile", "overview",
}

PROJECT_URL_RE = re.compile(
    r"\b(?:https?://|www\.|github\.com/)[^\s<>{}\[\]|\"^`\\]+",
    re.IGNORECASE,
)


# =============================================================================
# 3. CERTIFICATION RULES & PATTERNS
# =============================================================================

CERTIFICATION_LABEL_PATTERNS = {
    "issuer": re.compile(r"\b(?:Issued\s+by|Issuer|Organization|Authority|Provider)\s*[:\-–—]\s*([^\n,;]+)", re.IGNORECASE),
    "issued_date": re.compile(r"\b(?:Issued|Issue Date|Date Issued|Obtained)\s*[:\-–—]\s*([^\n,;]+)", re.IGNORECASE),
    "expiry_date": re.compile(r"\b(?:Expires|Expiry Date|Expiration Date|Valid Until|Valid Through)\s*[:\-–—]\s*([^\n,;]+)", re.IGNORECASE),
    "credential_id": re.compile(r"\b(?:Credential\s+ID|License\s+(?:Number|ID|#)|ID|Certificate\s+ID|Verification\s+ID)\s*[:\-–—]\s*([A-Za-z0-9_\-\.\/]+)", re.IGNORECASE),
    "credential_url": re.compile(r"\b(?:Credential\s+URL|URL|Verify\s+at|Verification\s+Link)\s*[:\-–—]\s*([^\s<>{}\[\]|\"^`\\]+)", re.IGNORECASE),
}

# Known major certification authorities to recognize when unlabelled
KNOWN_CERT_ISSUERS = {
    "aws": "Amazon Web Services",
    "amazon web services": "Amazon Web Services",
    "google cloud": "Google Cloud",
    "google": "Google",
    "microsoft": "Microsoft",
    "azure": "Microsoft",
    "cisco": "Cisco",
    "oracle": "Oracle",
    "red hat": "Red Hat",
    "hashicorp": "HashiCorp",
    "kubernetes": "Cloud Native Computing Foundation",
    "cncf": "Cloud Native Computing Foundation",
    "comptia": "CompTIA",
    "pmi": "Project Management Institute",
    "scrum alliance": "Scrum Alliance",
    "scrum.org": "Scrum.org",
    "salesforce": "Salesforce",
    "databricks": "Databricks",
    "meta": "Meta",
    "ibm": "IBM",
}


# =============================================================================
# 4. NATURAL LANGUAGE RULES & PROFICIENCY MAPPINGS
# =============================================================================

# Known natural languages (Human Languages)
NATURAL_LANGUAGES: Dict[str, str] = {
    "english": "English",
    "spanish": "Spanish",
    "espanol": "Spanish",
    "french": "French",
    "francais": "French",
    "german": "German",
    "deutsch": "German",
    "mandarin": "Mandarin",
    "chinese": "Chinese",
    "cantonese": "Cantonese",
    "hindi": "Hindi",
    "arabic": "Arabic",
    "bengali": "Bengali",
    "portuguese": "Portuguese",
    "russian": "Russian",
    "japanese": "Japanese",
    "punjabi": "Punjabi",
    "marathi": "Marathi",
    "telugu": "Telugu",
    "turkish": "Turkish",
    "tamil": "Tamil",
    "vietnamese": "Vietnamese",
    "korean": "Korean",
    "italian": "Italian",
    "gujarati": "Gujarati",
    "urdu": "Urdu",
    "polish": "Polish",
    "ukrainian": "Ukrainian",
    "dutch": "Dutch",
    "greek": "Greek",
    "swedish": "Swedish",
    "hebrew": "Hebrew",
    "danish": "Danish",
    "norwegian": "Norwegian",
    "finnish": "Finnish",
    "czech": "Czech",
    "hungarian": "Hungarian",
    "thai": "Thai",
    "indonesian": "Indonesian",
    "malay": "Malay",
    "filipino": "Filipino",
    "tagalog": "Tagalog",
    "persian": "Persian",
    "farsi": "Persian",
    "latin": "Latin",
    "sanskrit": "Sanskrit",
    "swahili": "Swahili",
    "kannada": "Kannada",
    "malayalam": "Malayalam",
    "oriya": "Odia",
    "odia": "Odia",
}

# Programming languages and technologies to strictly reject as natural languages
PROGRAMMING_LANGUAGES_REJECT_LIST: Set[str] = {
    "python", "java", "c", "c++", "c#", "javascript", "typescript", "ruby", "rust",
    "golang", "go", "php", "swift", "kotlin", "scala", "dart", "r", "matlab", "perl",
    "html", "css", "sql", "nosql", "bash", "shell", "powershell", "assembly", "fortran",
    "cobol", "vba", "groovy", "lua", "haskell", "elixir", "clojure", "erlang", "objective-c",
}

# Standard human language proficiency phrases mapping to normalized levels
# Level strings: "NATIVE", "FLUENT", "PROFESSIONAL_WORKING", "INTERMEDIATE", "ELEMENTARY"
PROFICIENCY_MAPPINGS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"\b(?:native(?:\s+proficiency)?(?:\s+or\s+bilingual)?|mother\s+tongue|first\s+language)\b", re.IGNORECASE),
        "NATIVE",
    ),
    (
        re.compile(r"\b(?:fluent|full\s+professional|bilingual|proficient)\b", re.IGNORECASE),
        "FLUENT",
    ),
    (
        re.compile(r"\b(?:professional(?:\s+working)?(?:\s+proficiency)?|working\s+proficiency|advanced|conversational)\b", re.IGNORECASE),
        "PROFESSIONAL_WORKING",
    ),
    (
        re.compile(r"\b(?:intermediate|moderate|competent)\b", re.IGNORECASE),
        "INTERMEDIATE",
    ),
    (
        re.compile(r"\b(?:basic|elementary|beginner|limited\s+working(?:\s+proficiency)?|rudimentary)\b", re.IGNORECASE),
        "ELEMENTARY",
    ),
]
