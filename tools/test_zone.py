"""区域划分回归：坐标 → 分路 / 河道 / 龙坑 / 野区 / 半场深处。

测试点全部来自**真机实测地标**，不是拍脑袋的坐标：
  - 冰鸟 (0.862, 0.293)：贴在小地图右边缘的红塔旁 → 下路
  - 红方上路二塔 (0.680, 0.170)、蓝方下路塔 (0.350, 0.860)/(0.620, 0.890) → 各自的分路
  - 黑默丁格 (0.493, 0.313)：离三条分路都远 → 红方上半野区
  - 两个龙坑、河道、两个基地

野区边界是**折线**（见 geo.BLUE_JUNGLE），不是"除去分路剩下的全部"：
二塔后面、高地墙外那圈空地叫"半场深处"，不许叫野区。这一条有专门的用例。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from antigank.geo import (JUNGLE_POLY, _seg_dist, describe_sides, is_lane,  # noqa: E402
                          zone_key, zone_label, zone_say)

CASES = [
    # (nx, ny, 期望, 说明)
    ((0.862, 0.293), "bot", "冰鸟贴着右边缘红塔 → 下路（以前误报成野区）"),
    ((0.680, 0.170), "top", "红方上路二塔 → 上路"),
    ((0.350, 0.860), "bot", "蓝方下路塔 → 下路"),
    ((0.620, 0.890), "bot", "蓝方下路塔 → 下路"),
    ((0.500, 0.500), "mid", "地图正中 → 中路"),
    ((0.750, 0.250), "mid", "正落在主对角线上 → 中路"),
    ((0.493, 0.313), "jg_red_top", "黑默丁格 → 红方上半野区"),
    ((0.620, 0.220), "jg_red_top", "红方上半野区深处"),
    ((0.780, 0.420), "jg_red_bot", "红方下半野区"),
    ((0.250, 0.450), "jg_blue_top", "蓝方上半野区"),
    ((0.700, 0.800), "jg_blue_bot", "蓝方下半野区"),
    # 野区收窄：以前"除去分路和河道剩下的全算野区"，二塔后面、高地墙外也被说成野区
    ((0.300, 0.800), "deep_blue", "蓝方下路二塔后 → 半场深处，不是野区"),
    ((0.700, 0.200), "deep_red", "红方上路二塔后 → 半场深处，不是野区"),
    ((0.220, 0.220), "river", "大龙坑上方的河道"),
    ((0.280, 0.280), "baron", "大龙/先锋坑"),
    ((0.310, 0.300), "baron", "大龙坑边缘"),
    ((0.720, 0.720), "dragon", "小龙坑"),
    ((0.450, 0.450), "river", "河道中央"),
    ((0.200, 0.200), "river", "河道偏大龙侧"),
    ((0.800, 0.800), "river", "河道偏小龙侧"),
    ((0.120, 0.880), "base_blue", "蓝方基地"),
    ((0.880, 0.120), "base_red", "红方基地"),
    ((0.200, 0.800), "base_blue", "蓝方基地平台边缘"),
]

# 绕地图中心转 180°：蓝红互换，上路↔下路，大龙坑↔小龙坑
FLIP180 = {
    "jg_blue_top": "jg_red_bot", "jg_blue_bot": "jg_red_top",
    "jg_red_top": "jg_blue_bot", "jg_red_bot": "jg_blue_top",
    "deep_blue": "deep_red", "deep_red": "deep_blue",
    "base_blue": "base_red", "base_red": "base_blue",
    "top": "bot", "bot": "top", "mid": "mid",
    "river": "river", "baron": "dragon", "dragon": "baron",
}


def main() -> None:
    bad = 0
    checked = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal bad, checked
        checked += 1
        if not ok:
            bad += 1
        print(("✓ " if ok else "✗ ") + msg)

    for (nx, ny), exp, why in CASES:
        got = zone_key(nx, ny)
        ok = got == exp
        bad += 0 if ok else 1
        checked += 1
        print(f"{'✓' if ok else '✗'} ({nx:.3f},{ny:.3f})  {got:12s} "
              f"期望 {exp:12s} {why}")

    print("\n播报过滤：分路应当被跳过")
    for k in ("top", "mid", "bot"):
        skip = is_lane(k)
        check(skip, f"{k:5s} is_lane={skip}")
    for k in ("river", "baron", "dragon", "jg_blue_top", "jg_red_bot",
              "deep_blue", "deep_red"):
        skip = is_lane(k)
        check(not skip, f"{k:12s} is_lane={skip}（应当播报）")

    # ---- 野区的"上半/下半"到底靠哪边 ----
    # 用户给的规则：上半野区 = 靠大龙那侧，下半野区 = 靠小龙那侧。
    # 这条对蓝红双方都成立，不用管我方在地图上半边还是下半边。
    print("\n野区上下：上半靠大龙，下半靠小龙")
    import math
    from collections import defaultdict

    acc = defaultdict(lambda: [0, 0.0, 0.0])
    N = 240
    for iy in range(N):
        for ix in range(N):
            nx, ny = (ix + 0.5) / N, (iy + 0.5) / N
            k = zone_key(nx, ny)
            if k.startswith("jg_"):
                a = acc[k]
                a[0] += 1
                a[1] += nx
                a[2] += ny
    for k in ("jg_blue_top", "jg_blue_bot", "jg_red_top", "jg_red_bot"):
        n, sx, sy = acc[k]
        cx, cy = sx / n, sy / n
        d_baron = math.hypot(cx - 0.28, cy - 0.28)
        d_dragon = math.hypot(cx - 0.72, cy - 0.72)
        want_baron = k.endswith("_top")
        ok = (d_baron < d_dragon) == want_baron
        near = "大龙" if d_baron < d_dragon else "小龙"
        check(ok, f"{k:14s} 质心 ({cx:.2f},{cy:.2f}) 离{near}近 "
                  f"（{k[-3:]} = {'靠大龙' if want_baron else '靠小龙'}）")

    # ---- 野区面积：太大就是老毛病回来了 ----
    print("\n野区占地：每一块都不该超过地图的 8%（以前一刀切是 7%~9%）")
    area = defaultdict(int)
    M = 400
    for iy in range(M):
        for ix in range(M):
            area[zone_key((ix + 0.5) / M, (iy + 0.5) / M)] += 1
    for k in ("jg_blue_top", "jg_blue_bot", "jg_red_top", "jg_red_bot"):
        pct = area[k] / (M * M) * 100
        check(pct <= 8.0, f"{k:14s} {pct:4.1f}%")

    # ---- 中心对称：用户要求"双方野区绕中心转 180° 必须一样" ----
    # 手绘线不精准没关系，代码里的形状必须严格对称，不能一边胖一边瘦。
    # 采样点正好压在折线边上时，算内算外都说得过去（浮点误差会让两边判反），
    # 这类点跳过不判 —— 它们只占万分之几。
    print("\n中心对称：整张图绕中心转 180° 判出来的区域必须一一对应")

    def on_jungle_edge(x: float, y: float, tol: float = 2e-3) -> bool:
        for poly in JUNGLE_POLY.values():
            for i in range(len(poly)):
                if _seg_dist(x, y, poly[i], poly[(i + 1) % len(poly)]) < tol:
                    return True
        return False

    S = 200
    mismatch = []
    for iy in range(S):
        for ix in range(S):
            nx, ny = (ix + 0.5) / S, (iy + 0.5) / S
            a, b = zone_key(nx, ny), zone_key(1 - nx, 1 - ny)
            if FLIP180.get(a) != b and not on_jungle_edge(nx, ny):
                mismatch.append((round(nx, 3), round(ny, 3), a, b))
    check(not mismatch,
          f"{S * S} 个采样点，{len(mismatch)} 处不对称（压线点已跳过）")
    for m in mismatch[:5]:
        print("    ", m)

    print("\n野区归属与说法（蓝方视角 / 红方视角）")
    for k in ("jg_blue_top", "jg_blue_bot", "jg_red_top", "jg_red_bot",
              "deep_blue", "deep_red"):
        a, b = zone_say(k, "ORDER"), zone_say(k, "CHAOS")
        print(f"  {k:14s} {a:12s} | {b}")
    pairs = [("jg_blue_top", "ORDER", "我方上半野区"),
             ("jg_blue_bot", "ORDER", "我方下半野区"),
             ("jg_red_top", "ORDER", "敌方上半野区"),
             ("jg_red_bot", "ORDER", "敌方下半野区"),
             ("jg_red_top", "CHAOS", "我方上半野区"),
             ("jg_red_bot", "CHAOS", "我方下半野区"),
             ("jg_blue_top", "CHAOS", "敌方上半野区"),
             ("deep_blue", "ORDER", "我方半场深处"),
             ("deep_red", "ORDER", "敌方半场深处")]
    for k, side, want in pairs:
        got = zone_say(k, side)
        check(got == want, f"{k} @{side} 说成「{got}」"
                           + ("" if got == want else f"，期望「{want}」"))
    print("  （不打印通过项，只有 ✗ 会出现在上面）")

    print("\n阵营方位：" + describe_sides("ORDER"))
    print("          " + describe_sides("CHAOS"))

    print("\n标签示例：")
    for k in ("top", "mid", "bot", "river", "baron", "dragon",
              "jg_blue_top", "jg_red_bot", "deep_blue"):
        print(f"  {k:12s} 蓝方视角 {zone_label(k, 'ORDER'):12s} "
              f"红方视角 {zone_label(k, 'CHAOS')}")

    print(f"\n{checked - bad}/{checked} 通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
