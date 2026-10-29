"""真实小地图回归测试。

用一张真机截图（1920x1200 下 278x278 的小地图）当基准，验证：
  * 两个敌方英雄都检出（红环只有 1~2px 宽，这是合成数据测不出来的）
  * 8 个红方塔/水晶一个都不误报

这张图的价值在于它是真实的：合成数据里我画的环是 2px 且圆更规整，
当年就是因为这个自测全绿、真机全漏。改任何 detect 参数后先跑这个。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from antigank.config import load
from antigank.detect import RingDetector

FIXTURE = ROOT / "assets" / "fixtures" / "minimap_real.png"

# 人工在这张图上标注的真值（像素坐标，原图 278x278）
HEROES = [(137, 89), (239, 81)]      # 红圈的敌方英雄
TOWERS = [(191, 27), (159, 53), (152, 44)]   # 红方塔，绝不能出现在结果里
TOL = 7


def main() -> int:
    if not FIXTURE.exists():
        print(f"缺少基准图 {FIXTURE}")
        print("跑一次 main.py run --debug 抓一帧存到这里即可")
        return 1

    img = cv2.imread(str(FIXTURE))
    cfg = load(str(ROOT / "config.json"))
    det = RingDetector(cfg["detect"])
    side = img.shape[1]
    found = det.detect(img)
    pts = [(int(f.nx * side), int(f.ny * side)) for f in found]

    print(f"基准图 {img.shape[1]}x{img.shape[0]}  检出 {len(found)} 个: {pts}")

    ok = True
    for hx, hy in HEROES:
        hit = any(abs(px - hx) <= TOL and abs(py - hy) <= TOL for px, py in pts)
        print(f"  英雄 ({hx:3d},{hy:3d})  {'检出 ✓' if hit else '漏检 ✗'}")
        ok &= hit

    for tx, ty in TOWERS:
        bad = any(abs(px - tx) <= TOL and abs(py - ty) <= TOL for px, py in pts)
        print(f"  红方塔 ({tx:3d},{ty:3d})  {'误报 ✗' if bad else '已排除 ✓'}")
        ok &= not bad

    if len(pts) > len(HEROES):
        print(f"  ! 还有 {len(pts) - len(HEROES)} 个多余检出: "
              f"{[p for p in pts if not any(abs(p[0]-h[0])<=TOL and abs(p[1]-h[1])<=TOL for h in HEROES)]}")
        ok = False

    print("\n" + ("通过" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
