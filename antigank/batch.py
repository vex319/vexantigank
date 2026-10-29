"""每 2 秒一批、一批最多一条的播报节奏。

用户定的规则：每 2 秒最多说一条，宁可这一批不说，也不叠在一起。

为什么是"攒一批再说"而不是"有事就喊"：

    1 Hz 采样下，5 个敌人各自进出视野，一秒内能同时冒出三四条信息。
    逐条喊的结果就是几句话叠在一起，谁也听不清 —— 原来的
    "0.9 秒内后一条直接丢掉"只是把问题藏起来，丢掉的恰恰可能是最要紧的那条。

排序（一批只能播一条时谁先说话）：

    1. **分路优先级**（用户可在「调整播报优先级」窗口里改）
       默认 中 > 野 > 辅 > 下 > 上
    2. 同一角色里按"有多具体"：我方半区野区 > 敌方半区野区/河道/龙坑
       > 从分路消失 > 回到分路 > 中路消失
    3. 还并列就按发生先后

    同一英雄只占一个名额 —— 两条播报都念同一个名字是浪费。
"""

from __future__ import annotations

from .behaviour import Event
from .roles import ROLE_KEYS

# 用户没设置过时的默认顺序。中路最需要提防 gank，所以排第一；
# 上路离你远、信息价值最低，排最后。改这个不会影响已保存的配置。
DEFAULT_PRIORITY = ("mid", "jungle", "support", "adc", "top")


class BatchScheduler:
    def __init__(self, period: float = 2.0, max_items: int = 1,
                 priority=None):
        self.period = float(period)
        self.max_items = int(max_items)
        self.priority = self._clean(priority)
        self._pending: dict[str, Event] = {}
        self._t0 = 0.0
        self.dropped = 0        # 累计因为名额不够被丢掉的条数，调试窗口会显示

    @staticmethod
    def _clean(order) -> list[str]:
        """只留合法角色；没给顺序就用默认，缺的角色按默认顺序补在后面。

        注意补的基准是 DEFAULT_PRIORITY，不是 ROLE_KEYS —— 早先写成
        ROLE_KEYS，结果"默认顺序"变成了 上>野>中>下>辅，跟注释完全不符。
        """
        base = [r for r in (order or DEFAULT_PRIORITY) if r in ROLE_KEYS]
        for r in DEFAULT_PRIORITY:
            if r not in base:
                base.append(r)
        return base

    def set_priority(self, order) -> None:
        self.priority = self._clean(order)

    def reset(self, now: float = 0.0) -> None:
        self._pending.clear()
        self._t0 = now
        self.dropped = 0

    def submit(self, events) -> None:
        for ev in events or ():
            old = self._pending.get(ev.slot)
            if old is None or ev.score >= old.score:
                self._pending[ev.slot] = ev

    def _rank(self, ev: Event) -> tuple:
        try:
            role = self.priority.index(ev.role)
        except ValueError:
            role = len(self.priority)
        return (role, -ev.score, ev.t)

    def due(self, now: float) -> list[Event]:
        """到点了就把这一批交出去；没到点返回空列表。

        这一批没轮上的**留在队列里等下一批** —— 每 2 秒只说一条，5 个人
        同时有动静时，后面那几条不该被直接扔掉，多说两批总能说出来。
        但只留 3 个周期（6 秒）：再晚就过时了，说"他刚才在野区"没有意义。
        """
        if now - self._t0 < self.period:
            return []
        self._t0 = now

        stale = self.period * 3
        for slot, ev in list(self._pending.items()):
            if now - ev.t > stale:
                self._pending.pop(slot, None)
        if not self._pending:
            return []

        by_hero: dict[str, Event] = {}
        for ev in self._pending.values():
            cur = by_hero.get(ev.key)
            if cur is None or ev.score > cur.score:
                by_hero[ev.key] = ev

        ranked = sorted(by_hero.values(), key=self._rank)
        picked = ranked[: self.max_items]
        # 被选中的英雄，连同他别的动作一起移出队列 —— 下一批再念同一个人的
        # 旧动作就是重复了
        for ev in picked:
            for slot in [s for s, e in self._pending.items() if e.key == ev.key]:
                self._pending.pop(slot, None)
        self.dropped += max(0, len(by_hero) - len(picked))
        return picked

    def pending(self) -> int:
        return len(self._pending)
