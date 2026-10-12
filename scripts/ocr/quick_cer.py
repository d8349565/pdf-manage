"""快速 CER 验证（前 6 页），用于 PoC 阶段验收。"""
from __future__ import annotations

import json
import re
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
OUTPUT_DIR = Path("scripts/ocr/quick_acc")


def lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref, hyp):
    rn = re.sub(r"\s+", "", ref)
    hn = re.sub(r"\s+", "", hyp)
    if not rn:
        return 0.0
    return lev(rn, hn) / len(rn)


def main() -> int:
    truth_path = Path("scripts/ocr/samples_poc/truth.json")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    bank = [truth["chinese_clear"], truth["bilingual_contract"], truth["invoice_digits"]]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(SAMPLES_DIR.glob("acceptance_*.pdf"))[:6]
    options = OCRRunOptions(page_indices=[], skip_existing_text_layer=True,
                            render_dpi=200)

    total_err = total_ref = 0
    print(f"[OK] Running CER on {len(files)} pages")
    for i, src in enumerate(files):
        dst = OUTPUT_DIR / (src.stem + "_OCR.pdf")
        options.output_path = str(dst)
        run_ocr(
            source_path=str(src),
            destination_path=str(dst),
            page_indices=None,
            options=options,
            progress_callback=None,
            cancel_check=lambda: False,
        )
        d = pymupdf.open(dst)
        hyp = (d[0].get_text("text") or "").strip()
        d.close()
        ref = " ".join(bank[i % len(bank)])
        c = cer(ref, hyp)
        rn = re.sub(r"\s+", "", ref)
        total_err += int(c * len(rn))
        total_ref += len(rn)
        print(f"  {src.name}: CER {c:.4%} (ref_len={len(rn)})")

    overall = total_err / total_ref if total_ref else 1.0
    print(f"\n=== Summary ===")
    print(f"  Overall CER: {overall:.4%}")
    print(f"  Target: <= 5%")
    print(f"  Verdict: {'GO' if overall <= 0.05 else 'NO-GO'}")
    return 0 if overall <= 0.05 else 1


if __name__ == "__main__":
    sys.exit(main())