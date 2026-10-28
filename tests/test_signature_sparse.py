"""Unsigned PDFs with gaps in their object numbers must remain mergeable."""
import hashlib

import pymupdf as p
import pytest

from app.pdfio import PdfError, PdfStore


def sparse_pdf(path, signature='none'):
    objects = {
        1: b'<< /Type /Catalog /Pages 2 0 R >>',
        2: b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        3: b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] >>',
        600: {
            'none': b'<< /Unused true >>',
            'present': b'<< /Type /Sig /ByteRange [0 1 2 3] /Contents <12345678> >>',
            'unknown': b'<< /FT /Sig /V 599 0 R >>',
        }[signature],
    }
    data = b'%PDF-1.7\n'
    offsets = {}
    for number, obj in objects.items():
        offsets[number] = len(data)
        data += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 1\n0000000000 65535 f \n'
    for number, offset in offsets.items():
        data += f'{number} 1\n{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size 601 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    path.write_bytes(data)
    return path


@pytest.mark.parametrize('deferred', [False, True])
@pytest.mark.parametrize('signature', ['none', 'present', 'unknown'])
def test_sparse_object_table_preserves_signature_checks_and_merge(tmp_path, deferred, signature):
    source = sparse_pdf(tmp_path / 'sparse.pdf', signature)
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    other = tmp_path / 'other.pdf'
    with p.open() as doc:
        doc.new_page()
        doc.save(other)
    store = PdfStore()
    try:
        pages = store.load(str(source), defer_safety=deferred)
        if deferred:
            assert store._source(pages[0].source_id).safety.signature == 'pending'
            assert store.operation_reason(pages, 'modify')
            while not store.prepare_safety_step(pages[0]):
                pass
        assert store._source(pages[0].source_id).safety.signature == signature
        combined = pages + store.load(str(other))
        target = tmp_path / 'merged.pdf'
        if signature == 'none':
            assert not store.operation_reason(combined, 'modify')
            store.write(combined, str(target))
            with p.open(target) as doc:
                assert doc.page_count == 2
        else:
            with pytest.raises(PdfError):
                store.write(combined, str(target))
            assert not target.exists()
        assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    finally:
        store.close_all()
