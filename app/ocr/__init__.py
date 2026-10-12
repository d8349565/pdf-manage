"""纸序 PDF 的轻量级 OCR 子包。

设计原则（与 PRD §5 / §13 对齐）：

* 不重构现有 PyMuPDF / PyQt6 架构；
* 不在启动时加载 OCR 推理库（按需懒加载）；
* 不联网、不引入 PyTorch / Transformers / CUDA 等重型依赖；
* 不直接写入原 PDF，输出独立文件；
* 所有 OCR 引擎异常转换为业务层可理解的错误。

子包内模块边界：

* ``contracts``    ——  数据结构与对外接口契约（OCRLine、OCRJobResult、RunOptions）
* ``model_manager``——  模型完整性 / 安装位置解析 / 离线校验
* ``engine``       ——  封装 RapidOCR 推理，提供同步与懒加载入口
* ``page_detector``——  判断页面是否需要 OCR（与 pdf2excel.analyzer 解耦）
* ``pdf_export``   ——  在 PDF 上写入隐藏文字层，不改变可见效果
* ``service``      ——  串起整条 OCR 流程，承担进度、取消、异常转换

UI 入口放在 ``app/ocrui.py``，与本子包保持单向依赖：UI → service → 其他模块。
"""

from app.ocr.contracts import (
    OCRLine,
    OCRJobResult,
    OCRRunOptions,
    OCRPageFailure,
)

__all__ = [
    "OCRLine",
    "OCRJobResult",
    "OCRRunOptions",
    "OCRPageFailure",
]