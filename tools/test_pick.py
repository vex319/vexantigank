"""自动化验证 pick 的交互链路（模拟点击两张卡片 + Enter）。

    python tools/test_pick.py

无对局时 pick 会列出全部英雄，默认全选。这里点掉第一个和第二个卡片，
按 Enter，期望 config 里 notify 恰好少了这两个英雄。
测试用 assets/.test_config.json，不会碰真正的 config.json。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = "antigank-pick"
u32 = ctypes.windll.user32
sys.path.insert(0, str(ROOT))

# 前两张卡片中心（picker: MARGIN=40, TOP=112, CW=200, CH=236, GAP=28）
CARDS = [(140, 230), (368, 230)]
DEMO = ["Ahri", "LeeSin", "Darius", "Yasuo", "Malphite"]


def click(x: int, y: int) -> None:
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.70)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0004, 0, 0, 0, 0)


def press(vk: int, hwnd: int, scan: int = 0x1C) -> None:
    """直接把按键消息投递进窗口队列。

    子进程调用 SetForegroundWindow 常被系统拒绝，keybd_event 会打到终端上，
    所以测试走 PostMessage —— 窗口线程的 PeekMessage 一样能收到。
    """
    u32.PostMessageW(hwnd, 0x0100, vk, 0x00000001 | (scan << 16))
    time.sleep(0.05)
    u32.PostMessageW(hwnd, 0x0101, vk, 0xC0000001 | (scan << 16))


def main() -> int:
    from antigank.champions import ChampionBook

    book = ChampionBook(ROOT / "assets" / "champions")
    pool = [k for k in DEMO if k in book.index]
    expect_drop = {pool[0], pool[1]}

    cfg = ROOT / "assets" / ".test_config.json"
    if cfg.exists():
        cfg.unlink()

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    orig = POINT()
    u32.GetCursorPos(ctypes.byref(orig))

    proc = subprocess.Popen([sys.executable, "-u", "main.py", "--config", str(cfg), "pick", "--demo"],
                            cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(3.0)
        if proc.poll() is not None:
            print("失败：窗口提前退出")
            print(proc.stdout.read())
            return 1

        hwnd = u32.FindWindowW(None, WIN)
        if not hwnd:
            print("失败：找不到窗口")
            proc.kill()
            return 1
        pt = wintypes.POINT(0, 0)
        u32.ClientToScreen(hwnd, ctypes.byref(pt))
        print(f"窗口客户区原点 ({pt.x},{pt.y})")

        for cx, cy in CARDS:
            click(pt.x + cx, pt.y + cy)
            time.sleep(0.45)
        press(0x0D, hwnd)                 # Enter

        try:
            out = proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0]
            print("失败：按 Enter 后窗口没有关闭（键盘事件没送到？）")
            print(out)
            return 1
    finally:
        u32.SetCursorPos(orig.x, orig.y)

    print("--- 程序输出 ---")
    print(out.strip())

    if not cfg.exists():
        print("失败：config.json 未生成")
        return 1
    notify = json.loads(cfg.read_text(encoding="utf-8"))["champions"]["notify"]
    print(f"--- notify = {notify}（应为 {[k for k in pool if k not in expect_drop]}）")
    ok = set(notify) == {k for k in pool if k not in expect_drop}
    print("结果:", "通过 ✓" if ok else f"不符，未排除 {expect_drop & set(notify)}")

    if cfg.exists():
        cfg.unlink()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
