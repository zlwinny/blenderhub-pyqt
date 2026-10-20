"""导出指定模块字节码的全部常量（含嵌套元组/列表中的字符串）。"""
import marshal
import os
import sys
import types

DECOMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_decomp")


def load(path):
    with open(path, "rb") as f:
        return marshal.loads(f.read())


def walk_consts(code, prefix=""):
    out = []
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            continue
        if isinstance(c, str) and len(c) > 1:
            out.append((prefix + code.co_name, repr(c)))
        elif isinstance(c, (tuple, list, frozenset)):
            flat = []
            for x in c:
                if isinstance(x, str):
                    flat.append(x)
                elif isinstance(x, (tuple, list)):
                    flat.append("/".join(str(y) for y in x if isinstance(y, str)))
            if flat:
                out.append((prefix + code.co_name, repr(flat)))
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out.extend(walk_consts(c, prefix + code.co_name + "."))
    return out


if __name__ == "__main__":
    for rel in sys.argv[1:]:
        path = os.path.join(DECOMP, rel.replace(".", os.sep) + ".pyc")
        code = load(path)
        print(f"\n===== {rel} 全部常量 =====")
        seen = set()
        for where, val in walk_consts(code):
            key = (where, val)
            if key in seen:
                continue
            seen.add(key)
            print(f"  [{where}] {val}")
