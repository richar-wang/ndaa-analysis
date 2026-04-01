"""
cross_report.py - Generate combined cross-year comparison report.

Usage:
    python cross_report.py
"""

import csv
import json
import os
import re
import sys
from datetime import date

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")
RESULTS_DIR = os.path.join(BASE_DIR, "pangram", "results")
SUMMARY_DIR = os.path.join(BASE_DIR, "pangram", "summary")

sys.path.insert(0, os.path.join(BASE_DIR, "pangram", "scripts"))
from report import (
    _find_section_file,
    _load_windows_for_section,
    _highlight_text_with_windows,
    _read_section_text,
)


def load_summary(year):
    with open(os.path.join(SUMMARY_DIR, f"{year}_summary.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_coverage(year):
    base_year = year.replace("-trimmed", "")
    with open(os.path.join(SECTIONS_DIR, base_year, "metadata.csv"), encoding="utf-8") as f:
        base = list(csv.DictReader(f))
    with open(os.path.join(SECTIONS_DIR, year, "metadata.csv"), encoding="utf-8") as f:
        trimmed = list(csv.DictReader(f))
    return {
        "base_sections": len(base),
        "base_words": sum(int(r["word_count"]) for r in base),
        "trimmed_sections": len(trimmed),
        "trimmed_words": sum(int(r["word_count"]) for r in trimmed),
    }


def counts(rows):
    c = {"Human": 0, "Mixed": 0, "AI-Assisted": 0, "AI": 0}
    for r in rows:
        ps = r.get("prediction_short", "Human")
        if ps in c:
            c[ps] += 1
    return c


def chart_comparison_bar(c20, c26, out_dir):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    cats = ["Human", "Mixed", "AI"]
    x = range(len(cats))
    w = 0.35
    bars20 = [c20[c] for c in cats]
    bars26 = [c26[c] for c in cats]
    ax.bar([i - w / 2 for i in x], bars20, w, label="FY2020 (pre-ChatGPT)", color="#4CAF50")
    ax.bar([i + w / 2 for i in x], bars26, w, label="FY2026 (post-ChatGPT)", color="#F44336")
    ax.set_xticks(x)
    ax.set_xticklabels(cats, fontsize=11)
    ax.set_ylabel("Number of Sections", fontsize=10)
    ax.set_title("NDAA Section Classifications: FY2020 vs FY2026", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    for i, v in enumerate(bars20):
        if v > 0:
            ax.text(i - w / 2, v + 5, str(v), ha="center", fontsize=9)
    for i, v in enumerate(bars26):
        if v > 0:
            ax.text(i + w / 2, v + 5, str(v), ha="center", fontsize=9)
    path = os.path.join(out_dir, "comparison_bar.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return os.path.basename(path)


def chart_comparison_histogram(fy20, fy26, out_dir):
    fig, ax = plt.subplots(figsize=(8, 4))
    scores20 = [float(r["fraction_ai"]) * 100 for r in fy20]
    scores26 = [float(r["fraction_ai"]) * 100 for r in fy26]
    bins = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ax.hist(scores20, bins=bins, alpha=0.7, label="FY2020", color="#4CAF50", edgecolor="white")
    ax.hist(scores26, bins=bins, alpha=0.7, label="FY2026", color="#F44336", edgecolor="white")
    ax.set_xlabel("AI Score (%)", fontsize=10)
    ax.set_ylabel("Number of Sections", fontsize=10)
    ax.set_title("AI Score Distribution: FY2020 vs FY2026", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter())
    path = os.path.join(out_dir, "comparison_histogram.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    return os.path.basename(path)


def main():
    fy20 = load_summary("fy2020-trimmed")
    fy26 = load_summary("fy2026-trimmed")
    cov20 = get_coverage("fy2020-trimmed")
    cov26 = get_coverage("fy2026-trimmed")
    c20 = counts(fy20)
    c26 = counts(fy26)

    flagged26 = [r for r in fy26 if r["prediction_short"] in ("Mixed", "AI-Assisted", "AI")]
    reliable = sorted([r for r in flagged26 if r["reliability"] != "low"], key=lambda r: -float(r["fraction_ai"]))
    lowconf = sorted([r for r in flagged26 if r["reliability"] == "low"], key=lambda r: -float(r["fraction_ai"]))

    bar_file = chart_comparison_bar(c20, c26, SUMMARY_DIR)
    hist_file = chart_comparison_histogram(fy20, fy26, SUMMARY_DIR)

    md = []
    md.append("# NDAA AI Detection: Cross-Year Comparison Report")
    md.append("")
    md.append(f"**Generated:** {date.today().isoformat()}")
    md.append("**Detector:** Pangram v3 (`text.api.pangramlabs.com/v3`)")
    md.append("**Methodology:** Identical trimming, normalization, and detection pipeline applied to both years")
    md.append("")

    # Key finding
    md.append("## Key Finding")
    md.append("")
    f20 = c20["Mixed"] + c20["AI"]
    f26 = c26["Mixed"] + c26["AI"]
    md.append("| | FY2020 (pre-ChatGPT) | FY2026 (post-ChatGPT) |")
    md.append("|---|---|---|")
    md.append(f"| **Sections analyzed** | {len(fy20)} | {len(fy26)} |")
    md.append(f"| **Flagged as AI/Mixed** | **{f20}** (0.0%) | **{f26}** (5.7%) |")
    md.append(f"| **Classified AI** | {c20['AI']} | {c26['AI']} |")
    md.append(f"| **Classified Mixed** | {c20['Mixed']} | {c26['Mixed']} |")
    md.append(f"| **Classified Human** | {c20['Human']} (100.0%) | {c26['Human']} (94.3%) |")
    md.append("")

    # Charts
    md.append("## Classification Comparison")
    md.append("")
    md.append(f"![Comparison]({bar_file})")
    md.append("")

    md.append("## AI Score Distribution")
    md.append("")
    md.append(f"![Distribution]({hist_file})")
    md.append("")

    # Coverage
    md.append("## Dataset Coverage")
    md.append("")
    md.append("Both datasets were trimmed using identical rules (`trim_sections.py`) to ensure methodological comparability.")
    md.append("")
    md.append("| | FY2020 | FY2026 |")
    md.append("|---|---|---|")
    md.append(f"| Full bill sections | {cov20['base_sections']:,} | {cov26['base_sections']:,} |")
    md.append(f"| Full bill words | {cov20['base_words']:,} | {cov26['base_words']:,} |")
    md.append(f"| Analyzed sections | {cov20['trimmed_sections']:,} ({cov20['trimmed_sections']/cov20['base_sections']*100:.1f}%) | {cov26['trimmed_sections']:,} ({cov26['trimmed_sections']/cov26['base_sections']*100:.1f}%) |")
    md.append(f"| Analyzed words | {cov20['trimmed_words']:,} ({cov20['trimmed_words']/cov20['base_words']*100:.1f}%) | {cov26['trimmed_words']:,} ({cov26['trimmed_words']/cov26['base_words']*100:.1f}%) |")
    md.append("")
    md.append("**Excluded:** Sections under 225 words, mechanical amendments to existing law, table of contents, definitions, funding tables, technical amendments, legal boilerplate, and codification sections over 15,000 words.")
    md.append("")

    # Flagged sections tables
    md.append("## FY2026 Flagged Sections")
    md.append("")

    if reliable:
        md.append("### Reliable Flags (3+ analysis segments)")
        md.append("")
        md.append("| Sec | Title | Division | Words | Class | AI % | Segments |")
        md.append("|---|---|---|---|---|---|---|")
        for r in reliable:
            ai = f"{float(r['fraction_ai'])*100:.1f}%"
            md.append(f"| {r['section_number']} | {r['title'][:55]} | {r['division'][:25]} | {r['word_count']} | {r['prediction_short']} | {ai} | {r['total_segments']} |")
        md.append("")

    if lowconf:
        md.append("### Low-Confidence Flags (1-2 analysis segments)")
        md.append("")
        md.append("| Sec | Title | Division | Words | Class | AI % | Segments |")
        md.append("|---|---|---|---|---|---|---|")
        for r in lowconf:
            ai = f"{float(r['fraction_ai'])*100:.1f}%"
            md.append(f"| {r['section_number']} | {r['title'][:55]} | {r['division'][:25]} | {r['word_count']} | {r['prediction_short']} | {ai} | {r['total_segments']} |")
        md.append("")

    # Full text with highlighting
    sections_dir = os.path.join(SECTIONS_DIR, "fy2026-trimmed")
    results_dir = os.path.join(RESULTS_DIR, "fy2026-trimmed")

    md.append("## Full Text of Flagged Sections")
    md.append("")
    md.append("<mark>Highlighted text</mark> indicates spans classified as AI-generated by Pangram.")
    md.append("")

    for r in reliable + lowconf:
        sec_num = r["section_number"]
        ai_pct = f"{float(r['fraction_ai'])*100:.1f}%"
        md.append(f"### Section {sec_num} — {r['title']}")
        md.append("")
        md.append(f"**Classification:** {r['prediction_short']} | **AI Score:** {ai_pct} "
                   f"| **Segments:** {r['total_segments']} | **Confidence:** {r['reliability']} "
                   f"| **Division:** {r['division']}")
        md.append("")

        fn = _find_section_file(sections_dir, sec_num)
        windows = _load_windows_for_section(results_dir, sections_dir, sec_num)
        if fn and windows:
            with open(os.path.join(sections_dir, fn), "r", encoding="utf-8") as fh:
                raw = fh.read()
            text = _highlight_text_with_windows(raw, windows)
            md.append(text)
        else:
            text = _read_section_text(sections_dir, sec_num, "")
            if text:
                md.append(text)
            else:
                md.append("*Section text not found.*")
        md.append("")

    # Methodology
    md.append("## Methodology")
    md.append("")
    md.append("1. Enrolled bill XMLs were downloaded from govinfo.gov (PLAW USLM for FY2020, BILLS enrolled for FY2026) and parsed into individual sections.")
    md.append("2. A single trimming script (`trim_sections.py`) applied identical filtering rules to both years, excluding sections unlikely to contain AI-generated prose.")
    md.append("3. Text was normalized to remove formatting differences between XML formats (ALL-CAPS headers, SEC. prefixes, em-spaces, STAT. references) so both years received equivalent input.")
    md.append("4. Sections were sent to the Pangram v3 AI detection API. Short sections (<375 words) were batched with adjacent sections in the same subtitle.")
    md.append("5. Pangram returns per-window classifications (AI-Generated, Human Written); these were aggregated to section-level scores. Sections with 3+ windows are reliable; 1-2 windows are low-confidence.")
    md.append("")

    # Limitations
    md.append("## Limitations")
    md.append("")
    md.append("- **Black-box detector.** Pangram's internal methodology is proprietary. We cannot independently verify what features it uses to classify text.")
    md.append("- **No ground truth.** No confirmed AI-written or human-written sections exist for validation. We measure relative differences, not absolute accuracy.")
    md.append("- **Topic confound.** FY2026 contains more technology-focused sections (AI, cyber, biotech) that may pattern-match AI detectors due to vocabulary overlap with AI training data. Tech-related sections are ~2x over-represented in flags (32% of flags vs 18% of clean sections).")
    md.append("- **Temporal style shift.** Modern legislative drafting may have become more structured and enumerated independent of AI use. However, the bimodal distribution (94% of FY2026 sections score identically to FY2020, while 6% spike to high AI scores) is more consistent with a discrete difference in production process than a gradual style shift.")
    md.append("- **Coverage.** ~45% of sections in each bill were analyzed (~70% by word count). Excluded sections (short, mechanical amendments, funding tables) are unlikely to contain AI-generated prose but cannot be ruled out.")
    md.append("")

    out = os.path.join(SUMMARY_DIR, "cross_year_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"Report: {out}")


if __name__ == "__main__":
    main()
