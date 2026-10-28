"""纸序 PDF 的产品信息；版本与对应 Git 标签保持一致。"""

APP_NAME = "纸序 PDF"
APP_SLUG = "ZhiXuPDF"
APP_EXE_NAME = f"{APP_SLUG}.exe"
APP_PROJECT_URL = "https://github.com/d8349565/pdf-manage"
APP_ORGANIZATION = "PDFManage"
__version__ = "0.10.4-preview"
APP_VERSION_CORE = __version__.split("-", 1)[0]
APP_VERSION_PARTS = tuple(map(int, APP_VERSION_CORE.split(".")))
APP_IS_PRERELEASE = "-" in __version__
APP_VERSION_DISPLAY = __version__ + ("（预览版）" if APP_IS_PRERELEASE else "")
