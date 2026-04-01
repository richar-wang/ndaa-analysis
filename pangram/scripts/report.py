"""
report.py - Generate markdown report with charts for NDAA AI detection results.

Usage:
    python report.py <ndaa-year>
    python report.py fy2026-trimmed
"""

import os
import sys
import csv
import json
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")
SUMMARY_DIR = os.path.join(BASE_DIR, "pangram", "summary")
REPORT_DIR = os.path.join(BASE_DIR, "pangram", "summary")

COLORS = {
    "Human": "#4CAF50",
    "Mixed": "#FFC107",
    "AI-Assisted": "#FF9800",
    "AI": "#F44336",
}


def load_summary(year):
    csv_path = os.path.join(SUMMARY_DIR, f"{year}_summary.csv")
    with open(csv_path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_coverage(year):
    if "-trimmed" not in year:
        return None
    base_year = year.replace("-trimmed", "")
    base_csv = os.path.join(SECTIONS_DIR, base_year, "metadata.csv")
    trimmed_csv = os.path.join(SECTIONS_DIR, year, "metadata.csv")
    cut_log_path = os.path.join(SECTIONS_DIR, year, "cut_log.csv")

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

    cut_reasons = {}
    if os.path.exists(cut_log_path):
        with open(cut_log_path, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                reason = r.get("reason", "unknown")
                cut_reasons[reason] = cut_reasons.get(reason, 0) + 1

    return {
        "base_year": base_year.upper(),
        "base_sections": base_sections,
        "base_words": base_words,
        "trimmed_sections": trimmed_sections,
        "trimmed_words": trimmed_words,
        "section_pct": trimmed_sections / base_sections * 100 if base_sections else 0,
        "word_pct": trimmed_words / base_words * 100 if base_words else 0,
        "cut_reasons": cut_reasons,
    }


def chart_classification_pie(rows, year, out_dir):
    counts = {"Human": 0, "Mixed": 0, "AI-Assisted": 0, "AI": 0}
    for r in rows:
        ps = r.get("prediction_short", "Human")
        if ps in counts:
            counts[ps] += 1

    labels = [k for k, v in counts.items() if v > 0]
    sizes = [counts[k] for k in labels]
    colors = [COLORS[k] for k in labels]

    fig, ax = plt.subplots(figsize=(6, 4.5))
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, autopct=lambda p: f"{p:.1f}%\n({int(round(p * sum(sizes) / 100))})",
        colors=colors, startangle=90, textprops={"fontsize": 10},
    )
    for at in autotexts:
        at.set_fontsize(9)
    ax.set_title(f"{year.upper()} NDAA — Section Classifications", fontsize=13, fontweight="bold")

    path = os.path.join(out_dir, f"{year}_classification_pie.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return os.path.basename(path)


def chart_flagged_bars(rows, year, out_dir):
    flagged = [r for r in rows if r["prediction_short"] in ("Mixed", "AI-Assisted", "AI")]
    flagged.sort(key=lambda r: float(r["fraction_ai"]))

    labels = []
    for r in flagged:
        sec = r["section_number"]
        title = r["title"][:45]
        rel = r["reliability"]
        labels.append(f"Sec {sec} — {title} [{rel}]")

    ai_vals = [float(r["fraction_ai"]) * 100 for r in flagged]
    human_vals = [float(r["fraction_human"]) * 100 for r in flagged]

    fig, ax = plt.subplots(figsize=(10, max(5, len(flagged) * 0.32)))
    y = range(len(flagged))
    ax.barh(y, ai_vals, color=COLORS["AI"], label="AI", height=0.7)
    ax.barh(y, human_vals, left=ai_vals, color=COLORS["Human"], label="Human", height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_xlabel("Percentage", fontsize=10)
    ax.set_xlim(0, 100)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter())
    ax.set_title(f"{year.upper()} NDAA — Flagged Sections by AI Score", fontsize=13, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.invert_yaxis()

    path = os.path.join(out_dir, f"{year}_flagged_bars.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return os.path.basename(path)


def chart_ai_score_histogram(rows, year, out_dir):
    scores = [float(r["fraction_ai"]) * 100 for r in rows]

    fig, ax = plt.subplots(figsize=(8, 4))
    bins = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ax.hist(scores, bins=bins, color="#5C6BC0", edgecolor="white", linewidth=0.5)
    ax.set_xlabel("AI Score (%)", fontsize=10)
    ax.set_ylabel("Number of Sections", fontsize=10)
    ax.set_title(f"{year.upper()} NDAA — Distribution of AI Scores", fontsize=13, fontweight="bold")
    ax.xaxis.set_major_formatter(mticker.PercentFormatter())

    path = os.path.join(out_dir, f"{year}_ai_score_histogram.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return os.path.basename(path)


def chart_division_breakdown(rows, year, out_dir):
    div_stats = {}
    for r in rows:
        div = r.get("division", "") or "Unknown"
        if div not in div_stats:
            div_stats[div] = {"total": 0, "flagged": 0}
        div_stats[div]["total"] += 1
        if r["prediction_short"] in ("Mixed", "AI-Assisted", "AI"):
            div_stats[div]["flagged"] += 1

    # Only show divisions with flags or many sections
    divs = sorted(div_stats.keys(), key=lambda d: -div_stats[d]["flagged"])
    divs = [d for d in divs if div_stats[d]["flagged"] > 0 or div_stats[d]["total"] >= 20]

    if not divs:
        return None

    labels = [d[:40] for d in divs]
    totals = [div_stats[d]["total"] for d in divs]
    flagged = [div_stats[d]["flagged"] for d in divs]
    clean = [t - f for t, f in zip(totals, flagged)]

    fig, ax = plt.subplots(figsize=(10, max(4, len(divs) * 0.4)))
    y = range(len(divs))
    ax.barh(y, clean, color=COLORS["Human"], label="Human", height=0.7)
    ax.barh(y, flagged, left=clean, color=COLORS["AI"], label="Flagged", height=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Number of Sections", fontsize=10)
    ax.set_title(f"{year.upper()} NDAA — Flags by Division", fontsize=13, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.invert_yaxis()

    path = os.path.join(out_dir, f"{year}_division_breakdown.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return os.path.basename(path)


def _read_section_text(sections_dir, section_number, title):
    """Find and read the .txt file for a given section number."""
    prefix = f"sec{section_number}_"
    for f in os.listdir(sections_dir):
        if f.startswith(prefix) and f.endswith(".txt"):
            path = os.path.join(sections_dir, f)
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
    # Fallback: try matching with lowercase 'a' suffix variants (e.g., sec2808a)
    for f in os.listdir(sections_dir):
        if f.endswith(".txt") and f.startswith(f"sec{section_number}"):
            path = os.path.join(sections_dir, f)
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
    return None


def generate_report(year):
    rows = load_summary(year)
    coverage = get_coverage(year)
    out_dir = os.path.join(REPORT_DIR)
    os.makedirs(out_dir, exist_ok=True)

    # Generate charts
    pie_file = chart_classification_pie(rows, year, out_dir)
    bars_file = chart_flagged_bars(rows, year, out_dir)
    hist_file = chart_ai_score_histogram(rows, year, out_dir)
    div_file = chart_division_breakdown(rows, year, out_dir)

    # Compute stats
    total = len(rows)
    pred_counts = {"Human": 0, "Mixed": 0, "AI-Assisted": 0, "AI": 0}
    total_frac_ai = 0.0
    for r in rows:
        ps = r.get("prediction_short", "Human")
        if ps in pred_counts:
            pred_counts[ps] += 1
        total_frac_ai += float(r.get("fraction_ai", 0))

    flagged = [r for r in rows if r["prediction_short"] in ("Mixed", "AI-Assisted", "AI")]
    reliable = [r for r in flagged if r["reliability"] != "low"]
    low_conf = [r for r in flagged if r["reliability"] == "low"]
    reliable.sort(key=lambda r: -float(r["fraction_ai"]))
    low_conf.sort(key=lambda r: -float(r["fraction_ai"]))

    avg_ai = total_frac_ai / total if total else 0
    flag_rate = len(flagged) / total * 100 if total else 0

    display_year = year.replace("-trimmed", "").upper()

    # Build markdown
    md = []
    md.append(f"# {display_year} NDAA — AI Detection Report")
    md.append(f"")
    md.append(f"**Generated:** {date.today().isoformat()}")
    md.append(f"**Detector:** Pangram v3 (`text.api.pangramlabs.com/v3`)")
    md.append(f"**Dataset:** `{year}`")
    md.append(f"")

    # Coverage
    if coverage:
        md.append(f"## Dataset Coverage")
        md.append(f"")
        md.append(f"The full {display_year} NDAA contains **{coverage['base_sections']:,} sections** "
                   f"({coverage['base_words']:,} words). To reduce API costs, sections unlikely to "
                   f"contain AI-generated prose were excluded prior to analysis.")
        md.append(f"")
        md.append(f"| | Sections | Words |")
        md.append(f"|---|---|---|")
        md.append(f"| **Full bill** | {coverage['base_sections']:,} | {coverage['base_words']:,} |")
        md.append(f"| **Analyzed** | {coverage['trimmed_sections']:,} ({coverage['section_pct']:.1f}%) "
                   f"| {coverage['trimmed_words']:,} ({coverage['word_pct']:.1f}%) |")
        md.append(f"| **Excluded** | {coverage['base_sections'] - coverage['trimmed_sections']:,} "
                   f"| {coverage['base_words'] - coverage['trimmed_words']:,} |")
        md.append(f"")
        if coverage["cut_reasons"]:
            md.append(f"**Exclusion reasons:**")
            md.append(f"")
            md.append(f"| Reason | Sections cut |")
            md.append(f"|---|---|")
            for reason, count in sorted(coverage["cut_reasons"].items(), key=lambda x: -x[1]):
                label = reason.replace("_", " ").title()
                md.append(f"| {label} | {count} |")
            md.append(f"")

    # Summary
    md.append(f"## Summary")
    md.append(f"")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Sections analyzed | {total} |")
    md.append(f"| Classified Human | {pred_counts['Human']} ({pred_counts['Human']/total*100:.1f}%) |")
    md.append(f"| Classified Mixed | {pred_counts['Mixed']} ({pred_counts['Mixed']/total*100:.1f}%) |")
    md.append(f"| Classified AI | {pred_counts['AI']} ({pred_counts['AI']/total*100:.1f}%) |")
    md.append(f"| Total flagged (non-Human) | {len(flagged)} ({flag_rate:.1f}%) |")
    md.append(f"| Average AI score | {avg_ai:.4f} |")
    md.append(f"")

    # Classification chart
    md.append(f"## Classification Breakdown")
    md.append(f"")
    md.append(f"![Classification]({pie_file})")
    md.append(f"")

    # AI score distribution
    md.append(f"## AI Score Distribution")
    md.append(f"")
    md.append(f"![AI Score Distribution]({hist_file})")
    md.append(f"")

    # Flagged sections chart
    md.append(f"## Flagged Sections")
    md.append(f"")
    md.append(f"![Flagged Sections]({bars_file})")
    md.append(f"")

    # Reliable flags table
    if reliable:
        md.append(f"### Reliable Flags (3+ segments)")
        md.append(f"")
        md.append(f"These sections had enough text for Pangram to analyze across multiple windows, "
                   f"increasing confidence in the classification.")
        md.append(f"")
        md.append(f"| Sec | Title | Division | Words | Classification | AI % | Segments |")
        md.append(f"|---|---|---|---|---|---|---|")
        for r in reliable:
            ai_pct = f"{float(r['fraction_ai'])*100:.1f}%"
            md.append(f"| {r['section_number']} | {r['title'][:55]} | {r['division'][:25]} | "
                       f"{r['word_count']} | {r['prediction_short']} | {ai_pct} | {r['total_segments']} |")
        md.append(f"")

    # Low confidence flags table
    if low_conf:
        md.append(f"### Low-Confidence Flags (1-2 segments)")
        md.append(f"")
        md.append(f"These sections were classified based on only 1-2 analysis windows. "
                   f"Results should be interpreted with caution.")
        md.append(f"")
        md.append(f"| Sec | Title | Division | Words | Classification | AI % | Segments |")
        md.append(f"|---|---|---|---|---|---|---|")
        for r in low_conf:
            ai_pct = f"{float(r['fraction_ai'])*100:.1f}%"
            md.append(f"| {r['section_number']} | {r['title'][:55]} | {r['division'][:25]} | "
                       f"{r['word_count']} | {r['prediction_short']} | {ai_pct} | {r['total_segments']} |")
        md.append(f"")

    # Full text of flagged sections
    all_flagged = reliable + low_conf
    sections_dir = os.path.join(SECTIONS_DIR, year)
    md.append(f"## Full Text of Flagged Sections")
    md.append(f"")
    for r in all_flagged:
        sec_num = r["section_number"]
        title = r["title"]
        ai_pct = f"{float(r['fraction_ai'])*100:.1f}%"
        pred = r["prediction_short"]
        rel = r["reliability"]

        # Find the .txt file for this section
        text = _read_section_text(sections_dir, sec_num, r.get("title", ""))

        md.append(f"### Section {sec_num} — {title}")
        md.append(f"")
        md.append(f"**Classification:** {pred} | **AI Score:** {ai_pct} "
                   f"| **Segments:** {r['total_segments']} | **Confidence:** {rel} "
                   f"| **Division:** {r['division']}")
        md.append(f"")
        if text:
            md.append(f"<details><summary>Show full text ({r['word_count']} words)</summary>")
            md.append(f"")
            md.append(f"```")
            md.append(text)
            md.append(f"```")
            md.append(f"")
            md.append(f"</details>")
        else:
            md.append(f"*Section text not found.*")
        md.append(f"")
        md.append(f"---")
        md.append(f"")

    # Division breakdown
    if div_file:
        md.append(f"## Flags by Division")
        md.append(f"")
        md.append(f"![Division Breakdown]({div_file})")
        md.append(f"")

    # Methodology
    md.append(f"## Methodology")
    md.append(f"")
    md.append(f"1. The {display_year} NDAA enrolled bill XML was downloaded from govinfo.gov "
               f"and parsed into individual sections.")
    md.append(f"2. Sections were filtered to exclude content unlikely to be AI-generated "
               f"(table of contents, definitions, mechanical amendments to existing law, "
               f"sections under 225 words, etc.).")
    md.append(f"3. Remaining sections were normalized (formatting differences removed) "
               f"and sent to the Pangram v3 AI detection API.")
    md.append(f"4. Short sections (<375 words) were batched with adjacent sections in the "
               f"same subtitle to provide sufficient context for detection.")
    md.append(f"5. Pangram returns per-window classifications; these were aggregated to "
               f"section-level scores. Sections with 3+ windows are considered reliable; "
               f"those with 1-2 windows are low-confidence.")
    md.append(f"")
    md.append(f"**Limitations:** This analysis has not yet been validated against a "
               f"pre-ChatGPT control (e.g., FY2020 NDAA). Without a false positive "
               f"baseline, flagged sections should be treated as preliminary findings.")
    md.append(f"")

    # Write
    report_path = os.path.join(out_dir, f"{year}_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"Report: {report_path}")
    print(f"Charts: {pie_file}, {bars_file}, {hist_file}" + (f", {div_file}" if div_file else ""))


def main():
    if len(sys.argv) < 2:
        print("Usage: python report.py <ndaa-year>")
        print("Example: python report.py fy2026-trimmed")
        sys.exit(1)
    generate_report(sys.argv[1].lower())


if __name__ == "__main__":
    main()
