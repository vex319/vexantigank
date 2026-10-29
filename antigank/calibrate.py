"""小地图区域校准（两点法）。

    python main.py calibrate

冻结当前屏幕，在上面点两下：
    ① 小地图左上角   ② 小地图右下角
Enter 保存，R 重选，Esc 取消。什么都不点直接 Enter 则接受默认猜测框。

为什么是两点而不是拖一个框：
  * 拖动如果在全屏独占的游戏窗口上丢了鼠标松开事件，框会卡在半路；
    两次独立点击没这个风险。
  * 点击落点比"拖动松手的那一瞬"更准，280px 的小地图上 2px 误差就是 0.7%。

为什么不用 tkinter：当前解释器是便携版 Python，没有打包 tcl/tk
（`import tkinter` 直接 ModuleNotFoundError），而依赖都装在这个解释器里。
所以改用 OpenCV 的全屏窗口 + Pillow 画中文，零额外系统依赖。
"""

from __future__ import annotations

import time

import numpy as np

from .capture import Capture, dpi_aware, screen_size
from . import config as cfgmod

WIN = "antigank-calibrate"
MIN_SIDE = 60          # 小于这个尺寸肯定不是小地图，大概率误点
FONTS = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
         "C:/Windows/Fonts/simsun.ttc")
ACCENT = (90, 255, 120)     # BGR：亮绿
GUESS = (0, 165, 255)       # BGR：橙


def square_from(p1: tuple[int, int], p2: tuple[int, int],
                sw: int, sh: int) -> tuple[int, int, int, int]:
    """两个对角点 → 小地图正方形区域 (left, top, w, h)。

    以两点中点为中心、取较长边为边长：小地图本来就是正方形，点得准时
    dx≈dy 没有位移；点歪一点时保证较远那个角仍被完整包含（宁可多含几像素
    深色底图，也不能裁掉小地图边缘的红点）。
    """
    x0, y0 = min(p1[0], p2[0]), min(p1[1], p2[1])
    x1, y1 = max(p1[0], p2[0]), max(p1[1], p2[1])
    side = min(max(x1 - x0, y1 - y0), sw, sh)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    left = max(0, min(sw - side, cx - side // 2))
    top = max(0, min(sh - side, cy - side // 2))
    return left, top, side, side


def _font(size: int):
    from PIL import ImageFont

    for path in FONTS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _render_hud(w: int, h: int, title: str, sub: str) -> np.ndarray:
    """把两行中文提示渲染成 RGBA 图层（只做一次，之后每帧只做合成）。"""
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f1, f2 = _font(30), _font(19)
    tw = d.textlength(title, font=f1)
    d.text(((w - tw) / 2, 32), title, font=f1, fill=(255, 255, 255, 255),
           stroke_width=3, stroke_fill=(0, 0, 0, 210))
    tw = d.textlength(sub, font=f2)
    d.text(((w - tw) / 2, 78), sub, font=f2, fill=(214, 214, 214, 255),
           stroke_width=2, stroke_fill=(0, 0, 0, 200))
    return np.array(layer)


def _over(bg: np.ndarray, layer_rgba: np.ndarray) -> np.ndarray:
    a = layer_rgba[:, :, 3].astype(np.float32) * (1.0 / 255.0)
    a = a[:, :, None]
    rgb = layer_rgba[:, :, 2::-1].astype(np.float32)      # RGBA 前三通道是 RGB，翻成 BGR
    out = bg.astype(np.float32) * (1.0 - a) + rgb * a
    return out.astype(np.uint8)


def _spotlight(img: np.ndarray, base: np.ndarray,
               center: tuple[int, int], radius: int = 130) -> None:
    """鼠标周围恢复原亮度，方便对准小地图那条细边框。

    只处理半径内的包围盒，全图级操作会白花十几毫秒。
    """
    import cv2

    x0, y0 = max(0, center[0] - radius), max(0, center[1] - radius)
    x1 = min(img.shape[1], center[0] + radius)
    y1 = min(img.shape[0], center[1] + radius)
    if x1 <= x0 or y1 <= y0:
        return
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.circle(m, (center[0] - x0, center[1] - y0), radius, 255, -1)
    sel = m > 0
    img[y0:y1, x0:x1][sel] = base[y0:y1, x0:x1][sel]
    cv2.circle(img, center, radius, (200, 200, 200), 1, cv2.LINE_AA)


def _put(img: np.ndarray, text: str, org: tuple[int, int],
         scale: float = 0.6, color: tuple[int, int, int] = (255, 255, 255),
         thick: int = 1) -> None:
    """带黑描边的 ASCII 文字（中文由 HUD 图层负责，cv2 不支持中文）。"""
    import cv2

    cv2.putText(img, text, (int(org[0]), int(org[1])), cv2.FONT_HERSHEY_SIMPLEX,
                scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, (int(org[0]), int(org[1])), cv2.FONT_HERSHEY_SIMPLEX,
                scale, color, thick, cv2.LINE_AA)


def _to_front(win: str, sw: int, sh: int) -> None:
    """把窗口顶到最前并抢焦点。

    OpenCV 的全屏窗口不一定是前台窗口，被遮住或没焦点时鼠标事件会先被
    系统拿去"激活窗口"而不是送进回调，表现为第一下点击丢失、坐标飘。
    """
    import ctypes
    import time

    u32 = ctypes.windll.user32
    for _ in range(12):                       # 窗口句柄有时晚几帧才可查到
        hwnd = u32.FindWindowW(None, win)
        if hwnd:
            u32.SetWindowPos(hwnd, -1, 0, 0, sw, sh, 0x0040)   # TOPMOST | SHOWWINDOW
            u32.SetForegroundWindow(hwnd)
            u32.BringWindowToTop(hwnd)
            u32.SetFocus(hwnd)
            return
        time.sleep(0.05)


def run(cfg_path: str = "config.json", delay: float = 3.0) -> tuple[int, int, int, int] | None:
    dpi_aware()
    import cv2

    cfg = cfgmod.load(cfg_path)
    sw, sh = screen_size()

    # 校准必须抓到"游戏画面"。全屏独占下 Alt+Tab 出来游戏已经最小化，
    # 这时候截屏拿到的是桌面，点出来的框必然是错的。所以留几秒让人切回去。
    if delay > 0:
        print(f"{delay:.0f} 秒后截屏 —— 现在切回游戏（Alt+Tab），让小地图露出来")
        for i in range(int(delay), 0, -1):
            print(f"  {i}...", flush=True)
            time.sleep(1)

    # 冻结一帧整屏当作画布：画面不动，点得从容，也不受游戏渲染影响
    cap = Capture((0, 0, sw, sh), "gdi")
    try:
        base = np.array(cap.grab(), copy=True)
    finally:
        cap.close()
    sh, sw = base.shape[:2]

    # 默认猜测框：LoL 小地图默认贴屏幕右下角，边长按 1080p 的 250px 缩放
    gl, gt, gside, _ = cfgmod.default_region(sw, sh)
    dark = (base.astype(np.float32) * 0.45).astype(np.uint8)

    st: dict = {"p1": None, "p2": None, "cur": None,
                "saved": None, "bg": dark.copy(), "quit": False}

    def set_hud(title: str, sub: str) -> None:
        st["bg"] = _over(dark, _render_hud(sw, sh, title, sub))

    def prompt(step: int) -> None:
        if step == 0:
            set_hud("① 点一下小地图的【左上角】",
                    "点两下就完成　·　不点、直接 Enter 用橙色默认框　·　Esc 取消")
        else:
            set_hud("② 再点一下小地图的【右下角】",
                    "点完立即生效　·　R 重选　·　Esc 取消")

    def draw() -> np.ndarray:
        img = st["bg"].copy()
        p1, p2 = st["p1"], st["p2"]
        if st["cur"] is not None and p2 is None:
            _spotlight(img, base, st["cur"], 130)     # 选点中：鼠标周围恢复原亮度
        if p1 is None:
            cv2.rectangle(img, (gl, gt), (gl + gside - 1, gt + gside - 1), GUESS, 2)
            _put(img, f"{gside}px", (gl + 4, max(24, gt - 12)), 0.62, GUESS)
        else:
            other = p2 if p2 is not None else st["cur"]
            if other is not None:
                l, t, w, h = square_from(p1, other, sw, sh)
                img[t:t + h, l:l + w] = base[t:t + h, l:l + w]   # 框内不着色
                solid = p2 is not None
                cv2.rectangle(img, (l, t), (l + w - 1, t + h - 1),
                              ACCENT, 3 if solid else 2)
                _put(img, f"{w}x{h}px  icon~{w * 0.07:.0f}px",
                     (l + 4, max(24, t - 12)), 0.66, ACCENT, 2 if solid else 1)
        for i, p in enumerate((p1, p2)):
            if p is None:
                continue
            cv2.drawMarker(img, (int(p[0]), int(p[1])), ACCENT,
                           cv2.MARKER_CROSS, 28, 2, cv2.LINE_AA)
            _put(img, str(i + 1), (int(p[0]) + 12, int(p[1]) - 10), 0.85, ACCENT)
        return img

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_MOUSEMOVE:
            st["cur"] = (x, y)
            return
        if event != cv2.EVENT_LBUTTONUP:
            return
        p = (int(x), int(y))
        if st["p1"] is None:
            st["p1"] = p
            prompt(1)
            return
        l, t, w, h = square_from(st["p1"], p, sw, sh)
        if w < MIN_SIDE:
            set_hud("框太小，请重新点【右下角】",
                    f"当前只有 {w}px，小地图不可能这么小　·　R 重选")
            return
        st["p2"] = p
        st["saved"] = (l, t, w, h)       # 两点即完成，不再要求确认

    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.imshow(WIN, base)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback(WIN, on_mouse)
    _to_front(WIN, sw, sh)
    prompt(0)

    frames = 0
    while st["saved"] is None and not st["quit"]:
        try:
            cv2.imshow(WIN, draw())
        except cv2.error:
            st["quit"] = True
            break
        k = cv2.waitKey(20) & 0xFF
        frames += 1
        if k == 27:                                   # Esc
            st["quit"] = True
        elif k in (13, 10) and st["p1"] is None:      # Enter：直接接受默认猜测框
            st["saved"] = (gl, gt, gside, gside)
        elif k in (ord("r"), ord("R")):
            st["p1"] = st["p2"] = None
            prompt(0)
        # 窗口被关掉就退出。前几帧 WND_PROP_VISIBLE 可能还没稳定，跳过检查
        if frames > 3 and cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            st["quit"] = True
    if st["saved"] is not None:                       # 定格一下，让用户看清框住了哪
        cv2.imshow(WIN, draw())
        cv2.waitKey(220)
    cv2.destroyAllWindows()

    rect = st["saved"]
    if not rect:
        print("已取消，配置未改动。")
        return None

    left, top, w, h = rect
    cfg["capture"]["region"] = {"left": left, "top": top, "width": w, "height": h}
    cfgmod.save(cfg, cfg_path)
    print(f"已保存小地图区域: left={left} top={top} size={w}x{h}")
    print(f"  左上角 ({left}, {top})　右下角 ({left + w}, {top + h})")
    print(f"  按预期英雄头像直径约 {w * 0.07:.0f} px")
    _verify(rect, cfg)
    return rect


def _verify(rect: tuple[int, int, int, int], cfg: dict) -> None:
    """立刻抓一张，把检测结果画出来存成 PNG，顺便报个数。"""
    import cv2

    from .detect import RingDetector, annotate

    try:
        cap = Capture(rect, cfg["capture"]["backend"])
        frame = cap.grab()
        cap.close()
    except Exception as exc:
        print(f"抓图失败: {exc}")
        return

    det = RingDetector(cfg["detect"])
    found = det.detect(frame)
    out = annotate(frame, found)
    cv2.imwrite("assets/calib_preview.png", out)
    print(f"当前画面检出红色目标 {len(found)} 个 → 已存 assets/calib_preview.png")
    print("  正常情况：只有敌方英雄被检到（0~5 个）。")
    print("  若数量偏多，多半是阈值太松；若一个都没有，检查是否框到了小地图。")
