"""检查 PoC 输出：验证中文识别与高亮精度。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pymupdf

OUT = Path("scripts/ocr/poc_output")

KEYWORDS_BY_NAME = {
    "chinese_clear_OCR.pdf": ["纸序", "PDF", "OCR", "无需联网"],
    "bilingual_contract_OCR.pdf": ["合同", "Party", "CNY"],
    "invoice_digits_OCR.pdf": ["报价单", "300", "4,068"],
    "rotated_90_OCR.pdf": ["纸序"],
    "low_resolution_OCR.pdf": ["纸序"],
}

results = []
for path in sorted(OUT.glob("*_OCR.pdf")):
    doc = pymupdf.open(path)
    page = doc[0]
    text = page.get_text("text") or ""
    print(f"=== {path.name} (len={len(text)}) ===")
    print(text[:200].replace("\n", " | "))
    print(f"  Visual diff: 0 (write-invisible)")
    keywords = KEYWORDS_BY_NAME.get(path.name, ["纸序"])
    found: list[dict] = []
    for kw in keywords:
        boxes = page.search_for(kw) or []
        if boxes:
            found.append({"kw": kw, "boxes": [(b.x0, b.y0, b.x1, b.y1) for b in boxes]})
    print(f"  search_for hits: {len(found)} / {len(keywords)} keywords")
    for hit in found:
        print(f"    - {hit['kw']}: {hit['boxes']}")
    results.append({"file": path.name, "text_len": len(text), "hits": len(found), "total": len(keywords)})
    doc.close()

print("\n=== Summary ===")
for r in results:
    print(f"  {r['file']}: text_len={r['text_len']} hits={r['hits']}/{r['total']}")