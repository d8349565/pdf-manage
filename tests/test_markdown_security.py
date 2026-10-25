"""Markdown input must not expand the user's selected local resource boundary."""
from pathlib import Path
from types import SimpleNamespace
import stat

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QImage, QTextDocument
from PyQt6.QtWidgets import QApplication

from app.markdownconvert import _LocalDocument


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def document(tmp_path,qapp):
    root = tmp_path / "markdown"
    root.mkdir()
    doc = _LocalDocument(root, 1000, 1400)
    yield doc
    doc.deleteLater()
    qapp.processEvents()


def save_picture(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(32, 24, QImage.Format.Format_RGB32)
    image.fill(0xff008800)
    assert image.save(str(path))


def test_local_subdirectory_and_encoded_filename_still_load(document):
    picture = document.root / "images/纸序 preview.png"
    save_picture(picture)
    for url in (QUrl("images/纸序%20preview.png"), QUrl.fromLocalFile(str(picture))):
        result = document._load_image(url, url.toString())
        assert result is not None and result.size() == QImage(str(picture)).size()


@pytest.mark.parametrize("address", ["http://example.invalid/image.png", "https://example.invalid/image.png",
                                    "file://untrusted.invalid/share/image.png", "//untrusted.invalid/share/image.png",
                                    "data:image/png;base64,aGVsbG8=", "qrc:/image.png"])
def test_remote_resources_are_rejected_before_filesystem_access(document, monkeypatch, address):
    def unexpected_access(*args, **kwargs):
        pytest.fail("remote image triggered a filesystem access")
    monkeypatch.setattr(Path, "lstat", unexpected_access)
    url = QUrl(address)
    assert document._load_image(url, address) is None
    assert document.warnings


def test_outside_image_is_not_read_or_embedded(document, monkeypatch):
    outside = document.root.parent / "private.png"
    save_picture(outside)
    def unexpected_open(*args, **kwargs):
        pytest.fail("image outside the Markdown directory was opened")
    monkeypatch.setattr(Path, "open", unexpected_open)
    for url in (QUrl("../private.png"), QUrl("%2e%2e/private.png"), QUrl.fromLocalFile(str(outside))):
        assert document._load_image(url, url.toString()) is None


def test_qt_resource_loading_preserves_the_same_boundary(document, monkeypatch):
    import urllib.request
    local = document.root / "local.png"
    outside = document.root.parent / "private.png"
    save_picture(local)
    save_picture(outside)
    calls = []
    def unexpected_network(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Markdown tried to access the network")
    monkeypatch.setattr(urllib.request, "urlopen", unexpected_network)
    outside_url = QUrl.fromLocalFile(str(outside))
    document.setHtml(f'<p>Resource boundary</p><img src="local.png">'
                     f'<img src="{outside_url.toString()}">'
                     '<img src="http://example.invalid/image.png">')
    resource_type = QTextDocument.ResourceType.ImageResource.value
    assert document.resource(resource_type, QUrl.fromLocalFile(str(local))) is not None
    assert document.resource(resource_type, outside_url) is None
    assert document.resource(resource_type, QUrl("http://example.invalid/image.png")) is None
    assert not calls


def test_reparse_point_is_rejected_before_following_it(document, monkeypatch):
    linked = document.root / "linked"
    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == linked:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        assert not path.is_relative_to(linked), "followed the linked directory"
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", lstat)
    assert document._load_image(QUrl("linked/private.png"), "linked/private.png") is None


@pytest.mark.parametrize("content", [
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///private.png"/></svg>',
    '<!DOCTYPE svg [<!ENTITY private SYSTEM "file:///private.png">]><svg/>',
])
def test_svg_cannot_introduce_an_external_resource_loader(document, content):
    path = document.root / "image.svg"
    path.write_text(content, encoding="utf-8")
    assert document._load_image(QUrl("image.svg"), "image.svg") is None
    assert document.warnings


def test_huge_svg_output_is_bounded(document, monkeypatch):
    import app.markdownconvert as conversion
    monkeypatch.setattr(conversion, "MAX_IMAGE_PIXELS", 4096)
    path = document.root / "image.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1000000" height="1000000">'
                    '<rect width="1000000" height="1000000" fill="red"/></svg>', encoding="utf-8")
    image = document._load_image(QUrl("image.svg"), "image.svg")
    assert image is not None and image.width() * image.height() <= 4096


def test_oversized_raster_is_rejected_before_decoding(document, monkeypatch):
    import app.markdownconvert as conversion
    class Reader:
        def __init__(self, *args):
            pass
        def format(self):
            return b"png"
        def size(self):
            from PyQt6.QtCore import QSize
            return QSize(10000, 10000)
        def read(self):
            pytest.fail("decoded an image over the pixel limit")
    path = document.root / "image.png"
    path.write_bytes(b"generated oversized header")
    monkeypatch.setattr(conversion, "QImageReader", Reader)
    assert document._load_image(QUrl("image.png"), "image.png") is None
    assert any("2000 万像素" in warning for warning in document.warnings)
