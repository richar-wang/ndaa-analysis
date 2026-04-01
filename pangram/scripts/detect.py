"""
detect.py - Send NDAA sections to Pangram AI detection API v3

Takes an NDAA year, reads section .txt files, sends each to Pangram v3,
and saves full JSON responses. Supports resumption if interrupted.

Short sections (<375 words) within the same subtitle are batched together
to give Pangram sufficient context (~512 tokens). A mapping file tracks
which character offsets correspond to which sections so window-level
results can be attributed back to individual sections.

Pangram v3 response includes:
  - headline: document-level classification
  - prediction_short: "Human", "Mixed", "AI-Assisted", "AI"
  - fraction_ai, fraction_ai_assisted, fraction_human (sum to 1.0)
  - windows[]: per-segment breakdown with label, ai_assistance_score, confidence

Usage:
    python detect.py <ndaa-year>
    python detect.py fy2026
    python detect.py fy2026 --estimate   # estimate credit cost without calling API
"""

import os
import re
import csv
import sys
import json
import time
import requests
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SECTIONS_DIR = os.path.join(BASE_DIR, "data", "sections")
RESULTS_DIR = os.path.join(BASE_DIR, "pangram", "results")

PANGRAM_API_URL = "https://text.api.pangramlabs.com/v3"
MAX_CHARS = 75_000      # Pangram v3 max input length
BATCH_MIN_WORDS = 375   # ~512 tokens, Pangram's internal context window
BATCH_MAX_WORDS = 2000  # cap batches to keep them manageable
REQUEST_DELAY = 0       # seconds between requests

# Sections to skip across all years (table of contents, funding tables)
SKIP_PATTERNS = [
    r"^sec2_organization_of_act",
    r"sec4201_research_development_test_and_evaluation",
]

# Separator inserted between sections in a batch
BATCH_SEPARATOR = "\n\n"


def get_api_key():
    """Read Pangram API key from environment."""
    key = os.environ.get("PANGRAM_API_KEY")
    if not key:
        print("ERROR: PANGRAM_API_KEY environment variable not set")
        print("Set it with: export PANGRAM_API_KEY=your_key_here")
        sys.exit(1)
    return key


def should_skip(filename):
    """Check if a section should be skipped based on filename patterns."""
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, filename):
            return True
    return False


def normalize_text(text):
    """Normalize NDAA section text to remove formatting differences between
    PLAW USLM and BILLS enrolled XML, so Pangram sees consistent input.
    """
    text = re.sub(r"^SEC\.\s*", "", text)
    text = re.sub(r"^\d+[a-zA-Z]?\.\s*", "", text)

    def _title_to_sentence_case(m):
        return m.group(0).capitalize()
    text = re.sub(r"^([A-Z][A-Z\s,;:\-\u2019']+\.?)", _title_to_sentence_case, text)

    text = re.sub(r"\d+\s+STAT\.\s+\d+", "", text)
    text = re.sub(r"\.\s*\u2014\s*", ". ", text)
    text = re.sub(r"\s*\u2014\s*", " - ", text)
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def send_to_pangram(text, api_key):
    """Send text to Pangram v3 API and return the JSON response."""
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    resp = requests.post(PANGRAM_API_URL, json={"text": text}, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def chunk_text(text, max_chars=MAX_CHARS):
    """Split text into chunks under the character limit.

    Tries paragraph boundaries first, then sentence boundaries as fallback
    for text with no newlines (e.g., single-blob XML extractions).
    """
    if len(text) <= max_chars:
        return [text]

    # Try paragraph-based splitting first
    if "\n\n" in text:
        chunks = []
        paragraphs = text.split("\n\n")
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 2 > max_chars:
                if current.strip():
                    chunks.append(current.strip())
                current = para
            else:
                current = current + "\n\n" + para if current else para
        if current.strip():
            chunks.append(current.strip())
        # Verify all chunks are under limit
        if all(len(c) <= max_chars for c in chunks):
            return chunks

    # Fallback: split on sentence boundaries (period followed by space + capital)
    import re
    sentences = re.split(r'(?<=\.)\s+(?=[A-Z\(])', text)
    chunks = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 > max_chars:
            if current.strip():
                chunks.append(current.strip())
            # Handle single sentences longer than max_chars (unlikely but safe)
            if len(sent) > max_chars:
                for i in range(0, len(sent), max_chars):
                    chunks.append(sent[i:i + max_chars].strip())
                current = ""
            else:
                current = sent
        else:
            current = current + " " + sent if current else sent
    if current.strip():
        chunks.append(current.strip())
    return chunks


def summarize_result(result):
    """Extract key fields from a Pangram v3 response for console output."""
    return (
        result.get("headline", "Unknown"),
        result.get("prediction_short", "Unknown"),
        result.get("fraction_ai", 0),
        result.get("fraction_ai_assisted", 0),
        result.get("fraction_human", 0),
    )


def is_flagged(result):
    """Check if a result indicates AI involvement."""
    return result.get("prediction_short", "Human") in ("Mixed", "AI-Assisted", "AI")


def load_metadata(year):
    """Load section metadata for grouping by subtitle."""
    csv_path = os.path.join(SECTIONS_DIR, year, "metadata.csv")
    metadata = {}
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                metadata[row["file_path"]] = row
    return metadata


def build_submission_plan(year, filtered_files, metadata, sections_dir):
    """Build a plan of API submissions: solo sections and batched groups.

    Returns a list of submission dicts, each with:
      - type: "solo" or "batch"
      - files: list of filenames
      - result_filename: name for the JSON result file
      - texts: dict of filename -> normalized text
      - total_words: total word count
    """
    submissions = []

    # Read and normalize all texts
    file_texts = {}
    file_words = {}
    for f in filtered_files:
        path = os.path.join(sections_dir, f)
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        text = normalize_text(raw)
        file_texts[f] = text
        file_words[f] = len(text.split())

    # Split into long (send solo) and short (batch by subtitle)
    long_files = [f for f in filtered_files if file_words[f] >= BATCH_MIN_WORDS]
    short_files = [f for f in filtered_files if file_words[f] < BATCH_MIN_WORDS]

    # Solo submissions for long sections
    for f in long_files:
        submissions.append({
            "type": "solo",
            "files": [f],
            "result_filename": f.replace(".txt", ".json"),
            "texts": {f: file_texts[f]},
            "total_words": file_words[f],
        })

    # Group short sections by (division, subtitle) for batching
    groups = defaultdict(list)
    for f in short_files:
        meta = metadata.get(f, {})
        key = (meta.get("division", ""), meta.get("subtitle", ""))
        groups[key].append(f)

    for (div, sub), group_files in groups.items():
        # Build batches within this subtitle group
        current_batch = []
        current_words = 0

        for f in group_files:
            wc = file_words[f]
            if current_words + wc > BATCH_MAX_WORDS and current_batch:
                # Flush current batch
                _add_batch(submissions, current_batch, file_texts, file_words)
                current_batch = []
                current_words = 0

            current_batch.append(f)
            current_words += wc

        # Handle remaining
        if current_batch:
            if len(current_batch) >= 2 or current_words >= BATCH_MIN_WORDS:
                _add_batch(submissions, current_batch, file_texts, file_words)
            else:
                # Single short section with no group — send solo
                f = current_batch[0]
                submissions.append({
                    "type": "solo",
                    "files": [f],
                    "result_filename": f.replace(".txt", ".json"),
                    "texts": {f: file_texts[f]},
                    "total_words": file_words[f],
                })

    return submissions


def _add_batch(submissions, batch_files, file_texts, file_words):
    """Create a batch submission entry."""
    # Result filename: use first section name with _batch suffix
    base = batch_files[0].replace(".txt", "")
    result_filename = f"{base}_batch{len(batch_files)}.json"

    submissions.append({
        "type": "batch",
        "files": list(batch_files),
        "result_filename": result_filename,
        "texts": {f: file_texts[f] for f in batch_files},
        "total_words": sum(file_words[f] for f in batch_files),
    })


def build_batch_text_and_mapping(submission):
    """Concatenate batch section texts and build character offset mapping.

    Returns:
      - combined_text: the concatenated text to send to Pangram
      - mapping: list of {filename, start_index, end_index, word_count}
    """
    mapping = []
    parts = []
    offset = 0

    for f in submission["files"]:
        text = submission["texts"][f]
        start = offset
        parts.append(text)
        offset += len(text)

        mapping.append({
            "filename": f,
            "start_index": start,
            "end_index": offset,
            "word_count": len(text.split()),
        })

        # Add separator (except after last)
        if f != submission["files"][-1]:
            parts.append(BATCH_SEPARATOR)
            offset += len(BATCH_SEPARATOR)

    combined_text = "".join(parts)
    return combined_text, mapping


def process_year(year, api_key):
    """Process all sections for a given NDAA year."""
    sections_dir = os.path.join(SECTIONS_DIR, year)
    results_dir = os.path.join(RESULTS_DIR, year)

    if not os.path.isdir(sections_dir):
        print(f"ERROR: Sections directory not found: {sections_dir}")
        print("Run parse_ndaa.py first.")
        sys.exit(1)

    os.makedirs(results_dir, exist_ok=True)

    section_files = sorted([
        f for f in os.listdir(sections_dir)
        if f.endswith(".txt")
    ])

    if not section_files:
        print(f"No .txt files found in {sections_dir}")
        return

    # Filter skipped sections
    filtered_files = [f for f in section_files if not should_skip(f)]
    skip_count = len(section_files) - len(filtered_files)
    if skip_count:
        print(f"Skipping {skip_count} sections (TOC, funding tables)")

    # Load metadata for subtitle grouping
    metadata = load_metadata(year)

    # Build submission plan
    submissions = build_submission_plan(year, filtered_files, metadata, sections_dir)

    solo_count = sum(1 for s in submissions if s["type"] == "solo")
    batch_count = sum(1 for s in submissions if s["type"] == "batch")
    batched_sections = sum(len(s["files"]) for s in submissions if s["type"] == "batch")

    print(f"Sections: {len(filtered_files)} total")
    print(f"Submissions: {len(submissions)} ({solo_count} solo + {batch_count} batches covering {batched_sections} sections)")

    # Check which are already done
    existing = set(os.listdir(results_dir))
    total = len(submissions)
    processed = 0
    skipped = 0
    flagged_list = []
    errors = []
    total_words = 0

    for i, sub in enumerate(submissions, 1):
        result_filename = sub["result_filename"]

        # Resume support
        if result_filename in existing:
            skipped += 1
            continue

        total_words += sub["total_words"]
        label = sub["files"][0] if sub["type"] == "solo" else f"batch({len(sub['files'])} sections)"

        print(f"  [{i}/{total}] {label} ({sub['total_words']} words)...", end=" ", flush=True)

        try:
            if sub["type"] == "solo":
                text = sub["texts"][sub["files"][0]]
                # Handle oversized solo sections
                chunks = chunk_text(text)
                if len(chunks) == 1:
                    result = send_to_pangram(chunks[0], api_key)
                else:
                    print(f"CHUNKING ({len(text)} chars)...", end=" ", flush=True)
                    chunk_results = []
                    for ci, chunk in enumerate(chunks):
                        if len(chunk.split()) < 50:
                            continue
                        cr = send_to_pangram(chunk, api_key)
                        chunk_results.append(cr)
                        if ci < len(chunks) - 1:
                            time.sleep(REQUEST_DELAY)

                    if chunk_results:
                        best = max(chunk_results, key=lambda r: r.get("fraction_ai", 0))
                        result = dict(best)
                        result["_all_chunks"] = chunk_results
                        result["_chunk_count"] = len(chunk_results)
                    else:
                        print("SKIP (all chunks < 50 words)")
                        continue

                result["_submission_type"] = "solo"

            else:
                # Batch submission
                combined_text, mapping = build_batch_text_and_mapping(sub)

                chunks = chunk_text(combined_text)
                if len(chunks) == 1:
                    result = send_to_pangram(chunks[0], api_key)
                else:
                    # Rare: batch exceeds 75K chars
                    print(f"CHUNKING batch...", end=" ", flush=True)
                    chunk_results = []
                    for ci, chunk in enumerate(chunks):
                        if len(chunk.split()) < 50:
                            continue
                        cr = send_to_pangram(chunk, api_key)
                        chunk_results.append(cr)
                        if ci < len(chunks) - 1:
                            time.sleep(REQUEST_DELAY)

                    if chunk_results:
                        result = max(chunk_results, key=lambda r: r.get("fraction_ai", 0))
                        result["_all_chunks"] = chunk_results
                    else:
                        print("SKIP")
                        continue

                result["_submission_type"] = "batch"
                result["_batch_files"] = sub["files"]
                result["_batch_mapping"] = mapping

            # Save result
            result_path = os.path.join(results_dir, result_filename)
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            # Report
            _, pred_short, frac_ai, frac_assisted, frac_human = summarize_result(result)
            processed += 1

            if is_flagged(result):
                print(f"FLAGGED [{pred_short}] ai={frac_ai:.0%} assisted={frac_assisted:.0%} human={frac_human:.0%}")
                flagged_list.append((label, pred_short, frac_ai, frac_assisted))
            else:
                print(f"ok [{pred_short}] human={frac_human:.0%}")

        except requests.HTTPError as e:
            print(f"HTTP ERROR: {e}")
            errors.append((label, str(e)))
        except requests.RequestException as e:
            print(f"REQUEST ERROR: {e}")
            errors.append((label, str(e)))
        except json.JSONDecodeError as e:
            print(f"JSON ERROR: {e}")
            errors.append((label, str(e)))

        time.sleep(REQUEST_DELAY)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Results for {year.upper()}:")
    print(f"  Total submissions: {total}")
    print(f"  Processed this run: {processed}")
    print(f"  Skipped (already done): {skipped}")
    print(f"  Errors: {len(errors)}")
    print(f"  Total words sent: {total_words:,}")
    print(f"  Estimated credits used: ~{total_words // 1000 + 1} (1 credit per 1K words)")

    if flagged_list:
        print(f"\n  Flagged ({len(flagged_list)}):")
        flagged_list.sort(key=lambda x: x[2], reverse=True)
        for label, pred, frac_ai, frac_assisted in flagged_list:
            print(f"    {label}: {pred} (ai={frac_ai:.0%}, assisted={frac_assisted:.0%})")

    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for label, error in errors:
            print(f"    {label}: {error}")


def estimate_cost(year):
    """Estimate API credit cost without calling the API."""
    sections_dir = os.path.join(SECTIONS_DIR, year)

    if not os.path.isdir(sections_dir):
        print(f"ERROR: Sections directory not found: {sections_dir}")
        sys.exit(1)

    metadata = load_metadata(year)
    section_files = [f for f in os.listdir(sections_dir) if f.endswith(".txt") and not should_skip(f)]

    total_words = 0
    total_chars = 0
    oversized = 0
    short = 0

    for filename in section_files:
        filepath = os.path.join(sections_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            text = normalize_text(f.read())
        wc = len(text.split())
        total_words += wc
        total_chars += len(text)
        if len(text) > MAX_CHARS:
            oversized += 1
        if wc < BATCH_MIN_WORDS:
            short += 1

    # Simulate submission plan for call count
    filtered = [f for f in sorted(os.listdir(sections_dir)) if f.endswith(".txt") and not should_skip(f)]
    submissions = build_submission_plan(year, filtered, metadata, sections_dir)

    credits = total_words // 1000 + 1
    cost_usd = credits * 0.05

    print(f"\nCost estimate for {year.upper()} NDAA:")
    print(f"  Sections: {len(section_files)} (after skips)")
    print(f"  Short sections (<{BATCH_MIN_WORDS}w): {short} (will be batched)")
    print(f"  API submissions: {len(submissions)} (vs {len(section_files)} without batching)")
    print(f"  Sections > 75K chars (need chunking): {oversized}")
    print(f"  Total words: {total_words:,}")
    print(f"  Estimated credits: ~{credits}")
    print(f"  Estimated cost (developer pricing): ~${cost_usd:.2f}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python detect.py <ndaa-year> [--estimate]")
        print("Example: python detect.py fy2026")
        print("         python detect.py fy2026 --estimate")
        sys.exit(1)

    year = sys.argv[1].lower()

    if "--estimate" in sys.argv:
        estimate_cost(year)
    else:
        api_key = get_api_key()
        print(f"Pangram AI Detection v3 - {year.upper()} NDAA")
        print(f"API endpoint: {PANGRAM_API_URL}")
        print("=" * 60)
        process_year(year, api_key)


if __name__ == "__main__":
    main()
