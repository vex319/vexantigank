"""自动化验证 calibrate 的两点流程（模拟鼠标点击）。

    python tools/test_calib.py

会短暂接管鼠标：以 (1200,900) 为左上角、(1480,1180) 为右下角点两下，
期望得到 280x280 的 region。跑完把鼠标还原到原位置、删掉临时配置。

注意：测试用 assets/.test_config.json，**不会碰你真正的 config.json**，
所以校准结果不会丢。
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
u32 = ctypes.windll.user32

P1, P2 = (1200, 900), (1480, 1180)
EXPECT = 280


def click(x: int, y: int) -> None:
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.30)
    u32.mouse_event(0x0002, 0, 0, 0, 0)     # LEFTDOWN
    time.sleep(0.08)
    u32.mouse_event(0x0004, 0, 0, 0, 0)     # LEFTUP


def main() -> int:
    cfg = ROOT / "assets" / ".test_config.json"
    if cfg.exists():
        cfg.unlink()

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    orig = POINT()
    u32.GetCursorPos(ctypes.byref(orig))

    proc = subprocess.Popen([sys.executable, "-u", "main.py", "--config", str(cfg), "calibrate"],
                            cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    # 校准程序自己会先倒计时 3 秒（留时间切回游戏），倒计时结束才截屏、才接收点击。
    # 等 4.2 秒是给"3 秒倒计时 + 整屏抓取"留余量；以前等 2.6 秒，点在了倒计时里，
    # 表现就是"点完两下窗口没有自动关闭"。
    time.sleep(4.2)
    if proc.poll() is not None:
        print("失败：校准窗口提前退出")
        print(proc.stdout.read())
        return 1

    click(*P1)
    time.sleep(0.60)
    click(*P2)
    try:
        out = proc.communicate(timeout=12)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        out = proc.communicate()[0]
        print("失败：点完两下窗口没有自动关闭")
        print(out)
        u32.SetCursorPos(orig.x, orig.y)
        return 1
    finally:
        u32.SetCursorPos(orig.x, orig.y)

    print("--- 程序输出 ---")
    print(out.strip())

    if not cfg.exists():
        print("失败：config.json 未生成")
        return 1
    region = json.loads(cfg.read_text(encoding="utf-8"))["capture"]["region"]
    print(f"--- 写入的区域 --- {region}")

    ok = (region["width"] == region["height"] == EXPECT
          and abs(region["left"] - P1[0]) <= 2 and abs(region["top"] - P1[1]) <= 2)
    print("结果:", "通过 ✓" if ok else f"不符，期望 {EXPECT}x{EXPECT} @ {P1}")

    if cfg.exists():
        cfg.unlink()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
