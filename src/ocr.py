"""Simple OCR Module for Invoice Processing.

Handles:
1. Locating Tesseract on Windows automatically.
2. Extracting raw text from images using Tesseract OCR.
3. Saving unedited OCR text to raw_ocr/ for auditing.
4. Handling errors gracefully so one bad image does not crash the entire batch.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
import pytesseract

# Auto-configure Tesseract path on Windows if not already on system PATH
for _candidate in [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe",
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
]:
    if _candidate.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_candidate)
        break


def process_image(
    image_path: Path,
    raw_ocr_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Extract text from one image safely without crashing on error.

    Args:
        image_path: Path to the image file.
        raw_ocr_dir: Optional folder to save extracted raw text.

    Returns:
        Dictionary with filename, filepath, raw_text, success, and error.
    """
    image_path = Path(image_path)

    try:
        # 1. Open image and extract text using OCR
        image = Image.open(image_path)
        raw_text = pytesseract.image_to_string(image)

        # 2. Save raw OCR text to raw_ocr/ for human verification
        if raw_ocr_dir:
            raw_ocr_dir = Path(raw_ocr_dir)
            raw_ocr_dir.mkdir(parents=True, exist_ok=True)
            text_file = raw_ocr_dir / f"{image_path.stem}.txt"
            text_file.write_text(raw_text, encoding="utf-8")

        return {
            "filename": image_path.name,
            "filepath": str(image_path),
            "raw_text": raw_text,
            "success": True,
            "error": None,
        }

    except Exception as exc:
        # Graceful failure: capture the error without halting the program
        return {
            "filename": image_path.name,
            "filepath": str(image_path),
            "raw_text": "",
            "success": False,
            "error": f"OCR failed for {image_path.name}: {exc}",
        }


def process_all_images(
    input_dir: Path,
    raw_ocr_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Find and process all supported invoice images in a folder.

    Supported formats: PNG, JPG, JPEG, TIFF, BMP.
    """
    input_dir = Path(input_dir)
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    supported_extensions = {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}
    image_files = sorted(
        f for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    )

    results = []
    for img in image_files:
        result = process_image(img, raw_ocr_dir=raw_ocr_dir)
        results.append(result)

    return results


if __name__ == "__main__":
    results = process_all_images(Path("input"), Path("raw_ocr"))
    print(f"Processed {len(results)} images:")
    for r in results:
        status = "SUCCESS" if r["success"] else "FAILED"
        print(f" - [{status}] {r['filename']}")
