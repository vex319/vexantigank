"""启动菜单窗口 —— 双击 启动.bat 后第一眼看到的东西。

之前 启动.bat 直接 `run --auto`，用户根本看不到"框选小地图"和"勾选英雄"
这两个窗口，只能靠命令行手动敲。现在把它们做成一个可视菜单：

    [1] 开始监测     常驻待机，开局自动接管
    [2] 框选小地图   点左上角 + 右下角两下
    [3] 勾选英雄     只播报选中的敌方英雄（需局内）
    [4] 分配分路     5 个敌人各占一路，决定谁盯哪条路
    [5] 播报优先级   同时有多条要说时，谁先说话

鼠标点击或按 1/2/3/4/5 都可以；Esc / 关窗退出。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import textdraw

# OpenCV 用 ANSI 接口建窗口，中文标题会变成乱码（"鍙?Gank 鍝ㄥ叺"），
# 所以标题栏保持 ASCII，中文写在窗口内容里。
WIN = "antigank"

BG = (26, 28, 33)
CARD = (38, 41, 48)
CARD_HOVER = (48, 53, 63)
EDGE = (64, 70, 82)
EDGE_HOVER = (86, 224, 132)
TXT = (238, 240, 244)
DIM = (146, 152, 164)
GREEN = (86, 224, 132)
WARN = (230, 176, 92)

W, H = 780, 742
MARGIN = 60
BTN_H, BTN_GAP = 82, 12
BTN_TOP = 228

ITEMS = [
    ("run", "开始监测", "常驻待机：开局自动接管，一局结束自动等下一局"),
    ("cal", "框选小地图", "点小地图左上角，再点右下角 —— 两下就好"),
    ("pick", "勾选要播报的英雄", "只列本局敌方阵容，点卡片切换勾选（需在局内）"),
    ("roles", "分配分路角色", "上 / 野 / 中 / 下 / 辅，决定谁盯分路、谁盯野区（需在局内）"),
    ("priority", "调整播报优先级", "同时有多条要说时谁先说话，越靠上越先播"),
]


def _btn_rects() -> list[tuple[int, int, int, int]]:
    out = []
    for i in range(len(ITEMS)):
        y = BTN_TOP + i * (BTN_H + BTN_GAP)
        out.append((MARGIN, y, W - MARGIN, y + BTN_H))
    return out


def _status(cfg: dict) -> tuple[str, tuple[int, int, int]]:
    r = cfg["capture"]["region"]
    if int(r["left"]) == 0 and int(r["top"]) == 0:
        return "小地图：还没框选（先用默认右下角猜的框，会不准）", WARN
    side = f"{int(r['width'])}×{int(r['height'])} @ ({int(r['left'])},{int(r['top'])})"
    return f"小地图：{side}", DIM


def _notify_text(cfg: dict) -> str:
    keys = cfg["champions"].get("notify") or []
    if not keys:
        return "播报名单：全部敌方英雄"
    try:
        from .champions import ChampionBook

        book = ChampionBook(Path(__file__).resolve().parents[1] / "assets" / "champions")
        return "播报名单：" + "、".join(book.speak(k) for k in keys)
    except Exception:
        return f"播报名单：{len(keys)} 个英雄"


def _roles_text(cfg: dict) -> str:
    saved = (cfg.get("roles") or {}).get("assign") or {}
    if not saved:
        return "分路角色：未设置（进游戏后点「分配分路角色」，或自动按上野中下辅填）"
    try:
        from .champions import ChampionBook
        from .roles import describe

        book = ChampionBook(Path(__file__).resolve().parents[1] / "assets" / "champions")
        return "分路角色：" + describe(saved, book.speak)
    except Exception:
        return f"分路角色：已设置 {len(saved)} 个"


def _priority_text(cfg: dict) -> str:
    from .batch import DEFAULT_PRIORITY
    from .roles import ROLE_NAME

    order = (cfg.get("behaviour") or {}).get("priority") or list(DEFAULT_PRIORITY)
    return "播报优先级：" + " > ".join(ROLE_NAME.get(r, r) for r in order) \
           + "　（每 2 秒最多 1 条）"


def _map_note() -> str:
    return "野区：上半靠大龙 / 下半靠小龙（按阵营换算）；只算折线围住的那块"


def _render(cfg: dict, hover: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    textdraw.text(d, (MARGIN, 44), "反 Gank 哨兵", 30, TXT)
    textdraw.text(d, (MARGIN + textdraw.measure("反 Gank 哨兵", 30) + 14, 56),
                  "antigank", 15, DIM)
    textdraw.text(d, (MARGIN, 92), "小地图上一出现红圈，就用中文念出是谁、去了哪条路",
                  15, DIM)

    s, scolor = _status(cfg)
    textdraw.text(d, (MARGIN, 112), s, 15, scolor)
    textdraw.text(d, (MARGIN, 134), _notify_text(cfg), 15, DIM)
    textdraw.text(d, (MARGIN, 156), _roles_text(cfg), 15, DIM)
    textdraw.text(d, (MARGIN, 178), _priority_text(cfg), 15, DIM)
    textdraw.text(d, (MARGIN, 200), _map_note(), 13, DIM)

    for i, (_act, title, desc) in enumerate(ITEMS):
        x0, y0, x1, y1 = _btn_rects()[i]
        on = i == hover
        d.rounded_rectangle((x0, y0, x1, y1), radius=12,
                            fill=CARD_HOVER if on else CARD,
                            outline=EDGE_HOVER if on else EDGE,
                            width=2 if on else 1)
        # 序号
        cx, cy = x0 + 44, y0 + BTN_H // 2
        d.ellipse((cx - 19, cy - 19, cx + 19, cy + 19),
                  fill=(24, 52, 34) if on else (30, 34, 40),
                  outline=GREEN if on else EDGE, width=2)
        textdraw.text(d, (cx, cy - 1), str(i + 1), 19, GREEN if on else DIM, anchor="mm")
        textdraw.text(d, (x0 + 84, y0 + 16), title, 22, TXT if on else (206, 210, 218))
        textdraw.text(d, (x0 + 84, y0 + 50), desc, 14, DIM)

    foot = "点击或按 1 / 2 / 3 / 4 / 5 选择　·　Esc 退出"
    textdraw.text(d, (W / 2, H - 26), foot, 15, DIM, anchor="ma")
    return np.array(img)[:, :, ::-1].copy()


def run_menu(config_path: str = "config.json") -> str | None:
    """返回 "run" / "cal" / "pick" / "roles" / "priority"，关窗口或 Esc 返回 None。"""
    import cv2

    from .picker import _to_front

    cfg = cfgmod.load(config_path)
    state = {"hover": -1, "result": None}

    def hit(x: int, y: int) -> int:
        for i, (x0, y0, x1, y1) in enumerate(_btn_rects()):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return i
        return -1

    def on_mouse(event, x, y, _f, _p):
        if event == cv2.EVENT_MOUSEMOVE:
            state["hover"] = hit(x, y)
        elif event == cv2.EVENT_LBUTTONUP:
            i = hit(x, y)
            if i >= 0:
                state["result"] = ITEMS[i][0]

    try:
        cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    except cv2.error:
        return None
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.imshow(WIN, _render(cfg, -1))
    try:
        cv2.moveWindow(WIN, 80, 80)
    except Exception:
        pass
    _to_front()

    last_hover = -2
    while state["result"] is None:
        if state["hover"] != last_hover:
            last_hover = state["hover"]
            cv2.imshow(WIN, _render(cfg, state["hover"]))
        k = cv2.waitKeyEx(20)
        if k in (27, ord("q"), ord("Q")):
            break
        if k in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
            state["result"] = ITEMS[k - ord("1")][0]
            break
        if k in (13, 10) and state["hover"] >= 0:
            state["result"] = ITEMS[state["hover"]][0]
            break
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break

    cv2.destroyAllWindows()
    return state["result"]
