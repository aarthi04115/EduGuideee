import pymupdf
from pathlib import Path


def extract_pages_from_file(file_path):
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".pdf":
        try:
            with pymupdf.open(
                stream=file_path.read_bytes(),
                filetype="pdf",
            ) as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported.")
                pages = [
                    (page_number, page.get_text())
                    for page_number, page in enumerate(document, start=1)
                ]
                has_text = any(text.strip() for _, text in pages)
                if not has_text and len(document) > 0:
                    # Attempt PyMuPDF OCR fallback if tessdata / Tesseract is available
                    try:
                        ocr_pages = []
                        for page_number, page in enumerate(document, start=1):
                            textpage = page.get_textpage_ocr()
                            ocr_pages.append((page_number, textpage.extractTEXT()))
                        if any(t.strip() for _, t in ocr_pages):
                            return ocr_pages
                    except Exception:
                        pass
                    raise ValueError(
                        "This PDF appears to be scanned or contains only images without readable digital text layers."
                    )
                return pages
        except pymupdf.FileDataError as error:
            raise ValueError("The uploaded PDF is damaged or invalid.") from error
    if file_path.suffix.lower() == ".txt":
        try:
            with file_path.open("r", encoding="utf-8-sig") as source_file:
                return [(1, source_file.read())]
        except UnicodeDecodeError:
            with file_path.open("r", encoding="latin-1", errors="replace") as source_file:
                return [(1, source_file.read())]
    raise ValueError("Only PDF and TXT files are supported.")


def extract_text_from_pdf(pdf_path):
    return "\n".join(text for _, text in extract_pages_from_file(pdf_path))


def extract_text_from_file(file_path):
    return "\n".join(text for _, text in extract_pages_from_file(file_path))