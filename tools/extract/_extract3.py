"""导出 PyInstaller 产物中 core.* / modules.* 的原始字节码（raw marshal，供反汇编）。

用法：
  python _extract3.py            # 默认取项目根的 dist/BlenderHub.exe
  python _extract3.py <exe路径>  # 指定任意 PyInstaller 产物
输出：同目录 _decomp/
"""
import os
import sys

from PyInstaller.archive.readers import CArchiveReader

EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dist", "BlenderHub.exe")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_decomp")

arch = CArchiveReader(EXE)
pyz_name = next(n for n, v in arch.toc.items() if v[4] == "z")
pyz = arch.open_embedded_archive(pyz_name)

targets = [n for n in sorted(pyz.toc) if n.startswith("core.") or n.startswith("modules.")]
os.makedirs(OUT, exist_ok=True)
for n in targets:
    data = pyz.extract(n, raw=True)
    rel = n.replace(".", os.sep) + ".pyc"
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
print(f"导出 {len(targets)} 个模块:")
for n in targets:
    print("  ", n)
