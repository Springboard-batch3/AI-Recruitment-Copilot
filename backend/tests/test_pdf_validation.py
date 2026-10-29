import dataclasses
import io
from pathlib import Path
import pytest
import pypdf
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

from app.models.resume import ArtifactFormat
from app.services.pdf_validation import (
    EmptyPDFError,
    EncryptedPDFError,
    InvalidPDFSignatureError,
    MalformedPDFError,
    PDFPageLimitExceededError,
    PDFValidationError,
    UnsafePDFContentError,
    validate_pdf_file,
)


def _create_simple_pdf_bytes(page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# 1. A neutral one-page PDF passes.
def test_valid_one_page_pdf(tmp_path):
    pdf_path = tmp_path / "valid.pdf"
    pdf_path.write_bytes(_create_simple_pdf_bytes(1))

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


# 2. A PDF at exactly max_resume_pages passes.
def test_valid_max_pages_pdf(tmp_path):
    pdf_path = tmp_path / "exact_max.pdf"
    pdf_path.write_bytes(_create_simple_pdf_bytes(5))

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 5


# 3. A PDF above the configured page limit fails.
def test_exceed_page_limit_pdf(tmp_path):
    pdf_path = tmp_path / "over_limit.pdf"
    pdf_path.write_bytes(_create_simple_pdf_bytes(6))

    with pytest.raises(PDFPageLimitExceededError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "PDF_PAGE_LIMIT_EXCEEDED"
    assert exc_info.value.status_code == 422


# 4. A zero-page PDF fails.
def test_zero_page_pdf(tmp_path):
    writer = PdfWriter()
    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "zero_page.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises((EmptyPDFError, MalformedPDFError)) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.status_code == 422


# 5. A file with a .pdf claim but missing %PDF- fails.
def test_invalid_signature_pdf(tmp_path):
    pdf_path = tmp_path / "fake.pdf"
    pdf_path.write_bytes(b"NOT_A_PDF_HEADER %PDF-1.4 inside")

    with pytest.raises(InvalidPDFSignatureError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "INVALID_PDF_SIGNATURE"
    assert exc_info.value.status_code == 415


# 6. A header-only or truncated PDF fails as malformed.
def test_truncated_pdf(tmp_path):
    pdf_path = tmp_path / "truncated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    with pytest.raises(MalformedPDFError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "MALFORMED_PDF"
    assert exc_info.value.status_code == 422


# 7. An encrypted PDF fails.
def test_encrypted_pdf(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret_password")
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(EncryptedPDFError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "ENCRYPTED_PDF"
    assert exc_info.value.status_code == 422


# 8. A JavaScript/OpenAction PDF fails.
def test_unsafe_open_action_javascript(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_js("app.alert('hello');")
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "js.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"
    assert exc_info.value.status_code == 422


# 9. A PDF containing an embedded attachment fails.
def test_unsafe_embedded_file(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_attachment("sample.txt", b"attachment data")
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "attachment.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"
    assert exc_info.value.status_code == 422


# 10. A PDF containing a normal URI link is not rejected solely for that link.
def test_safe_uri_link_annot(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_uri(
        page_number=0,
        uri="https://linkedin.com/in/example",
        rect=(10, 10, 50, 50),
    )
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "safe_link.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


# 11. Validation does not return extracted text or metadata.
# 12. Raw parser exception messages are not included in public failures.
def test_validation_result_cleanliness_and_error_encapsulation(tmp_path):
    pdf_path = tmp_path / "clean.pdf"
    pdf_path.write_bytes(_create_simple_pdf_bytes(1))

    result = validate_pdf_file(pdf_path, max_pages=5)
    result_dict = dataclasses.asdict(result)
    assert set(result_dict.keys()) == {"detected_format", "page_count"}

    # Error code structure check
    err = MalformedPDFError()
    assert err.code == "MALFORMED_PDF"
    assert "pypdf" not in err.message.lower()


# =============================================================================
# 13. REGRESSION TESTS (Phase 1B False Positive & Active Content Hardening)
# =============================================================================

REPORTLAB_SAMPLE_PDF = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Count 1
/Kids [ 3 0 R ]
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [ 0 0 612 792 ]
/Resources <<
/Font << /F1 4 0 R >>
>>
/Contents 5 0 R
>>
endobj
4 0 obj
<<
/Type /Font
/Subtype /Type1
/Name /F1
/BaseFont /Helvetica
/Encoding /WinAnsiEncoding
>>
endobj
5 0 obj
<<
/Filter [ /ASCII85Decode ]
/Length 24
>>
stream
87cURD]j7BIB2(&F*&5~>
endstream
endobj
6 0 obj
<<
/Author (Aarav Example)
/Producer (ReportLab PDF Library)
>>
endobj
xref
1 6
0000000009 00000 n 
0000000056 00000 n 
0000000111 00000 n 
0000000212 00000 n 
0000000311 00000 n 
0000000397 00000 n 
trailer
<<
/Size 7
/Root 1 0 R
/Info 6 0 R
>>
startxref
462
%%EOF"""


def test_reportlab_ascii85_pdf_accepted(tmp_path):
    """Standard ReportLab PDF with 1-indexed xref table, /ASCII85Decode and /Author is accepted."""
    pdf_path = tmp_path / "reportlab.pdf"
    pdf_path.write_bytes(REPORTLAB_SAMPLE_PDF)

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


def test_author_metadata_does_not_trigger_action_check(tmp_path):
    """Names like /Author, /ASCII85Decode, /WinAnsiEncoding do not trigger action checks."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({
        "/Author": "Aarav Example",
        "/Producer": "ReportLab 3.6",
    })
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "author.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


def test_openaction_destination_array_accepted(tmp_path):
    """Standard OpenAction destination array [page_ref /XYZ ...] is accepted."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    dest = ArrayObject([page.indirect_reference, NameObject("/XYZ"), NumberObject(0), NumberObject(0), NumberObject(0)])
    writer._root_object[NameObject("/OpenAction")] = dest
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "openaction_dest.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


def test_unsafe_uri_scheme_rejected(tmp_path):
    """URI link with javascript: or data: scheme is rejected."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_uri(
        page_number=0,
        uri="javascript:alert(document.cookie)",
        rect=(10, 10, 50, 50),
    )
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "js_link.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_unsafe_launch_action_rejected(tmp_path):
    """Launch action (/S /Launch) in annotation is rejected."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)

    launch_act = DictionaryObject({
        NameObject("/Type"): NameObject("/Action"),
        NameObject("/S"): NameObject("/Launch"),
        NameObject("/F"): TextStringObject("cmd.exe"),
    })
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(50)]),
        NameObject("/A"): launch_act,
    })
    page[NameObject("/Annots")] = ArrayObject([annot])

    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "launch.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_unsafe_page_aa_rejected(tmp_path):
    """Additional Actions (/AA) on page dictionary is rejected."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    page[NameObject("/AA")] = DictionaryObject({
        NameObject("/O"): DictionaryObject({
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/JavaScript"),
            NameObject("/JS"): TextStringObject("app.alert('pwned');"),
        })
    })

    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "page_aa.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_unsafe_annot_aa_rejected(tmp_path):
    """Additional Actions (/AA) on annotation dictionary is rejected."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(50)]),
        NameObject("/AA"): DictionaryObject({
            NameObject("/E"): DictionaryObject({
                NameObject("/S"): NameObject("/JavaScript"),
                NameObject("/JS"): TextStringObject("app.alert('hover');"),
            })
        }),
    })
    page[NameObject("/Annots")] = ArrayObject([annot])

    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "annot_aa.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_unsafe_acroform_xfa_rejected(tmp_path):
    """AcroForm containing /XFA is rejected."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/XFA"): ArrayObject([TextStringObject("template"), TextStringObject("<xml></xml>")]),
    })

    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "xfa.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_unsafe_richmedia_annot_rejected(tmp_path):
    """RichMedia annotation subtype is rejected."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/RichMedia"),
        NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(50)]),
    })
    page[NameObject("/Annots")] = ArrayObject([annot])

    buf = io.BytesIO()
    writer.write(buf)
    pdf_path = tmp_path / "richmedia.pdf"
    pdf_path.write_bytes(buf.getvalue())

    with pytest.raises(UnsafePDFContentError) as exc_info:
        validate_pdf_file(pdf_path, max_pages=5)
    assert exc_info.value.code == "UNSAFE_PDF_CONTENT"


def test_empty_trans_dict_accepted(tmp_path):
    """Empty /Trans <<>> on page dictionary is accepted."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    page[NameObject("/Trans")] = DictionaryObject({})
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "empty_trans.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


def test_standard_visual_page_transition_accepted(tmp_path):
    """Standard visual page transition /Trans << /Type /Trans /S /Dissolve /D 1 >> is accepted."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    page[NameObject("/Trans")] = DictionaryObject({
        NameObject("/Type"): NameObject("/Trans"),
        NameObject("/S"): NameObject("/Dissolve"),
        NameObject("/D"): NumberObject(1),
    })
    buf = io.BytesIO()
    writer.write(buf)

    pdf_path = tmp_path / "dissolve_trans.pdf"
    pdf_path.write_bytes(buf.getvalue())

    result = validate_pdf_file(pdf_path, max_pages=5)
    assert result.detected_format == ArtifactFormat.pdf
    assert result.page_count == 1


def test_validation_determinism(tmp_path):
    """Validating the same upload multiple times is strictly deterministic."""
    pdf_path = tmp_path / "deterministic.pdf"
    pdf_path.write_bytes(REPORTLAB_SAMPLE_PDF)

    res1 = validate_pdf_file(pdf_path, max_pages=5)
    res2 = validate_pdf_file(pdf_path, max_pages=5)

    assert res1.detected_format == res2.detected_format
    assert res1.page_count == res2.page_count

