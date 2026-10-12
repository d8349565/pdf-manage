"""OCR 核心业务服务。

串联环境校验、扫描页筛选、逐页渲染识别、装配文字层与原子文件写出。
提供规范的 progress_callback 与 cancel_check，符合 PRD §4.3 / §5.3 / §7。
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Sequence

import pymupdf

from app.ocr.contracts import (
    CancelCheck,
    OCRJobResult,
    OCRLine,
    OCRPageFailure,
    OCRRunOptions,
    ProgressCallback,
)
from app.ocr.engine import OCREngine, OCREngineError, OCRNotAvailableError
from app.ocr.model_manager import check_environment
from app.ocr.page_detector import assess_page
from app.ocr.pdf_export import assemble_searchable_pdf
from app.output import check_cancel, commit_file, stamp

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """OCR 业务层异常基类。"""


def resolve_default_output_path(source_path: str, custom_dest: str | None = None) -> str:
    """生成默认目标文件路径。"""
    if custom_dest:
        return os.path.abspath(custom_dest)

    src = Path(source_path)
    default_name = f"{src.stem}_OCR.pdf"
    target = src.parent / default_name
    return str(target)


def get_non_conflicting_path(target_path: str) -> str:
    """如果文件已存在，自动生成带递增序号的不冲突文件名。"""
    p = Path(target_path)
    if not p.exists():
        return str(p)

    stem = p.stem
    parent = p.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}.pdf"
        if not candidate.exists():
            return str(candidate)
        counter += 1


def run_ocr(
    source_path: str,
    destination_path: str | None = None,
    page_indices: Sequence[int] | None = None,
    options: OCRRunOptions | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> OCRJobResult:
    """执行 OCR 任务的主接口。

    参数：
    - source_path: 源 PDF 绝对路径
    - destination_path: 输出 PDF 目标路径（若未提供则默认 生成 _OCR.pdf）
    - page_indices: 指定页码（0-based 索引列表），为 None 则代表全部页面
    - options: OCR 执行选项
    - progress_callback: 进度回调 (current, total, message)
    - cancel_check: 取消检查回调 () -> bool
    """
    options = options or OCRRunOptions()
    source_abs = os.path.abspath(source_path)

    if not os.path.isfile(source_abs):
        raise OCRError(f"源文件不存在：{source_path}")

    # 1. 检测 OCR 环境
    status = check_environment()
    if not status.is_ready:
        raise OCRError(status.message)

    # 2. 检查 PDF 合规性与安全性
    try:
        doc = pymupdf.open(source_abs)
    except Exception as exc:
        raise OCRError(f"无法打开源 PDF 文件：{exc}") from exc

    try:
        if doc.is_encrypted:
            raise OCRError("该 PDF 文件已被加密，请先解密或移除密码保护后再执行 OCR。")

        # 检查数字签名（PRD §6.3 / §7.1 要求：数字签名文件禁止直接生成修改后的副本）
        try:
            from app.pdfstructure import inspect_safety
            safety = inspect_safety(doc, 0)
            if safety.signature != "none":
                raise OCRError("该 PDF 包含有效数字签名。为保证签名真实有效，禁止写入 OCR 文字层。")
        except ImportError:
            pass

        total_doc_pages = doc.page_count
        if total_doc_pages == 0:
            raise OCRError("该 PDF 文件没有有效页面。")

        # 3. 确定待处理页码范围
        all_targets = (
            [idx for idx in page_indices if 0 <= idx < total_doc_pages]
            if page_indices is not None
            else list(range(total_doc_pages))
        )

        to_process: list[int] = []
        skipped: list[int] = []

        for idx in all_targets:
            if options.skip_existing_text_layer:
                info = assess_page(doc[idx])
                if not info.is_scanned:
                    skipped.append(idx)
                    continue
            to_process.append(idx)

        # 4. 确定输出路径
        resolved_dest = resolve_default_output_path(source_abs, destination_path)
        if not options.overwrite_existing and os.path.exists(resolved_dest):
            resolved_dest = get_non_conflicting_path(resolved_dest)

        result = OCRJobResult(
            output_path=resolved_dest,
            skipped_pages=list(skipped),
        )

        if not to_process:
            if progress_callback:
                progress_callback(1, 1, "所有页面均已具备文字层，无需重复识别")
            result.warnings.append("所有指定页面均已包含可用文字层，已跳过。")
            return result

        # 5. 执行识别
        engine = OCREngine.get(status.model_dir)
        page_lines_map: dict[int, list[OCRLine]] = {}
        page_pix_sizes: dict[int, tuple[float, float]] = {}
        total_steps = len(to_process)

        for step_idx, page_idx in enumerate(to_process, 1):
            check_cancel(cancel_check)

            if progress_callback:
                progress_callback(
                    step_idx,
                    total_steps,
                    f"正在识别第 {page_idx + 1} 页（共 {total_steps} 页需要处理）…",
                )

            page = doc[page_idx]
            try:
                pix = page.get_pixmap(dpi=options.render_dpi)
                page_pix_sizes[page_idx] = (pix.width, pix.height)
                lines = engine.recognize_pixmap(pix, page_idx)
                page_lines_map[page_idx] = lines
                result.processed_pages.append(page_idx)
            except OCREngineError as exc:
                logger.warning("第 %d 页识别失败：%s", page_idx + 1, exc)
                result.failed_pages.append(OCRPageFailure(page_idx, str(exc)))
                continue

        check_cancel(cancel_check)

        if not result.processed_pages and result.failed_pages:
            raise OCRError(f"所有指定页面均识别失败，首个错误：{result.failed_pages[0].reason}")

        # 6. 生成双层可搜索 PDF（写入临时文件）
        if progress_callback:
            progress_callback(total_steps, total_steps, "正在写入隐藏文字层并优化文件…")

        dest_dir = os.path.dirname(resolved_dest) or "."
        os.makedirs(dest_dir, exist_ok=True)
        fd, temp_pdf_path = tempfile.mkstemp(suffix=".pdf", prefix=".ocr-tmp-", dir=dest_dir)
        os.close(fd)

        try:
            stats = assemble_searchable_pdf(
                source_abs,
                temp_pdf_path,
                page_lines_map,
                page_pix_sizes,
                cancel_check=cancel_check,
            )

            check_cancel(cancel_check)

            # 7. 原子提交目标文件
            expected_stamp = stamp(resolved_dest)
            commit_file(temp_pdf_path, resolved_dest, expected_stamp, cancel=cancel_check)

            if stats.get("failed", 0) > 0:
                result.warnings.append(f"有 {stats['failed']} 个页面未成功写入文字层。")

            return result
        except Exception:
            # 清理临时文件
            try:
                if os.path.exists(temp_pdf_path):
                    os.unlink(temp_pdf_path)
            except OSError:
                pass
            raise

    finally:
        doc.close()
