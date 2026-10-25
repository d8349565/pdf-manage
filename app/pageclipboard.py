"""Read one user-requested clipboard snapshot; never poll, clear or change it."""
from pathlib import Path
from dataclasses import dataclass
from uuid import uuid4

from PyQt6.QtCore import QMimeData
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap

from app.imaging import IMAGE_SUFFIXES

PAGE_MIME='application/x-zhixupdf-page-selection-v1'


@dataclass(frozen=True)
class PageToken:
    token: str


@dataclass(frozen=True)
class CopiedPages:
    token: str
    pages: tuple


def write_pages(pages,*,set_mime=None):
    """Publish an opaque handle; PDF bytes and passwords stay inside this window."""
    selection=CopiedPages(uuid4().hex,tuple(pages))
    mime=QMimeData();mime.setData(PAGE_MIME,selection.token.encode('ascii'))
    if set_mime is None:
        QGuiApplication.clipboard().setMimeData(mime)
    else:
        set_mime(mime)
    return selection


def read_pages(mime=None):
    """Return local file paths or an owned bitmap, preferring files over previews."""
    if mime is None:
        mime=QGuiApplication.clipboard().mimeData()
    if mime is None:
        raise ValueError('剪贴板为空。请先复制 PDF、图片文件或截图。')
    if mime.hasFormat(PAGE_MIME):
        data=mime.data(PAGE_MIME)
        if data.size()!=32:
            raise ValueError('页面复制记录无效，请重新复制页面。')
        token=bytes(data).decode('ascii',errors='replace')
        if any(value not in '0123456789abcdef' for value in token):
            raise ValueError('页面复制记录无效，请重新复制页面。')
        return (),PageToken(token)
    if mime.hasUrls():
        urls=mime.urls()
        local=[url.toLocalFile() for url in urls if url.isLocalFile()]
        if local:
            if len(local)!=len(urls):
                raise ValueError('请复制本机的 PDF 或图片文件，暂不导入网址。')
            for value in local:
                path=Path(value)
                if path.suffix.lower() not in ('.pdf','.ofd',*IMAGE_SUFFIXES):
                    raise ValueError('不支持粘贴 '+path.name+'；请选择 PDF、OFD 或图片文件。')
            return tuple(local),None
    if mime.hasImage():
        image=mime.imageData()
        if isinstance(image,QPixmap):
            image=image.toImage()
        if not isinstance(image,QImage) or image.isNull():
            raise ValueError('剪贴板图片无法读取，请重新复制。')
        if image.width()*image.height()>64_000_000:
            raise ValueError('剪贴板图片超过 6400 万像素，请先缩小尺寸。')
        return (),image.copy()
    raise ValueError('请先复制 PDF、图片文件或截图，再粘贴为页面。')
