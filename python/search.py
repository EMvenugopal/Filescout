#!/usr/bin/env python3
import os
import sys
import argparse
import json
import time
import hashlib
import tempfile
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
from index_db import IndexDatabase

db = None

def get_db():
    global db
    if db is None:
        db = IndexDatabase()
    return db


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


def collect_files(folder):
    """Collect all supported files in a folder, skipping irrelevant directories."""
    folder_path = Path(folder)
    files = []
    for item in folder_path.rglob("*"):
        if not item.is_file():
            continue
        parts = item.parts
        if any(part.startswith('.') or part in ['node_modules', '__pycache__', '.git', 'dist', 'build'] for part in parts):
            continue
        ext = item.suffix.lower()
        if ext in SUPPORTED:
            files.append(item)
    return files


def _index_file(filepath, folder, ocr_languages, ocr_quality):
    """Extract text from a file and store it in the database. Returns (rel_path, success, method, confidence)."""
    rel = str(filepath.relative_to(folder))
    print(f"Indexing: {rel}", file=sys.stderr, flush=True)

    try:
        text, method, confidence = extract_text(str(filepath), ocr_languages, ocr_quality)
    except Exception as e:
        print(f"ERROR: {rel} - extraction failed: {e}", file=sys.stderr, flush=True)
        return rel, False, "unknown", 0.0

    if not text.strip():
        print(f"SKIP: {rel} (no text extracted)", file=sys.stderr, flush=True)
        return rel, False, method or "unknown", confidence

    try:
        text_lang, _ = detect_language(text)
    except Exception:
        text_lang = "en"

    try:
        words = text.split()
        positions = []
        current_pos = 0
        for word in words:
            positions.append(current_pos)
            current_pos += len(word) + 1

        database = get_db()
        database.add_file(
            filepath=str(filepath),
            filename=rel,
            extension=Path(filepath).suffix.lower(),
            ocr_method=method or "unknown",
            confidence=confidence,
            language=text_lang or "en",
            content_hash=_cache_key(str(filepath)),
            words=words,
            positions=positions
        )
        print(f"INDEXED: {rel} ({method or 'unknown'}, {confidence:.0%})", file=sys.stderr, flush=True)
        return rel, True, method or "unknown", confidence
    except Exception as e:
        print(f"ERROR: {rel} - database update failed: {e}", file=sys.stderr, flush=True)
        return rel, False, method or "unknown", confidence


def index_folder(folder, ocr_quality='balanced'):
    """Walk a folder, extract text from all supported files, store in DB.
    Only processes files not already indexed (by content hash).
    Returns summary dict."""
    database = get_db()
    abs_folder = str(Path(folder).resolve())

    files = collect_files(folder)
    total = len(files)
    print(f"Found {total} supported files in {folder}", file=sys.stderr, flush=True)

    # Filter out already-indexed files (by content hash)
    uncached = []
    already_indexed = 0
    for filepath in files:
        ck = _cache_key(str(filepath))
        if database.is_file_indexed(str(filepath), ck):
            already_indexed += 1
            continue
        uncached.append(filepath)

    print(f"Already indexed: {already_indexed}, to process: {len(uncached)}", file=sys.stderr, flush=True)

    if not uncached:
        print("All files are up to date.", file=sys.stderr, flush=True)
        return {
            "folder": folder,
            "total_files": total,
            "already_indexed": already_indexed,
            "indexed": 0,
            "skipped": 0,
            "errors": 0,
            "search_time_seconds": 0,
        }

    ocr_languages = resolve_ocr_languages(None)
    folder_obj = Path(folder)
    max_workers = min(os.cpu_count() or 2, 4)
    indexed = 0
    skipped = 0
    errors = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_index_file, fp, folder_obj, ocr_languages, ocr_quality): fp
            for fp in uncached
        }
        for future in as_completed(futures):
            rel, success, method, confidence = future.result()
            if success:
                indexed += 1
            else:
                # Check if it was skipped (no text) or error
                if method in ("unknown",) and confidence == 0.0:
                    errors += 1
                else:
                    skipped += 1

    elapsed = round(time.time() - start, 2)
    return {
        "folder": folder,
        "total_files": total,
        "already_indexed": already_indexed,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "search_time_seconds": elapsed,
    }


def index_status(folder):
    """Check how many files in a folder are indexed."""
    database = get_db()
    files = collect_files(folder)
    total = len(files)
    indexed = 0
    for filepath in files:
        ck = _cache_key(str(filepath))
        if database.is_file_indexed(str(filepath), ck):
            indexed += 1
    return {
        "folder": folder,
        "total_files": total,
        "indexed_files": indexed,
        "is_fully_indexed": indexed == total and total > 0,
    }


def search_folder_from_db(folder, keyword, context_chars, search_language=None):
    """Search only from the SQLite database — no file extraction."""
    database = get_db()
    abs_folder = str(Path(folder).resolve())
    keyword_norm = normalize_unicode(keyword)

    db_results = database.search(keyword, search_language, abs_folder)

    results = []
    for db_result in db_results:
        # Reconstruct snippet matches from stored positions
        matches = db_result.get('matches', [])
        # The DB returns word-level matches; rebuild snippets
        # For now, return the word matches directly
        results.append({
            "file": db_result['filename'],
            "filepath": db_result['filepath'],
            "match_count": len(matches),
            "matches": matches,
            "extraction_method": db_result['ocr_method'],
            "confidence": db_result['confidence'],
            "detected_language": db_result['language'],
        })
        print(f"DB MATCH: {len(matches)} match(es) in {db_result['filename']}", file=sys.stderr, flush=True)

    return results


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

        # Add to database
        try:
            words = text.split()
            positions = []
            current_pos = 0
            for word in words:
                positions.append(current_pos)
                current_pos += len(word) + 1

            database = get_db()
            database.add_file(
                filepath=str(filepath),
                filename=rel,
                extension=Path(filepath).suffix.lower(),
                ocr_method=method or "unknown",
                confidence=confidence,
                language=text_lang or "en",
                content_hash=_cache_key(str(filepath)),
                words=words,
                positions=positions
            )
        except Exception as e:
            print(f"ERROR: {rel} - database update failed: {e}", file=sys.stderr, flush=True)

        return result, text, method, confidence
    else:
        print(f"NONE: {rel}", file=sys.stderr, flush=True)
        return None, text, method, confidence


def search_folder(folder, keyword, context_chars, search_language=None, ocr_quality='balanced'):
    folder_path = Path(folder)
    files = collect_files(folder)

    results = []

    ocr_languages = resolve_ocr_languages(search_language)
    keyword_norm = normalize_unicode(keyword)

    supported = []
    for filepath in files:
        ext = filepath.suffix.lower()
        if ext in SUPPORTED:
            supported.append(filepath)

    # First, check database for instant results (filtered by folder)
    database = get_db()
    abs_folder = str(Path(folder).resolve())
    db_results = database.search(keyword, search_language, abs_folder)

    # Convert database results to expected format
    for db_result in db_results:
        results.append({
            "file": db_result['filename'],
            "filepath": db_result['filepath'],
            "match_count": len(db_result['matches']),
            "matches": db_result['matches'],
            "extraction_method": db_result['ocr_method'],
            "confidence": db_result['confidence'],
            "detected_language": db_result['language'],
        })
        print(f"DATABASE FOUND: {len(db_result['matches'])} match(es) in {db_result['filename']}", file=sys.stderr, flush=True)

    # Check which files still need processing
    uncached = []
    for filepath in supported:
        rel = str(filepath.relative_to(folder))
        found_in_db = any(r['file'] == rel for r in results)
        if not found_in_db:
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

    return results


def main():
    parser = argparse.ArgumentParser(description="FileScout search engine")
    parser.add_argument("--folder",  "-f", required=False)
    parser.add_argument("--keyword", "-k", required=False)
    parser.add_argument("--context", "-c", type=int, default=80)
    parser.add_argument("--json",    action="store_true", help="Output JSON (default mode)")
    parser.add_argument("--language", "-l", default=None, help="Search language code (en, hi, kn, te, ta, mr)")
    parser.add_argument("--ocr-quality", choices=["fast", "balanced", "best"], default="balanced")
    parser.add_argument("--init-db", action="store_true", help="Initialize database and exit")
    parser.add_argument("--clear-db", action="store_true", help="Clear database and exit")
    parser.add_argument("--index-only", action="store_true", help="Index folder (extract + store) without searching")
    parser.add_argument("--search-only", action="store_true", help="Search from DB only (no file extraction)")
    parser.add_argument("--index-status", action="store_true", help="Check index status for a folder")
    args = parser.parse_args()

    # Allow --init-db and --clear-db without other arguments
    if args.init_db or args.clear_db:
        if args.folder or args.keyword:
            print("Error: --init-db and --clear-db cannot be used with --folder or --keyword", file=sys.stderr)
            sys.exit(1)

    database = get_db()

    if args.init_db:
        print("Database initialized successfully", file=sys.stderr)
        sys.exit(0)

    if args.clear_db:
        database.clear_index()
        print("Database cleared successfully", file=sys.stderr)
        sys.exit(0)

    if not os.path.isdir(args.folder):
        print(json.dumps({"error": f"Not a directory: {args.folder}", "results": []}), flush=True)
        sys.exit(1)

    # --index-status: just report how many files are indexed
    if args.index_status:
        status = index_status(args.folder)
        print("###JSON_START###")
        print(json.dumps(status))
        print("###JSON_END###", flush=True)
        return

    # --index-only: extract text from all files and store in DB
    if args.index_only:
        start = time.time()
        result = index_folder(args.folder, args.ocr_quality)
        elapsed = round(time.time() - start, 2)
        result["search_time_seconds"] = elapsed
        print("###JSON_START###")
        print(json.dumps(result))
        print("###JSON_END###", flush=True)
        return

    # --search-only: query DB only, no file extraction
    if args.search_only:
        start = time.time()
        results = search_folder_from_db(args.folder, args.keyword, args.context, args.language)
        elapsed = round(time.time() - start, 2)
        output = {
            "keyword": args.keyword,
            "folder": args.folder,
            "language": args.language or "en",
            "total_files_matched": len(results),
            "search_time_seconds": elapsed,
            "results": results,
        }
        print("###JSON_START###")
        print(json.dumps(output))
        print("###JSON_END###", flush=True)
        return

    # Default: full search (DB cache + extract uncached files)
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
    # Wrap output in delimiters so the Electron host can extract JSON
    # even if stderr progress lines leaked into the stdout pipe (Windows).
    print("###JSON_START###")
    print(json.dumps(output))
    print("###JSON_END###", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"error": f"Unhandled exception: {e}", "results": []}), flush=True)
        sys.exit(1)
    finally:
        if db:
            db.close()
