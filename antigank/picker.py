"""勾选要播报的敌方英雄。

    python main.py pick

只列**本局敌方那 5 个英雄**（从本地对局接口 127.0.0.1:2999 读的，不是猜的），
点一下取消勾选，Enter 保存。没在局内就直接提示，不弹窗、不列全部英雄。

    · 点击卡片 / 勾选框   切换该英雄
    · A 全选    N 全不选
    · Enter 保存    Esc 取消

保存结果写进 config.json 的 champions.notify：空列表 = 全部播报（默认全选）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import textdraw

WIN = "antigank-pick"
CHAMP_DIR = Path(__file__).resolve().parents[1] / "assets" / "champions"

BG = (26, 28, 33)
CARD = (38, 41, 48)
CARD_ON = (46, 74, 56)
EDGE = (72, 78, 90)
EDGE_ON = (86, 224, 132)
TXT = (238, 240, 244)
DIM = (146, 152, 164)
GREEN = (86, 224, 132)

MARGIN, TOP, BOTTOM = 40, 112, 78
CW, CH, GAP = 200, 236, 28
MAX_COLS = 5
TITLE = "勾选要播报的敌方英雄"
SUB = "本局敌方阵容　·　点击切换　·　A 全选　·　N 全不选　·　Enter 保存　·　Esc 取消"

_icons: dict[tuple[str, int], object] = {}


def _icon(key: str, size: int):
    """圆形头像；缺图时给一个灰底圆占位。"""
    from PIL import Image, ImageDraw

    hit = _icons.get((key, size))
    if hit is not None:
        return hit
    base = Image.new("RGB", (size, size), CARD)
    path = CHAMP_DIR / f"{key}.png"
    if path.exists():
        try:
            src = Image.open(path).convert("RGB").resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            base.paste(src, (0, 0), mask)
            _icons[(key, size)] = base
            return base
        except Exception:
            pass
    d = ImageDraw.Draw(base)
    d.ellipse((0, 0, size - 1, size - 1), fill=(56, 60, 68))
    textdraw.text(d, (size / 2, size / 2 - 2), key[:2], int(size * 0.32), DIM, anchor="mm")
    _icons[(key, size)] = base
    return base


def _layout(n: int) -> dict:
    """一行最多 5 个；超过（极少数模式）就换行。

    只有一两个英雄时，窗口也要宽到装得下标题，否则标题被窗口边缘截断
    （"Ente" 半个单词露在外面，看起来像界面坏了）。
    """
    cols = min(max(n, 1), MAX_COLS)
    rows = (n + cols - 1) // cols
    cards_w = cols * CW + (cols - 1) * GAP
    min_w = max(textdraw.measure(TITLE, 26), textdraw.measure(SUB, 15)) + 2 * MARGIN
    w = int(max(cards_w + 2 * MARGIN, min_w))
    h = TOP + rows * CH + (rows - 1) * GAP + BOTTOM
    return {"cols": cols, "rows": rows, "W": w, "H": h, "cards_w": cards_w}


def _rects(state: dict) -> list[tuple[int, int, int, int]]:
    lay, out = state["lay"], []
    left = (lay["W"] - lay["cards_w"]) // 2
    for i in range(len(state["all"])):
        r, c = divmod(i, lay["cols"])
        x = left + c * (CW + GAP)
        y = TOP + r * (CH + GAP)
        out.append((x, y, x + CW, y + CH))
    return out


def _checkbox(d, x: int, y: int, size: int, on: bool) -> None:
    if on:
        d.rounded_rectangle([x, y, x + size, y + size], radius=5, fill=GREEN)
        w = max(2, size // 7)
        d.line([(x + size * 0.24, y + size * 0.53),
                (x + size * 0.43, y + size * 0.72),
                (x + size * 0.77, y + size * 0.29)],
               fill=(18, 34, 22), width=w)
    else:
        d.rounded_rectangle([x, y, x + size, y + size], radius=5,
                            outline=(118, 124, 136), width=max(2, size // 9))


def _render(state: dict) -> np.ndarray:
    from PIL import Image, ImageDraw

    W, H = state["lay"]["W"], state["lay"]["H"]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    textdraw.text(d, (W / 2, 30), TITLE, 26, TXT, anchor="ma")
    textdraw.text(d, (W / 2, 72), SUB, 15, DIM, anchor="ma")

    for key, (x0, y0, x1, y1) in zip(state["all"], _rects(state)):
        on = key in state["sel"]
        d.rounded_rectangle((x0, y0, x1, y1), radius=12,
                            fill=CARD_ON if on else CARD,
                            outline=EDGE_ON if on else EDGE,
                            width=3 if on else 1)
        img.paste(_icon(key, 150), (x0 + (CW - 150) // 2, y0 + 16))
        textdraw.text(d, ((x0 + x1) / 2, y0 + 178), state["names"].get(key, key), 21,
                      TXT if on else DIM, anchor="ma")
        sub = state["subs"].get(key, "")
        if sub and sub != state["names"].get(key):
            textdraw.text(d, ((x0 + x1) / 2, y0 + 208), sub, 13, DIM, anchor="ma")
        _checkbox(d, x1 - 36, y0 + 8, 28, on)

    chosen = [k for k in state["all"] if k in state["sel"]]
    footer = ("将播报：全部敌方英雄" if len(chosen) == len(state["all"])
              else ("一个都不播报" if not chosen
                    else "将播报：" + "、".join(state["names"].get(k, k) for k in chosen)))
    if textdraw.measure(footer, 16) > W - 2 * MARGIN:
        footer = f"将播报 {len(chosen)} 个英雄"
    color = GREEN if chosen else (230, 120, 110)
    textdraw.text(d, (W / 2, H - 48), footer, 16, color, anchor="ma")
    return np.array(img)[:, :, ::-1].copy()


def pick(cfg_path: str = "config.json", demo: bool = False) -> list[str] | None:
    import cv2

    from .champions import ChampionBook
    from .live import LiveClient

    cfg = cfgmod.load(cfg_path)
    book = ChampionBook(CHAMP_DIR, version=cfg["champions"].get("version", ""))

    if demo:
        enemies = [k for k in ("Ahri", "LeeSin", "Darius", "Yasuo", "Malphite")
                   if k in book.index]
        print("演示模式：用固定阵容 " + "、".join(book.speak(k) for k in enemies))
    else:
        print("正在读取本局对局信息…")
        snap = LiveClient(timeout=1.5)._poll() or {}
        # 国服接口给中文名（"齐天大圣"），归一成英文 key 才对得上头像和语音
        enemies = [book.resolve(e["champion"]) for e in snap.get("enemies", [])
                   if e.get("champion")]

    pool = [k for k in enemies if k in book.index] or list(enemies)
    if not pool:
        cached = [k for k in cfg["champions"].get("roster", []) if k in book.index]
        if cached:
            pool = cached
            print("没连上对局接口，先用上一局的阵容：" + "、".join(book.speak(k) for k in cached))
            print("  进游戏后再点一次「勾选英雄」，才会列本局真实的 5 个人")
        else:
            print("没有读到本局敌方阵容（127.0.0.1:2999 无响应）。")
            _notice("没有读到本局敌方阵容", [
                "对局接口 127.0.0.1:2999 没有响应。",
                "请在选人结束、进入游戏之后再点一次「勾选英雄」。",
                "想先看界面效果：python main.py pick --demo",
            ])
            return None
    print("敌方阵容：" + "、".join(book.speak(k) for k in pool))

    saved = [k for k in cfg["champions"].get("notify", []) if k in pool]
    sel = set(saved) if saved else set(pool)

    lay = _layout(len(pool))
    state = {
        "all": pool, "lay": lay, "sel": sel,
        # 大字 = 播报时念的称号（大发明家 / 冰晶凤凰），小字 = 本名，两边都能认
        "names": {k: book.speak(k) for k in pool},
        "subs": {k: book.name(k) for k in pool},
        "result": None,
    }

    def on_mouse(event, x, y, _f, _p):
        if event != cv2.EVENT_LBUTTONUP:
            return
        for i, (x0, y0, x1, y1) in enumerate(_rects(state)):
            if x0 <= x <= x1 and y0 <= y <= y1:
                state["sel"].symmetric_difference_update({state["all"][i]})
                return

    cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WIN, on_mouse)
    cv2.imshow(WIN, _render(state))
    try:
        cv2.moveWindow(WIN, 60, 60)
    except Exception:
        pass
    _to_front()

    while state["result"] is None:
        cv2.imshow(WIN, _render(state))
        k = cv2.waitKeyEx(20)
        if k == 27:                                     # Esc
            break
        if k in (13, 10):                               # Enter
            state["result"] = [x for x in pool if x in state["sel"]]
            break
        if k in (ord("a"), ord("A")):
            state["sel"] = set(pool)
        elif k in (ord("n"), ord("N")):
            state["sel"] = set()
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()

    if state["result"] is None:
        print("已取消，配置未改动。")
        return None

    chosen = state["result"]
    # 全选等于"不限制"，存空列表；下一局换阵容也不会因为名单过期而整局静音
    cfg["champions"]["notify"] = [] if len(chosen) == len(pool) else chosen
    cfgmod.save(cfg, cfg_path)
    if not cfg["champions"]["notify"]:
        print("已保存：播报全部敌方英雄")
    elif not chosen:
        print("已保存：一个都不播报（重跑本命令可改回来）")
    else:
        print("已保存：只播报 " + "、".join(book.speak(k) for k in chosen))
    return cfg["champions"]["notify"]


def _notice(title: str, lines: list[str]) -> None:
    """没有阵容时也要弹个窗口把话说明白。

    以前这里是直接 return，用户双击后屏幕毫无反应，只能以为"程序坏了"。
    """
    import cv2
    from PIL import Image, ImageDraw

    w, h = 760, 268
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, outline=EDGE, width=2)
    textdraw.text(d, (w / 2, 52), title, 24, TXT, anchor="ma")
    y = 108
    for line in lines:
        textdraw.text(d, (w / 2, y), line, 16, DIM, anchor="ma")
        y += 30
    textdraw.text(d, (w / 2, h - 30), "按任意键关闭", 14, DIM, anchor="ma")

    win = "antigank-notice"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    cv2.imshow(win, np.array(img)[:, :, ::-1].copy())
    try:
        cv2.moveWindow(win, 120, 120)
    except Exception:
        pass
    _to_front(win)
    while True:
        if cv2.waitKeyEx(20) >= 0:
            break
        if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()


def _to_front(win: str = WIN) -> int:
    """顶到最前并**真正**拿到焦点，返回窗口句柄。

    OpenCV 建出来的窗口默认不是前台窗口，此时用户的第一次点击会被系统
    拿去"激活窗口"而不送进鼠标回调，表现就是"点了没反应，得点两下"。
    SetForegroundWindow 单独调用常被拒（调用者不是前台进程），所以先
    AttachThreadInput 挂到前台进程的输入队列上再抢。
    """
    import ctypes
    import time

    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    for _ in range(15):
        hwnd = u32.FindWindowW(None, win)
        if hwnd:
            u32.SetWindowPos(hwnd, -1, 60, 60, 0, 0, 0x0041)   # TOPMOST|SHOW|NOSIZE
            fg = u32.GetForegroundWindow()
            tid_fg = u32.GetWindowThreadProcessId(fg, None) if fg else 0
            tid_me = k32.GetCurrentThreadId()
            attached = False
            if tid_fg and tid_fg != tid_me:
                attached = bool(u32.AttachThreadInput(tid_fg, tid_me, True))
            u32.SetForegroundWindow(hwnd)
            u32.SetActiveWindow(hwnd)
            u32.SetFocus(hwnd)
            if attached:
                u32.AttachThreadInput(tid_fg, tid_me, False)
            return hwnd
        time.sleep(0.05)
    return 0
