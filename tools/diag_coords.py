"""诊断：OpenCV 全屏窗口的坐标映射（DPI / 客户区偏移）。只读，不改任何配置。"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import cv2
import numpy as np

WIN = "diag-fullscreen"
u32 = ctypes.windll.user32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    u32.SetProcessDPIAware()

POINTS = [(1200, 900), (1480, 1180), (400, 300)]
got: list = []
done = threading.Event()


def grab_geometry(tag: str) -> None:
    hwnd = u32.FindWindowW(None, WIN)
    gw, cw = wintypes.RECT(), wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(gw))
    u32.GetClientRect(hwnd, ctypes.byref(cw))
    pt = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    try:
        dpi = u32.GetDpiForWindow(hwnd)
    except Exception:
        dpi = -1
    print(f"[{tag}] hwnd={hwnd}")
    print(f"  GetWindowRect      left={gw.left} top={gw.top} "
          f"size={gw.right - gw.left}x{gw.bottom - gw.top}")
    print(f"  GetClientRect      size={cw.right}x{cw.bottom}")
    print(f"  ClientToScreen     origin=({pt.x},{pt.y})")
    print(f"  getWindowImageRect {cv2.getWindowImageRect(WIN)}")
    print(f"  GetDpiForWindow    {dpi}")


def clicker() -> None:
    time.sleep(1.2)
    for x, y in POINTS:
        u32.SetCursorPos(x, y)
        time.sleep(0.25)
        u32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.05)
        u32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.45)
    time.sleep(0.4)
    done.set()


def on_mouse(event, x, y, _f, _p):
    if event != cv2.EVENT_LBUTTONUP:
        return
    p = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    got.append((x, y, p.x, p.y))
    print(f"  cv2 回调=({x},{y})   GetCursorPos=({p.x},{p.y})")


def main() -> None:
    sw, sh = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    print(f"GetSystemMetrics  {sw} x {sh}")
    img = np.full((sh, sw, 3), 70, np.uint8)
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.imshow(WIN, img)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.waitKey(300)
    hwnd = u32.FindWindowW(None, WIN)
    u32.SetWindowPos(hwnd, -1, 0, 0, sw, sh, 0x0040)   # HWND_TOPMOST + SHOWWINDOW
    u32.SetForegroundWindow(hwnd)
    u32.BringWindowToTop(hwnd)
    cv2.waitKey(200)
    grab_geometry("全屏后")

    threading.Thread(target=clicker, daemon=True).start()
    t0 = time.time()
    while not done.is_set() and time.time() - t0 < 12:
        cv2.waitKey(30)
    cv2.destroyAllWindows()

    print("\n--- 汇总（屏幕点 → cv2 回调 → GetCursorPos）---")
    for (sx, sy), (cx, cy, px, py) in zip(POINTS, got):
        print(f"  ({sx},{sy}) → cv2({cx},{cy})  cursor({px},{py})")
    if len(got) >= 2:
        a, b = got[0], got[1]
        d_cv2 = (b[0] - a[0], b[1] - a[1])
        d_scr = (POINTS[1][0] - POINTS[0][0], POINTS[1][1] - POINTS[0][1])
        print(f"  两点距离 cv2={d_cv2}  屏幕={d_scr}  "
              f"缩放=({d_cv2[0] / d_scr[0]:.4f}, {d_cv2[1] / d_scr[1]:.4f})")


if __name__ == "__main__":
    main()
