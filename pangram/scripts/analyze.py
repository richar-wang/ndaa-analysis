"""
analyze.py - Analyze Pangram v3 detection results for NDAA sections

Handles both solo and batch submissions. For batch results, uses the
_batch_mapping to attribute window-level scores back to individual sections.

Usage:
    python analyze.py <ndaa-year>
    python analyze.py --compare
"""

import os
import sys
import csv
import json

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")
RESULTS_DIR = os.path.join(BASE_DIR, "pangram", "results")
SUMMARY_DIR = os.path.join(BASE_DIR, "pangram", "summary")

NDAA_YEARS = ["fy2020", "fy2023", "fy2024", "fy2026"]

# Trimmed variants used for budget-constrained runs
TRIMMED_YEARS = ["fy2020-trimmed", "fy2026-trimmed"]


def load_metadata(year):
    """Load the metadata CSV for a given NDAA year, keyed by file_path."""
    csv_path = os.path.join(SECTIONS_DIR, year, "metadata.csv")
    metadata = {}
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                metadata[row["file_path"]] = row
    return metadata


def extract_section_scores_from_batch(result):
    """For a batch result, attribute windows back to individual sections.

    Uses _batch_mapping (char offsets per section) and windows[] (char offsets
    per segment) to compute per-section scores.

    Returns dict of filename -> {fraction_ai, fraction_ai_assisted, fraction_human,
    prediction_short, headline, total_segments, num_ai_segments, ...}
    """
    mapping = result.get("_batch_mapping", [])
    windows = result.get("windows", [])

    if not mapping or not windows:
        return {}

    section_scores = {}

    for section in mapping:
        fname = section["filename"]
        sec_start = section["start_index"]
        sec_end = section["end_index"]

        # Find windows that fall within this section's character range
        sec_windows = [
            w for w in windows
            if w.get("start_index", 0) >= sec_start and w.get("start_index", 0) < sec_end
        ]

        if not sec_windows:
            section_scores[fname] = {
                "fraction_ai": 0.0,
                "fraction_ai_assisted": 0.0,
                "fraction_human": 1.0,
                "prediction_short": "Human",
                "headline": "",
                "total_segments": 0,
                "num_ai_segments": 0,
                "num_ai_assisted_segments": 0,
                "num_human_segments": 0,
                "reliability": "low",
            }
            continue

        # Classify each window and compute fractions
        num_ai = 0
        num_assisted = 0
        num_human = 0
        for w in sec_windows:
            label = w.get("label", "Human-Written")
            if label == "AI-Generated":
                num_ai += 1
            elif label in ("Lightly AI-Assisted", "Moderately AI-Assisted", "Heavily AI-Assisted"):
                num_assisted += 1
            else:
                num_human += 1

        total = num_ai + num_assisted + num_human
        frac_ai = num_ai / total if total > 0 else 0
        frac_assisted = num_assisted / total if total > 0 else 0
        frac_human = num_human / total if total > 0 else 0

        # Derive prediction_short from fractions
        if frac_ai >= 0.5:
            pred = "AI"
        elif frac_ai + frac_assisted >= 0.5:
            pred = "AI-Assisted"
        elif frac_ai + frac_assisted > 0:
            pred = "Mixed"
        else:
            pred = "Human"

        if total <= 2:
            reliability = "low"
        elif total <= 5:
            reliability = "medium"
        else:
            reliability = "high"

        section_scores[fname] = {
            "fraction_ai": frac_ai,
            "fraction_ai_assisted": frac_assisted,
            "fraction_human": frac_human,
            "prediction_short": pred,
            "headline": "",
            "total_segments": total,
            "num_ai_segments": num_ai,
            "num_ai_assisted_segments": num_assisted,
            "num_human_segments": num_human,
            "reliability": reliability,
        }

    return section_scores


def extract_fields_solo(result):
    """Extract fields from a solo (non-batch) result."""
    num_ai = result.get("num_ai_segments", 0)
    num_asst = result.get("num_ai_assisted_segments", 0)
    num_human = result.get("num_human_segments", 0)
    total_segments = num_ai + num_asst + num_human

    if total_segments <= 2:
        reliability = "low"
    elif total_segments <= 5:
        reliability = "medium"
    else:
        reliability = "high"

    return {
        "headline": result.get("headline", ""),
        "prediction_short": result.get("prediction_short", ""),
        "fraction_ai": result.get("fraction_ai", 0.0),
        "fraction_ai_assisted": result.get("fraction_ai_assisted", 0.0),
        "fraction_human": result.get("fraction_human", 0.0),
        "num_ai_segments": num_ai,
        "num_ai_assisted_segments": num_asst,
        "num_human_segments": num_human,
        "total_segments": total_segments,
        "reliability": reliability,
    }


def is_flagged(fields):
    """Check if a result should be flagged."""
    return fields["prediction_short"] in ("Mixed", "AI-Assisted", "AI")


def _build_row(meta, fields):
    """Build a CSV row from metadata and detection fields."""
    return {
        "section_number": meta.get("section_number", ""),
        "title": meta.get("title", ""),
        "division": meta.get("division", ""),
        "word_count": meta.get("word_count", ""),
        "headline": fields.get("headline", ""),
        "prediction_short": fields["prediction_short"],
        "fraction_ai": f"{fields['fraction_ai']:.4f}",
        "fraction_ai_assisted": f"{fields['fraction_ai_assisted']:.4f}",
        "fraction_human": f"{fields['fraction_human']:.4f}",
        "total_segments": fields["total_segments"],
        "num_ai_segments": fields["num_ai_segments"],
        "num_ai_assisted_segments": fields["num_ai_assisted_segments"],
        "num_human_segments": fields["num_human_segments"],
        "reliability": fields["reliability"],
    }


def _count(row, headline_counts, pred_counts):
    """Update running counters."""
    hl = row["headline"]
    if hl:
        headline_counts[hl] = headline_counts.get(hl, 0) + 1
    ps = row["prediction_short"] or "Human"
    if ps in pred_counts:
        pred_counts[ps] += 1


def get_coverage_stats(year):
    """For trimmed datasets, report what fraction of the original NDAA was analyzed."""
    if "-trimmed" not in year:
        return None

    base_year = year.replace("-trimmed", "")
    base_csv = os.path.join(SECTIONS_DIR, base_year, "metadata.csv")
    trimmed_csv = os.path.join(SECTIONS_DIR, year, "metadata.csv")
    cut_log = os.path.join(SECTIONS_DIR, year, "cut_log.csv")

    if not os.path.exists(base_csv) or not os.path.exists(trimmed_csv):
        return None

    with open(base_csv, "r", encoding="utf-8") as f:
        base_rows = list(csv.DictReader(f))
    with open(trimmed_csv, "r", encoding="utf-8") as f:
        trimmed_rows = list(csv.DictReader(f))

    base_sections = len(base_rows)
    base_words = sum(int(r.get("word_count", 0)) for r in base_rows)
    trimmed_sections = len(trimmed_rows)
    trimmed_words = sum(int(r.get("word_count", 0)) for r in trimmed_rows)

    stats = {
        "base_year": base_year.upper(),
        "base_sections": base_sections,
        "base_words": base_words,
        "trimmed_sections": trimmed_sections,
        "trimmed_words": trimmed_words,
        "section_coverage_pct": (trimmed_sections / base_sections * 100) if base_sections else 0,
        "word_coverage_pct": (trimmed_words / base_words * 100) if base_words else 0,
    }

    # Load cut reasons if available
    if os.path.exists(cut_log):
        with open(cut_log, "r", encoding="utf-8") as f:
            cut_rows = list(csv.DictReader(f))
        reason_counts = {}
        for r in cut_rows:
            reason = r.get("reason", "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        stats["cut_reasons"] = reason_counts

    return stats


def analyze_year(year):
    """Analyze all Pangram results for a single NDAA year."""
    results_dir = os.path.join(RESULTS_DIR, year)

    if not os.path.isdir(results_dir):
        print(f"No results found for {year.upper()} at {results_dir}")
        return None

    metadata = load_metadata(year)
    os.makedirs(SUMMARY_DIR, exist_ok=True)

    result_files = sorted([f for f in os.listdir(results_dir) if f.endswith(".json")])

    if not result_files:
        print(f"No JSON result files in {results_dir}")
        return None

    rows = []
    headline_counts = {}
    pred_counts = {"Human": 0, "Mixed": 0, "AI-Assisted": 0, "AI": 0}
    flagged_rows = []

    for filename in result_files:
        filepath = os.path.join(results_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            result = json.load(f)

        sub_type = result.get("_submission_type", "solo")

        if sub_type == "batch":
            section_scores = extract_section_scores_from_batch(result)
            for sec_file, fields in section_scores.items():
                meta = metadata.get(sec_file, {})
                row = _build_row(meta, fields)
                rows.append(row)
                _count(row, headline_counts, pred_counts)
                if is_flagged(fields):
                    flagged_rows.append(row)
        else:
            fields = extract_fields_solo(result)
            txt_filename = filename.replace(".json", ".txt")
            meta = metadata.get(txt_filename, {})
            row = _build_row(meta, fields)
            rows.append(row)
            _count(row, headline_counts, pred_counts)
            if is_flagged(fields):
                flagged_rows.append(row)

    # Save summary CSV
    csv_path = os.path.join(SUMMARY_DIR, f"{year}_summary.csv")
    fieldnames = [
        "section_number", "title", "division", "word_count",
        "headline", "prediction_short",
        "fraction_ai", "fraction_ai_assisted", "fraction_human",
        "total_segments", "num_ai_segments", "num_ai_assisted_segments",
        "num_human_segments", "reliability",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Print report
    total = len(rows)
    print(f"\n{'=' * 60}")
    print(f"Analysis: {year.upper()} NDAA")
    print(f"{'=' * 60}")
    print(f"  Total sections analyzed: {total}")

    coverage = get_coverage_stats(year)
    if coverage:
        print(f"\n  Coverage (trimmed dataset):")
        print(f"    Analyzed {coverage['trimmed_sections']} of {coverage['base_sections']} "
              f"sections ({coverage['section_coverage_pct']:.1f}%)")
        print(f"    Covering {coverage['trimmed_words']:,} of {coverage['base_words']:,} "
              f"words ({coverage['word_coverage_pct']:.1f}%)")
        if "cut_reasons" in coverage:
            print(f"    Excluded sections by reason:")
            for reason, count in sorted(coverage["cut_reasons"].items(), key=lambda x: -x[1]):
                print(f"      {reason}: {count}")

    print(f"\n  Classification (prediction_short):")
    for tier in ("Human", "Mixed", "AI-Assisted", "AI"):
        pct = (pred_counts[tier] / total * 100) if total > 0 else 0
        print(f"    {tier}: {pred_counts[tier]} ({pct:.1f}%)")

    print(f"\n  Headline breakdown:")
    for hl, count in sorted(headline_counts.items(), key=lambda x: -x[1]):
        pct = (count / total * 100) if total > 0 else 0
        print(f"    {hl}: {count} ({pct:.1f}%)")

    if flagged_rows:
        reliable = [r for r in flagged_rows if r["reliability"] != "low"]
        unreliable = [r for r in flagged_rows if r["reliability"] == "low"]
        reliable.sort(key=lambda r: float(r["fraction_ai"]), reverse=True)
        unreliable.sort(key=lambda r: float(r["fraction_ai"]), reverse=True)

        if reliable:
            print(f"\n  Flagged sections - reliable ({len(reliable)}, >= 3 segments):")
            for row in reliable:
                rel_tag = f"[{row['reliability']}]"
                print(f"    Sec {row['section_number']:>6s} | {row['prediction_short']:<12s} | "
                      f"ai={row['fraction_ai']} asst={row['fraction_ai_assisted']} | "
                      f"{row['total_segments']:>2} seg {rel_tag:<8s} | {row['title'][:40]}")

        if unreliable:
            print(f"\n  Flagged sections - low confidence ({len(unreliable)}, 1-2 segments):")
            for row in unreliable:
                print(f"    Sec {row['section_number']:>6s} | {row['prediction_short']:<12s} | "
                      f"ai={row['fraction_ai']} asst={row['fraction_ai_assisted']} | "
                      f"{row['total_segments']:>2} seg          | {row['title'][:40]}")
    else:
        print(f"\n  No sections flagged.")

    print(f"\n  Summary CSV: {csv_path}")
    return {"year": year, "total": total, "pred_counts": pred_counts, "flagged_count": len(flagged_rows)}


def compare_years():
    """Generate cross-year comparison of detection rates.

    Automatically discovers which years have results, including trimmed variants.
    Prefers trimmed results when both trimmed and full exist for the same base year.
    """
    # Discover which years have summary CSVs or results
    available = []
    for year in NDAA_YEARS + TRIMMED_YEARS:
        csv_path = os.path.join(SUMMARY_DIR, f"{year}_summary.csv")
        results_dir = os.path.join(RESULTS_DIR, year)
        if os.path.exists(csv_path) or os.path.isdir(results_dir):
            available.append(year)

    if not available:
        print("No results found for any year. Run detect.py first.")
        return

    print("\n" + "=" * 80)
    print("Cross-Year NDAA AI Detection Comparison (Pangram v3)")
    print("=" * 80)
    print(f"{'Year':<20} {'Total':>6} {'Human':>7} {'Mixed':>7} {'AI-Asst':>8} {'AI':>5} {'Flag%':>7} {'Avg frac_ai':>12}")
    print("-" * 80)

    all_stats = []
    for year in available:
        csv_path = os.path.join(SUMMARY_DIR, f"{year}_summary.csv")
        if not os.path.exists(csv_path):
            # Try to generate it
            result = analyze_year(year)
            if not result:
                print(f"{year.upper():<20} {'(no results)':>6}")
            continue

        with open(csv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            print(f"{year.upper():<20} {'(no results)':>6}")
            continue

        pred_counts = {"Human": 0, "Mixed": 0, "AI-Assisted": 0, "AI": 0}
        total_frac_ai = 0.0
        for row in rows:
            ps = row.get("prediction_short", "Human")
            if ps in pred_counts:
                pred_counts[ps] += 1
            try:
                total_frac_ai += float(row.get("fraction_ai", 0))
            except ValueError:
                pass

        total = sum(pred_counts.values())
        flagged = pred_counts["Mixed"] + pred_counts["AI-Assisted"] + pred_counts["AI"]
        flag_rate = (flagged / total * 100) if total > 0 else 0
        avg_frac_ai = (total_frac_ai / total) if total > 0 else 0

        print(f"{year.upper():<20} {total:>6} {pred_counts['Human']:>7} "
              f"{pred_counts['Mixed']:>7} {pred_counts['AI-Assisted']:>8} "
              f"{pred_counts['AI']:>5} {flag_rate:>6.1f}% {avg_frac_ai:>11.4f}")

        all_stats.append({
            "year": year, "total": total,
            "human": pred_counts["Human"], "mixed": pred_counts["Mixed"],
            "ai_assisted": pred_counts["AI-Assisted"], "ai": pred_counts["AI"],
            "flag_rate_pct": round(flag_rate, 2), "avg_fraction_ai": round(avg_frac_ai, 4),
        })

    if all_stats:
        csv_path = os.path.join(SUMMARY_DIR, "cross_year_comparison.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "year", "total", "human", "mixed", "ai_assisted", "ai",
                "flag_rate_pct", "avg_fraction_ai",
            ])
            writer.writeheader()
            writer.writerows(all_stats)
        print(f"\nComparison CSV: {csv_path}")

    print("\nNote: FY2020 and FY2023 predate ChatGPT (Nov 2022) and serve as")
    print("false positive baselines.")


def main():
    if "--compare" in sys.argv:
        compare_years()
    elif len(sys.argv) >= 2:
        year = sys.argv[1].lower()
        analyze_year(year)
    else:
        print("Usage:")
        print("  python analyze.py <ndaa-year>     # analyze single year")
        print("  python analyze.py --compare        # cross-year comparison")
        sys.exit(1)


if __name__ == "__main__":
    main()
