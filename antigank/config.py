"""配置读写。config.json 缺失时用内置默认值补齐。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULTS = {
    "capture": {
        # gdi = mss(BitBlt)，dxgi = dxcam/bettercam
        "backend": "gdi",
        "fps": 1,          # 每秒检测一次（1 Hz 下敌人最多移动 7px，够跟踪）
        # 小地图在屏幕上的像素矩形，由 calibrate 写入
        "region": {"left": 0, "top": 0, "width": 280, "height": 280},
    },
    "detect": {
        # 敌方英雄在小地图上是"红色描边 + 彩色头像"。两组红色色相区间。
        "red_lo": [0, 150, 90],
        "red_hi": [12, 255, 255],
        "red_lo2": [168, 150, 90],
        "red_hi2": [180, 255, 255],
        # 头像直径占小地图边长的比例（1920x1200 下约 0.07）
        "min_icon_ratio": 0.040,
        "max_icon_ratio": 0.115,
        # 红色像素占外接矩形的比例。实测真机：英雄红环只有 1~2px 宽，
        # 27x27 的框里 fill 只有 0.18 左右（模拟图是 0.45，差得远）
        "fill_min": 0.10,
        "fill_max": 0.42,
        # 内部红色占比：实心塔/水晶 0.6~1.0，英雄头像 0.0~0.2
        "inner_red_max": 0.45,
        # 圆形度：英雄红环 ≈0.78，塔的盾形轮廓 ≈0.35~0.55
        "circ_min": 0.62,
    },
    "track": {
        "gate": 0.055,        # 相邻两帧匹配的最大位移（归一化）
        "lost_timeout": 4.0,  # 轨迹保留时长，用于"消失在迷雾"判断
        "confirm_hits": 1,    # 1 Hz 下每帧间隔就是 1 秒，等 2 帧等于 2 秒延迟
        "static_eps": 0.010,  # 视为"没动"的阈值
        "static_hold": 4.0,   # 静止超过这个秒数判定为建筑，不报
    },
    "threat": {
        "map_units": 14800.0,
        "move_speed": 385.0,  # 敌方移速估算（二速鞋 + 天赋）
        "detour": 1.30,       # 绕墙系数
        "alert_horizon": 6.0, # 几秒内能到中路就报警
        "danger_tta": 2.5,     # 几秒内到脸 → 最高级报警
        "fog_radius": 0.22,   # 在这个半径内消失 → 提示"附近有人不见"
        "fog_delay": 1.8,
        "max_alerts_per_min": 20,
        "report_jungle": True,   # 野区/河道有视野就报位置
        "identity_grace": 1.8,  # 名号没解析出来时，情报类播报最多等这么久
    },
    "self": {
        # mid = 以中路中心线为参照；fixed = 固定点；auto = 尝试检测镜头框
        "mode": "mid",
        "point": [0.5, 0.5],
    },
    "behaviour": {
        # 每 2 秒一批，一批最多一条 —— 宁可这一批不说，也不让两句叠在一起
        "period": 2.0,
        "max_items": 1,
        # 多条待播时先播哪条：按这个"分路优先级"排，越靠前越先说话
        # （python main.py priority 可以改，默认 中 > 野 > 辅 > 下 > 上）
        "priority": ["mid", "jungle", "support", "adc", "top"],
        # 单条语音最长秒数（超过自动提高语速重合成）
        "line_max": 1.95,
        "warmup": 30.0,        # 开局静默，只建立"谁本来在哪"的基线
        "visible_hold": 1.5,   # 多久没检测到算看不见
        "leave_hold": 3.0,     # 离开分路要持续这么久才算真离开
        "enter_hold": 2.0,     # 回到分路要持续这么久才算真回来
        "zone_hold": 1.0,      # 野区区域防抖
        "mid_vanish": 10.0,    # 中路连续多久没露头就报"消失"
        "cd": 25.0,            # 同一英雄同一动作的冷却
    },
    "roles": {
        # 敌方英雄 → 角色（top/jungle/mid/adc/support），python main.py roles 设置
        "assign": {},
    },
    "voice": {
        "enabled": True,
        "min_interval": 1.2,  # 两次播报最小间隔（秒）
    },
    "champions": {"version": "16.21.1", "template_size": 26,
                  # 只播报这些英雄（python main.py pick 设置）；空列表 = 全部播报
                  "notify": [],
                  # 上一局的敌方阵容，用来在没连上对局接口时列出"最近 5 个"
                  "roster": []},
    "live": {"enabled": True, "interval": 4.0},
    "debug": {"window": False, "print": True},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path = "config.json") -> dict:
    p = Path(path)
    if not p.exists():
        return copy.deepcopy(DEFAULTS)
    try:
        user = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return copy.deepcopy(DEFAULTS)
    return _deep_merge(DEFAULTS, user)


def save(cfg: dict, path: str | Path = "config.json") -> None:
    Path(path).write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def region_tuple(cfg: dict) -> tuple[int, int, int, int]:
    r = cfg["capture"]["region"]
    l, t = int(r["left"]), int(r["top"])
    return l, t, int(r["width"]), int(r["height"])


def default_region(sw: int, sh: int) -> tuple[int, int, int, int]:
    """没校准时用的猜测框：LoL 小地图默认贴屏幕右下角。

    边长按 1080p 的 250px 线性缩放（1200p → 278px）。HUD 缩放改过的会偏，
    所以这只是"先能跑起来"的兜底，真正的准头还得靠 calibrate 或自动定位。
    """
    side = max(160, int(round(sh * 250.0 / 1080.0)))
    side = min(side, min(sw, sh))
    return sw - side, sh - side, side, side
