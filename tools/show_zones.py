# -*- coding: utf-8 -*-
"""画一张野区划分示意图（assets/zones_map.png）。

改了 `geo.BLUE_JUNGLE` 之后跑一次，眼睛过一遍：四块是不是大小相当、是不是
绕地图中心严格对称、有没有谁压到河道或基地上。

    python tools/show_zones.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from antigank.geo import (BARON_PIT, BASE_BLUE, BASE_RED, DRAGON_PIT,  # noqa: E402
                          JUNGLE_POLY, LANE_BOT, LANE_MID, LANE_TOP, zone_key)

S = 640
PAD = 46
FONT = r"C:\Windows\Fonts\msyh.ttc"

BG = (22, 24, 30)
GRID = (255, 255, 255, 18)
LANE = (150, 160, 220)
RIVER = (56, 140, 175)

FILL = {
    "jg_blue_top": (40, 155, 95, 150),
    "jg_blue_bot": (40, 110, 190, 150),
    "jg_red_top": (190, 70, 55, 150),
    "jg_red_bot": (195, 150, 40, 150),
}
NAME = {
    "jg_blue_top": "蓝方上半野区\n(靠大龙)",
    "jg_blue_bot": "蓝方下半野区\n(靠小龙)",
    "jg_red_top": "红方上半野区\n(靠大龙)",
    "jg_red_bot": "红方下半野区\n(靠小龙)",
}


def P(x: float, y: float) -> tuple[float, float]:
    return (PAD + x * S, PAD + y * S)


def font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def main() -> None:
    W = S + 2 * PAD
    im = Image.new("RGB", (W, W), BG)
    d = ImageDraw.Draw(im, "RGBA")

    for i in range(11):
        k = i / 10
        d.line([P(k, 0), P(k, 1)], fill=GRID)
        d.line([P(0, k), P(1, k)], fill=GRID)

    # 河道
    d.line([P(0, 0), P(1, 1)], fill=RIVER, width=16)
    # 分路
    for poly in (LANE_TOP, LANE_BOT, LANE_MID):
        d.line([P(x, y) for x, y in poly], fill=LANE, width=7)

    # 野区
    for key, poly in JUNGLE_POLY.items():
        pts = [P(x, y) for x, y in poly]
        d.polygon(pts, fill=FILL[key], outline=(255, 255, 255, 210))
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        d.multiline_text((cx, cy), NAME[key], font=font(19), fill=(255, 255, 255, 235),
                         anchor="mm", align="center", spacing=6)

    # 龙坑
    for (cx, cy), col, txt in ((BARON_PIT, (190, 120, 255, 230), "大龙"),
                               (DRAGON_PIT, (235, 220, 90, 230), "小龙")):
        d.ellipse([P(cx - 0.065, cy - 0.065), P(cx + 0.065, cy + 0.065)],
                  outline=col, width=3)
        d.text(P(cx, cy - 0.10), txt, font=font(17), fill=col, anchor="mm")

    # 基地
    for (cx, cy), col, txt in ((BASE_BLUE, (90, 150, 255, 230), "蓝方基地"),
                               (BASE_RED, (255, 90, 90, 230), "红方基地")):
        d.ellipse([P(cx - 0.15, cy - 0.15), P(cx + 0.15, cy + 0.15)],
                  outline=(col[0], col[1], col[2], 120), width=2)
        d.text(P(cx, cy), txt, font=font(16), fill=col, anchor="mm")

    # 中心对称标记
    d.ellipse([P(0.5 - 0.006, 0.5 - 0.006), P(0.5 + 0.006, 0.5 + 0.006)],
              fill=(255, 255, 255, 160))

    d.text((W / 2, 16), "野区 = 折线围出来的那一块（四块绕地图中心严格对称）",
           font=font(21), fill=(235, 235, 245), anchor="mt")
    d.text((W / 2, W - 34), "折线之外、基地之外的空地叫「半场深处」，不算野区",
           font=font(17), fill=(170, 175, 190), anchor="mt")

    out = ROOT / "assets" / "zones_map.png"
    im.save(out)
    # 顺手核对几个关键点
    for name, xy in (("蓝上", (0.25, 0.45)), ("蓝下", (0.70, 0.80)),
                     ("红上", (0.49, 0.31)), ("红下", (0.78, 0.42)),
                     ("蓝塔后", (0.30, 0.80))):
        print(f"  {name} {xy} → {zone_key(*xy)}")
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
