"""用 Python 3.14 marshal 解析导出的字节码，输出模块结构摘要。"""
import marshal
import os
import sys

DECOMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_decomp")


def load(path):
    with open(path, "rb") as f:
        return marshal.loads(f.read())


def summarize(code, out, depth=0, path=""):
    pad = "  " * depth
    qual = getattr(code, "co_qualname", code.co_name)
    args = code.co_varnames[: code.co_argcount]
    out.append(f"{pad}{qual}({', '.join(args)})")
    consts = [c for c in code.co_consts if isinstance(c, str)]
    if consts:
        joined = " | ".join(c.replace("\n", "\\n")[:120] for c in consts[:14])
        out.append(f"{pad}  str: {joined}")
    if code.co_names:
        out.append(f"{pad}  names: {', '.join(code.co_names[:40])}")
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            summarize(c, out, depth + 1)


import types  # noqa: E402

if __name__ == "__main__":
    targets = sys.argv[1:]
    for rel in targets:
        path = os.path.join(DECOMP, rel.replace(".", os.sep) + ".pyc")
        code = load(path)
        out = [f"\n{'=' * 78}", f"== {rel} ==", f"{'=' * 78}"]
        summarize(code, out)
        print("\n".join(out))
