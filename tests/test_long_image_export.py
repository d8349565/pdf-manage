"""Exercise the real PDF-to-images menu, settings, worker and image files."""
import errno
from pathlib import Path
from threading import Event

import pymupdf
import pytest
from PIL import Image, ImageChops
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app import imaging, ui
from app.exportdialogs import ImageExportDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def exporter(qapp, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    settings.setValue("updates/automatic", False)
    monkeypatch.setattr(ui, "QSettings", lambda *args: settings)
    window = ui.MainWindow()
    window.resize(1100, 900)
    window.show()
    folder = tmp_path / "out"
    folder.mkdir()
    summaries, warnings = [], []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(folder))

    def summary(box):
        summaries.append((box.windowTitle(), box.text(), box.detailedText()))
        return QMessageBox.StandardButton.Ok.value

    def warning(parent, title, message):
        warnings.append((title, message))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "exec", summary)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    yield window, folder, summaries, warnings
    window.ws.reset()
    window.close()
    qapp.processEvents()


def _export(window, configure):
    window.image_export_action.trigger()
    assert window.export_tools.currentIndex() == 5
    assert window.image_export_button.isVisible() and window.image_export_button.isEnabled()
    errors = []

    def use_settings():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, ImageExportDialog)
            configure(dialog)
            if dialog.isVisible():
                assert dialog.start.isEnabled(), dialog.feedback.text()
                dialog.start.click()
        except Exception as exc:
            errors.append(exc)
            if isinstance(dialog, ImageExportDialog):
                dialog.reject()

    QTimer.singleShot(0, use_settings)
    window.image_export_button.click()
    assert not errors, errors


def _long_settings(dialog, fmt="png", dpi=150, *, page_range=None, overwrite=False):
    assert dialog.mode.currentText() == "每页一张"
    if page_range is not None:
        dialog.range_mode.click()
        dialog.range.setText(page_range)
    dialog.format.setCurrentIndex(dialog.format.findData(fmt))
    dialog.dpi.setCurrentIndex(dialog.dpi.findData(dpi))
    dialog.overwrite.setChecked(overwrite)
    dialog.mode.setCurrentIndex(1)


def _pages(path, sizes):
    with pymupdf.open() as doc:
        for width, height in sizes:
            doc.new_page(width=width, height=height)
        doc.save(path)
    return str(path)


@pytest.fixture
def mixed_pdf(tmp_path):
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as doc:
        for width, height, crop, rotation, color in (
            (144, 216, (0, 0, 144, 216), 0, (1, 0, 0)),
            (240, 168, (24, 24, 216, 144), 90, (0, 1, 0)),
            (216, 120, (0, 0, 216, 120), 90, (0, 0, 1)),
        ):
            page = doc.new_page(width=width, height=height)
            x, y, _, _ = crop
            page.draw_rect(page.rect, color=None, fill=(1, 0, 1))
            page.draw_rect(pymupdf.Rect(crop), color=None, fill=color)
            page.draw_rect(pymupdf.Rect(x + 8, y + 8, x + 24, y + 24), color=None, fill=(0, 0, 0))
            page.set_cropbox(pymupdf.Rect(crop))
            page.set_rotation(rotation)
        doc.save(path)
    return str(path)


def test_default_per_page_and_single_page_png_pixels(exporter, mixed_pdf):
    window, folder, summaries, warnings = exporter
    assert window.open_files_in_tabs([mixed_pdf])
    window.ws.set_selection([2, 0, 1], 0)

    def defaults(dialog):
        assert dialog.mode.currentText() == "每页一张"
        assert dialog.current_mode.isChecked()
        dialog.selected_mode.click()

    _export(window, defaults)
    assert sorted(p.name for p in folder.iterdir()) == [f"mixed_{n:04d}.png" for n in range(1, 4)]
    target = folder / "mixed_长图.png"
    target.write_bytes(b"existing output")
    _export(window, _long_settings)
    assert target.read_bytes() == b"existing output"
    assert "跳过 1 张" in summaries[-1][1]

    _export(window, lambda dialog: _long_settings(dialog, overwrite=True))
    with Image.open(folder / "mixed_0001.png") as single, Image.open(target) as long_image:
        assert single.size == long_image.size == (300, 450)
        assert ImageChops.difference(single, long_image).getbbox() is None
        assert single.info["dpi"] == long_image.info["dpi"]
    assert "已完整输出 1 张" in summaries[-1][1]
    assert not warnings and not list(folder.glob(".pageark-image-*"))


@pytest.mark.parametrize("fmt,dpi,scope", [
    ("png", 150, "range"), ("jpeg", 300, "range"), ("png", 150, "selected"),
])
def test_multi_page_layout_order_and_rotation(exporter, mixed_pdf, fmt, dpi, scope):
    window, folder, summaries, warnings = exporter
    assert window.open_files_in_tabs([mixed_pdf])
    window.ws.set_selection([1], 1)
    window.ws.rotate(-90)  # Cancel the PDF's intrinsic 90° on the green page.
    window.ws.move_pages([2], 0)  # Now blue, red, green.
    window.ws.set_selection([2, 0], 2)
    blue = (250, 450, (0, 0, 255), True)
    red = (300, 450, (255, 0, 0), False)
    green = (400, 250, (0, 255, 0), False)
    specs = [blue, red, green]
    order = (3, 1, 2) if scope == "range" else (1, 3)
    scale = dpi // 150
    fitted_heights = [720, 600, 250]
    height = sum(fitted_heights[index - 1] for index in order) * scale
    width = 400 * scale

    def configure(dialog):
        if scope == "selected":
            dialog.selected_mode.click()
        _long_settings(dialog, fmt, dpi, page_range="3,1-2,3" if scope == "range" else None)
        assert f"{width} × {height}" in dialog.feedback.text()
        assert "预计额外峰值" in dialog.feedback.text()

    _export(window, configure)
    target = folder / f"mixed_长图.{fmt}"
    assert list(folder.iterdir()) == [target]
    assert "已完整输出 1 张" in summaries[-1][1] and not warnings
    with Image.open(target) as output, pymupdf.open(mixed_pdf) as source:
        assert output.format == fmt.upper()
        assert output.size == (width, height)
        assert output.info["dpi"] == pytest.approx((dpi, dpi), abs=.05)

        def pixel(x, y, color):
            assert output.getpixel((x, y)) == pytest.approx(color, abs=4 if fmt == "jpeg" else 0)

        y = 0
        for index in order:
            page_width, page_height, color, turned = specs[index - 1]
            factor = width / page_width
            h = fitted_heights[index - 1] * scale
            assert h == pytest.approx(page_height * factor)
            pixel(width // 2, y + h // 2, color)
            marker = round(33 * factor)
            pixel(width - marker if turned else marker, y + marker, (0, 0, 0))
            pixel(2, y + h // 2, color)
            pixel(width - 3, y + h // 2, color)
            if fmt == "png":
                source_index, rotation = [(2, 0), (0, 0), (1, 270)][index - 1]
                page = source[source_index]
                visible_width = page.rect.height if rotation else page.rect.width
                px_per_pt = width / visible_width
                expected = page.get_pixmap(matrix=pymupdf.Matrix(px_per_pt, px_per_pt).prerotate(rotation))
                assert (expected.width, expected.height) == (width, h)
                with Image.frombytes("RGB", (width, h), expected.samples) as reference:
                    assert ImageChops.difference(output.crop((0, y, width, y + h)), reference).getbbox() is None
            y += h
        assert y == output.height


def test_fractional_crop_preserves_source_margins(exporter, tmp_path):
    window, folder, _, warnings = exporter
    path = tmp_path / "crop.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=216, height=144)
        page = doc.new_page(width=154, height=100)
        crop = pymupdf.Rect(11.125, 7.375, 139.75, 91.625)
        page.draw_rect(page.rect, color=None, fill=(1, 0, 1))
        page.draw_rect(crop, color=None, fill=(1, 1, 1))
        page.draw_rect(crop + (2.25, 0, -3.5, 0), color=None, fill=(0, .3, .65))
        page.set_cropbox(crop)
        doc.save(path)
    assert window.open_files_in_tabs([str(path)])
    _export(window, lambda dialog: _long_settings(dialog, page_range="1-2"))
    with Image.open(folder / "crop_长图.png") as output, pymupdf.open(path) as doc:
        page = doc[1]
        scale = 450 / page.rect.width
        expected = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        assert (output.width, output.height) == (450, 300 + expected.height)
        with Image.frombytes("RGB", (expected.width, expected.height), expected.samples) as reference:
            crop_image = output.crop((0, 300, 450, output.height))
            assert ImageChops.difference(crop_image, reference).getbbox() is None
            assert crop_image.getpixel((5, expected.height // 2)) == (255, 255, 255)
            assert crop_image.getpixel((445, expected.height // 2)) == (255, 255, 255)
            assert crop_image.getpixel((15, expected.height // 2)) != (255, 255, 255)
    assert not warnings


def test_width_rounding_does_not_add_or_crop_an_edge(qapp, tmp_path):
    path = tmp_path / "rounding.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=9600, height=12)
        page = doc.new_page(width=8980.541, height=17.3)
        page.draw_rect(page.rect, color=None, fill=(1, .55, 0))
        page.draw_rect(page.rect + (page.rect.width - 3, 0, 0, 0), color=None, fill=(0, 0, 0))
        doc.save(path)
    store = imaging.PdfStore()
    try:
        with pymupdf.open(path) as doc:
            rect = doc[1].rect
            naive_scale = 40000 / rect.width
            assert (rect * pymupdf.Matrix(naive_scale, naive_scale)).irect.width == 40001
        refs = store.load(str(path))
        done, _, failed, cancelled = imaging.pdf_to_long_image(
            store, refs, [0, 1], str(tmp_path), "rounding", "png", 300, False, lambda *_: None, lambda: False)
        assert not failed and not cancelled
        with Image.open(done[0]) as output:
            assert output.size == (40000, 128)
            assert output.getpixel((0, 95))[0] == 255
            assert output.getpixel((39998, 95)) == (0, 0, 0)
            assert max(output.getpixel((39999, 95))) < 32  # Subpixel antialiasing at the edge.
    finally:
        store.close_all()


@pytest.mark.parametrize("fmt,extra", [("jpeg", 0), ("jpeg", .12), ("png", 0)])
def test_encoder_height_limit_and_png_fallback(exporter, tmp_path, fmt, extra):
    window, folder, _, warnings = exporter
    page_height = 1572 if fmt == "jpeg" else 2400
    limit = 65500 if fmt == "jpeg" else 100000
    size = [(48, page_height)] * 9 + [(24, (page_height + extra) / 2)]
    assert window.open_files_in_tabs([_pages(tmp_path / "tall.pdf", size)])

    def configure(dialog):
        _long_settings(dialog, fmt, 300, page_range="1-10")
        assert str(limit) in dialog.feedback.text()
        if extra:
            assert "200 × 65501" in dialog.feedback.text()
            assert not dialog.start.isEnabled()
            assert "请选择 PNG" in dialog.feedback.text()
            dialog.accept()
            assert dialog.isVisible()
            dialog.format.setCurrentIndex(dialog.format.findData("png"))

    _export(window, configure)
    with Image.open(folder / f"tall_长图.{'png' if extra else fmt}") as output:
        assert output.size == (200, limit + bool(extra))
        output.load()
    assert not warnings


@pytest.mark.parametrize("sizes,range_text,expected", [
    ([(480, 600), (120, 720), (120, 720)], "1-3", "预计内存超出 256 MiB 预算"),
    ([(48, 2400)] * 9 + [(24, 1200.06)], "1-10", "宽度或高度超出 PNG 边长上限"),
])
def test_oversize_is_blocked_before_choosing_a_folder(exporter, tmp_path, monkeypatch, sizes, range_text, expected):
    window, folder, summaries, warnings = exporter
    assert window.open_files_in_tabs([_pages(tmp_path / "large.pdf", sizes)])

    def choose(*args):
        pytest.fail("Preflight must run before choosing a folder")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)

    def configure(dialog):
        _long_settings(dialog, dpi=300, page_range=range_text)
        assert expected in dialog.feedback.text()
        assert "降低 DPI" in dialog.feedback.text()
        assert not dialog.start.isEnabled()
        dialog.accept()
        assert dialog.isVisible()
        dialog.reject()

    _export(window, configure)
    assert not list(folder.iterdir()) and not summaries and not warnings


def test_cancel_in_encoding_keeps_previous_file_and_cleans_temp(exporter, mixed_pdf, monkeypatch):
    window, folder, summaries, warnings = exporter
    assert window.open_files_in_tabs([mixed_pdf])
    target = folder / "mixed_长图.png"
    target.write_bytes(b"keep this old image")
    ready, release = Event(), Event()
    native_save = pymupdf.Pixmap.save

    def pause_before_save(pix, filename, *args, **kwargs):
        ready.set()
        if not release.wait(5):
            raise OSError("test did not reach the cancel button")
        return native_save(pix, filename, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Pixmap, "save", pause_before_save)
    seen = []
    timer = QTimer(window)

    def cancel_from_window():
        task = getattr(window, "_task_dialog", None)
        if ready.is_set() and task is not None and task.isVisible() and not seen:
            seen.append((task.stage.text(), list(folder.glob(".pageark-image-*")), task.percent.text()))
            task.cancel_button.click()
            seen.append(task.cancel_button.text())
            release.set()

    timer.timeout.connect(cancel_from_window)
    timer.start(10)
    try:
        _export(window, lambda dialog: _long_settings(dialog, page_range="1-3", overwrite=True))
    finally:
        timer.stop()
        release.set()
    assert len(seen) == 2 and "正在编码" in seen[0][0] and seen[0][1] and seen[0][2] != "100%"
    assert seen[1] == "正在取消" and "已完整输出 0 张" in summaries[-1][1]
    assert list(folder.iterdir()) == [target] and target.read_bytes() == b"keep this old image"
    assert not warnings


@pytest.mark.parametrize("failure", ["render", "encoder", "overwrite_conflict", "target_is_directory", "overwrite_retry"])
def test_save_failures_preserve_destination(exporter, mixed_pdf, monkeypatch, failure):
    window, folder, summaries, warnings = exporter
    assert window.open_files_in_tabs([mixed_pdf])
    target = folder / "mixed_长图.png"
    destination = target
    if failure == "target_is_directory":
        target.mkdir()
        destination = target / "old-file"
    original = b"existing output"
    incoming = b"changed by another writer"
    destination.write_bytes(original)
    native_save = pymupdf.Pixmap.save
    if failure == "render":
        native_render = imaging._render_pdf_image

        def fail_second_page(store, ref, matrix, cancel):
            if ref.source_page == 1:
                raise RuntimeError("simulated render error on page 2")
            return native_render(store, ref, matrix, cancel)
        monkeypatch.setattr(imaging, "_render_pdf_image", fail_second_page)
    elif failure == "encoder":
        def fail_save(pix, filename, *args, **kwargs):
            Path(filename).write_bytes(b"partial")
            raise OSError(errno.ENOSPC, "simulated disk full")
        monkeypatch.setattr(pymupdf.Pixmap, "save", fail_save)
    elif failure == "overwrite_conflict":
        def change_target(pix, filename, *args, **kwargs):
            native_save(pix, filename, *args, **kwargs)
            destination.write_bytes(incoming)
        monkeypatch.setattr(pymupdf.Pixmap, "save", change_target)
    elif failure == "overwrite_retry":
        from app import output
        native_replace = output.os.replace

        def locked_target(temp, dest, *args, **kwargs):
            if dest == str(target):
                destination.write_bytes(incoming)
                raise PermissionError("simulated sharing violation")
            return native_replace(temp, dest, *args, **kwargs)
        monkeypatch.setattr(output.os, "replace", locked_target)

    _export(window, lambda dialog: _long_settings(dialog, page_range="1-3", overwrite=True))
    assert not summaries and len(warnings) == 1 and warnings[0][0] == "导出未完成"
    if failure == "render":
        assert "simulated render error on page 2" in warnings[0][1]
    if failure == "encoder":
        assert "simulated disk full" in warnings[0][1]
    if failure in ("overwrite_conflict", "overwrite_retry"):
        assert "目标文件在处理期间发生变化" in warnings[0][1]
    assert destination.read_bytes() == (incoming if failure.startswith("overwrite_") else original)
    assert list(folder.iterdir()) == [target]
