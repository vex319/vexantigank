"""按分路角色的行为播报（方案.txt 的规则）。

和上一版"威胁报警"完全不同：不再算"几秒到中路"，而是盯着**状态改变**。

    上路 / 下路 / 辅助   离开自己的分路 → "敌方X从上路消失"
                        出现在自己的分路 → "敌方X出现在上路"
    打野 / 辅助         出现在野区、河道、龙坑 → "敌方X出现在敌方上半野区"
    中路                连续 10 秒没出现在视野里 → "敌方X消失"

两条设计上的要点：

1. **状态挂在英雄身上，不挂在轨迹上。**
   轨迹会因为"走进迷雾 → 4 秒后被回收"而消失，可"中路已经 10 秒没露头"
   恰恰要求轨迹已经没了还能算。所以每个英雄 key 一份 HeroState，
   轨迹只负责往里喂"最后可见时间 + 当前区域"。

排序：先按用户在「调整播报优先级」里定的**分路优先级**，同一角色内部再按
"有多具体"排：我方半区野区 > 敌方半区野区/河道/龙坑 > 从分路消失 > 回到分路
> 中路消失。野区位置排在"从分路消失"前面，是因为它更具体 —— 辅助走下路进
河道时，说"出现在河道"比"从下路消失"有用。

2. **全部是边沿，不是状态。**
   一个敌人在上路补了 5 分钟刀，就不该每帧喊一次"在上路"。只有
   "在路 → 不在路"的那一次翻转才播。为了防止贴着分路边线来回蹭
   造成抖动，两个方向都要求状态**持续**一段时间（离开 3 秒、出现 2 秒）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geo import is_my_half, zone_key, zone_say
from .roles import LANE_OF, ROLE_NAME, WATCH_JUNGLE

# 值得播报的野区类区域（分路和基地不在里面）
# deep_* 是野区折线之外、基地之外的那圈腹地（二塔后面、高地墙内）。
# 它不算野区，但敌人摸到那儿更该说一声，所以照样播，只是说法换成"半场深处"。
JUNGLE_ZONES = {
    "river",
    "baron",
    "dragon",
    "jg_blue_top", "jg_blue_bot", "jg_red_top", "jg_red_bot",
    "deep_blue", "deep_red",
}

LANE_SAY = {"top": "上路", "bot": "下路"}


@dataclass
class Event:
    """一条待播报的信息 = 英雄 + 动作。"""

    key: str        # 英雄 key
    action: str     # off_top / on_bot / z_jg_my_top / vanish
    text: str       # 整句文本，例如 "敌方盲僧从上路消失"
    score: float    # 同一角色内部排队用
    t: float = 0.0
    role: str = ""  # 这个英雄的角色，批播时按用户的"分路优先级"排序

    @property
    def slot(self) -> str:
        """同一英雄同一个动作只留一条。"""
        return f"{self.key}:{self.action}"


@dataclass
class HeroState:
    key: str
    role: str
    last_seen: float = -1e9
    zone: str = ""
    zone_since: float = 0.0
    last_zone_evt: str = ""
    lane_state: bool | None = None   # None = 还没建立基线
    on_since: float = 0.0
    off_since: float = 0.0
    vanished: bool = False           # 中路的"消失"是不是已经报过了
    cd: dict = field(default_factory=dict)   # action -> 上次播报时间


@dataclass
class _Cfg:
    period: float = 2.0
    max_items: int = 1
    warmup: float = 30.0        # 开局静默，只建立基线
    visible_hold: float = 1.5   # 多久没检测到算"看不见"
    leave_hold: float = 3.0     # 离开分路要持续这么久才算真离开
    enter_hold: float = 2.0     # 回到分路要持续这么久才算真回来
    zone_hold: float = 1.0      # 野区区域防抖
    mid_vanish: float = 10.0    # 中路连续多久没露头就报"消失"
    cd: float = 25.0            # 同一英雄同一动作的冷却
    line_max: float = 2.0       # 单条语音最长秒数


class BehaviourEngine:
    def __init__(self, bcfg: dict | None = None, namer=None):
        cfg = _Cfg()
        for k, v in (bcfg or {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, type(getattr(cfg, k))(v))
        self.cfg = cfg
        self.namer = namer or (lambda k: k or "敌人")
        self.roles: dict[str, str] = {}
        self.my_side: str | None = None
        self._st: dict[str, HeroState] = {}

    # ---------- 生命周期 ----------
    def set_roles(self, roles: dict[str, str]) -> None:
        """角色表换了（手动改了分配）→ 只更新角色，不清状态。"""
        self.roles = dict(roles or {})
        for key, st in self._st.items():
            if key in self.roles:
                st.role = self.roles[key]

    def reset(self) -> None:
        self._st.clear()

    # ---------- 主流程 ----------
    def update(self, tracks, roles: dict[str, str], now: float) -> list[Event]:
        if roles and roles != self.roles:
            self.set_roles(roles)
        if not self.roles:
            return []

        self._observe(tracks, now)

        events: list[Event] = []
        for key, st in self._st.items():
            events.extend(self._lane_edges(st, now))
            events.extend(self._zone_edges(st, now))
            events.extend(self._mid_vanish(st, now))
        return events

    # ---------- 喂数据 ----------
    def _observe(self, tracks, now: float) -> None:
        """把这一帧的"谁在哪里、什么时候看见的"写进英雄状态。"""
        hold = self.cfg.visible_hold
        for tr in tracks:
            key = tr.ident()
            if not key or key not in self.roles:
                continue
            if not tr.visible(now, hold):
                continue
            st = self._st.get(key)
            if st is None:
                st = HeroState(key=key, role=self.roles[key])
                self._st[key] = st
            # 同一个英雄万一有两条轨迹（重生接续失败），以更近的这条为准
            if tr.t_last < st.last_seen:
                continue
            st.last_seen = tr.t_last
            zk = zone_key(tr.nx, tr.ny)
            if zk != st.zone:
                st.zone, st.zone_since = zk, now
            st.vanished = False

    # ---------- 分路进出 ----------
    def _lane_edges(self, st: HeroState, now: float) -> list[Event]:
        lane = LANE_OF.get(st.role)
        if lane is None:
            return []

        seen = (now - st.last_seen) <= self.cfg.visible_hold
        in_lane = seen and st.zone == lane

        if in_lane:
            if st.on_since == 0.0:
                st.on_since = now
            st.off_since = 0.0
        else:
            if st.off_since == 0.0:
                st.off_since = now
            st.on_since = 0.0

        warm = now < self.cfg.warmup

        # 还没建立基线：第一次看清他在哪一侧，只记不说
        if st.lane_state is None:
            if in_lane and now - st.on_since >= self.cfg.enter_hold:
                st.lane_state = True
            elif not in_lane and now - st.off_since >= self.cfg.leave_hold:
                st.lane_state = False
            return []

        if st.lane_state and not in_lane and (now - st.off_since) >= self.cfg.leave_hold:
            st.lane_state = False
            if warm or not self._ready(st, f"off_{lane}", now):
                return []
            return [self._ev(st, f"off_{lane}",
                             f"敌方{self.namer(st.key)}从{LANE_SAY[lane]}消失",
                             now, 0.70)]

        if not st.lane_state and in_lane and (now - st.on_since) >= self.cfg.enter_hold:
            st.lane_state = True
            if warm or not self._ready(st, f"on_{lane}", now):
                return []
            return [self._ev(st, f"on_{lane}",
                             f"敌方{self.namer(st.key)}出现在{LANE_SAY[lane]}",
                             now, 0.50)]

        return []

    # ---------- 野区 / 河道 / 龙坑 ----------
    def _zone_edges(self, st: HeroState, now: float) -> list[Event]:
        if st.role not in WATCH_JUNGLE:
            return []
        seen = (now - st.last_seen) <= self.cfg.visible_hold
        if not seen or not st.zone:
            return []
        # 区域要先稳定一会儿，站在边界上来回蹭不算
        if now - st.zone_since < self.cfg.zone_hold:
            return []
        if st.zone not in JUNGLE_ZONES:
            # 回到分路/基地：下次再进野区要重新报一次
            st.last_zone_evt = ""
            return []
        if st.zone == st.last_zone_evt:
            return []
        action = f"z_{st.zone}"
        if not self._ready(st, action, now):
            st.last_zone_evt = st.zone        # 冷却中，记下来别再重复触发
            return []
        st.last_zone_evt = st.zone
        danger = is_my_half(st.zone, self.my_side)
        return [self._ev(st, action,
                         f"敌方{self.namer(st.key)}出现在{zone_say(st.zone, self.my_side)}",
                         now, 1.00 if danger else 0.80)]

    # ---------- 中路：连续无视野 ----------
    def _mid_vanish(self, st: HeroState, now: float) -> list[Event]:
        if st.role != "mid" or st.vanished:
            return []
        if st.last_seen < 0:
            return []      # 整局还没见过 → 不是"消失"，是"没出现过"
        if now - st.last_seen < self.cfg.mid_vanish:
            return []
        st.vanished = True
        if now < self.cfg.warmup or not self._ready(st, "vanish", now):
            return []
        return [self._ev(st, "vanish", f"敌方{self.namer(st.key)}消失",
                         now, 0.45)]

    # ---------- 工具 ----------
    def _ready(self, st: HeroState, action: str, now: float) -> bool:
        if now - st.cd.get(action, -1e9) < self.cfg.cd:
            return False
        st.cd[action] = now
        return True

    def _ev(self, st: HeroState, action: str, text: str,
            now: float, score: float) -> Event:
        return Event(key=st.key, action=action, text=text, score=score,
                     t=now, role=st.role)

    # ---------- 给预览窗口用 ----------
    def snapshot(self, now: float) -> list[tuple[str, str, str, bool]]:
        """(英雄 key, 角色中文, 当前区域, 是否在视野里)"""
        out = []
        for key, st in self._st.items():
            out.append((key,
                        ROLE_NAME.get(st.role, st.role),
                        st.zone or "-",
                        (now - st.last_seen) <= self.cfg.visible_hold))
        return out
