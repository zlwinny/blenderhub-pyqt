import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import time

from PyQt6.QtWidgets import QApplication

from blenderhub import detect
from blenderhub import theme as T


def main() -> None:
    app = QApplication(sys.argv)
    T.load_fonts()
    T.apply_theme(app, T.DARK)
    from blenderhub.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()
    projects = win._pages[2]
    snap = win._snapshot
    expect = sum(
        1 for p in snap.projects if detect.read_blend_thumbnail(p.path) is not None
    )
    end = time.time() + 15
    while time.time() < end:
        filled = sum(1 for c, *_ in projects._cards if c._thumb_src is not None)
        if filled >= expect:
            break
        app.processEvents()
        time.sleep(0.05)
    print("src set:", sum(1 for c, *_ in projects._cards if c._thumb_src is not None),
          "/", expect)
    for i, (card, name, *_rest) in enumerate(projects._cards[:8]):
        pm = card._thumb.pixmap()
        tw = card._thumb.width()
        pm_desc = "no-pm" if pm is None else (
            "null" if pm.isNull() else f"{pm.width()}x{pm.height()}"
        )
        print(f"{i} {name[:18]:22s} label_w={tw:4d} src="
              f"{'Y' if card._thumb_src else 'N'} pm={pm_desc}")


if __name__ == "__main__":
    main()
