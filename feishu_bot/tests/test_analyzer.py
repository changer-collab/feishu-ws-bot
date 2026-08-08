from pathlib import Path

from feishu_bot import analyzer


def test_extract_pdf_content_uses_ocr_for_every_page(monkeypatch, tmp_path):
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    monkeypatch.setattr(analyzer, "_extract_pdf_page_texts",
                        lambda _: ["native page 1", "native page 2"])
    calls = []

    def fake_ocr(path: Path):
        calls.append(path)
        return ["ocr page 1", "ocr page 2"]

    monkeypatch.setattr(analyzer, "_ocr_pdf_pages", fake_ocr)

    result = analyzer.extract_pdf_content(pdf_path)

    assert calls == [pdf_path]
    assert result.text == "native page 1\nnative page 2"
    assert result.ocr_text == "ocr page 1\nocr page 2"
    assert result.combined_text == (
        "native page 1\nnative page 2\nocr page 1\nocr page 2")


def test_extract_text_from_pdf_returns_combined_content(monkeypatch, tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        analyzer,
        "extract_pdf_content",
        lambda _: analyzer.PdfTextContent(text="", ocr_text="recognized text"),
    )

    assert analyzer.extract_text_from_pdf(pdf_path) == "recognized text"
