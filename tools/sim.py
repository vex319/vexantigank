"""合成小地图 + 检测精度自测。

不需要启动游戏就能验证检测逻辑：
    python main.py selftest

它会画一张假的召唤师峡谷小地图，放上会动的"敌方英雄"（红环 + 彩色头像）
和一堆红色建筑（实心方块 / 实心菱形），然后跑检测器统计召回率和误报率。
"""

from __future__ import annotations

import random
import time
from pathlib import Path

import cv2
import numpy as np

S = 280  # 小地图边长
CHAMP_DIR = Path(__file__).resolve().parents[1] / "assets" / "champions"
_ICONS: dict = {}


def icon_sprite(key: str, dia: int):
    """把 Data Dragon 方形头像裁成圆形，模拟小地图上的英雄图标。"""
    cached = _ICONS.get((key, dia))
    if cached is not None:
        return cached
    path = CHAMP_DIR / f"{key}.png"
    if not path.exists():
        return None
    img = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = cv2.resize(img, (dia, dia), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[0:dia, 0:dia]
    c = (dia - 1) / 2.0
    m = ((xx - c) ** 2 + (yy - c) ** 2) <= (dia * 0.5) ** 2
    sprite = np.dstack([img, (m * 255).astype(np.uint8)])
    _ICONS[(key, dia)] = sprite
    return sprite


def draw_hero_icon(img, cx: int, cy: int, key: str, dia: int = 20) -> bool:
    spr = icon_sprite(key, dia)
    if spr is None:
        return False
    h = dia // 2
    x0, y0 = cx - h, cy - h
    if x0 < 0 or y0 < 0 or x0 + dia > img.shape[1] or y0 + dia > img.shape[0]:
        return False
    roi = img[y0:y0 + dia, x0:x0 + dia]
    m = spr[:, :, 3] > 0
    roi[m] = spr[:, :, :3][m]
    # 线宽必须跟真机一致：实测真实小地图上红环只有 1~1.5px 宽（约 21px 直径），
    # 画成 2px 会让 fill 到 0.50，跟方框干扰项撞在一起，自测就失去了判别力。
    cv2.circle(img, (cx, cy), h, (30, 30, 235), 1)   # 红色描边
    return True


def _hsv_color(h: int, s: int = 210, v: int = 210) -> tuple[int, int, int]:
    px = np.uint8([[[max(0, min(179, h)), s, v]]])
    b, g, r = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


def background() -> np.ndarray:
    img = np.full((S, S, 3), (38, 36, 30), np.uint8)
    # 河道（副对角线）
    cv2.line(img, (6, 6), (S - 6, S - 6), (78, 82, 74), 26)
    # 中路（主对角线）
    cv2.line(img, (S - 14, 14), (14, S - 14), (96, 92, 72), 20)
    # 上路 / 下路
    cv2.line(img, (14, S - 14), (14, 14), (86, 84, 68), 14)
    cv2.line(img, (14, 14), (S - 14, 14), (86, 84, 68), 14)
    cv2.line(img, (14, S - 14), (S - 14, S - 14), (86, 84, 68), 14)
    cv2.line(img, (S - 14, S - 14), (S - 14, 14), (86, 84, 68), 14)
    # 野区点缀
    rng = random.Random(7)
    for _ in range(60):
        x, y = rng.randrange(10, S - 10), rng.randrange(10, S - 10)
        cv2.circle(img, (x, y), rng.randrange(2, 6), (52, 64, 48), -1)
    return img


def draw_buildings(img: np.ndarray) -> list[tuple[int, int]]:
    """红色建筑：实心方块 + 旋转菱形。两者都该被过滤掉。"""
    pts = []
    for i, (x, y) in enumerate([(24, 24), (24, 60), (60, 24), (S - 24, S - 24),
                                (S - 24, S - 60), (S - 60, S - 24), (24, S - 24),
                                (S - 24, 24)]):
        if i % 2 == 0:
            cv2.rectangle(img, (x - 6, y - 6), (x + 6, y + 6), (36, 40, 210), -1)
        else:
            d = np.array([[0, -7], [7, 0], [0, 7], [-7, 0]]) + np.array([x, y])
            cv2.fillConvexPoly(img, d.astype(np.int32), (30, 40, 205))
        pts.append((x, y))
    return pts


def draw_hero(img: np.ndarray, cx: int, cy: int, seed: int) -> None:
    """红环 + 彩色头像。

    环宽按实测真机来：约 21px 直径、1~1.5px 线宽。画粗了会让红色填充率
    升到 0.4+，反而跟实心建筑撞在一起，自测就分不出真假了。
    """
    rng = random.Random(seed)
    r = 10
    cv2.circle(img, (cx, cy), r, (40, 40, 215), -1)          # 红色底盘
    inner = _hsv_color(rng.randrange(40, 160), 200, 190)      # 非红的内饰
    cv2.circle(img, (cx, cy), r - 1, inner, -1)               # 只留 1px 环
    for _ in range(3):                                        # 头像上的杂色
        ox, oy = rng.randrange(-3, 4), rng.randrange(-3, 4)
        cv2.circle(img, (cx + ox, cy + oy), 2,
                   _hsv_color(rng.randrange(40, 160), 190, 230), -1)
    cv2.circle(img, (cx, cy), r, (30, 30, 240), 1)            # 红色描边


def frame_with_icons(items: list[tuple[int, int, str]]) -> np.ndarray:
    """items = [(x, y, championKey), ...]，用真实头像渲染。"""
    img = background()
    draw_buildings(img)
    for cx, cy, key in items:
        draw_hero_icon(img, cx, cy, key)
    return img


def frame_with_heroes(positions: list[tuple[int, int]], tick: int) -> np.ndarray:
    img = background()
    draw_buildings(img)
    for i, (cx, cy) in enumerate(positions):
        draw_hero(img, cx, cy, seed=i * 31 + tick // 20)
    return img


def _trajectory(start, step, n, tick):
    x, y = start
    return int(x + step[0] * tick), int(y + step[1] * tick)


def selftest(detector, frames: int = 40, verbose: bool = True) -> dict:
    cases = [
        # (起点, 每帧位移)  —— 模拟敌人从不同方向往中路靠
        ((40, 200), (1.2, -1.6)),
        ((230, 60), (-1.4, 1.5)),
        ((60, 60), (1.0, 1.0)),
        ((200, 220), (-1.1, -1.2)),
        ((140, 40), (0.0, 1.4)),
    ]
    hit = miss = 0
    fp_total = 0
    cost: list[float] = []

    for tick in range(frames):
        truth = [_trajectory(s, st, 1, tick) for s, st in cases]
        frame = frame_with_heroes(truth, tick)
        t0 = time.perf_counter()
        found = detector.detect(frame)
        cost.append((time.perf_counter() - t0) * 1000.0)

        matched_truth = set()
        for f in found:
            fx, fy = f.nx * S, f.ny * S
            for i, (tx, ty) in enumerate(truth):
                if i in matched_truth:
                    continue
                if abs(fx - tx) <= 5 and abs(fy - ty) <= 5:
                    matched_truth.add(i)
                    break
        hit += len(matched_truth)
        miss += len(truth) - len(matched_truth)
        fp_total += len(found) - len(matched_truth)

    total = hit + miss
    recall = hit / total if total else 0.0
    precision = hit / (hit + fp_total) if (hit + fp_total) else 0.0
    res = {
        "frames": frames,
        "truth": total,
        "hit": hit,
        "miss": miss,
        "false_positive": fp_total,
        "recall": recall,
        "precision": precision,
        "mean_ms": float(np.mean(cost)),
        "p95_ms": float(np.percentile(cost, 95)),
    }
    if verbose:
        print(f"帧数        {frames}")
        print(f"真值目标    {total}")
        print(f"命中        {hit}    漏检 {miss}    误报 {fp_total}")
        print(f"召回率      {recall:.1%}")
        print(f"准确率      {precision:.1%}")
        print(f"单帧耗时    mean {res['mean_ms']:.2f} ms / p95 {res['p95_ms']:.2f} ms")
    return res
