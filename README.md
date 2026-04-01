# Detecting AI-Generated Text in the FY2026 National Defense Authorization Act

This project uses the [Pangram](https://pangramlabs.com/) AI detection API to analyze whether sections of the FY2026 NDAA (P.L. 119-60) contain AI-generated text, using the pre-ChatGPT FY2020 NDAA (P.L. 116-92) as a false positive control.

## Key Finding

| | FY2020 (pre-ChatGPT) | FY2026 (post-ChatGPT) |
|---|---|---|
| **Sections analyzed** | 566 | 592 |
| **Flagged as AI/Mixed** | **0** (0.0%) | **34** (5.7%) |
| **Classified AI** | 0 | 18 |
| **Classified Mixed** | 0 | 16 |
| **Classified Human** | 566 (100.0%) | 558 (94.3%) |

Zero false positives on pre-ChatGPT legislative text. 34 sections flagged in the post-ChatGPT bill.

**[Read the full cross-year comparison report](pangram/summary/cross_year_report.md)**

## Reports

- **[Cross-year comparison](pangram/summary/cross_year_report.md)** - Side-by-side FY2020 vs FY2026 with charts, flagged section tables, full text with AI-span highlighting, methodology, and limitations
- [FY2026 detailed report](pangram/summary/fy2026-trimmed_report.md) - All FY2026 results
- [FY2020 detailed report](pangram/summary/fy2020-trimmed_report.md) - All FY2020 results (control)

## Repository Structure

```
ndaa-analysis/
├── README.md                          <- You are here
├── requirements.txt                   <- Python dependencies
│
├── data/
│   ├── raw-xml/                       <- Enrolled bill XMLs from govinfo.gov
│   │   ├── ndaa_fy2020.xml
│   │   ├── ndaa_fy2023.xml
│   │   ├── ndaa_fy2024.xml
│   │   └── ndaa_fy2026.xml
│   └── sections/                      <- Parsed individual sections (.txt)
│       ├── fy2020/                    <- All 1,217 FY2020 sections
│       ├── fy2020-trimmed/            <- 566 sections after filtering
│       ├── fy2026/                    <- All 1,311 FY2026 sections
│       ├── fy2026-trimmed/            <- 589 sections after filtering
│       ├── fy2023/, fy2023-trimmed/   <- Trimmed but not yet scanned
│       └── fy2024/, fy2024-trimmed/   <- Trimmed but not yet scanned
│
├── pangram/
│   ├── scripts/                       <- All analysis scripts
│   │   ├── fetch_ndaa.py              <- Download XMLs from govinfo.gov
│   │   ├── parse_ndaa.py              <- Parse XML into section .txt files
│   │   ├── trim_sections.py           <- Filter sections (identical rules for all years)
│   │   ├── detect.py                  <- Send sections to Pangram API
│   │   ├── analyze.py                 <- Aggregate results into summary CSVs
│   │   ├── report.py                  <- Generate per-year markdown reports
│   │   ├── cross_report.py            <- Generate cross-year comparison report
│   │   └── attribute.py               <- Deep-dive attribution for flagged sections
│   ├── results/                       <- Raw Pangram API responses (JSON per section)
│   │   ├── fy2020-trimmed/
│   │   └── fy2026-trimmed/
│   └── summary/                       <- Reports, charts, and summary CSVs
│       ├── cross_year_report.md       <- Main report
│       ├── fy2026-trimmed_report.md
│       ├── fy2020-trimmed_report.md
│       ├── *.png                      <- Charts
│       └── *.csv                      <- Summary data
│
└── paper/                             <- (Future) Write-up
```

## Pipeline

```
1. fetch_ndaa.py     Download enrolled bill XMLs from govinfo.gov
         |
2. parse_ndaa.py     Parse XML into individual section .txt files
         |
3. trim_sections.py  Filter out non-prose sections (identical rules per year)
         |
4. detect.py         Send sections to Pangram v3 API
         |
5. analyze.py        Aggregate JSON results into summary CSVs
         |
6. report.py         Generate per-year markdown report with charts
   cross_report.py   Generate cross-year comparison report
```

## Methodology

1. Enrolled bill XMLs downloaded from [govinfo.gov](https://www.govinfo.gov/) (PLAW USLM for FY2020, BILLS enrolled for FY2026)
2. Parsed into individual sections; text normalized to remove formatting differences between XML formats
3. Sections filtered using identical rules for both years: removed sections under 225 words, mechanical amendments, tables of contents, definitions, funding tables, and codification sections
4. Remaining sections sent to Pangram v3 AI detection API; short sections batched for sufficient context
5. Results aggregated; sections classified as Human, Mixed, or AI based on Pangram's per-window analysis

## Limitations

- **Black-box detector.** Pangram's methodology is proprietary.
- **No ground truth.** No confirmed AI-written sections exist for validation.
- **Topic confound.** FY2026 has more tech-focused sections that may pattern-match AI detectors (~2x over-represented in flags).
- **Coverage.** ~45% of sections analyzed per year (~70% by word count).

See the [full report](pangram/summary/cross_year_report.md) for detailed discussion.

## Reproducing

```bash
pip install -r requirements.txt
export PANGRAM_API_KEY=your_key_here

# Parse and trim (data already included in repo)
python pangram/scripts/parse_ndaa.py fy2026
python pangram/scripts/trim_sections.py fy2026 fy2020

# Run detection
python pangram/scripts/detect.py fy2026-trimmed
python pangram/scripts/detect.py fy2020-trimmed

# Generate reports
python pangram/scripts/analyze.py fy2026-trimmed
python pangram/scripts/analyze.py fy2020-trimmed
python pangram/scripts/cross_report.py
```
