"""Names and references for files stored in a PDF."""
from dataclasses import dataclass
import math
from pathlib import PureWindowsPath
import re

from app.i18n import tr
from app.workspace import PageRef


@dataclass(frozen=True)
class PdfAttachment:
    ref: PageRef
    key: int | str  # Catalogue entry index or page annotation ID.
    name: str
    page_number: int | None = None


@dataclass(frozen=True)
class EmbeddedPdfLink:
    attachment: int | tuple[int | str, int | str]  # Link xref or page and annotation selectors.
    destination: int | str  # Page number (zero-based) or named destination in the child PDF.
    point: tuple[float, float] | None = None


@dataclass(frozen=True)
class EmbeddedLinkSnapshot:
    xref: int
    rect: tuple[float, float, float, float]
    flags: int
    destination: str
    attachment: str | tuple[int, str]  # Original PDF byte-string literal or page and annotation ID.


def attachment_name(info):
    name = info.get('ufilename') or info.get('filename') or info.get('name') or ''
    name = re.split(r'[/\\]', name)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f\u202a-\u202e\u2066-\u2069]', '_', name).strip(' .')
    if not name:
        return 'attachment'
    path = PureWindowsPath(name)
    prefix = '_' if path.is_reserved() else ''
    return prefix + path.stem[:120] + path.suffix[:20]


def file_attachment_annotations(page):
    import pymupdf
    from app.pdfedits import identified_annotations

    kind = pymupdf.PDF_ANNOT_FILE_ATTACHMENT
    if any(annotation_type == kind for _, annotation_type, _ in page.annot_xrefs()):
        yield from ((identifier, annot) for identifier, annot in identified_annotations(page)
                    if annot.type[0] == kind)


def _pdf_selector(mupdf, value, *, allow_name=False):
    if mupdf.pdf_is_int(value):
        number = mupdf.pdf_to_int(value)
        return number if number >= 0 else None
    if mupdf.pdf_is_string(value):
        return mupdf.pdf_to_text_string(value) or None
    if allow_name and mupdf.pdf_is_name(value):
        return mupdf.pdf_to_name(value) or None
    return None


def _embedded_pdf_target(mupdf, action, xref):
    get = mupdf.pdf_dict_gets
    target = get(action, 'T')
    if (not mupdf.pdf_is_null(get(action, 'F')) or not mupdf.pdf_is_null(get(target, 'T'))
            or mupdf.pdf_to_name(get(target, 'R')) != 'C'):
        return None
    name = get(target, 'N')
    page = _pdf_selector(mupdf, get(target, 'P'))
    annotation = _pdf_selector(mupdf, get(target, 'A'))
    if mupdf.pdf_is_string(name) and page is None and annotation is None:
        attachment = xref
    elif mupdf.pdf_is_null(name) and page is not None and annotation is not None:
        attachment = (page, annotation)
    else:
        return None
    destination = get(action, 'D')
    point = None
    if mupdf.pdf_is_array(destination):
        number = _pdf_selector(mupdf, mupdf.pdf_array_get(destination, 0))
        if not isinstance(number, int):
            return None
        view = mupdf.pdf_to_name(mupdf.pdf_array_get(destination, 1))
        if view == 'XYZ':
            coords = [mupdf.pdf_array_get(destination, i) for i in (2, 3)]
            if all(mupdf.pdf_is_number(coord) for coord in coords):
                x, y = (mupdf.pdf_to_real(coord) for coord in coords)
                if math.isfinite(x) and math.isfinite(y):
                    point = (x, y)
        return EmbeddedPdfLink(attachment, number, point)
    name = _pdf_selector(mupdf, destination, allow_name=True)
    return EmbeddedPdfLink(attachment, name) if isinstance(name, str) else None


def _embedded_pdf_actions(page):
    """Read GoToE actions omitted by Page.get_links(), using raw PDF coordinates."""
    import pymupdf as p

    mupdf = p.mupdf
    pdf = p._as_pdf_document(page.parent)
    get = mupdf.pdf_dict_gets
    for xref, kind, _ in page.annot_xrefs():
        if kind != p.PDF_ANNOT_LINK:
            continue
        try:
            annot = mupdf.pdf_load_object(pdf, xref)
            action = get(annot, 'A')
            if mupdf.pdf_to_name(get(action, 'S')) != 'GoToE':
                continue
            flags = mupdf.pdf_to_int(get(annot, 'F'))
            rect = p.Rect(mupdf.pdf_to_rect(get(annot, 'Rect'))).normalize()
            if not rect.is_empty and not rect.is_infinite and all(map(math.isfinite, rect)):
                yield xref, rect, flags, action, _embedded_pdf_target(mupdf, action, xref)
        except (mupdf.FzErrorBase, ValueError, RuntimeError):
            continue


def embedded_pdf_links(page):
    import pymupdf as p

    for _, rect, flags, _, target in _embedded_pdf_actions(page):
        if not flags & (p.PDF_ANNOT_IS_HIDDEN | p.PDF_ANNOT_IS_NO_VIEW):
            yield rect, target


def _pdf_literal(p, obj):
    return p.JM_BinFromBuffer(p.JM_object_to_buffer(obj, 1, 1)).decode('ascii')


def embedded_link_snapshots(page):
    """Capture local attachment targets independently of PDF object numbers."""
    import pymupdf as p

    mupdf = p.mupdf
    document = page.parent
    pdf = p._as_pdf_document(document)
    get = mupdf.pdf_dict_gets
    for xref, rect, flags, action, link in _embedded_pdf_actions(page):
        if link is None:
            continue
        try:
            if isinstance(link.attachment, int):
                key = get(get(action, 'T'), 'N')
                filespec = mupdf.pdf_lookup_name(pdf, p.PDF_NAME('EmbeddedFiles'), key)
                if not mupdf.pdf_is_embedded_file(filespec):
                    continue
                attachment = _pdf_literal(p, mupdf.pdf_resolve_indirect_chain(key))
            else:
                attachment = page_attachment_reference(document, *link.attachment)
                if attachment is None:
                    continue
            value = mupdf.pdf_resolve_indirect_chain(get(action, 'D'))
            if mupdf.pdf_is_array(value):
                # A child destination array contains page, view, and coordinates.
                # Preserve their values without copying any source object references.
                parts = [mupdf.pdf_resolve_indirect_chain(mupdf.pdf_array_get(value, index))
                         for index in range(mupdf.pdf_array_len(value))]
                if not all(mupdf.pdf_is_number(part) or mupdf.pdf_is_name(part) or mupdf.pdf_is_null(part)
                           for part in parts):
                    continue
                destination = '[' + ' '.join(_pdf_literal(p, part) for part in parts) + ']'
            else:
                destination = _pdf_literal(p, value)
            yield EmbeddedLinkSnapshot(xref, tuple(rect), flags, destination, attachment)
        except (mupdf.FzErrorBase, ValueError, RuntimeError):
            continue


def catalogue_target_name(name):
    import pymupdf as p

    # embfile_add() creates name-tree keys with MuPDF's text string encoding.
    return _pdf_literal(p, p.mupdf.pdf_new_text_string(name))


def page_attachment_target(document, number, identifier):
    import pymupdf as p

    page = document[number]
    for token, annot in file_attachment_annotations(page):
        if token != identifier:
            continue
        name = p.get_pdf_str(identifier)
        # An annotation index can change when unrelated annotations are deleted.
        if annot.info.get('id') != identifier:
            document.xref_set_key(annot.xref, 'NM', name)
        return f'/P {number} /A {name}'
    raise ValueError(tr('找不到此附件。'))


def stabilize_page_attachment_links(document, links):
    """Keep page-attachment links working when edits change annotation order."""
    for link in links:
        if not isinstance(link.attachment, tuple):
            continue
        target = page_attachment_target(document, *link.attachment)
        kind, value = document.xref_get_key(link.xref, 'A')
        xref, key = (int(value.split()[0]), 'T') if kind == 'xref' else (link.xref, 'A/T')
        document.xref_set_key(xref, key, f'<< /R /C {target} >>')


def insert_embedded_pdf_link(page, link, target):
    # PyMuPDF's insert_link() has no kind for GoToE. Use its underlying
    # annotation writer with the validated destination and raw PDF rectangle.
    rect = ' '.join(f'{coordinate:.9f}' for coordinate in link.rect)
    page._addAnnot_FromString((f'<< /Type /Annot /Subtype /Link /Rect [{rect}] '
                               f'/F {link.flags} /BS << /W 0 >> /A << /S /GoToE '
                               f'/D {link.destination} /T << /R /C {target} >> >> >>',))


def _catalogue_file(p, filespec, name):
    mupdf = p.mupdf
    info = {key: mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(filespec, field))
            for key, field in (('ufilename', 'UF'), ('filename', 'F'), ('description', 'Desc'))}
    info['name'] = name
    data = p.JM_BinFromBuffer(mupdf.pdf_load_embedded_file_contents(filespec))
    return info, data


def catalogue_link_attachment(document, xref):
    """Look up the original byte-string key in the EmbeddedFiles name tree."""
    import pymupdf as p

    mupdf = p.mupdf
    pdf = p._as_pdf_document(document)
    annot = mupdf.pdf_load_object(pdf, xref)
    key = mupdf.pdf_dict_getl(annot, p.PDF_NAME('A'), p.PDF_NAME('T'), p.PDF_NAME('N'))
    filespec = mupdf.pdf_lookup_name(pdf, p.PDF_NAME('EmbeddedFiles'), key)
    if not mupdf.pdf_is_embedded_file(filespec):
        return None
    info, data = _catalogue_file(p, filespec, mupdf.pdf_to_text_string(key))
    return attachment_name(info), data


def catalogue_files(document):
    """Keep the tree's byte-string keys, including names below /Kids."""
    import pymupdf as p

    mupdf = p.mupdf
    pdf = p._as_pdf_document(document)
    pending = [_catalogue_tree(p, pdf)]
    seen = set()
    while pending:
        node = pending.pop()
        xref = mupdf.pdf_to_num(node)
        if xref:
            if xref in seen:
                raise ValueError(tr('无法读取附件。'))
            seen.add(xref)
        names = mupdf.pdf_dict_gets(node, 'Names')
        for index in range(0, mupdf.pdf_array_len(names), 2):
            key = mupdf.pdf_resolve_indirect_chain(mupdf.pdf_array_get(names, index))
            filespec = mupdf.pdf_array_get(names, index + 1)
            if not mupdf.pdf_is_string(key) or not mupdf.pdf_is_embedded_file(filespec):
                raise ValueError(tr('无法读取附件。'))
            info, data = _catalogue_file(p, filespec, mupdf.pdf_to_text_string(key))
            yield _pdf_literal(p, key), info, data
        kids = mupdf.pdf_dict_gets(node, 'Kids')
        pending.extend(mupdf.pdf_array_get(kids, index)
                       for index in reversed(range(mupdf.pdf_array_len(kids))))


def _catalogue_tree(p, pdf):
    mupdf = p.mupdf
    root = mupdf.pdf_dict_gets(mupdf.pdf_trailer(pdf), 'Root')
    return mupdf.pdf_dict_getl(root, p.PDF_NAME('Names'), p.PDF_NAME('EmbeddedFiles'))


def select_pages_preserving_attachments(document, pages):
    """Keep the original name tree and its byte-string keys across Document.select()."""
    import pymupdf as p

    mupdf = p.mupdf
    pdf = p._as_pdf_document(document)
    files = _catalogue_tree(p, pdf)
    document.select(pages)
    if not mupdf.pdf_is_null(files):
        root = mupdf.pdf_dict_gets(mupdf.pdf_trailer(pdf), 'Root')
        mupdf.pdf_dict_putl(root, files, p.PDF_NAME('Names'), p.PDF_NAME('EmbeddedFiles'))


def named_pdf_destination(document, name):
    import pymupdf as p
    from app.pdfstructure import _destination

    try:
        return _destination(document, {'kind': p.LINK_NAMED, 'name': name})
    except (p.mupdf.FzErrorBase, ValueError, RuntimeError):
        return None


def page_attachment_reference(document, page, annotation):
    """Resolve /P and /A against the original page's Annots array."""
    if isinstance(page, str):
        page = (named_pdf_destination(document, page) or {}).get('page', -1)
    if not 0 <= page < document.page_count:
        return None
    source = document[page]
    by_index = isinstance(annotation, int)
    xrefs = source.annot_xrefs()
    if by_index and not 0 <= annotation < len(xrefs):
        return None
    for identifier, annot in file_attachment_annotations(source):
        if (annot.xref == xrefs[annotation][0] if by_index else annot.info.get('id') == annotation):
            return page, identifier
    return None
