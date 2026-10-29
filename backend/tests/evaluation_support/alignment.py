"""
Deterministic one-to-one record alignment for structured resume records.
Ensures an expected record matches at most one predicted record and vice-versa,
using deterministic score-maximizing assignment without heavy third-party dependencies.
"""
from typing import Any, Callable, Dict, Generic, List, Optional, Tuple, TypeVar

from .normalization import compare_dates, compare_scalar, normalize_scalar_text

T_Exp = TypeVar("T_Exp")
T_Pred = TypeVar("T_Pred")


def score_employment_pair(expected: Any, predicted: Any) -> float:
    """
    Score similarity between expected employment record and predicted employment record.
    Score range: 0.0 to 4.0 based on:
    - job_title (1.0)
    - organization (1.0)
    - start_date (1.0)
    - end_date (1.0)
    """
    score = 0.0
    # Job title
    exp_title = getattr(expected.job_title, "value", None) or getattr(expected.job_title, "normalized_value", None)
    pred_title = getattr(predicted.job_title, "value", None)
    if exp_title and pred_title:
        if normalize_scalar_text(exp_title) == normalize_scalar_text(pred_title):
            score += 1.0
        elif (
            normalize_scalar_text(exp_title) in normalize_scalar_text(pred_title)
            or normalize_scalar_text(pred_title) in normalize_scalar_text(exp_title)
        ):
            score += 0.5

    # Organization
    exp_org = getattr(expected.organization, "value", None) or getattr(expected.organization, "normalized_value", None)
    pred_org = getattr(predicted.organization, "value", None)
    if exp_org and pred_org:
        if normalize_scalar_text(exp_org) == normalize_scalar_text(pred_org):
            score += 1.0
        elif (
            normalize_scalar_text(exp_org) in normalize_scalar_text(pred_org)
            or normalize_scalar_text(pred_org) in normalize_scalar_text(exp_org)
        ):
            score += 0.5

    # Start date
    exp_start = getattr(expected.start_date, "normalized_value", None) or getattr(expected.start_date, "value", None)
    pred_start = getattr(predicted.start_date, "normalized_value", None) or getattr(predicted.start_date, "value", None)
    if exp_start and pred_start and compare_dates(exp_start, pred_start):
        score += 1.0

    # End date
    exp_end = getattr(expected.end_date, "normalized_value", None) or getattr(expected.end_date, "value", None)
    pred_end = getattr(predicted.end_date, "normalized_value", None) or getattr(predicted.end_date, "value", None)
    if exp_end and pred_end and compare_dates(exp_end, pred_end):
        score += 1.0

    return score


def score_education_pair(expected: Any, predicted: Any) -> float:
    """
    Score similarity between expected education record and predicted education record.
    Score range: 0.0 to 3.0 based on:
    - degree (1.0)
    - institution (1.0)
    - field_of_study (1.0)
    """
    score = 0.0
    # Degree
    exp_deg = getattr(expected.degree, "value", None) or getattr(expected.degree, "normalized_value", None)
    pred_deg = getattr(predicted.degree, "value", None)
    if exp_deg and pred_deg:
        if normalize_scalar_text(exp_deg) == normalize_scalar_text(pred_deg):
            score += 1.0
        elif (
            normalize_scalar_text(exp_deg) in normalize_scalar_text(pred_deg)
            or normalize_scalar_text(pred_deg) in normalize_scalar_text(exp_deg)
        ):
            score += 0.5

    # Institution
    exp_inst = getattr(expected.institution, "value", None) or getattr(expected.institution, "normalized_value", None)
    pred_inst = getattr(predicted.institution, "value", None)
    if exp_inst and pred_inst:
        if normalize_scalar_text(exp_inst) == normalize_scalar_text(pred_inst):
            score += 1.0
        elif (
            normalize_scalar_text(exp_inst) in normalize_scalar_text(pred_inst)
            or normalize_scalar_text(pred_inst) in normalize_scalar_text(exp_inst)
        ):
            score += 0.5

    # Field of study
    exp_field = getattr(expected, "field_of_study", None)
    exp_field_val = (
        getattr(exp_field, "value", None)
        if exp_field
        else None
    )
    pred_field = getattr(predicted, "field_of_study", None)
    pred_field_val = getattr(pred_field, "value", None) if pred_field else None
    if exp_field_val and pred_field_val:
        if normalize_scalar_text(exp_field_val) == normalize_scalar_text(pred_field_val):
            score += 1.0

    return score


def score_project_pair(expected: Any, predicted: Any) -> float:
    """
    Score similarity between expected project record and predicted project record.
    Score range: 0.0 to 2.0 based on:
    - name (1.0)
    - description / overlap (1.0)
    """
    score = 0.0
    exp_name = getattr(expected.name, "value", None) or getattr(expected.name, "normalized_value", None)
    pred_name = getattr(predicted.name, "value", None)
    if exp_name and pred_name:
        if normalize_scalar_text(exp_name) == normalize_scalar_text(pred_name):
            score += 1.0
        elif (
            normalize_scalar_text(exp_name) in normalize_scalar_text(pred_name)
            or normalize_scalar_text(pred_name) in normalize_scalar_text(exp_name)
        ):
            score += 0.5

    exp_desc = getattr(expected, "description", None)
    exp_desc_val = getattr(exp_desc, "value", None) if exp_desc else None
    pred_desc = getattr(predicted, "description", None)
    pred_desc_val = getattr(pred_desc, "value", None) if pred_desc else None
    if exp_desc_val and pred_desc_val:
        if normalize_scalar_text(exp_desc_val) == normalize_scalar_text(pred_desc_val):
            score += 1.0
        elif len(exp_desc_val) > 20 and normalize_scalar_text(exp_desc_val)[:30] in normalize_scalar_text(pred_desc_val):
            score += 0.5

    return score


def score_certification_pair(expected: Any, predicted: Any) -> float:
    """
    Score similarity between expected certification record and predicted certification record.
    Score range: 0.0 to 2.0 based on:
    - name (1.0)
    - issuer (1.0)
    """
    score = 0.0
    exp_name = getattr(expected.name, "value", None) or getattr(expected.name, "normalized_value", None)
    pred_name = getattr(predicted.name, "value", None)
    if exp_name and pred_name:
        if normalize_scalar_text(exp_name) == normalize_scalar_text(pred_name):
            score += 1.0
        elif (
            normalize_scalar_text(exp_name) in normalize_scalar_text(pred_name)
            or normalize_scalar_text(pred_name) in normalize_scalar_text(exp_name)
        ):
            score += 0.5

    exp_issuer = getattr(expected.issuer, "value", None) or getattr(expected.issuer, "normalized_value", None)
    pred_issuer = getattr(predicted.issuer, "value", None)
    if exp_issuer and pred_issuer:
        if normalize_scalar_text(exp_issuer) == normalize_scalar_text(pred_issuer):
            score += 1.0
        elif (
            normalize_scalar_text(exp_issuer) in normalize_scalar_text(pred_issuer)
            or normalize_scalar_text(pred_issuer) in normalize_scalar_text(exp_issuer)
        ):
            score += 0.5

    return score


def align_records_one_to_one(
    expected_list: List[T_Exp],
    predicted_list: List[T_Pred],
    scorer: Callable[[T_Exp, T_Pred], float],
    min_score_threshold: float = 0.5,
) -> Tuple[List[Tuple[T_Exp, T_Pred]], List[T_Exp], List[T_Pred]]:
    """
    Deterministic 1-to-1 maximum score alignment.
    Returns:
    - matched_pairs: List of (expected, predicted) tuples
    - unmatched_expected: List of expected records that had no predicted match
    - unmatched_predicted: List of predicted records that had no expected match
    """
    if not expected_list and not predicted_list:
        return [], [], []
    if not expected_list:
        return [], [], list(predicted_list)
    if not predicted_list:
        return [], list(expected_list), []

    # Compute all pair candidate scores
    candidates = []
    for exp_idx, exp in enumerate(expected_list):
        for pred_idx, pred in enumerate(predicted_list):
            score = scorer(exp, pred)
            if score >= min_score_threshold:
                # Include index for deterministic tie-breaking
                candidates.append((score, exp_idx, pred_idx))

    # Sort descending by score, then ascending by exp_idx, pred_idx
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    matched_pairs: List[Tuple[T_Exp, T_Pred]] = []
    used_exp: set = set()
    used_pred: set = set()

    for score, exp_idx, pred_idx in candidates:
        if exp_idx not in used_exp and pred_idx not in used_pred:
            used_exp.add(exp_idx)
            used_pred.add(pred_idx)
            matched_pairs.append((expected_list[exp_idx], predicted_list[pred_idx]))

    unmatched_exp = [exp for i, exp in enumerate(expected_list) if i not in used_exp]
    unmatched_pred = [pred for j, pred in enumerate(predicted_list) if j not in used_pred]

    return matched_pairs, unmatched_exp, unmatched_pred
