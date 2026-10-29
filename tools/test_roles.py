"""自动化验证「分配分路」窗口的交互链路（模拟点角色按钮 + Enter）。

    python tools/test_roles.py

演示阵容是 Ahri/LeeSin/Darius/Yasuo/Malphite，接口位置对应 中/野/上/下/辅。
这里把九尾妖狐（第 1 张卡）点到"辅"，期望它和熔岩巨兽**互换**：
九尾妖狐=辅助、熔岩巨兽=中路，其余三人不动。

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
WIN = "antigank-roles"
u32 = ctypes.windll.user32
sys.path.insert(0, str(ROOT))

# roles_ui: MARGIN=40, TOP=128, CW=200, CH=252, GAP=28
# 卡片 x0 = (W - cards_w)//2 + i*(CW+GAP)，W=1172 时 left=40
# 按钮行：x0+19 起，每个宽 30 间隔 3，y0+186 起，高 34
CARD_LEFT = 40
CARD_TOP = 128
BTN_Y = CARD_TOP + 186 + 17

DEMO = ["Ahri", "LeeSin", "Darius", "Yasuo", "Malphite"]
EXPECT = {"Ahri": "support", "LeeSin": "jungle", "Darius": "top",
          "Yasuo": "adc", "Malphite": "mid"}


def btn_center(card_idx: int, role_idx: int) -> tuple[int, int]:
    x0 = CARD_LEFT + card_idx * 228
    bx = x0 + 19 + role_idx * 33
    return bx + 15, BTN_Y


def click(x: int, y: int) -> None:
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.70)
    u32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.mouse_event(0x0004, 0, 0, 0, 0)


def press(vk: int, hwnd: int, scan: int = 0x1C) -> None:
    u32.PostMessageW(hwnd, 0x0100, vk, 0x00000001 | (scan << 16))
    time.sleep(0.05)
    u32.PostMessageW(hwnd, 0x0101, vk, 0xC0000001 | (scan << 16))


def main() -> int:
    cfg = ROOT / "assets" / ".test_config.json"
    if cfg.exists():
        cfg.unlink()

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    orig = POINT()
    u32.GetCursorPos(ctypes.byref(orig))

    proc = subprocess.Popen([sys.executable, "-u", "main.py", "--config", str(cfg), "roles", "--demo"],
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

        # 第 1 张卡（九尾妖狐）→ 点第 5 个按钮"辅"
        cx, cy = btn_center(0, 4)
        print(f"点击 ({cx},{cy}) = 第 1 张卡 · 辅")
        click(pt.x + cx, pt.y + cy)
        time.sleep(0.5)
        press(0x0D, hwnd)

        try:
            out = proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            out = proc.communicate()[0]
            print("失败：按 Enter 后窗口没有关闭")
            print(out)
            return 1
    finally:
        u32.SetCursorPos(orig.x, orig.y)

    print("--- 程序输出 ---")
    print(out.strip())

    if not cfg.exists():
        print("失败：config.json 未生成")
        return 1
    got = json.loads(cfg.read_text(encoding="utf-8")).get("roles", {}).get("assign", {})
    print(f"--- roles.assign = {got}")
    print(f"--- 期望          = {EXPECT}")
    ok = got == EXPECT
    print("结果:", "通过 ✓" if ok else "不符")
    if not ok:
        for k in EXPECT:
            if got.get(k) != EXPECT[k]:
                print(f"    {k}: 期望 {EXPECT[k]}，实际 {got.get(k)}")

    if cfg.exists():
        cfg.unlink()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
