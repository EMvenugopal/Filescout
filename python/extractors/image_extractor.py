import sys
import os
import threading
from pathlib import Path

_EASYOCR_READERS = {}
_READER_LOCK = threading.Lock()

def _get_easyocr_reader(languages):
    key = tuple(sorted(languages))
    with _READER_LOCK:
        if key in _EASYOCR_READERS:
            return _EASYOCR_READERS[key]
    try:
        import easyocr
        reader = easyocr.Reader(list(key), gpu=False)
        with _READER_LOCK:
            _EASYOCR_READERS[key] = reader
        return reader
    except Exception:
        return None


def extract_image_text_easyocr(filepath, languages):
    if not languages:
        languages = ['en']

    combined = list(set(languages))
    reader = _get_easyocr_reader(combined)
    if reader is None:
        return "", 0.0, "no_reader"

    try:
        results = reader.readtext(filepath, paragraph=False)
        if not results:
            return "", 0.0, "no_text_detected"
        lines = []
        confidences = []
        for item in results:
            bbox, text, conf = item
            text = text.strip()
            if text:
                lines.append(text)
                confidences.append(conf)
        if lines:
            avg_conf = sum(confidences) / len(confidences)
            return "\n".join(lines), avg_conf, "easyocr"
        return "", 0.0, "no_text_detected"
    except Exception as e:
        print(f"EASYOCR ERROR: {e}", file=sys.stderr, flush=True)
        return "", 0.0, "easyocr_error"


def extract_image_text_tesseract(filepath):
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(filepath)
        text = pytesseract.image_to_string(img)
        return text.strip(), "tesseract"
    except Exception:
        return "", "tesseract_error"


def extract_image_text_pil_alt(filepath):
    try:
        from PIL import Image
        img = Image.open(filepath)
        w, h = img.size
        if w < 20 or h < 20:
            return "", "too_small"
        return "", "no_text"
    except Exception:
        return "", "pil_error"


def extract_svg_text(filepath):
    try:
        import cairosvg
        import io
        from PIL import Image
        png_data = cairosvg.svg2png(url=filepath)
        img = Image.open(io.BytesIO(png_data))
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
            img.save(tmp.name)
            text, method = extract_image_text_tesseract(tmp.name)
            os.unlink(tmp.name)
            if text:
                return text, "svg_ocr"
        return "", "svg_empty"
    except ImportError:
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(filepath)
            root = tree.getroot()
            texts = []
            ns = '{http://www.w3.org/2000/svg}'
            for t in root.iter(f'{ns}text'):
                if t.text:
                    texts.append(t.text)
            for t in root.iter(f'{ns}tspan'):
                if t.text:
                    texts.append(t.text)
            return "\n".join(texts), "svg_native"
        except Exception:
            return "", "svg_error"
    except Exception:
        return "", "svg_error"


def extract_image_text(filepath, languages=None, quality='balanced'):
    if languages is None:
        languages = ['en']
    ext = Path(filepath).suffix.lower()

    if ext == '.svg':
        text, method = extract_svg_text(filepath)
        if text:
            return text, 0.0, method
        return "", 0.0, method

    has_non_english = any(l != 'en' for l in languages)

    if quality == 'fast':
        text, method = extract_image_text_tesseract(filepath)
        if text.strip():
            return text, 0.5, method
        text, conf, method = extract_image_text_easyocr(filepath, languages)
        if text.strip():
            return text, conf, method
        return "", 0.0, "no_text"

    if quality == 'best':
        text, conf, method = extract_image_text_easyocr(filepath, languages)
        if text.strip():
            return text, conf, method
        text, method = extract_image_text_tesseract(filepath)
        if text.strip():
            return text, 0.5, method
        return "", 0.0, "no_text"

    if not has_non_english:
        text, method = extract_image_text_tesseract(filepath)
        if text.strip():
            return text, 0.5, method

    text, conf, method = extract_image_text_easyocr(filepath, languages)
    if text.strip():
        return text, conf, method

    if has_non_english:
        text, method = extract_image_text_tesseract(filepath)
        if text.strip():
            return text, 0.5, method

    return "", 0.0, "no_text"
