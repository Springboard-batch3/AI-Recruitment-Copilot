"""
Command-line runner and reporting tool for direct-text parser accuracy evaluation.
Outputs formatted console summaries and machine-readable JSON reports.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from tests.evaluation_support.evaluator import (
    APPROVED_BENCHMARK_CORPUS_SIZE,
    FullEvaluationReport,
    ParserAccuracyEvaluator,
)


def format_console_report(report: FullEvaluationReport) -> str:
    lines = []
    lines.append("=" * 80)
    lines.append("        PHASE 1E.3: FORMAL PARSER ACCURACY EVALUATION REPORT")
    lines.append("=" * 80)
    lines.append(f"Evaluator Version         : {report.evaluator_version}")
    lines.append(f"Parser Pipeline Version   : {report.parser_pipeline_version}")
    lines.append(f"Date Rules Version        : {report.date_rules_version}")
    lines.append(f"Skill Catalog Version     : {report.skill_catalog_version}")
    lines.append(f"Manifest SHA-256          : {report.manifest_hash}")
    lines.append(f"Execution Timestamp (UTC) : {report.execution_timestamp}")
    lines.append(f"Corpus Integrity Check    : {report.integrity_status}")
    lines.append("-" * 80)
    lines.append("CORPUS SUMMARY & BENCHMARK SUFFICIENCY:")
    lines.append(f"  • Development / Regression Fixtures : {report.corpus_summary['dev_fixtures_count']}")
    lines.append(f"  • Held-Out Evaluation Fixtures      : {report.corpus_summary['held_out_fixtures_count']}")
    lines.append(f"  • Approved Benchmark Size Target    : {report.corpus_summary['required_benchmark_held_out_count']}")
    lines.append(f"  • Additional Held-Out Resumes Needed: {report.corpus_summary['additional_held_out_needed']}")
    lines.append("-" * 80)

    # 1. Held-Out Evaluation Section
    lines.append("1. HELD-OUT SPLIT METRICS (FORMAL ACCEPTANCE BENCHMARK)")
    lines.append("   (Strictly isolated from development/regression fixtures)")
    h = report.held_out_metrics
    lines.append(f"   Corpus Sufficiency Status: {'SUFFICIENT' if h.corpus_sufficient else 'INSUFFICIENT CORPUS'}")
    lines.append(f"   Documents Evaluated      : {h.document_count}")
    lines.append(
        f"   Required-Field Micro     : P={h.required_field_micro.precision:.4f} | "
        f"R={h.required_field_micro.recall:.4f} | F1={h.required_field_micro.f1:.4f} "
        f"(TP={h.required_field_micro.tp}, FP={h.required_field_micro.fp}, FN={h.required_field_micro.fn})"
    )
    lines.append(f"   Required-Field Macro-F1  : {h.required_field_macro_f1:.4f}")
    lines.append(f"   Employment-Date Accuracy : {h.employment_date_accuracy * 100:.2f}%")
    lines.append(f"   Valid Evidence Coverage  : {h.evidence_coverage * 100:.2f}%")
    lines.append(f"   Invalid Evidence Count   : {h.invalid_evidence_count}")
    lines.append(f"   Complete Record Accuracy : {h.complete_record_accuracy}")
    lines.append(f"   Complete Profile Accuracy: {h.complete_profile_accuracy * 100:.2f}%")
    lines.append(f"   Processing Failure Rate  : {h.processing_stats.failure_rate * 100:.2f}% "
                 f"(PDF: {h.processing_stats.pdf_failure_rate * 100:.2f}%, DOCX: {h.processing_stats.docx_failure_rate * 100:.2f}%)")

    lines.append("\n   ACCEPTANCE TARGETS DECISION:")
    for tgt_name, status in h.acceptance_decisions.items():
        lines.append(f"     - {tgt_name.replace('_', ' ').title():<35}: [{status}]")

    lines.append("-" * 80)

    # 2. Development / Regression Section
    lines.append("2. DEVELOPMENT / REGRESSION SPLIT METRICS")
    d = report.dev_metrics
    lines.append(f"   Documents Evaluated      : {d.document_count}")
    lines.append(
        f"   Required-Field Micro     : P={d.required_field_micro.precision:.4f} | "
        f"R={d.required_field_micro.recall:.4f} | F1={d.required_field_micro.f1:.4f} "
        f"(TP={d.required_field_micro.tp}, FP={d.required_field_micro.fp}, FN={d.required_field_micro.fn})"
    )
    lines.append(f"   Required-Field Macro-F1  : {d.required_field_macro_f1:.4f}")
    lines.append(f"   Employment-Date Accuracy : {d.employment_date_accuracy * 100:.2f}%")
    lines.append(f"   Valid Evidence Coverage  : {d.evidence_coverage * 100:.2f}%")
    lines.append(f"   Complete Record Accuracy : {d.complete_record_accuracy}")
    lines.append(f"   Complete Profile Accuracy: {d.complete_profile_accuracy * 100:.2f}%")
    lines.append("-" * 80)

    # 3. Per-Field Summary (Held-Out)
    lines.append("3. PER-FIELD ACCURACY BREAKDOWN (HELD-OUT):")
    lines.append(f"   {'Field Category':<30} | {'TP':<4} | {'FP':<4} | {'FN':<4} | {'Prec':<7} | {'Rec':<7} | {'F1':<7}")
    lines.append("   " + "-" * 72)
    for cat, m in h.per_field_metrics.items():
        lines.append(
            f"   {cat:<30} | {m['tp']:<4} | {m['fp']:<4} | {m['fn']:<4} | "
            f"{m['precision']:<7.4f} | {m['recall']:<7.4f} | {m['f1']:<7.4f}"
        )
    lines.append("=" * 80)
    return "\n".join(lines)


def run_evaluation_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run formal parser accuracy evaluation benchmark.")
    parser.add_argument(
        "--evaluation-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "fixtures" / "evaluation",
        help="Path to evaluation corpus directory containing manifest.json",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Path to write machine-readable JSON evaluation report",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output",
    )
    args = parser.parse_args(argv)

    evaluator = ParserAccuracyEvaluator(evaluation_dir=args.evaluation_dir)
    report = evaluator.evaluate()

    if not args.quiet:
        print(format_console_report(report))

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        # Convert dataclasses to dict
        report_dict = asdict(report)
        args.output_json.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"\nMachine-readable report written to: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(run_evaluation_cli())
