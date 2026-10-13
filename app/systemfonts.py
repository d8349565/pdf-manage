"""Read an exact installed Windows font; never accept GDI's substitute face."""
from functools import lru_cache
import re
import struct
import sys


_ALIASES = {
    'arialmt': 'Arial', 'timesnewromanpsmt': 'Times New Roman',
    'microsoftyahei': 'Microsoft YaHei', 'microsoftyaheiui': 'Microsoft YaHei UI',
    '微软雅黑': 'Microsoft YaHei', '微软雅黑ui': 'Microsoft YaHei UI',
    '宋体': 'SimSun', '新宋体': 'NSimSun', '黑体': 'SimHei',
    '仿宋': 'FangSong', '楷体': 'KaiTi', '等线': 'DengXian',
}


def _key(name):
    return re.sub(r'[\s_,\-]', '', name).casefold()


def font_face(preferred, flags=0):
    name = preferred.split('+')[-1]
    bold = bool(flags & 16) or 'bold' in name.casefold()
    italic = bool(flags & 2) or any(s in name.casefold() for s in ('italic', 'oblique'))
    name = re.sub(r'[-, ]?(?:BoldItalic|BoldOblique|Bold|Italic|Oblique|Regular)(?:MT)?$',
                  '', name, flags=re.IGNORECASE)
    if name == 'TimesNewRomanPS':
        name = 'Times New Roman'
    return _ALIASES.get(_key(name), name), bold, italic


def _standalone_font(gdi, dc, data):
    """TTC table offsets refer to the collection: rebuild the selected SFNT face."""
    import ctypes as c
    count = int.from_bytes(data[4:6], 'big')
    directory_end = 12 + count * 16
    if not 1 <= count <= 256 or directory_end > len(data):
        return None
    entries = [struct.unpack_from('>4sIII', data, 12 + i * 16) for i in range(count)]
    result = bytearray(data[:directory_end])
    head = None
    for index, (tag, checksum, _, length) in enumerate(entries):
        if length > 64 * 1024 * 1024 or len(result) + length > 64 * 1024 * 1024:
            return None
        table = c.create_string_buffer(length)
        if length and gdi.GetFontData(dc, int.from_bytes(tag, 'little'), 0, table, length) != length:
            return None
        offset = len(result)
        result.extend(table.raw)
        result.extend(b'\0' * (-len(result) % 4))
        if tag == b'head' and length >= 12:
            head = offset
            result[offset + 8:offset + 12] = b'\0' * 4
        struct.pack_into('>4sIII', result, 12 + index * 16, tag, checksum, offset, length)
    if head is None:
        return None
    checksum = sum(value[0] for value in struct.iter_unpack('>I', result)) & 0xffffffff
    struct.pack_into('>I', result, head + 8, (0xB1B0AFBA - checksum) & 0xffffffff)
    return bytes(result)


@lru_cache(maxsize=4)
def installed_font_buffer(preferred, flags=0):
    """Return embeddable bytes for the selected face, or None if unavailable."""
    if sys.platform != 'win32':
        return None
    import ctypes as c
    from ctypes import wintypes as w
    family, bold, italic = font_face(preferred, flags)
    if not family or len(family) >= 32:
        return None
    gdi = c.WinDLL('gdi32', use_last_error=True)
    gdi.CreateCompatibleDC.argtypes, gdi.CreateCompatibleDC.restype = [w.HDC], w.HDC
    gdi.CreateFontW.argtypes = [c.c_int] * 5 + [w.DWORD] * 8 + [w.LPCWSTR]
    gdi.CreateFontW.restype = w.HANDLE
    gdi.SelectObject.argtypes, gdi.SelectObject.restype = [w.HDC, w.HANDLE], w.HANDLE
    gdi.GetTextFaceW.argtypes, gdi.GetTextFaceW.restype = [w.HDC, c.c_int, w.LPWSTR], c.c_int
    gdi.GetFontData.argtypes = [w.HDC, w.DWORD, w.DWORD, c.c_void_p, w.DWORD]
    gdi.GetFontData.restype = w.DWORD
    gdi.DeleteObject.argtypes, gdi.DeleteObject.restype = [w.HANDLE], w.BOOL
    gdi.DeleteDC.argtypes, gdi.DeleteDC.restype = [w.HDC], w.BOOL
    dc = gdi.CreateCompatibleDC(None)
    if not dc:
        return None
    handle = previous = None
    try:
        handle = gdi.CreateFontW(-32, 0, 0, 0, 700 if bold else 400,
                                 int(italic), 0, 0, 1, 7, 0, 0, 0, family)
        if not handle:
            return None
        previous = gdi.SelectObject(dc, handle)
        if not previous or previous == c.c_void_p(-1).value:
            previous = None
            return None
        face = c.create_unicode_buffer(128)
        if not gdi.GetTextFaceW(dc, len(face), face):
            return None
        actual = _ALIASES.get(_key(face.value), face.value)
        if _key(actual) != _key(family):
            return None
        # OS/2 fsType: restricted embedding, no subsetting, or bitmap-only.
        permissions = c.create_string_buffer(10)
        if gdi.GetFontData(dc, int.from_bytes(b'OS/2', 'little'), 0, permissions, 10) != 10:
            return None
        if int.from_bytes(permissions.raw[8:10], 'big') & (0x2 | 0x100 | 0x200):
            return None
        size = gdi.GetFontData(dc, 0, 0, None, 0)
        if not 12 <= size <= 64 * 1024 * 1024:
            return None
        data = c.create_string_buffer(size)
        if gdi.GetFontData(dc, 0, 0, data, size) != size:
            return None
        return _standalone_font(gdi, dc, data.raw)
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if handle:
            gdi.DeleteObject(handle)
        gdi.DeleteDC(dc)
