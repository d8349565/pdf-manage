"""Collect the actual build's notices and check that distribution keeps them."""
from importlib.metadata import distribution
from pathlib import Path, PurePosixPath
import subprocess
import sys


COMPONENTS = {
    "PyQt6": "https://pypi.org/project/PyQt6/{version}/#files",
    "PyQt6-Qt6": "https://download.qt.io/archive/qt/{series}/{version}/single/qt-everywhere-src-{version}.zip",
    "PyQt6-sip": "https://pypi.org/project/PyQt6-sip/{version}/#files",
    "PyMuPDF": "https://github.com/pymupdf/PyMuPDF/tree/{version}",
    "Pillow": "https://pypi.org/project/Pillow/{version}/#files",
    "fonttools": "https://pypi.org/project/fonttools/{version}/#files",
    "openpyxl": "https://pypi.org/project/openpyxl/{version}/#files",
    "et-xmlfile": "https://pypi.org/project/et-xmlfile/{version}/#files",
    "Markdown": "https://pypi.org/project/Markdown/{version}/#files",
    "pywin32": "https://github.com/mhammond/pywin32/tree/b{version}",
    "PyInstaller": "https://github.com/pyinstaller/pyinstaller/tree/v{version}",
}
REQUIRED_NOTICES = ("LICENSE", "AGPL-3.0.txt", "GPL-3.0.txt", "LGPL-3.0.txt",
                    "THIRD-PARTY.txt", "MuPDF-NOTICE.txt", "SOURCES.txt", "python/LICENSE.txt")


def collect_license_data(root):
    """Return PyInstaller data entries; never download during a build."""
    from app import APP_PROJECT_URL, __version__
    from pymupdf import mupdf_version

    root = Path(root)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python LICENSE.txt 缺失，不能生成发布包。")
    datas = [(str(root / "LICENSE"), "licenses"), (str(python_license), "licenses/python")]
    lines = ["纸序 PDF：许可、构建版本与对应源码", "",
             f"程序版本：{__version__}", f"正式版源码与附件：{APP_PROJECT_URL}/releases/tag/v{__version__}",
             "正式版源码下载：在同一发布页选择 Source code (zip) 或 Source code (tar.gz)。",
             "构建脚本与依赖版本见源码中的 docs/DEVELOPMENT.md、pdf-manage.spec 和 packaging/requirements-build.txt。",
             "自有代码的 MIT 许可位于 LICENSE；组合发行说明位于 THIRD-PARTY.txt。", ""]
    if (root / ".git").exists():
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                  capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root,
                               check=True, capture_output=True, text=True).stdout.strip()
        tags = subprocess.run(["git", "tag", "--points-at", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.splitlines()
        lines += [f"构建提交：{revision}"]
        if dirty or f"v{__version__}" not in tags:
            lines += ["本地预览：含未发布修改或尚无对应版本标签，发布页源码不代表此预览构建；重新分发时须另外提供与此构建匹配的源码。"]
    python_version = sys.version.split()[0]
    lines += ["", f"Python {python_version}: https://www.python.org/ftp/python/{python_version}/Python-{python_version}.tar.xz",
              f"MuPDF {mupdf_version}: https://github.com/ArtifexSoftware/mupdf/tree/{mupdf_version}",
              "MuPDF 的第三方组件及字体来源、各自声明：同版源码的 thirdparty/、resources/ 和 .gitmodules。", ""]
    for name, source_url in COMPONENTS.items():
        package = distribution(name)
        notices = []
        for entry in package.files or ():
            relative = PurePosixPath(str(entry).replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if not any(word in relative.name.lower() for word in ("license", "licence", "copying", "notice", "copyright")):
                continue
            source = Path(package.locate_file(entry))
            if source.is_file():
                destination = PurePosixPath("licenses/dependencies") / name / relative.parent
                notices.append((str(source), str(destination)))
        if not notices:
            raise RuntimeError(f"{name} 缺少上游许可文件，不能生成发布包。")
        datas.extend(notices)
        license_name = package.metadata.get("License-Expression") or package.metadata.get("License") or "见所附上游许可"
        lines += [f"{name} {package.version} ({license_name})",
                  source_url.format(version=package.version, series=".".join(package.version.split(".")[:2])),
                  f"许可与版权声明：dependencies/{name}/", ""]
    output = root / "build/licenses/SOURCES.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    datas.append((str(output), "licenses"))
    return datas


def verify_license_bundle(internal):
    """Fail closed on missing notices; return all paths required in the portable ZIP."""
    internal = Path(internal)
    for name in REQUIRED_NOTICES:
        path = internal / "licenses" / name
        if not path.is_file() or path.stat().st_size < 100:
            raise ValueError(f"发布包缺少完整许可或源码说明：licenses/{name}")
    for name in COMPONENTS:
        folder = internal / "licenses/dependencies" / name
        if not folder.is_dir() or not any(path.is_file() for path in folder.rglob("*")):
            raise ValueError(f"发布包缺少 {name} 的上游许可。")
    return tuple(path.relative_to(internal).as_posix()
                 for path in (internal / "licenses").rglob("*") if path.is_file())
