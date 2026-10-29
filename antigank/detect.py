"""小地图敌方英雄检测（纯 CV，单帧 <2 ms，不用任何模型）。

原理
----
敌方英雄在小地图上的渲染特征是固定的：
    一个圆形头像，外面套一圈红色描边，内部是英雄原画。
同色系的干扰项有：红方防御塔 / 水晶 / 兵营 / 蘑菇等建筑。

区分手段有两个，都不需要训练：
    1) 形状：红色连通域的外接矩形近似正方形，
       且红色像素占外接矩形的比例在 0.2~0.75（圆环是空心的，实心块接近 1.0）；
       再看圆心区域红色占比，环内部是彩色头像，实心建筑则是纯红。
    2) 时间：建筑永远不动。轨迹连续 4 秒位移接近 0 → 判为静态物，不参与报警。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Found:
    nx: float          # 归一化横坐标
    ny: float          # 归一化纵坐标
    d: float           # 头像直径（占小地图边长比例）
    fill: float        # 红色填充率
    conf: float
    box: tuple[int, int, int, int] = (0, 0, 0, 0)  # 原图 bbox，用于身份识别


def _merge_boxes(boxes: list[list[int]], pad: int) -> list[list[int]]:
    """把断裂的红弧拼回一个整环（抗锯齿会让环断成几段）。"""
    boxes = [list(b) for b in boxes]
    changed = True
    while changed and boxes:
        changed = False
        out: list[list[int]] = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            a = boxes[i]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                b = boxes[j]
                if not (a[2] + pad < b[0] or b[2] + pad < a[0]
                        or a[3] + pad < b[1] or b[3] + pad < a[1]):
                    a = [min(a[0], b[0]), min(a[1], b[1]),
                         max(a[2], b[2]), max(a[3], b[3])]
                    used[j] = True
                    changed = True
            out.append(a)
        boxes = out
    return boxes


class RingDetector:
    def __init__(self, dcfg: dict):
        self.red_lo = tuple(int(v) for v in dcfg["red_lo"])
        self.red_hi = tuple(int(v) for v in dcfg["red_hi"])
        self.red_lo2 = tuple(int(v) for v in dcfg["red_lo2"])
        self.red_hi2 = tuple(int(v) for v in dcfg["red_hi2"])
        self.min_icon_ratio = float(dcfg["min_icon_ratio"])
        self.max_icon_ratio = float(dcfg["max_icon_ratio"])
        self.fill_min = float(dcfg["fill_min"])
        self.fill_max = float(dcfg["fill_max"])
        self.inner_red_max = float(dcfg["inner_red_max"])
        self.circ_min = float(dcfg.get("circ_min", 0.62))
        self._k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    def red_mask(self, bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, self.red_lo, self.red_hi)
        m |= cv2.inRange(hsv, self.red_lo2, self.red_hi2)
        # 只做闭运算把断成几段的弧接起来。
        # 绝对不能做开运算：实测真实小地图上敌方英雄的红环只有 1~2px 宽，
        # 3x3 的 OPEN 会把整条环腐蚀没了，表现为"右边那个敌人完全检不出来"。
        # 去噪交给后面的面积/尺寸/填充率过滤。
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._k_close)
        return m

    def detect(self, bgr: np.ndarray) -> list[Found]:
        h, w = bgr.shape[:2]
        side = float(min(h, w))
        mask = self.red_mask(bgr)

        n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8, cv2.CV_32S)
        raw: list[list[int]] = []
        for i in range(1, n):
            x, y, bw, bh, area = (
                int(stats[i, 0]), int(stats[i, 1]), int(stats[i, 2]),
                int(stats[i, 3]), int(stats[i, 4]),
            )
            if area < 6 or bw < 3 or bh < 3:
                continue
            ar = bw / bh
            if ar < 0.45 or ar > 2.2:
                continue
            raw.append([x, y, x + bw, y + bh])

        pad = max(1, int(side // 120))
        found: list[Found] = []
        for x0, y0, x1, y1 in _merge_boxes(raw, pad):
            bw, bh = x1 - x0, y1 - y0
            d = (bw + bh) / 2.0
            if d < self.min_icon_ratio * side or d > self.max_icon_ratio * side:
                continue
            if not (0.62 <= bw / max(bh, 1) <= 1.62):
                continue

            sub = mask[y0:y1, x0:x1]
            if sub.size == 0:
                continue
            fill = float(sub.mean()) / 255.0
            if not (self.fill_min <= fill <= self.fill_max):
                continue

            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            r = max(2, int(min(bw, bh) * 0.22))
            inner = mask[max(0, cy - r):min(h, cy + r + 1),
                         max(0, cx - r):min(w, cx + r + 1)]
            if inner.size == 0:
                continue
            inner_red = float(inner.mean()) / 255.0

            # 圆形度：英雄的红环外轮廓接近正圆（实测 0.78），
            # 红方塔/水晶是盾形或多边形（实测 0.35~0.55）。
            circ = 0.0
            cnts, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                c0 = max(cnts, key=cv2.contourArea)
                per = cv2.arcLength(c0, True)
                if per > 0:
                    circ = 4.0 * math.pi * cv2.contourArea(c0) / (per * per)

            # 排除实心建筑：必须"内部也是红的"且"轮廓不圆"同时成立才排除。
            # 单看内部红会误杀红色系皮肤的英雄（火焰/红发），单看圆度会误杀
            # 被镜头框或另一个头像切断的英雄（圆度会掉到 0.07）。
            if inner_red > self.inner_red_max and circ < self.circ_min:
                continue

            # 四角判据：正圆的外接矩形四个角一定是空的，方形框的角是实的。
            # 实测方框的圆形度也是 0.785（正方形的理论值），光靠圆度分不开，
            # 但看四个角一眼就能分开。
            csz = max(2, min(bw, bh) // 4)
            if csz * 2 <= min(bw, bh):
                corners = (sub[:csz, :csz], sub[:csz, -csz:],
                           sub[-csz:, :csz], sub[-csz:, -csz:])
                corner_red = sum(float(c.mean()) / 255.0 for c in corners) / 4.0
                if corner_red > 0.40:
                    continue

            # 越接近理想环（fill≈0.45）置信度越高
            conf = 1.0 - min(1.0, abs(fill - 0.45) / 0.45) * 0.5
            found.append(
                Found(
                    nx=(cx + 0.5) / w,
                    ny=(cy + 0.5) / h,
                    d=d / side,
                    fill=fill,
                    conf=float(conf),
                    box=(x0, y0, x1, y1),
                )
            )

        return self._nms(found)

    @staticmethod
    def _nms(items: list[Found]) -> list[Found]:
        items = sorted(items, key=lambda f: -f.conf)
        keep: list[Found] = []
        for it in items:
            if all(abs(it.nx - k.nx) > it.d * 0.8 or abs(it.ny - k.ny) > it.d * 0.8
                   for k in keep):
                keep.append(it)
        return keep


_PALETTE = [(60, 60, 220), (60, 200, 60), (220, 180, 60), (180, 60, 180), (60, 200, 200)]


def annotate(bgr: np.ndarray, found: list[Found], labels: list[str] | None = None) -> np.ndarray:
    """给调试窗口画标注。"""
    h, w = bgr.shape[:2]
    side = min(h, w)
    out = cv2.resize(bgr, (side * 3, side * 3), interpolation=cv2.INTER_NEAREST)
    # 中路参考线
    cv2.line(out, (0, side * 3), (side * 3, 0), (90, 90, 90), 1)
    for i, f in enumerate(found):
        cx, cy = int(f.nx * side * 3), int(f.ny * side * 3)
        rr = max(6, int(f.d * side * 1.5))
        color = _PALETTE[i % len(_PALETTE)]
        cv2.circle(out, (cx, cy), rr, color, 2)
        txt = labels[i] if labels and i < len(labels) else f"{f.conf:.2f}"
        cv2.putText(out, txt, (cx - rr, cy - rr - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return out
