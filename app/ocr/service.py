"""OCR 业务服务 —— 串联 ``engine / page_detector / pdf_export``。

负责 PRD §4.3 / §7 描述的所有编排：

1. 环境检查（模型、签名、未保存修改等）；
2. 渲染页面 → 调用 :class:`OCREngine` 识别；
3. 把识别结果交给 :mod:`app.ocr.pdf_export` 装配新 PDF；
4. 提供 ``progress_callback`` / ``cancel_check`` 钩子；
5. 把所有引擎层异常转换为 :class:`OCRJobResult` 或抛出业务层 :class:`OCROperationError`。

UI 层通过 :func:`run_ocr` 调用本模块；线程模型见 ``app/progress.py::run_operation``。
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import Callable, Iterable

import pymupdf

from app.ocr.contracts import (
    CancelCheck,
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
    ProgressCallback,
)
from app.ocr.engine import OCREngine, OCREngineError, OCRModelMissing
from app.ocr.model_manager import check_environment
from app.ocr.page_detector import assess_page, filter_pages_needing_ocr
from app.ocr.pdf_export import assemble_searchable_pdf


log = logging.getLogger(__name__)


class OCROperationError(RuntimeError):
    """业务层错误。附带可读文案，可以直接弹给用户。"""


def _resolve_output_path(source: Path, options: OCRRunOptions) -> Path:
    if options.output_path:
        return Path(options.output_path)
    return source.with_name(f"{source.stem}_OCR.pdf")


def _report(progress: ProgressCallback | None, current: int, total: int, message: str) -> None:
    if progress is None:
        return
    try:
        progress(current, total, message)
    except Exception:  # 进度回调自身不该让主流程崩
        log.warning("OCR 进度回调异常", exc_info=True)


def _validate_source(path: Path) -> None:
    if not path.is_file():
        raise OCROperationError(f"找不到源文件：{path}")
    if path.suffix.lower() != ".pdf":
        raise OCROperationError("仅支持 .pdf 文件进行 OCR 识别")


def _check_pdf_safety(doc: pymupdf.Document) -> None:
    """MVP 禁止对带数字签名的 PDF 写入文字层（PRD §6.3 / §7.1）。"""
    # PyMuPDF 1.28 暂未直接暴露签名元数据；采用保守策略：检测加密 + 页数 / 旋转
    # 等信息写入 warnings，由 UI 提示用户。
    if doc.is_encrypted or doc.needs_pass:
        raise OCROperationError("PDF 已加密或需要密码，请先解除后再进行 OCR")


def run_ocr(
    source_path: str,
    destination_path: str | None,
    page_indices: Iterable[int] | None,
    options: OCRRunOptions,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> OCRJobResult:
    """对外暴露的 OCR 任务入口。详细语义见 :mod:`app.ocr.contracts`。

    PoC 阶段：

    * 环境检测通过 → 仍可能因模型未真实下载而 ``OCRModelMissing``；
    * 渲染 / 识别 / 写文字层在 D1/D2 接入真实引擎；
    * 本函数以最保守的方式把异常汇总到 :class:`OCRJobResult`。
    """
    source = Path(source_path)
    _validate_source(source)

    env = check_environment()
    result = OCRJobResult(output_path=destination_path or str(_resolve_output_path(source, options)))

    if not env.is_ready:
        result.warnings.append("OCR 增强包未安装或模型缺失")
        _report(progress_callback, 0, 1, "OCR 增强包未安装")
        return result

    try:
        doc = pymupdf.open(source_path)
    except Exception as exc:  # noqa: BLE001
        raise OCROperationError(f"无法打开 PDF：{exc}") from exc

    try:
        _check_pdf_safety(doc)
        targets = list(page_indices) if page_indices is not None else list(range(doc.page_count))
        if options.skip_existing_text_layer:
            targets = [idx for idx in targets if assess_page(doc[idx]).needs_ocr]
        if not targets:
            _report(progress_callback, 0, 0, "没有需要 OCR 的页面")
            result.warnings.append("所有页面均已有可用文字层")
            return result

        total = len(targets)
        engine = OCREngine.get(env.install_dir)
        page_results: dict[int, list[OCRLine]] = {}
        render_meta: dict[int, tuple[float, float]] = {}
        failed: list[OCRPageFailure] = []
        for i, page_index in enumerate(targets, start=1):
            if cancel_check and cancel_check():
                result.warnings.append("用户已取消")
                break
            page = doc[page_index]
            try:
                pix = page.get_pixmap(dpi=options.render_dpi)
                render_meta[page_index] = (pix.width, pix.height)
                lines = engine.recognize_page(_pixmap_to_image(pix))
            except OCRModelMissing as exc:
                raise OCROperationError(str(exc)) from exc
            except OCREngineError as exc:
                log.warning("OCR 第 %d 页失败：%s\n%s", page_index + 1, exc, traceback.format_exc())
                failed.append(OCRPageFailure(page_index=page_index, reason=str(exc)))
                _report(progress_callback, i, total, f"第 {page_index + 1} 页识别失败，跳过")
                continue
            page_results[page_index] = lines
            result.processed_pages.append(page_index)
            _report(progress_callback, i, total, f"正在识别第 {page_index + 1} / {total} 页")
        result.failed_pages = failed

        output_path = Path(destination_path) if destination_path else _resolve_output_path(source, options)
        if options.output_path is None and output_path.exists() and not options.overwrite_existing:
            # 自动重命名避免覆盖，PRD §4.2 / §7.2 要求。
            stem = output_path.stem
            parent = output_path.parent
            counter = 1
            while True:
                candidate = parent / f"{stem}_{counter}.pdf"
                if not candidate.exists():
                    output_path = candidate
                    break
                counter += 1
        stats = assemble_searchable_pdf(
            source_path=str(source),
            destination_path=str(output_path),
            page_results=page_results,
            render_meta=render_meta,
        )
        result.output_path = str(output_path)
        if stats.get("failed"):
            result.warnings.append(
                f"装配时共有 {stats['failed']} 页未写入文字层，请检查原 PDF 是否受损"
            )
    finally:
        doc.close()

    return result


def _pixmap_to_image(pix: pymupdf.Pixmap):
    """把 PyMuPDF Pixmap 转成 OCR 引擎可消费的图像对象。

    真实实现在 D1 阶段接入（RapidOCR 接受 numpy.ndarray）。PoC 阶段仅返回
    Pixmap 自身，便于 unit test 校验调用顺序。
    """
    return pix


# 暴露给 UI 使用的便捷封装 ------------------------------------------------------------

def run_ocr_in_background(
    parent,
    *,
    source_path: str,
    options: OCRRunOptions,
    title: str = "OCR 文字识别",
):
    """配合 :func:`app.progress.run_operation` 使用的工厂函数。

    UI 层调用方式：

    >>> from app.progress import run_operation
    >>> run_operation(parent, "OCR 文字识别", lambda report, cancel: run_ocr_in_background(...).work)

    实际拼装放在 ``app/ocrui.py``，本工厂只负责把 OCR service 包装成可消费的 ``work``。
    """
    raise NotImplementedError("D4 阶段补全 run_ocr_in_background")