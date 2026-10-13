"""Conservative heuristic; also protect existing text in partially scanned pages."""


def has_effective_text(page):
    text = page.get_text().strip()
    if not any(c.isalnum() for c in text):
        return False
    area = page.rect.width * page.rect.height
    large_scan = any(abs(item['bbox'][2] - item['bbox'][0]) *
                     abs(item['bbox'][3] - item['bbox'][1]) >= area * .5
                     for item in page.get_image_info())
    if not large_scan:
        return True
    # A short page produced by this feature is already a complete, validated OCR layer.
    # Do not run it again merely because it falls below the mixed-page text threshold.
    if any(font[4] == 'ZhiXuOCR' for font in page.get_fonts()):
        if any(span['type'] == 3 and any(chr(char[0]).isalnum() for char in span['chars'])
               for span in page.get_texttrace()):
            return True
    words = page.get_text('words')
    coverage = sum(max(0, w[2] - w[0]) * max(0, w[3] - w[1]) for w in words)
    # ponytail: headers alone are insufficient; real samples may refine this heuristic.
    return sum(c.isalnum() for c in text) >= 80 and coverage >= area * .01
