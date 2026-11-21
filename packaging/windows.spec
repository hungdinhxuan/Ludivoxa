"""PyInstaller onedir bundle. Run with the same environment as the application."""

import os
from importlib import metadata
from pathlib import Path
import tomllib

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata
from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo

root = Path(SPECPATH).parent
release_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
# Windows needs numeric resource fields; retain the full PEP 440 RC in strings.
version_tuple = tuple(int(part) for part in release_version.split("rc", 1)[0].split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Ludivoxa contributors"),
            StringStruct("FileDescription", "Ludivoxa - offline game translation and speech"),
            StringStruct("FileVersion", release_version),
            StringStruct("InternalName", "Ludivoxa"),
            StringStruct("OriginalFilename", "Ludivoxa.exe"),
            StringStruct("ProductName", "Ludivoxa"),
            StringStruct("ProductVersion", release_version),
            StringStruct("LegalCopyright", "See LICENSE and THIRD_PARTY_NOTICES.txt"),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
datas = [(str(root / "src/ludivoxa/resources"), "ludivoxa/resources")]
binaries = []
hiddenimports = ["pynput.keyboard._win32", "pynput.mouse._win32"]

# PaddleX resolves inference classes dynamically and reads package metadata and
# source/config files at runtime. Piper includes its bundled eSpeak data/DLL.
for package in ("paddle", "paddleocr", "paddlex", "piper", "meikiocr", "ctranslate2",
                "sentencepiece", "onnxruntime", "windows_capture", "sounddevice", "_sounddevice_data"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

# ONNX-only voice cloning; neither upstream's web UI nor PyTorch is used.
for package in ("vieneu", "sea_g2p"):
    datas += collect_data_files(package)
for package in ("soxr", "kaldi_native_fbank", "soundfile", "_soundfile_data", "tokenizers"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports
hiddenimports += ["vieneu.v3nano", "sea_g2p.sea_g2p_rs"]

# PaddleX checks installed distribution versions, including transitive deps.
for distribution in metadata.distributions():
    name = distribution.metadata.get("Name")
    if name:
        datas += copy_metadata(name)

a = Analysis(
    [str(root / "packaging/windows_entry.py")],
    pathex=[str(root / "src"), str(root / "packaging")],
    binaries=binaries, datas=datas, hiddenimports=hiddenimports,
    excludes=["pytest", "IPython", "torch", "tensorflow", "PyQt5", "PyQt6",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick"],
    module_collection_mode={"paddle": "py", "paddlex": "py", "paddleocr": "py"},
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Ludivoxa", console=False,
          icon=str(root / "src/ludivoxa/resources/icons/icon.ico"), version=version_info, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Ludivoxa", upx=False)
