"""Phase 3D.2: Deterministic Answer Segmentation Service.

Extracts immutable, verifiable text segments with exact character offsets
from submitted simulation answers for fine-grained provenance binding.
"""

import re
import uuid
import logging
from typing import List, Tuple
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from app.models.interview import (
    SimulationAnswerRecord,
    SimulationAnswerSegmentRecord,
)

logger = logging.getLogger("ai_hiring.segmentation")

SEGMENTATION_VERSION: str = "1.0.0"
MIN_MEANINGFUL_ANSWER_LENGTH: int = 10
LONG_PARAGRAPH_THRESHOLD: int = 300

# Regex for sentence splitting: matches sentence terminal punctuation followed by whitespace
SENTENCE_SPLIT_REGEX = re.compile(r'([.!?]+(?:\s+|$))')


def extract_segments_from_text(
    text: str,
    answer_id: uuid.UUID,
    attempt_id: uuid.UUID,
    question_id: uuid.UUID,
) -> List[SimulationAnswerSegmentRecord]:
    """Deterministically parses raw answer text into ordered segments with exact character offsets.
    
    Rules:
    - Splits text on paragraph boundaries (two or more newlines).
    - If a paragraph exceeds LONG_PARAGRAPH_THRESHOLD (300 chars) and contains multiple sentences,
      splits into individual sentences while preserving exact character slice bounds in the original string.
    - Strips leading/trailing whitespace from segment boundaries while updating offsets accurately.
    - Ignores whitespace-only fragments.
    - Assigns deterministic, opaque segment UUIDs derived from namespace + coordinates.
    """
    if not text or len(text.strip()) < MIN_MEANINGFUL_ANSWER_LENGTH:
        return []

    # 1. Identify raw paragraph spans [start, end]
    raw_paragraphs: List[Tuple[int, int]] = []
    para_pattern = re.compile(r'[^\r\n]+(?:\r?\n[^\r\n]+)*')
    for match in para_pattern.finditer(text):
        p_start, p_end = match.span()
        if text[p_start:p_end].strip():
            raw_paragraphs.append((p_start, p_end))

    if not raw_paragraphs:
        return []

    # 2. Process paragraphs and split long ones into sentences
    candidate_spans: List[Tuple[int, int]] = []
    for p_start, p_end in raw_paragraphs:
        para_text = text[p_start:p_end]
        if len(para_text) > LONG_PARAGRAPH_THRESHOLD:
            # Split sentences within this paragraph
            last_idx = 0
            for sent_match in SENTENCE_SPLIT_REGEX.finditer(para_text):
                sent_end_rel = sent_match.end()
                sent_text = para_text[last_idx:sent_end_rel].strip()
                if sent_text:
                    # Find exact substring start/end within original text
                    rel_start = para_text.find(sent_text, last_idx)
                    abs_start = p_start + rel_start
                    abs_end = abs_start + len(sent_text)
                    candidate_spans.append((abs_start, abs_end))
                last_idx = sent_end_rel
            # Any remaining text in paragraph after last sentence boundary
            remaining = para_text[last_idx:].strip()
            if remaining:
                rel_start = para_text.find(remaining, last_idx)
                abs_start = p_start + rel_start
                abs_end = abs_start + len(remaining)
                candidate_spans.append((abs_start, abs_end))
        else:
            # Paragraph as a single segment, trimmed
            trimmed = para_text.strip()
            if trimmed:
                rel_start = para_text.find(trimmed)
                abs_start = p_start + rel_start
                abs_end = abs_start + len(trimmed)
                candidate_spans.append((abs_start, abs_end))

    # 3. Build records with 1-indexed sequence order and stable opaque UUID
    records: List[SimulationAnswerSegmentRecord] = []
    for seq_idx, (c_start, c_end) in enumerate(candidate_spans, start=1):
        seg_text = text[c_start:c_end]
        if not seg_text.strip():
            continue

        # Stable opaque UUID deterministic on answer_id, seq, start_char, end_char
        seed = f"{answer_id}:{seq_idx}:{c_start}:{c_end}:{SEGMENTATION_VERSION}"
        segment_id = uuid.uuid5(uuid.NAMESPACE_DNS, seed)

        rec = SimulationAnswerSegmentRecord(
            segment_id=segment_id,
            answer_id=answer_id,
            attempt_id=attempt_id,
            question_id=question_id,
            sequence_order=seq_idx,
            start_char=c_start,
            end_char=c_end,
            segment_text=seg_text,
            segmentation_version=SEGMENTATION_VERSION,
        )
        records.append(rec)

    return records


def segment_submitted_answer(
    db: Session,
    answer: SimulationAnswerRecord,
) -> List[SimulationAnswerSegmentRecord]:
    """Idempotently segments a submitted answer, storing records and returning them.
    
    If the answer text is empty or too short, returns an empty list.
    """
    # Verify answer is submitted
    if not answer.is_submitted:
        logger.warning("Attempted to segment unsubmitted answer '%s'", answer.answer_id)
        return []

    # Check for existing segments
    stmt = (
        select(SimulationAnswerSegmentRecord)
        .where(SimulationAnswerSegmentRecord.answer_id == answer.answer_id)
        .order_by(SimulationAnswerSegmentRecord.sequence_order)
    )
    existing = list(db.execute(stmt).scalars().all())

    # Generate expected segments
    expected = extract_segments_from_text(
        text=answer.answer_text,
        answer_id=answer.answer_id,
        attempt_id=answer.attempt_id,
        question_id=answer.question_id,
    )

    if not expected:
        # Clear any stale segments if text was emptied
        if existing:
            db.execute(
                delete(SimulationAnswerSegmentRecord).where(
                    SimulationAnswerSegmentRecord.answer_id == answer.answer_id
                )
            )
            db.flush()
        return []

    # If existing segments match expected count and version, return existing
    if len(existing) == len(expected) and all(
        e.start_char == exp.start_char and e.end_char == exp.end_char and e.segment_id == exp.segment_id
        for e, exp in zip(existing, expected)
    ):
        return existing

    # Otherwise, replace existing segments with newly calculated ones
    db.execute(
        delete(SimulationAnswerSegmentRecord).where(
            SimulationAnswerSegmentRecord.answer_id == answer.answer_id
        )
    )
    db.flush()

    for seg in expected:
        db.add(seg)
    db.flush()

    return expected
