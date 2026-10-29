"""敌方五个英雄的分路角色。

方案.txt 的第一条：为敌方所有英雄分配分路，功能与"选小地图""选播报英雄"并列。

五个角色就是客户端里的五个位置：

    top      上路
    jungle   打野
    mid      中路
    adc      下路
    support  辅助

角色决定了"什么行为值得播报"：

    top / adc / support   盯着自己那条分路的进出
    jungle / support      盯着野区、河道、龙坑
    mid                   只盯着"连续 10 秒不见人"

自动填法：Live Client API 的 players[].position 在排位/匹配里是准的
（TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY），人机和部分模式会给空字符串，
这时按剩下没人要的角色顺序补齐，用户在「分配分路」窗口里改一下即可。
"""

from __future__ import annotations

ROLE_KEYS = ("top", "jungle", "mid", "adc", "support")

ROLE_NAME = {
    "top": "上路",
    "jungle": "打野",
    "mid": "中路",
    "adc": "下路",
    "support": "辅助",
}

# 只需要一个字的按钮（角色分配窗口里 5 个按钮挤在一张卡下面）
ROLE_SHORT = {"top": "上", "jungle": "野", "mid": "中", "adc": "下", "support": "辅"}

# 这个角色盯哪条分路。打野没有分路，中路单独用"消失"逻辑，所以不在表里。
LANE_OF = {"top": "top", "adc": "bot", "support": "bot"}

# 这个角色要盯野区 / 河道 / 资源点
WATCH_JUNGLE = {"jungle", "support"}

# Live Client API 的 position 字段 → 角色
POS2ROLE = {
    "top": "top",
    "jungle": "jungle",
    "middle": "mid",
    "mid": "mid",
    "bottom": "adc",
    "bot": "adc",
    "adc": "adc",
    "utility": "support",
    "support": "support",
}


def normalize_role(raw: str | None) -> str | None:
    return POS2ROLE.get((raw or "").strip().lower())


def auto_assign(roster: list[str], positions: dict[str, str] | None = None) -> dict[str, str]:
    """给 5 个敌人排角色。positions = {英雄 key: 接口给的 position}。

    接口给的角色优先；给不出来的（人机常见）按 top → jungle → mid → adc → support
    的顺序捡剩下的空位。返回 {英雄 key: 角色}。
    """
    out: dict[str, str] = {}
    used: set[str] = set()
    positions = positions or {}

    for key in roster:
        role = normalize_role(positions.get(key))
        if role and role not in used:
            out[key] = role
            used.add(role)

    for key in roster:
        if key in out:
            continue
        for role in ROLE_KEYS:
            if role not in used:
                out[key] = role
                used.add(role)
                break

    return out


def merge_assign(roster: list[str], saved: dict[str, str] | None,
                 positions: dict[str, str] | None = None) -> dict[str, str]:
    """自动分配 + 用户手动改的那几条。

    saved 可能过期（上一局的英雄这局没出现），只在"英雄还在本局阵容里"时才采用。
    关键是用 swap 而不是覆盖：用户把辅助挪到上路，上路那位应该换到辅助去，
    而不是被挤得连环错位（之前就是 thresh→top 之后另外四个人全错了一格）。
    """
    out = auto_assign(roster, positions)
    for key, role in (saved or {}).items():
        if key in out and role in ROLE_KEYS:
            out = swap(out, key, role)
    return out


def swap(assign: dict[str, str], key: str, role: str) -> dict[str, str]:
    """把 key 指派到 role；原本占着这个角色的英雄跟它换位置。

    只换不抢 —— 5 个角色必须各有一个英雄，否则会出现两个"辅助"、
    而没有"上路"，行为规则就乱了。
    """
    out = dict(assign)
    old_role = out.get(key)
    if old_role == role:
        return out
    holder = next((k for k, r in out.items() if r == role and k != key), None)
    out[key] = role
    if holder is not None and old_role:
        out[holder] = old_role
    return out


def describe(assign: dict[str, str], namer=lambda k: k) -> str:
    """一行摘要，按角色顺序：上路：盖伦　打野：盲僧　…"""
    order = {r: i for i, r in enumerate(ROLE_KEYS)}
    items = sorted(assign.items(), key=lambda kv: order.get(kv[1], 9))
    return "　".join(f"{ROLE_NAME.get(r, r)}：{namer(k)}" for k, r in items)
