"""分配分路角色 —— 和"框选小地图""勾选英雄"并列的一个窗口。

    python main.py roles

敌方 5 个英雄各占一个角色（上路 / 打野 / 中路 / 下路 / 辅助），角色决定
"什么行为值得播报"。默认值从对局接口的 position 字段自动填；人机模式那里
是空的，就按 上→野→中→下→辅 的顺序补齐 —— 所以进窗口时通常是"已经填好但
可能不对"，手动点几下即可。

    · 点卡片下面的 上/野/中/下/辅 按钮切换角色
    · 两个人抢同一个角色 = 互换位置（永远 5 个角色各一人）
    · Enter 保存    Esc 取消

保存结果写进 config.json 的 roles.assign：{英雄 key: 角色}。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import textdraw
from .picker import _icon, _notice, _to_front
from .roles import ROLE_KEYS, ROLE_NAME, ROLE_SHORT, describe, merge_assign, swap

WIN = "antigank-roles"
CHAMP_DIR = Path(__file__).resolve().parents[1] / "assets" / "champions"

BG = (26, 28, 33)
CARD = (38, 41, 48)
EDGE = (72, 78, 90)
TXT = (238, 240, 244)
DIM = (146, 152, 164)
GREEN = (86, 224, 132)
GREEN_BG = (46, 74, 56)

MARGIN, TOP, BOTTOM = 40, 128, 92
CW, CH, GAP = 200, 252, 28

TITLE = "分配分路角色"
SUB = "点击卡片下方的 上 / 野 / 中 / 下 / 辅 切换　·　Enter 保存　·　Esc 取消"

# 卡片内几何
ICON = 128
BTN_W, BTN_H, BTN_GAP = 30, 34, 3
BTN_Y = 186


def _layout(n: int) -> dict:
    cols = max(1, min(n, 5))
    cards_w = cols * CW + (cols - 1) * GAP
    min_w = max(textdraw.measure(TITLE, 26), textdraw.measure(SUB, 15)) + 2 * MARGIN
    w = int(max(cards_w + 2 * MARGIN, min_w))
    return {"cols": cols, "W": w, "H": TOP + CH + BOTTOM, "cards_w": cards_w}


def _cards(state) -> list[tuple[int, int, int, int]]:
    lay = state["lay"]
    left = (lay["W"] - lay["cards_w"]) // 2
    return [(left + i * (CW + GAP), TOP, left + i * (CW + GAP) + CW, TOP + CH)
            for i in range(len(state["all"]))]


def _role_buttons(rect) -> list[tuple[str, tuple[int, int, int, int]]]:
    x0, y0, x1, _y1 = rect
    total = len(ROLE_KEYS) * BTN_W + (len(ROLE_KEYS) - 1) * BTN_GAP
    left = x0 + (CW - total) // 2
    out = []
    for j, role in enumerate(ROLE_KEYS):
        bx = left + j * (BTN_W + BTN_GAP)
        out.append((role, (bx, y0 + BTN_Y, bx + BTN_W, y0 + BTN_Y + BTN_H)))
    return out


def _render(state) -> np.ndarray:
    from PIL import Image, ImageDraw

    W, H = state["lay"]["W"], state["lay"]["H"]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    textdraw.text(d, (W / 2, 30), TITLE, 26, TXT, anchor="ma")
    textdraw.text(d, (W / 2, 72), SUB, 15, DIM, anchor="ma")

    for key, rect in zip(state["all"], _cards(state)):
        x0, y0, x1, y1 = rect
        d.rounded_rectangle((x0, y0, x1, y1), radius=12, fill=CARD, outline=EDGE, width=1)
        img.paste(_icon(key, ICON), (x0 + (CW - ICON) // 2, y0 + 16))
        textdraw.text(d, ((x0 + x1) / 2, y0 + 158), state["names"].get(key, key), 21,
                      TXT, anchor="ma")

        for role, (bx0, by0, bx1, by1) in _role_buttons(rect):
            on = state["assign"].get(key) == role
            hot = state["hover"] == (key, role)
            d.rounded_rectangle((bx0, by0, bx1, by1), radius=7,
                                fill=GREEN_BG if on else ((52, 57, 66) if hot else (32, 35, 41)),
                                outline=GREEN if on else (EDGE if not hot else (120, 128, 142)),
                                width=2 if on else 1)
            textdraw.text(d, ((bx0 + bx1) / 2, (by0 + by1) / 2 - 1), ROLE_SHORT[role], 17,
                          (16, 30, 20) if on else (TXT if hot else DIM), anchor="mm")

    line = describe(state["assign"], lambda k: state["names"].get(k, k))
    if textdraw.measure(line, 16) > W - 2 * MARGIN:
        line = "　".join(f"{ROLE_NAME[r]}已分配" for r in ROLE_KEYS)
    textdraw.text(d, (W / 2, H - 56), line, 16, GREEN, anchor="ma")
    textdraw.text(d, (W / 2, H - 28),
                  "角色决定谁盯哪条路：上/下/辅助盯分路进出，打野/辅助盯野区河道，中路只报消失",
                  14, DIM, anchor="ma")
    return np.array(img)[:, :, ::-1].copy()


def assign(cfg_path: str = "config.json", demo: bool = False) -> dict | None:
    import cv2

    from .champions import ChampionBook
    from .live import LiveClient

    cfg = cfgmod.load(cfg_path)
    book = ChampionBook(CHAMP_DIR, version=cfg["champions"].get("version", ""))

    positions: dict[str, str] = {}
    if demo:
        pool = [k for k in ("Ahri", "LeeSin", "Darius", "Yasuo", "Malphite")
                if k in book.index]
        positions = {"Darius": "TOP", "LeeSin": "JUNGLE", "Ahri": "MIDDLE",
                     "Yasuo": "BOTTOM", "Malphite": "UTILITY"}
        print("演示模式：用固定阵容 " + "、".join(book.speak(k) for k in pool))
    else:
        print("正在读取本局对局信息…")
        snap = LiveClient(timeout=1.5)._poll() or {}
        enemies = [e for e in snap.get("enemies", []) if e.get("champion")]
        pool = [book.resolve(e["champion"]) for e in enemies]
        for e in enemies:
            key = book.resolve(e["champion"])
            if e.get("position"):
                positions[key] = e["position"]

    pool = [k for k in pool if k in book.index] or list(pool)
    if not pool:
        cached = [k for k in cfg["champions"].get("roster", []) if k in book.index]
        if cached:
            pool = cached
            print("没连上对局接口，先用上一局的阵容：" + "、".join(book.speak(k) for k in cached))
        else:
            print("没有读到本局敌方阵容（127.0.0.1:2999 无响应）。")
            _notice("没有读到本局敌方阵容", [
                "对局接口 127.0.0.1:2999 没有响应。",
                "请在选人结束、进入游戏之后再点一次「分配分路」。",
                "想先看界面效果：python main.py roles --demo",
            ])
            return None

    saved = cfg.get("roles", {}).get("assign") or {}
    state = {
        "all": pool,
        "lay": _layout(len(pool)),
        "assign": merge_assign(pool, saved, positions),
        "names": {k: book.speak(k) for k in pool},
        "hover": None,
        "result": None,
    }

    def on_mouse(event, x, y, _f, _p):
        if event == cv2.EVENT_MOUSEMOVE:
            hit = None
            for key, rect in zip(state["all"], _cards(state)):
                for role, (bx0, by0, bx1, by1) in _role_buttons(rect):
                    if bx0 <= x <= bx1 and by0 <= y <= by1:
                        hit = (key, role)
            state["hover"] = hit
            return
        if event != cv2.EVENT_LBUTTONUP:
            return
        for key, rect in zip(state["all"], _cards(state)):
            for role, (bx0, by0, bx1, by1) in _role_buttons(rect):
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    state["assign"] = swap(state["assign"], key, role)
                    return

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.imshow(WIN, _render(state))
    try:
        cv2.moveWindow(WIN, 60, 60)
    except Exception:
        pass
    _to_front(WIN)

    last = None
    while state["result"] is None:
        snap = (state["hover"], tuple(sorted(state["assign"].items())))
        if snap != last:
            last = snap
            cv2.imshow(WIN, _render(state))
        k = cv2.waitKeyEx(20)
        if k == 27:
            break
        if k in (13, 10):
            state["result"] = dict(state["assign"])
            break
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()

    if state["result"] is None:
        print("已取消，配置未改动。")
        return None

    cfg.setdefault("roles", {})["assign"] = state["result"]
    cfgmod.save(cfg, cfg_path)
    print("已保存分路：" + describe(state["result"], book.speak))
    return state["result"]
