"""Names and references for files stored in a PDF."""
from dataclasses import dataclass
import math
from pathlib import PureWindowsPath
import re

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


def embedded_pdf_links(page):
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
            if flags & (p.PDF_ANNOT_IS_HIDDEN | p.PDF_ANNOT_IS_NO_VIEW):
                continue
            rect = p.Rect(mupdf.pdf_to_rect(get(annot, 'Rect'))).normalize()
            if not rect.is_empty and not rect.is_infinite and all(map(math.isfinite, rect)):
                yield rect, _embedded_pdf_target(mupdf, action, xref)
        except (mupdf.FzErrorBase, ValueError, RuntimeError):
            continue


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
    info = {name: mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(filespec, field))
            for name, field in (('ufilename', 'UF'), ('filename', 'F'))}
    info['name'] = mupdf.pdf_to_text_string(key)
    data = p.JM_BinFromBuffer(mupdf.pdf_load_embedded_file_contents(filespec))
    return attachment_name(info), data


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
