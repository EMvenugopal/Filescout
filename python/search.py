#!/usr/bin/env python3
"""
FileScout Search Engine
=======================
Searches a folder of PDFs and images for a keyword.
Outputs structured JSON to stdout; progress to stderr.

Usage:
    python search.py --folder /path/to/folder --keyword "invoice" --context 80 --json
"""

import os
import sys
import argparse
import re
import json
from pathlib import Path

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_pdf_text(filepath: str) -> str:
    text = ""

    # 1. pdfplumber (best for text PDFs)
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
    except Exception:
        pass

    # 2. pypdf fallback
    if not text.strip():
        try:
            from pypdf import PdfReader
            for page in PdfReader(filepath).pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        except Exception:
            pass

    # 3. OCR fallback for scanned PDFs
    if not text.strip():
        text = ocr_pdf(filepath)

    return text


def ocr_pdf(filepath: str) -> str:
    text = ""
    try:
        import subprocess, tempfile, glob
        import pytesseract
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["pdftoppm", "-jpeg", "-r", "200", filepath, f"{tmpdir}/page"],
                capture_output=True
            )
            if result.returncode != 0:
                return ""
            for img_path in sorted(glob.glob(f"{tmpdir}/page-*.jpg")):
                text += pytesseract.image_to_string(Image.open(img_path)) + "\n"
    except Exception:
        pass
    return text


def extract_image_text(filepath: str) -> str:
    try:
        import pytesseract
        from PIL import Image
        return pytesseract.image_to_string(Image.open(filepath))
    except Exception:
        return ""


def extract_text(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(filepath)
    elif ext in SUPPORTED:
        return extract_image_text(filepath)
    return ""


# ── Search ─────────────────────────────────────────────────────────────────────

def find_matches(text: str, keyword: str, context_chars: int) -> list:
    matches = []
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    for m in pattern.finditer(text):
        start = max(0, m.start() - context_chars)
        end   = min(len(text), m.end() + context_chars)
        snippet = text[start:end].replace("\n", " ").strip()
        matches.append({
            "snippet": snippet,
            "match_start": m.start() - start,
            "match_end":   m.end()   - start,
            "match_text":  m.group(),
        })
    return matches


def search_folder(folder: str, keyword: str, context_chars: int) -> list:
    files = [
        f for f in sorted(Path(folder).rglob("*"))
        if f.is_file() and f.suffix.lower() in SUPPORTED
    ]

    results = []

    for filepath in files:
        rel = str(filepath.relative_to(folder))
        # Progress to stderr so stdout stays clean JSON
        print(f"Scanning: {rel}", file=sys.stderr, flush=True)

        text = extract_text(str(filepath))
        if not text.strip():
            print(f"SKIP: {rel} (no text extracted)", file=sys.stderr, flush=True)
            continue

        matches = find_matches(text, keyword, context_chars)
        if matches:
            results.append({
                "file": rel,
                "filepath": str(filepath),
                "match_count": len(matches),
                "matches": matches,
            })
            print(f"FOUND: {len(matches)} match(es) in {rel}", file=sys.stderr, flush=True)
        else:
            print(f"NONE: {rel}", file=sys.stderr, flush=True)

    return results


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FileScout search engine")
    parser.add_argument("--folder",  "-f", required=True)
    parser.add_argument("--keyword", "-k", required=True)
    parser.add_argument("--context", "-c", type=int, default=80)
    parser.add_argument("--json",    action="store_true", help="Output JSON (default mode)")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(json.dumps({"error": f"Not a directory: {args.folder}", "results": []}))
        sys.exit(1)

    results = search_folder(args.folder, args.keyword, args.context)

    # Always output JSON to stdout
    print(json.dumps({
        "keyword": args.keyword,
        "folder": args.folder,
        "total_files_matched": len(results),
        "results": results,
    }))


if __name__ == "__main__":
    main()
