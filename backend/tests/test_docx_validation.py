import dataclasses
import io
import zipfile
from pathlib import Path

import pytest

from app.models.resume import ArtifactFormat
from app.services.docx_validation import (
    DOCXArchiveLimitExceededError,
    DOCXValidationError,
    EncryptedDOCXError,
    InvalidDOCXSignatureError,
    InvalidDOCXStructureError,
    MalformedDOCXError,
    UnsafeDOCXContentError,
    validate_docx_file,
)

MINIMAL_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

MINIMAL_RELS = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

MINIMAL_DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Confidential Candidate Resume</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


def _create_minimal_docx_bytes(extra_files: dict[str, bytes] = None, exclude_files: set[str] = None) -> bytes:
    """Create a minimal valid DOCX package in memory."""
    buf = io.BytesIO()
    base_files = {
        "[Content_Types].xml": MINIMAL_CONTENT_TYPES,
        "_rels/.rels": MINIMAL_RELS,
        "word/document.xml": MINIMAL_DOCUMENT_XML,
    }
    if exclude_files:
        for ex in exclude_files:
            base_files.pop(ex, None)
    if extra_files:
        base_files.update(extra_files)

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in base_files.items():
            zf.writestr(name, content)

    return buf.getvalue()


# 1. A minimal valid non-macro DOCX passes.
def test_valid_minimal_docx(tmp_path):
    docx_path = tmp_path / "valid.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes())

    result = validate_docx_file(docx_path)
    assert result.detected_format == ArtifactFormat.docx
    assert result.entry_count == 3
    assert result.total_expanded_bytes > 0


# 2. An ordinary ZIP missing Word package structure fails.
def test_ordinary_zip_missing_structure(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.txt", b"plain text inside zip")

    docx_path = tmp_path / "plain_zip.docx"
    docx_path.write_bytes(buf.getvalue())

    with pytest.raises(InvalidDOCXStructureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_STRUCTURE"
    assert exc_info.value.status_code == 422


# 3. A non-ZIP file claiming to be DOCX fails signature validation.
def test_invalid_signature_docx(tmp_path):
    docx_path = tmp_path / "fake.docx"
    docx_path.write_bytes(b"NOT_A_ZIP_HEADER and not docx")

    with pytest.raises(InvalidDOCXSignatureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_SIGNATURE"
    assert exc_info.value.status_code == 415


def test_ole_signature_docx_rejected(tmp_path):
    docx_path = tmp_path / "old_doc.docx"
    docx_path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00\x00\x00")

    with pytest.raises(InvalidDOCXSignatureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_SIGNATURE"
    assert exc_info.value.status_code == 415


# 4. A corrupt central directory or CRC fails.
def test_corrupt_zip_fails(tmp_path):
    docx_path = tmp_path / "corrupt.docx"
    valid_bytes = _create_minimal_docx_bytes()
    # Corrupt the header/central directory
    corrupt_bytes = valid_bytes[:30] + b"\xff" * 50
    docx_path.write_bytes(corrupt_bytes)

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"
    assert exc_info.value.status_code == 422


# 5. Missing [Content_Types].xml fails.
def test_missing_content_types_fails(tmp_path):
    docx_path = tmp_path / "no_content_types.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(exclude_files={"[Content_Types].xml"}))

    with pytest.raises(InvalidDOCXStructureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_STRUCTURE"


# 6. Missing _rels/.rels fails.
def test_missing_rels_fails(tmp_path):
    docx_path = tmp_path / "no_rels.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(exclude_files={"_rels/.rels"}))

    with pytest.raises(InvalidDOCXStructureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_STRUCTURE"


# 7. Missing word/document.xml fails.
def test_missing_document_xml_fails(tmp_path):
    docx_path = tmp_path / "no_doc.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(exclude_files={"word/document.xml"}))

    with pytest.raises(InvalidDOCXStructureError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "INVALID_DOCX_STRUCTURE"


# 8. A macro-enabled content type fails.
def test_macro_content_type_fails(tmp_path):
    macro_content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/>
</Types>
"""
    docx_path = tmp_path / "macro_type.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"[Content_Types].xml": macro_content_types}))

    with pytest.raises(UnsafeDOCXContentError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"
    assert exc_info.value.status_code == 422


# 9. word/vbaProject.bin fails.
def test_vba_project_bin_fails(tmp_path):
    docx_path = tmp_path / "vba.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/vbaProject.bin": b"VBA_BINARY"}))

    with pytest.raises(UnsafeDOCXContentError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"
    assert exc_info.value.status_code == 422


# 10. word/activeX content fails.
def test_activex_content_fails(tmp_path):
    docx_path = tmp_path / "activex.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/activeX/activeX1.xml": b"<xml/>"}))

    with pytest.raises(UnsafeDOCXContentError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"
    assert exc_info.value.status_code == 422


# 11. word/embeddings content fails.
def test_embeddings_content_fails(tmp_path):
    docx_path = tmp_path / "embed.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/embeddings/oleObject1.bin": b"OLE_DATA"}))

    with pytest.raises(UnsafeDOCXContentError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"
    assert exc_info.value.status_code == 422


# 12. A path traversal member fails.
def test_path_traversal_member_fails(tmp_path):
    docx_path = tmp_path / "traversal.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"../../evil.xml": b"<xml/>"}))

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"
    assert exc_info.value.status_code == 422


# 13. A backslash traversal member fails.
def test_backslash_traversal_member_fails(tmp_path):
    docx_path = tmp_path / "bs_traversal.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"..\\..\\evil.xml": b"<xml/>"}))

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"


# 14. An absolute-path member fails.
def test_absolute_path_member_fails(tmp_path):
    docx_path = tmp_path / "abs_path.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"/etc/passwd.xml": b"<xml/>"}))

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"


# 15. A symbolic-link member fails.
def test_symbolic_link_member_fails(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", MINIMAL_CONTENT_TYPES)
        zf.writestr("_rels/.rels", MINIMAL_RELS)
        zf.writestr("word/document.xml", MINIMAL_DOCUMENT_XML)

        # Add symlink entry
        symlink_info = zipfile.ZipInfo("symlink_entry")
        symlink_info.create_system = 3  # UNIX
        symlink_info.external_attr = 0o120777 << 16  # S_IFLNK
        zf.writestr(symlink_info, "/etc/passwd")

    docx_path = tmp_path / "symlink.docx"
    docx_path.write_bytes(buf.getvalue())

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"


# 16. Duplicate normalized archive entries fail.
def test_duplicate_archive_entries_fail(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", MINIMAL_CONTENT_TYPES)
        zf.writestr("_rels/.rels", MINIMAL_RELS)
        zf.writestr("word/document.xml", MINIMAL_DOCUMENT_XML)
        # Duplicate entry with different case
        zf.writestr("WORD/DOCUMENT.XML", MINIMAL_DOCUMENT_XML)

    docx_path = tmp_path / "dup.docx"
    docx_path.write_bytes(buf.getvalue())

    with pytest.raises(MalformedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "MALFORMED_DOCX"


# 17. Archive entry-count limit fails using a small test-specific limit.
def test_entry_count_limit_fails(tmp_path):
    docx_path = tmp_path / "many_entries.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes())

    # Limit of 2 entries while archive has 3
    with pytest.raises(DOCXArchiveLimitExceededError) as exc_info:
        validate_docx_file(docx_path, max_docx_archive_entries=2)
    assert exc_info.value.code == "DOCX_ARCHIVE_LIMIT_EXCEEDED"
    assert exc_info.value.status_code == 422


# 18. Expanded-size limit fails using a small test-specific limit.
def test_expanded_size_limit_fails(tmp_path):
    docx_path = tmp_path / "expanded_limit.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes())

    # Limit of 50 bytes while minimal docx is ~700 bytes
    with pytest.raises(DOCXArchiveLimitExceededError) as exc_info:
        validate_docx_file(docx_path, max_docx_expanded_bytes=50)
    assert exc_info.value.code == "DOCX_ARCHIVE_LIMIT_EXCEEDED"


# 19. Compression-ratio limit fails using a small test-specific limit.
def test_compression_ratio_limit_fails(tmp_path):
    docx_path = tmp_path / "ratio_limit.docx"
    # Uncompressed text repeated many times compresses heavily
    huge_repetitive = b"A" * 50000
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/extra.xml": huge_repetitive}))

    # Low compression ratio limit (e.g. 2)
    with pytest.raises(DOCXArchiveLimitExceededError) as exc_info:
        validate_docx_file(docx_path, max_docx_compression_ratio=2)
    assert exc_info.value.code == "DOCX_ARCHIVE_LIMIT_EXCEEDED"


# 20. An encrypted ZIP member is rejected.
def test_encrypted_zip_member_rejected(tmp_path):
    raw_bytes = bytearray(_create_minimal_docx_bytes())
    # Set bit 0 of flag_bits in the central directory entry and local file header
    cd_idx = raw_bytes.find(b"PK\x01\x02")
    assert cd_idx != -1
    raw_bytes[cd_idx + 8] |= 0x01

    loc_idx = raw_bytes.find(b"PK\x03\x04")
    assert loc_idx != -1
    raw_bytes[loc_idx + 6] |= 0x01

    docx_path = tmp_path / "encrypted.docx"
    docx_path.write_bytes(bytes(raw_bytes))

    with pytest.raises(EncryptedDOCXError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "ENCRYPTED_DOCX"
    assert exc_info.value.status_code == 422


# 21. An external attached-template relationship fails.
def test_external_attached_template_fails(tmp_path):
    unsafe_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" Target="https://evil.com/template.dotm" TargetMode="External"/>
</Relationships>
"""
    docx_path = tmp_path / "template.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/_rels/document.xml.rels": unsafe_rels}))

    with pytest.raises(UnsafeDOCXContentError) as exc_info:
        validate_docx_file(docx_path)
    assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"


# 22. A javascript/file/data relationship fails.
def test_unsafe_schemes_in_relationship_fail(tmp_path):
    for scheme in ["javascript:alert(1)", "file:///etc/passwd", "data:text/html;base64,PHNjcmlwdD4="]:
        unsafe_rels = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{scheme}" TargetMode="External"/>
</Relationships>
""".encode("utf-8")
        buf = _create_minimal_docx_bytes(extra_files={"word/_rels/document.xml.rels": unsafe_rels})
        docx_path = tmp_path / f"unsafe_scheme_{hash(scheme)}.docx"
        docx_path.write_bytes(buf)

        with pytest.raises(UnsafeDOCXContentError) as exc_info:
            validate_docx_file(docx_path)
        assert exc_info.value.code == "UNSAFE_DOCX_CONTENT"


# 23. A normal HTTPS hyperlink is allowed.
def test_safe_https_hyperlink_allowed(tmp_path):
    safe_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://linkedin.com/in/example" TargetMode="External"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="mailto:candidate@example.com" TargetMode="External"/>
</Relationships>
"""
    docx_path = tmp_path / "safe_links.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/_rels/document.xml.rels": safe_rels}))

    result = validate_docx_file(docx_path)
    assert result.detected_format == ArtifactFormat.docx


# 24. Normal word/media images are allowed within limits.
def test_normal_media_image_allowed(tmp_path):
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    docx_path = tmp_path / "with_image.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes(extra_files={"word/media/image1.png": png_header}))

    result = validate_docx_file(docx_path)
    assert result.detected_format == ArtifactFormat.docx
    assert result.entry_count == 4


# 25. The validator never extracts files to disk.
def test_validator_never_extracts_to_disk(tmp_path):
    before_files = set(tmp_path.iterdir())
    docx_path = tmp_path / "test_extract.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes())

    validate_docx_file(docx_path)

    after_files = set(tmp_path.iterdir())
    # Only the created input file exists
    assert after_files - before_files == {docx_path}


# 26. The result contains no document text.
def test_validation_result_contains_no_document_text(tmp_path):
    docx_path = tmp_path / "clean_result.docx"
    docx_path.write_bytes(_create_minimal_docx_bytes())

    result = validate_docx_file(docx_path)
    res_dict = dataclasses.asdict(result)
    assert set(res_dict.keys()) == {"detected_format", "entry_count", "total_expanded_bytes"}
    assert "Confidential" not in str(res_dict)
    assert "Resume" not in str(res_dict)
