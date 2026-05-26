from app.core.errors import AppError
from app.domains.knowledge.service import _extract_text_from_file_bytes


def test_extract_txt_file_bytes() -> None:
    out = _extract_text_from_file_bytes("notes.txt", "text/plain", b"Support policy for premium users")
    assert "Support policy" in out


def test_extract_docx_with_wrong_extension_still_works() -> None:
    # MIME should still route to docx parser even if extension is unexpected.
    from docx import Document
    import io

    buffer = io.BytesIO()
    doc = Document()
    doc.add_paragraph("This is a DOCX paragraph.")
    doc.save(buffer)
    payload = buffer.getvalue()

    out = _extract_text_from_file_bytes("upload.bin", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", payload)
    assert "DOCX paragraph" in out


def test_extract_doc_rtf_payload() -> None:
    # Common legacy upload case: .doc extension with RTF body.
    payload = br"{\rtf1\ansi\deff0 {\fonttbl {\f0 Arial;}} \f0\fs24 Refund policy is 30 days.}"
    out = _extract_text_from_file_bytes("policy.doc", "application/msword", payload)
    assert "Refund policy is 30 days" in out


def test_unsupported_file_type_raises_clear_error() -> None:
    try:
        _extract_text_from_file_bytes("archive.zip", "application/zip", b"PK\x03\x04")
        assert False, "Expected AppError"
    except AppError as exc:
        assert exc.code == "knowledge.file_type_unsupported"
        assert ".pdf, .txt, .doc, or .docx" in exc.message
