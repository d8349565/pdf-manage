"""30 页验收样本 CER 评估（PRD §11.2）。

目标：清晰扫描件的中文字符错误率 CER ≤ 5%。

CER = (S + D + I) / N
* S = substitutions, D = deletions, I = insertions, N = reference length

由于样本是合成真值（已知文字），可以做严格的 CER 评估。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pymupdf

from app.ocr.contracts import OCRRunOptions
from app.ocr.service import run_ocr


SAMPLES_DIR = Path("scripts/ocr/samples_acc")
OUTPUT_DIR = Path("scripts/ocr/acc_output")
REPORT_PATH = Path("docs/ocr/ACCEPTANCE-REPORT.md")
CER_TARGET = 0.05


# --------------------------------------------------------------------------- #
# Levenshtein 距离 / CER
# --------------------------------------------------------------------------- #


def _levenshtein(a: str, b: str) -> int:
    """经典 DP。"""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,            # deletion
                curr[j] + 1,                 # insertion
                prev[j] + (ca != cb),         # substitution
            ))
        prev = curr
    return prev[-1]


def _cer(reference: str, hypothesis: str) -> float:
    """归一化的字符错误率：忽略所有空白，避免 OCR 与真值的空格差异放大指标。

    PRD §11.2 的"CER ≤ 5%"指的是中文文字内容错误率，空白不应计入。
    """
    if not reference:
        return 0.0 if not hypothesis else 1.0
    ref_norm = _normalize(reference)
    hyp_norm = _normalize(hypothesis)
    if not ref_norm:
        return 0.0 if not hyp_norm else 1.0
    return _levenshtein(ref_norm, hyp_norm) / len(ref_norm)


def _normalize(text: str) -> str:
    """去掉所有空白字符与常见标点，便于跨样本 CER 比较。"""
    import re
    return re.sub(r"\s+", "", text)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def main() -> int:
    truth_path = SAMPLES_DIR / "truth.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    files = truth.get("acceptance_files") or []
    if not files:
        print(f"[X] truth.json 缺少 acceptance_files")
        return 2

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    options = OCRRunOptions(page_indices=[], skip_existing_text_layer=True, render_dpi=200)

    # 30 页样本对应的真值文字行（轮询已有 bank）
    poc_truth_path = Path("scripts/ocr/samples_poc/truth.json")
    poc_truth = json.loads(poc_truth_path.read_text(encoding="utf-8"))
    bank = [
        poc_truth["chinese_clear"],
        poc_truth["bilingual_contract"],
        poc_truth["invoice_digits"],
    ]

    per_page: list[dict] = []
    total_ref = 0
    total_err = 0
    print(f"[OK] {len(files)} acceptance pages")

    for i, rel in enumerate(files):
        src = SAMPLES_DIR / rel
        dst = OUTPUT_DIR / f"{src.stem}_OCR.pdf"
        options.output_path = str(dst)
        # 渲染 + 识别 + 写入
        t0 = time.perf_counter()
        run_ocr(
            source_path=str(src),
            destination_path=str(dst),
            page_indices=None,
            options=options,
            progress_callback=None,
            cancel_check=lambda: False,
        )
        elapsed = time.perf_counter() - t0

        # 取识别文字
        if not dst.is_file():
            per_page.append({"page": i + 1, "status": "fail", "cer": 1.0})
            continue
        doc = pymupdf.open(dst)
        hyp = (doc[0].get_text("text") or "").strip()
        doc.close()
        ref_lines = bank[i % len(bank)]
        ref = " ".join(ref_lines)
        cer = _cer(ref, hyp)
        per_page.append({
            "page": i + 1,
            "cer": round(cer, 4),
            "ref_len": len(ref),
            "hyp_len": len(hyp),
            "elapsed_s": round(elapsed, 2),
        })
        total_ref += len(ref)
        total_err += int(cer * len(ref))

    overall_cer = total_err / total_ref if total_ref else 1.0
    pass_count = sum(1 for r in per_page if r.get("cer", 1.0) <= CER_TARGET)

    _write_report(per_page, overall_cer, pass_count, total_ref, total_err)
    print(f"\n=== Acceptance Summary ===")
    print(f"  Pages: {len(files)}")
    print(f"  Pass (CER <= {CER_TARGET:.0%}): {pass_count}")
    print(f"  Overall CER: {overall_cer:.4%}")
    print(f"  Verdict: {'GO' if overall_cer <= CER_TARGET else 'NO-GO'}")
    print(f"  Report: {REPORT_PATH}")
    return 0 if overall_cer <= CER_TARGET else 1


def _write_report(per_page: list[dict], overall: float, passed: int,
                  total_ref: int, total_err: int) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# 验收报告（PRD §11.2 30 页 CER 评估）\n")
    lines.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"目标 CER：≤ {CER_TARGET:.0%}\n")
    lines.append("\n## 1. 总体\n")
    lines.append(f"- 样本数：{len(per_page)}")
    lines.append(f"- 通过（单页 CER ≤ 5%）：{passed}")
    lines.append(f"- 总体 CER：**{overall:.4%}**")
    lines.append(f"- 决策：**{'GO' if overall <= CER_TARGET else 'NO-GO'}**\n")
    lines.append("## 2. 逐页明细\n")
    lines.append("| 页 | CER | 真值长度 | 识别长度 | 耗时 (s) |")
    lines.append("|---|---|---|---|---|")
    for r in per_page:
        lines.append(
            f"| {r['page']} | {r.get('cer', 'n/a')} | "
            f"{r.get('ref_len', 'n/a')} | {r.get('hyp_len', 'n/a')} | "
            f"{r.get('elapsed_s', 'n/a')} |"
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())