#!/usr/bin/env python3
import os
import sys
import argparse
import json
import time
import hashlib
import tempfile
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp", ".svg"}

from extractors.image_extractor import extract_image_text as extract_image_text_wrapper
from search_engine import find_matches_multilingual
from processors.language_processor import (
    detect_language, resolve_ocr_languages, normalize_unicode,
    has_indic_script, get_script_name,
)

CACHE_DIR = os.path.join(tempfile.gettempdir(), 'filescout_cache')
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_LOCK = threading.Lock()


def _cache_path():
    return os.path.join(CACHE_DIR, 'ocr_cache.json')


def _load_cache():
    try:
        with open(_cache_path(), 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache):
    try:
        with CACHE_LOCK:
            with open(_cache_path(), 'w') as f:
                json.dump(cache, f)
    except Exception:
        pass


def _cache_key(filepath):
    stat = os.stat(filepath)
    raw = f"{filepath}|{stat.st_mtime}|{stat.st_size}"
    return hashlib.md5(raw.encode()).hexdigest()


def extract_pdf_text(filepath, ocr_quality='balanced'):
    text = ""
    method_used = None

    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
        if text.strip():
            method_used = "pdfplumber"
    except Exception:
        pass

    if not text.strip():
        try:
            from pypdf import PdfReader
            for page in PdfReader(filepath).pages:
                t = page.extract_text()
                if t:
                    text += t + "\n"
            if text.strip():
                method_used = "pypdf"
        except Exception:
            pass

    if not text.strip():
        text = ocr_pdf_fallback(filepath, ocr_quality)
        if text.strip():
            method_used = "pdftoppm_ocr"

    return text, method_used


def ocr_pdf_fallback(filepath, ocr_quality='balanced'):
    text = ""
    try:
        import subprocess
        import glob
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
                try:
                    text += pytesseract.image_to_string(Image.open(img_path)) + "\n"
                except Exception:
                    pass
    except Exception:
        pass
    return text


def extract_text(filepath, ocr_languages=None, ocr_quality='balanced'):
    if ocr_languages is None:
        ocr_languages = ['en']
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        text, method = extract_pdf_text(filepath, ocr_quality)
        return text, method, 0.0
    elif ext in SUPPORTED:
        text, conf, method = extract_image_text_wrapper(filepath, ocr_languages, ocr_quality)
        return text, method, conf
    return "", None, 0.0


def _process_file(filepath, folder, ocr_languages, ocr_quality, keyword_norm, context_chars, search_language):
    rel = str(filepath.relative_to(folder))
    print(f"Scanning: {rel}", file=sys.stderr, flush=True)

    try:
        text, method, confidence = extract_text(str(filepath), ocr_languages, ocr_quality)
    except Exception as e:
        print(f"ERROR: {rel} - extraction failed: {e}", file=sys.stderr, flush=True)
        return None, "", method if 'method' in dir() else None, 0.0

    if not text.strip():
        print(f"SKIP: {rel} (no text extracted)", file=sys.stderr, flush=True)
        return None, text, method, confidence

    if search_language:
        try:
            text_lang, _ = detect_language(text)
        except Exception:
            text_lang = None
    else:
        text_lang = None

    try:
        matches = find_matches_multilingual(text, keyword_norm, context_chars, search_language)
    except Exception as e:
        print(f"ERROR: {rel} - search failed: {e}", file=sys.stderr, flush=True)
        return None, text, method, confidence

    if matches:
        result = {
            "file": rel,
            "filepath": str(filepath),
            "match_count": len(matches),
            "matches": matches,
            "extraction_method": method or "unknown",
            "confidence": round(confidence, 3),
            "detected_language": text_lang or "en",
        }
        print(f"FOUND: {len(matches)} match(es) in {rel}", file=sys.stderr, flush=True)
        return result, text, method, confidence
    else:
        print(f"NONE: {rel}", file=sys.stderr, flush=True)
        return None, text, method, confidence


def search_folder(folder, keyword, context_chars, search_language=None, ocr_quality='balanced'):
    files = sorted(Path(folder).rglob("*"))
    results = []

    ocr_languages = resolve_ocr_languages(search_language)
    keyword_norm = normalize_unicode(keyword)

    supported = []
    for filepath in files:
        if not filepath.is_file():
            continue
        ext = filepath.suffix.lower()
        if ext in SUPPORTED:
            supported.append(filepath)

    cache = _load_cache()
    cache_updated = False

    # Check cache first (fast path, no OCR needed)
    uncached = []
    for filepath in supported:
        ck = _cache_key(str(filepath))
        if ck in cache:
            entry = cache[ck]
            text = entry.get("text", "")
            if text:
                rel = str(filepath.relative_to(folder))
                if search_language:
                    try:
                        text_lang, _ = detect_language(text)
                    except Exception:
                        text_lang = None
                else:
                    text_lang = None

                matches = find_matches_multilingual(text, keyword_norm, context_chars, search_language)
                if matches:
                    results.append({
                        "file": rel,
                        "filepath": str(filepath),
                        "match_count": len(matches),
                        "matches": matches,
                        "extraction_method": entry.get("method", "cached"),
                        "confidence": entry.get("confidence", 0.0),
                        "detected_language": text_lang or "en",
                    })
                    print(f"CACHED FOUND: {len(matches)} match(es) in {rel}", file=sys.stderr, flush=True)
                else:
                    print(f"CACHED NONE: {rel}", file=sys.stderr, flush=True)
                continue
        uncached.append(filepath)

    if not uncached:
        return results

    folder_obj = Path(folder)
    max_workers = min(os.cpu_count() or 2, 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _process_file, fp, folder_obj, ocr_languages, ocr_quality,
                keyword_norm, context_chars, search_language
            ): fp
            for fp in uncached
        }
        for future in as_completed(futures):
            fp = futures[future]
            result, text, method, confidence = future.result()
            if result:
                results.append(result)

            if text.strip():
                ck = _cache_key(str(fp))
                cache[ck] = {
                    "text": text,
                    "method": method or "unknown",
                    "confidence": round(confidence, 3),
                }
                cache_updated = True

    if cache_updated:
        _save_cache(cache)

    return results


def main():
    parser = argparse.ArgumentParser(description="FileScout search engine")
    parser.add_argument("--folder",  "-f", required=True)
    parser.add_argument("--keyword", "-k", required=True)
    parser.add_argument("--context", "-c", type=int, default=80)
    parser.add_argument("--json",    action="store_true", help="Output JSON (default mode)")
    parser.add_argument("--language", "-l", default=None, help="Search language code (en, hi, kn, te, ta, mr)")
    parser.add_argument("--ocr-quality", choices=["fast", "balanced", "best"], default="balanced")
    args = parser.parse_args()

    if not os.path.isdir(args.folder):
        print(json.dumps({"error": f"Not a directory: {args.folder}", "results": []}), flush=True)
        sys.exit(1)

    start = time.time()
    results = search_folder(args.folder, args.keyword, args.context, args.language, args.ocr_quality)
    elapsed = round(time.time() - start, 2)

    output = {
        "keyword": args.keyword,
        "folder": args.folder,
        "language": args.language or "en",
        "total_files_matched": len(results),
        "search_time_seconds": elapsed,
        "results": results,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": f"Unhandled exception: {e}", "results": []}), flush=True)
        sys.exit(1)
