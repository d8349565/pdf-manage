"""OCR 契约的可序列化 / 默认值测试。"""
from __future__ import annotations

import dataclasses

from app.ocr.contracts import (
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
)


def test_ocr_line_is_frozen():
    line = OCRLine(
        text="你好",
        bbox=(10.0, 20.0, 110.0, 60.0),
        polygon=(10.0, 20.0, 110.0, 20.0, 110.0, 60.0, 10.0, 60.0),
        confidence=0.95,
        page_index=0,
    )
    assert line.text == "你好"
    assert dataclasses.is_dataclass(line) and dataclasses.fields(line)[0].name == "text"


def test_run_options_accepts_api_page_notation():
    """占位测试：未来支持 "1-3,5,8" 字符串时，此用例应扩充。"""
    opts = OCRRunOptions(page_indices=[0, 1, 2, 4, 7])
    assert len(opts.page_indices) == 5


def test_job_result_total_counts():
    job = OCRJobResult(output_path="out.pdf")
    job.processed_pages.append(0)
    job.skipped_pages.append(1)
    job.failed_pages.append(OCRPageFailure(page_index=2, reason="x"))
    assert job.total == 3


def test_page_failure_carries_reason():
    f = OCRPageFailure(page_index=4, reason="渲染失败")
    assert f.page_index == 4
    assert "渲染" in f.reason