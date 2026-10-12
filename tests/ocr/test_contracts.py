"""OCR 数据契约测试。"""
from __future__ import annotations

import dataclasses

from app.ocr.contracts import (
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
)


def test_ocr_line_frozen():
    line = OCRLine(
        text="纸序PDF",
        bbox=(10.0, 20.0, 100.0, 50.0),
        polygon=(10.0, 20.0, 100.0, 20.0, 100.0, 50.0, 10.0, 50.0),
        confidence=0.98,
        page_index=0,
    )
    assert line.text == "纸序PDF"
    assert line.bbox[0] == 10.0
    assert line.confidence == 0.98
    assert dataclasses.is_dataclass(line)


def test_ocr_run_options_defaults():
    opts = OCRRunOptions()
    assert opts.skip_existing_text_layer is True
    assert opts.render_dpi == 200
    assert opts.output_path is None
    assert opts.overwrite_existing is False


def test_job_result_stats():
    res = OCRJobResult(output_path="test_OCR.pdf")
    res.processed_pages.append(0)
    res.skipped_pages.append(1)
    res.failed_pages.append(OCRPageFailure(2, "识别错误"))
    assert res.total == 3
    assert not res.success  # 有失败页

    res2 = OCRJobResult(output_path="out.pdf", processed_pages=[0, 1])
    assert res2.success
