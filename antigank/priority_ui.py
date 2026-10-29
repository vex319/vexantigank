"""调整播报优先级 —— 和"分配分路角色"并列的一个窗口。

    python main.py priority

现在每 2 秒最多只说一条。万一同一时刻有一条以上要说的（比如打野进了河道、
中路同时消失），先播谁由这个顺序决定：**越靠上越先说话**。

    ┌ 1  中路      只报"连续 10 秒不见人"        [↑] [↓]
    ├ 2  打野      盯野区 / 河道 / 龙坑
    ├ 3  辅助      盯下路进出 + 野区河道
    ├ 4  下路      盯下路进出
    └ 5  上路      盯上路进出

默认 中 > 野 > 辅 > 下 > 上：中路最容易被 gank，上路离你通常最远。
按住不动也没关系，随时可以改回来。Enter 保存，Esc 取消。
"""

from __future__ import annotations

import numpy as np

from . import config as cfgmod
from . import textdraw
from .batch import DEFAULT_PRIORITY
from .picker import _to_front
from .roles import ROLE_NAME

WIN = "antigank-priority"

BG = (26, 28, 33)
CARD = (38, 41, 48)
EDGE = (72, 78, 90)
TXT = (238, 240, 244)
DIM = (146, 152, 164)
GREEN = (86, 224, 132)
GREEN_BG = (46, 74, 56)
ARROW_BG = (32, 35, 41)
ARROW_HOT = (52, 57, 66)

W = 660
MARGIN = 40
TOP = 148
ROW_H, ROW_GAP = 62, 10
BOTTOM = 104
BTN = 34          # 上下箭头按钮的边长

# 每个角色的"它在盯什么"，让人一眼知道调顺序在调什么
ROLE_DESC = {
    "top": "盯上路进出",
    "jungle": "盯野区 / 河道 / 龙坑",
    "mid": "只报「连续 10 秒不见人」",
    "adc": "盯下路进出",
    "support": "盯下路进出 + 野区河道",
}

TITLE = "调整播报优先级"
SUB = "每 2 秒最多播 1 条；同时有多条待播时，越靠上越先播"
FOOT = "点右侧上下箭头调整顺序　·　Enter 保存　·　Esc 取消"


def _height(n: int) -> int:
    return TOP + n * ROW_H + (n - 1) * ROW_GAP + BOTTOM


def _row_rects(n: int) -> list[tuple[int, int, int, int]]:
    return [(MARGIN, TOP + i * (ROW_H + ROW_GAP), W - MARGIN, TOP + i * (ROW_H + ROW_GAP) + ROW_H)
            for i in range(n)]


def _arrows(rect) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """(上移按钮, 下移按钮) 的矩形。两个箭头竖着摞，各 22 高，不重叠。"""
    x0, y0, x1, y1 = rect
    cy = (y0 + y1) // 2
    right = x1 - 16
    left = right - 40
    return ((left, cy - 25, right, cy - 3), (left, cy + 3, right, cy + 25))


def _render(state) -> np.ndarray:
    from PIL import Image, ImageDraw

    order = state["order"]
    H = _height(len(order))
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    textdraw.text(d, (W / 2, 30), TITLE, 26, TXT, anchor="ma")
    textdraw.text(d, (W / 2, 72), SUB, 15, DIM, anchor="ma")

    for i, role in enumerate(order):
        x0, y0, x1, y1 = _row_rects(len(order))[i]
        d.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=CARD, outline=EDGE, width=1)

        # 序号圆
        cx, cy = x0 + 34, (y0 + y1) // 2
        first = i == 0
        d.ellipse((cx - 17, cy - 17, cx + 17, cy + 17),
                  fill=GREEN_BG if first else (30, 34, 40),
                  outline=GREEN if first else EDGE, width=2)
        textdraw.text(d, (cx, cy - 1), str(i + 1), 17, GREEN if first else DIM, anchor="mm")

        textdraw.text(d, (x0 + 68, y0 + 12), ROLE_NAME.get(role, role), 20, TXT)
        textdraw.text(d, (x0 + 68, y0 + 38), ROLE_DESC.get(role, ""), 13, DIM)
        if first:
            textdraw.text(d, (x0 + 68 + textdraw.measure(ROLE_NAME.get(role, role), 20) + 10,
                              y0 + 15), "最先播", 12, GREEN)

        up, down = _arrows((x0, y0, x1, y1))
        for kind, (bx0, by0, bx1, by1) in (("up", up), ("down", down)):
            hot = state["hover"] == (i, kind)
            disabled = (kind == "up" and i == 0) or (kind == "down" and i == len(order) - 1)
            d.rounded_rectangle((bx0, by0, bx1, by1), radius=7,
                                fill=ARROW_HOT if (hot and not disabled) else ARROW_BG,
                                outline=(120, 128, 142) if (hot and not disabled) else EDGE,
                                width=1)
            color = DIM if disabled else (TXT if hot else (196, 202, 212))
            # 用三角画箭头：字体里没有 ↑↓，画出来更保险
            mx, my = (bx0 + bx1) / 2, (by0 + by1) / 2
            if kind == "up":
                pts = [(mx, my - 7), (mx - 7, my + 5), (mx + 7, my + 5)]
            else:
                pts = [(mx, my + 7), (mx - 7, my - 5), (mx + 7, my - 5)]
            d.polygon(pts, fill=color)

    textdraw.text(d, (W / 2, H - 66),
                  "当前：" + " > ".join(ROLE_NAME.get(r, r) for r in order), 16, GREEN, anchor="ma")
    textdraw.text(d, (W / 2, H - 34), FOOT, 14, DIM, anchor="ma")
    return np.array(img)[:, :, ::-1].copy()


def _load_order(cfg: dict) -> list[str]:
    saved = (cfg.get("behaviour") or {}).get("priority") or list(DEFAULT_PRIORITY)
    out = [r for r in saved if r in ROLE_NAME]
    for r in DEFAULT_PRIORITY:
        if r not in out:
            out.append(r)
    return out


def adjust(cfg_path: str = "config.json") -> list[str] | None:
    """打开窗口调顺序，返回新的优先级列表（取消返回 None）。"""
    import cv2

    cfg = cfgmod.load(cfg_path)
    state = {"order": _load_order(cfg), "hover": None, "result": None}

    def move(i: int, delta: int) -> None:
        j = i + delta
        order = state["order"]
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]

    def hit(x: int, y: int):
        for i, rect in enumerate(_row_rects(len(state["order"]))):
            for kind, (bx0, by0, bx1, by1) in zip(("up", "down"), _arrows(rect)):
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    return i, kind
        return None

    def on_mouse(event, x, y, _f, _p):
        if event == cv2.EVENT_MOUSEMOVE:
            state["hover"] = hit(x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            h = hit(x, y)
            if h:
                move(h[0], -1 if h[1] == "up" else 1)

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.imshow(WIN, _render(state))
    try:
        cv2.moveWindow(WIN, 120, 100)
    except Exception:
        pass
    _to_front(WIN)

    last = None
    while state["result"] is None:
        snap = (state["hover"], tuple(state["order"]))
        if snap != last:
            last = snap
            cv2.imshow(WIN, _render(state))
        k = cv2.waitKeyEx(20)
        if k == 27:
            break
        if k in (13, 10):
            state["result"] = list(state["order"])
            break
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()

    if state["result"] is None:
        print("已取消，优先级未改动。")
        return None

    cfg.setdefault("behaviour", {})["priority"] = state["result"]
    cfgmod.save(cfg, cfg_path)
    print("已保存播报优先级：" + " > ".join(ROLE_NAME.get(r, r) for r in state["result"]))
    return state["result"]
