"""Command-line interface and operations for Phase 2E.3 Matching Evaluation and Accuracy Gate."""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.schemas.evaluation import (
    BandCalibrationPolicy,
    EvaluationAccuracyStatus,
    EvaluationManifest,
    EvaluationSplit,
    compute_manifest_checksum,
)
from app.services.evaluation import (
    calibrate_score_bands_on_development,
    evaluate_matching_gate,
    generate_evaluation_markdown_report,
)

logger = logging.getLogger("ai_hiring.cli.matching_evaluation")


def validate_and_freeze_manifest(manifest_path: Path) -> EvaluationManifest:
    """Validates and loads a frozen evaluation manifest file."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest file not found at {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = EvaluationManifest.model_validate(data)
    # Check checksum integrity
    records_raw = [r.model_dump(mode="json", by_alias=True) for r in manifest.records]
    exclusions_raw = [e.model_dump(mode="json", by_alias=True) for e in manifest.exclusions]
    computed_chk = compute_manifest_checksum(
        dataset_version=manifest.dataset_version,
        provenance_reference=manifest.provenance_reference,
        authorization_reference=manifest.authorization_reference,
        match_policy_version=manifest.match_policy_version,
        records=records_raw,
        exclusions=exclusions_raw,
    )
    if computed_chk != manifest.manifest_checksum:
        raise ValueError(
            f"Manifest checksum mismatch! Expected: {manifest.manifest_checksum}, Computed: {computed_chk}"
        )

    return manifest


def run_development_calibration(
    manifest_path: Path,
    predictions_path: Path,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Runs development-only calibration on development pairs."""
    manifest = validate_and_freeze_manifest(manifest_path)

    with open(predictions_path, "r", encoding="utf-8") as f:
        preds_data = json.load(f)

    # preds_data mapping: pair_id -> score (or {"score": float})
    scores_map: Dict[str, float] = {}
    for k, v in preds_data.items():
        if isinstance(v, dict):
            scores_map[k] = float(v.get("score", 0.0))
        else:
            scores_map[k] = float(v)

    calib_report = calibrate_score_bands_on_development(manifest, scores_map)
    result_dict = calib_report.model_dump(mode="json")

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / "calibration_report.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result_dict, f, indent=2)

    return result_dict


def run_held_out_gate(
    manifest_path: Path,
    predictions_path: Path,
    calibration_policy_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Runs explicit held-out accuracy gate evaluation."""
    manifest = validate_and_freeze_manifest(manifest_path)

    with open(predictions_path, "r", encoding="utf-8") as f:
        raw_preds = json.load(f)

    # Standardize predictions map
    std_preds: Dict[str, Dict[str, Any]] = {}
    for k, v in raw_preds.items():
        if isinstance(v, dict):
            std_preds[k] = v
        else:
            std_preds[k] = {"score": float(v)}

    policy = None
    if calibration_policy_path and calibration_policy_path.exists():
        with open(calibration_policy_path, "r", encoding="utf-8") as f:
            p_data = json.load(f)
        policy = BandCalibrationPolicy.model_validate(p_data)

    eval_report = evaluate_matching_gate(
        manifest=manifest,
        system_predictions=std_preds,
        calibration_policy=policy,
        target_split=EvaluationSplit.HELD_OUT,
    )

    report_json = eval_report.model_dump(mode="json")
    markdown_text = generate_evaluation_markdown_report(eval_report)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "evaluation_report.json", "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2)
        with open(output_dir / "evaluation_report.md", "w", encoding="utf-8") as f:
            f.write(markdown_text)

    return report_json


def main():
    parser = argparse.ArgumentParser(description="Milestone 2 Matching Evaluation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: calibrate
    calib_parser = subparsers.add_parser("calibrate", help="Calibrate score bands on development split")
    calib_parser.add_argument("--manifest", required=True, type=Path, help="Path to frozen evaluation manifest JSON")
    calib_parser.add_argument("--predictions", required=True, type=Path, help="Path to system match scores JSON")
    calib_parser.add_argument("--output-dir", type=Path, default=Path("eval_output"), help="Output directory")

    # Subcommand: evaluate-gate
    gate_parser = subparsers.add_parser("evaluate-gate", help="Run held-out accuracy gate evaluation")
    gate_parser.add_argument("--manifest", required=True, type=Path, help="Path to frozen evaluation manifest JSON")
    gate_parser.add_argument("--predictions", required=True, type=Path, help="Path to system match predictions JSON")
    gate_parser.add_argument("--policy", type=Path, default=None, help="Path to calibrated policy JSON")
    gate_parser.add_argument("--output-dir", type=Path, default=Path("eval_output"), help="Output directory")

    args = parser.parse_args()

    if args.command == "calibrate":
        res = run_development_calibration(args.manifest, args.predictions, args.output_dir)
        print(f"Calibration completed. Selected configuration: {res['selected_configuration']}")
        print(f"Dev Macro-F1: {res['dev_macro_f1']}")
    elif args.command == "evaluate-gate":
        res = run_held_out_gate(args.manifest, args.predictions, args.policy, args.output_dir)
        print(f"Accuracy Gate Status: {res['evaluation_status']}")
        print(f"Target Met: {res['target_met']}")
        print(f"Conclusion: {res['acceptance_conclusion']}")


if __name__ == "__main__":
    main()
