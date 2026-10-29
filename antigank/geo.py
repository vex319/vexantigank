"""地图几何换算与区域划分。

坐标系约定（与英雄联盟小地图一致）
--------------------------------
归一化坐标 nx, ny ∈ [0, 1]，原点在小地图左上角，y 向下。

    蓝方基地 ≈ (0.12, 0.88)      红方基地 ≈ (0.88, 0.12)
    中路      = 主对角线        nx + ny = 1     （两个基地正好在这条线的两端）
    河道      = 副对角线        nx = ny         （大龙在左上的坑，小龙在右下的坑）

地图只分三类
-----------
    分路   上路 / 中路 / 下路   —— 塔就修在分路上，塔所在的这条带就是分路
    河道   副对角线附近的带     —— 大龙坑、小龙坑都在河道上
    野区   被"分路 — 二塔连线 — 中路 — 河道"围住的那一块（见 JUNGLE_POLY）
    深处   野区之外、基地之外的我方/敌方半场腹地（高地墙内、塔后空地）
            —— 不是野区，播报时也不说"在野区"

为什么分路要用"到折线的距离"而不是"离地图边多近"
--------------------------------------------
上路是**折线**：从蓝方基地沿左边向上，再沿上边向右到红方基地；下路同理走下边+右边。
早期版本用 `min(nx, ny) < 0.11` 这种"贴边就算上路"，结果**贴着右边墙的下路二塔附近
（比如 (0.86, 0.29)）会被算成上路/野区**，实战里就是"明明在分路上，却被报成在野区"。
现在改成分别量到三条分路中心线的垂距，取最近的一条：距离 ≤ LANE_HALF 就算在分路上。
三个中心线都经过两个基地，所以靠近基地时几条线的距离会同时变小 —— 基地先单独判掉。
"""

from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)

MAP_UNITS = 14800.0

# 基地（小地图上有一圈平台，比一个点大得多）
BASE_BLUE = (0.12, 0.88)
BASE_RED = (0.88, 0.12)
BASE_RADIUS = 0.15

BARON_PIT = (0.28, 0.28)
DRAGON_PIT = (0.72, 0.72)
PIT_RADIUS = 0.065   # 坑本体直径约 0.07，再留一点靠坑的余量；放大到 0.085 会吞掉旁边野区

# 三条分路的中心线（实测：红色方二塔在 (0.68,0.17)，正落在上边这条线上；
# 蓝色方下路塔在 (0.35,0.86)、(0.62,0.89)，落在下边这条线上）
LANE_TOP = ((0.12, 0.88), (0.12, 0.12), (0.88, 0.12))
LANE_BOT = ((0.12, 0.88), (0.88, 0.88), (0.88, 0.12))
LANE_MID = ((0.12, 0.88), (0.88, 0.12))

# 分路半宽（归一化）。真机实测：塔离中心线 0.02~0.05，野区里的英雄离中心线 ≥0.13，
# 中间这条空档很宽，0.062 落在中间，不会误判。
LANE_HALF = 0.062

# 河道半宽
RIVER_HALF = 0.045

# ---------------------------------------------------------------- 野区边界
# 野区**不是**"除去分路和河道之后剩下的全部" —— 那样连二塔后面、高地墙外的
# 空地都会被说成野区，实战里敌人站在塔后补刀也会报"在野区"。真正算野区的只有
# 被折线围住的这一块：
#
#      上路 ─────────┬────────────────
#                    │   野   区     │
#                    │               │    ← 里侧是二塔连线（不是基地）
#      二塔连线 ─────┴─ 中路 ────────→ 河道
#
# 只写蓝方半场（河道左下，nx < ny）的两块，红方两块由**绕地图中心转 180°** 得到。
# 地图本身是中心对称的，这样两边形状永远一致，不会一边胖一边瘦。
# 注意：转 180° 会把"靠大龙侧"换成"靠小龙侧"（大龙在左上、小龙在右下），
# 所以红方上半是蓝方下半翻过来的那块 —— 不是抄错。
BLUE_JUNGLE = {
    # 靠大龙侧：蓝方上路一塔 → 沿左墙下来 → 过二塔连线 → 顺中路 → 沿河道回去
    "top": ((0.128, 0.235), (0.122, 0.655), (0.262, 0.700), (0.430, 0.530)),
    # 靠小龙侧：河道口 → 中路一塔 → 下路二塔 → 下路一塔 → 河道口
    "bot": ((0.500, 0.600), (0.345, 0.775), (0.385, 0.935), (0.715, 0.935),
            (0.775, 0.885), (0.620, 0.700)),
}


def _flip180(poly):
    return tuple((1.0 - x, 1.0 - y) for x, y in poly)


JUNGLE_POLY = {
    "jg_blue_top": BLUE_JUNGLE["top"],
    "jg_blue_bot": BLUE_JUNGLE["bot"],
    "jg_red_top": _flip180(BLUE_JUNGLE["bot"]),
    "jg_red_bot": _flip180(BLUE_JUNGLE["top"]),
}

# 区域 key 与中文标签；蓝/红会在拿到对局信息后改称 我方/敌方
ZONE_LABELS = {
    "mid": "中路",
    "top": "上路",
    "bot": "下路",
    "river": "河道",
    "baron": "大龙坑（先锋）",
    "dragon": "小龙坑",
    "base_blue": "蓝方基地",
    "base_red": "红方基地",
    "jg_blue_top": "蓝方上半野区",
    "jg_blue_bot": "蓝方下半野区",
    "jg_red_top": "红方上半野区",
    "jg_red_bot": "红方下半野区",
    "deep_blue": "蓝方半场深处",
    "deep_red": "红方半场深处",
}

# 分路不值得播报：玩家要的是"谁离开了分路、进了野区/河道"
LANE_KEYS = ("top", "mid", "bot")

ZONE_KEYS = tuple(ZONE_LABELS)


def norm_dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def mid_perp_dist(nx: float, ny: float) -> float:
    return abs(nx + ny - 1.0) / SQRT2


def river_dist(nx: float, ny: float) -> float:
    return abs(nx - ny) / SQRT2


def mid_nearest_point(nx: float, ny: float) -> tuple[float, float]:
    t = (nx - ny + 1.0) / 2.0
    t = min(max(t, 0.07), 0.93)
    return t, 1.0 - t


def _seg_dist(px: float, py: float, a: tuple[float, float],
              b: tuple[float, float]) -> float:
    """点到线段的距离。"""
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    l2 = vx * vx + vy * vy
    t = 0.0 if l2 <= 1e-12 else max(0.0, min(1.0, (wx * vx + wy * vy) / l2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _poly_dist(px: float, py: float, poly: tuple[tuple[float, float], ...]) -> float:
    return min(_seg_dist(px, py, poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def lane_dists(nx: float, ny: float) -> tuple[float, float, float]:
    """到 上路 / 下路 / 中路 中心线的垂距。"""
    return (_poly_dist(nx, ny, LANE_TOP),
            _poly_dist(nx, ny, LANE_BOT),
            _poly_dist(nx, ny, LANE_MID))


def point_in_poly(nx: float, ny: float,
                  poly: tuple[tuple[float, float], ...]) -> bool:
    """射线法：点任意引一条射线，和边的交点个数为奇数就在内部。

    多边形可以凹（野区靠河道那侧是折的），所以不能用凸包判定。
    """
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > ny) != (y2 > ny):
            # 交点在边的哪一侧；用乘除避免除零
            x_cross = x1 + (ny - y1) * (x2 - x1) / (y2 - y1)
            if nx < x_cross:
                inside = not inside
    return inside


def nearest_lane(nx: float, ny: float) -> tuple[str, float]:
    d_top, d_bot, d_mid = lane_dists(nx, ny)
    name, d = min((("top", d_top), ("bot", d_bot), ("mid", d_mid)), key=lambda p: p[1])
    return name, d


def time_to_arrive(
    nx: float,
    ny: float,
    *,
    ref: tuple[float, float] | None = None,
    map_units: float = MAP_UNITS,
    move_speed: float = 385.0,
    detour: float = 1.30,
) -> float:
    """从 (nx, ny) 走到参考点需要几秒。ref 为 None 时取中路上的最近投影。"""
    rx, ry = ref if ref is not None else mid_nearest_point(nx, ny)
    d_units = norm_dist(nx, ny, rx, ry) * map_units
    return d_units * detour / max(move_speed, 1.0)


def direction_word(dx: float, dy: float) -> str:
    if abs(dx) < 0.012 and abs(dy) < 0.012:
        return "附近"
    ang = math.degrees(math.atan2(-dy, dx)) % 360.0
    names = ("右", "右上", "上", "左上", "左", "左下", "下", "右下")
    return names[int((ang + 22.5) // 45) % 8]


def zone_key(nx: float, ny: float) -> str:
    """把归一化坐标映射到区域 key：分路 / 河道 / 野区 / 半场深处。"""
    if norm_dist(nx, ny, *BASE_BLUE) < BASE_RADIUS:
        return "base_blue"
    if norm_dist(nx, ny, *BASE_RED) < BASE_RADIUS:
        return "base_red"
    if norm_dist(nx, ny, *BARON_PIT) < PIT_RADIUS:
        return "baron"
    if norm_dist(nx, ny, *DRAGON_PIT) < PIT_RADIUS:
        return "dragon"

    # 分路优先：塔就在分路上，站在塔边就是"在分路"
    lane, d_lane = min(zip(("top", "bot", "mid"), lane_dists(nx, ny)),
                       key=lambda p: p[1])
    if d_lane <= LANE_HALF:
        return lane

    if river_dist(nx, ny) < RIVER_HALF:
        return "river"

    # 野区：河道把地图切成蓝/红两个半区，每半区再按"靠大龙还是靠小龙"
    # 分上半/下半（见 jungle_half），最后还得落在这一块的折线里面。
    side = "blue" if nx < ny else "red"
    key = f"jg_{side}_{jungle_half(nx, ny)}"
    if point_in_poly(nx, ny, JUNGLE_POLY[key]):
        return key
    # 折线外面的空地是半场腹地（高地墙内、二塔后面），**不算野区**
    return f"deep_{side}"


def jungle_half(nx: float, ny: float) -> str:
    """这块野区算"上半"还是"下半"。

    判据只有一个：**离大龙近还是离小龙近**。
    沿主对角线（中路）看，`nx + ny < 1` 的那一半在大龙这头，
    `> 1` 的那一半在小龙那头：

        大龙 (0.28,0.28) ─────────────────── 小龙 (0.72,0.72)
        蓝方上半野区    半区中心 nx+ny=1     蓝方下半野区

    为什么不用"离上路近还是离下路近"：这个说法在四条边上有歧义 ——
    左边墙既是上路也是红方的上半野区入口，判起来要额外绕弯。
    "靠大龙/靠小龙"是玩家嘴里"上半野区/下半野区"的真正含义，
    而且对蓝红双方都自动成立，不用按我方在哪一侧再翻一次。
    """
    return "top" if (nx + ny) < 1.0 else "bot"


# 我方在河道哪一侧。真实小地图是固定的：蓝方基地在左下（河道下方），
# 红方基地在右上（河道上方）。所以蓝方=下侧、红方=上侧。
# 注意：网上有些示意图把蓝方画在上边，那是转过 180° 的图，别照抄。
UPPER_SIDE = {"ORDER": "lower", "CHAOS": "upper"}


def my_side_of_map(my_side: str | None) -> str:
    """我方位于地图的"上侧"还是"下侧"（以河道为界）。"""
    return UPPER_SIDE.get(my_side or "", "")


def describe_sides(my_side: str | None) -> str:
    """启动日志用的一行说明，方便和实际小地图对一眼。"""
    if my_side not in ("ORDER", "CHAOS"):
        return "阵营：未知（还没连上对局接口）"
    me = "蓝方" if my_side == "ORDER" else "红方"
    foe = "红方" if my_side == "ORDER" else "蓝方"
    where = "下侧" if my_side_of_map(my_side) == "lower" else "上侧"
    other = "上侧" if where == "下侧" else "下侧"
    return (f"阵营：我方 {me}（位于地图{where}）· 敌方 {foe}（位于{other}）"
            f"　—　上半野区 = 靠大龙那侧，下半野区 = 靠小龙那侧")



def is_lane(key: str) -> bool:
    return key in LANE_KEYS


def zone_label(key: str, my_side: str | None = None) -> str:
    """区域中文名。my_side = 'ORDER'/'CHAOS' 时把蓝/红换成本方视角的说法。"""
    label = ZONE_LABELS.get(key, key)
    if my_side not in ("ORDER", "CHAOS"):
        return label
    mine, theirs = ("蓝方", "红方") if my_side == "ORDER" else ("红方", "蓝方")
    return label.replace(mine, "我方").replace(theirs, "敌方")


def zone_say(key: str, my_side: str | None = None) -> str:
    """区域怎么说出口。

    和 zone_label 的区别只有一个：念的时候不要"（先锋）"这三个字 ——
    语音里"大龙坑括号先锋"很难听，TTS 处理括号也不稳定。
    """
    return zone_label(key, my_side).replace("（先锋）", "")


def is_my_half(key: str, my_side: str | None) -> bool:
    """这块野区是不是我方半区（用来给播报排优先级）。"""
    if not key.startswith("jg_") or my_side not in ("ORDER", "CHAOS"):
        return False
    mine = "blue" if my_side == "ORDER" else "red"
    return key.split("_")[1] == mine


def lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k
