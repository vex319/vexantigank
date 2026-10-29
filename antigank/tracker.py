"""多目标跟踪：把逐帧检测点连成轨迹，估算速度，判断"消失在迷雾"。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .detect import Found
from .geo import norm_dist


@dataclass
class Track:
    tid: int
    nx: float
    ny: float
    t_first: float
    t_last: float
    vx: float = 0.0
    vy: float = 0.0
    hits: int = 1
    d: float = 0.06
    conf: float = 1.0
    hist: list = field(default_factory=list)  # [(t, nx, ny)]
    det_idx: int = -1                          # 本帧对应第几个检测
    votes: Counter = field(default_factory=Counter)  # 身份投票
    key: str | None = None                     # 判定出的英雄 key
    probs: dict = field(default_factory=dict)  # 累计相关度（候选名都可以有分）
    guess: str | None = None                   # 排除法/最高分推测出的名字

    def ident(self) -> str | None:
        """当前能叫出的名字：确定身份 > 排除法推测 > 累计相关度最高。

        为什么要"推测"这一档：用户要的是"敌方<英雄>出现在<区域>"，
        1 Hz 下等确认票数就是 2 秒空窗，没名字的播报等于没播。
        """
        if self.key:
            return self.key
        if self.guess:
            return self.guess
        if self.probs:
            k, v = max(self.probs.items(), key=lambda kv: kv[1])
            return k if v > 0 else None
        return None

    def add_scores(self, row) -> None:
        """把一帧里所有候选的相关度都累进来（不只是赢家）。

        单帧 top-1 只有 70~90%，但"哪个英雄长期分数最高"很稳；
        有了这张表，就算票数还没攒够也能马上报出名字。
        """
        for key, sc in row or ():
            if sc > 0:
                self.probs[key] = self.probs.get(key, 0.0) + sc * sc

    def add_identity(self, key: str | None, score: float = 0.0,
                     min_votes: int = 2, min_share: float = 0.45,
                     instant_score: float = 0.86) -> None:
        """给轨迹投票确定英雄身份。

        1 Hz 下"3 票"意味着 3 秒才能叫出名字，可第一次报警在 0.5 秒就发生了，
        结果就是只有位置没有名字。所以：
          - 单帧相关度 ≥ instant_score 直接认定（真机实测正确识别通常 0.9+）
          - 否则 2 票 + 45% 占比（约 2 秒）
        """
        if not key:
            return
        self.votes[key] += 1
        if score >= instant_score:
            self.key = key
            self.votes[key] += 2      # 让后面的低分帧不容易翻盘
            return
        total = sum(self.votes.values())
        if total >= min_votes:
            best, n = self.votes.most_common(1)[0]
            if n / total >= min_share:
                self.key = best

    def adopt(self, key: str) -> None:
        """从"上一段同名轨迹"继承身份（敌人短暂离开视野后重新出现）。"""
        self.key = key
        self.votes[key] += 3

    def hint(self, key: str) -> None:
        """推测出来的名字。不是确认身份，所以不写进 key ——
        后面的真实匹配还可以推翻它。"""
        if not self.key:
            self.guess = key

    def lost_for(self, now: float) -> float:
        return now - self.t_last

    def visible(self, now: float, hold: float = 0.4) -> bool:
        return (now - self.t_last) <= hold

    @property
    def speed(self) -> float:
        return (self.vx * self.vx + self.vy * self.vy) ** 0.5

    def is_static(self, now: float, eps: float, hold: float) -> bool:
        """最近 hold 秒内几乎没位移 → 是建筑/地形图标，不是英雄。"""
        window = [p for p in self.hist if now - p[0] <= hold]
        if len(window) < 3 or (now - window[0][0]) < hold * 0.6:
            return False
        xs = [p[1] for p in window]
        ys = [p[2] for p in window]
        return (max(xs) - min(xs) < eps) and (max(ys) - min(ys) < eps)


class Tracker:
    def __init__(self, tcfg: dict):
        self.gate = float(tcfg["gate"])
        self.lost_timeout = float(tcfg["lost_timeout"])
        self.confirm_hits = int(tcfg["confirm_hits"])
        self.static_eps = float(tcfg["static_eps"])
        self.static_hold = float(tcfg["static_hold"])
        self.tracks: list[Track] = []
        self._next_id = 1

    def update(self, dets: list[Found], now: float) -> list[Track]:
        pairs: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(self.tracks):
            for di, d in enumerate(dets):
                dist = norm_dist(tr.nx, tr.ny, d.nx, d.ny)
                if dist <= self.gate:
                    pairs.append((dist, ti, di))
        pairs.sort()

        used_t: set[int] = set()
        used_d: set[int] = set()
        for _dist, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            tr, d = self.tracks[ti], dets[di]
            dt = max(1e-3, now - tr.t_last)
            vx = (d.nx - tr.nx) / dt
            vy = (d.ny - tr.ny) / dt
            k = 0.55
            tr.vx = tr.vx * (1 - k) + vx * k
            tr.vy = tr.vy * (1 - k) + vy * k
            tr.nx, tr.ny = d.nx, d.ny
            tr.d, tr.conf = d.d, d.conf
            tr.det_idx = di
            tr.t_last = now
            tr.hits += 1
            tr.hist.append((now, d.nx, d.ny))

        for di, d in enumerate(dets):
            if di in used_d:
                continue
            # 敌人走进迷雾再出来，或者 1 Hz 下漏了一两帧，都会产生一条新轨迹，
            # 身份票数跟着清零 —— 表现就是"刚才还叫得出名字，突然变成了'敌人'"。
            # 若附近有一条刚消失、且已认出名号的轨迹，直接把身份接过来。
            heir = self._inherit(d, now, used_t)
            tr = Track(
                tid=self._next_id,
                nx=d.nx,
                ny=d.ny,
                t_first=now,
                t_last=now,
                d=d.d,
                conf=d.conf,
                hist=[(now, d.nx, d.ny)],
                det_idx=di,
            )
            if heir:
                tr.adopt(heir)
            self.tracks.append(tr)
            self._next_id += 1

        horizon = 6.0
        for tr in self.tracks:
            if len(tr.hist) > 8 and tr.hist[0][0] < now - horizon:
                tr.hist = [p for p in tr.hist if p[0] >= now - horizon]

        self.tracks = [t for t in self.tracks if (now - t.t_last) <= self.lost_timeout]
        return self.tracks

    def confirmed(self, now: float) -> list[Track]:
        out = []
        for t in self.tracks:
            if t.hits < self.confirm_hits:
                continue
            # 已认出名号的一定是真英雄，站着不动（回城、蹲草、补刀）也不能丢；
            # 只有身份未知的才用"静止=建筑图标"这条兜底过滤。
            if t.key is None and t.is_static(now, self.static_eps, self.static_hold):
                continue
            out.append(t)
        return out

    def reconcile(self, roster: list[str], now: float, hold: float = 0.8,
                  min_hits: int = 2, min_score: float = 0.22) -> int:
        """用"本局敌方阵容"给叫不出名字的轨迹补名字。

        敌方只有 5 个人，且这 5 个英雄一定各不相同：
          · 场上 5 条轨迹、其中 4 条已确认 → 第 5 条只能是剩下的那个（排除法，最可靠）
          · 有多条未知 → 按累计相关度做贪心指派，一人一个，不重复
        这样能堵住"票数差一票就喊成'敌人'"的漏洞。
        """
        roster = [c for c in (roster or []) if c]
        if not roster:
            return 0
        live = [t for t in self.tracks
                if (now - t.t_last) <= hold and t.hits >= min_hits]
        known = {t.key for t in live if t.key}
        unknown = [t for t in live if not t.key]
        if not unknown:
            return 0
        pool = [c for c in roster if c not in known]
        if not pool:
            return 0

        if len(unknown) == 1 and len(pool) == 1:
            unknown[0].hint(pool[0])
            return 1

        pairs = [(t.probs.get(c, 0.0), i, c)
                 for i, t in enumerate(unknown) for c in pool]
        pairs.sort(key=lambda x: -x[0])
        used_t: set[int] = set()
        used_c: set[str] = set()
        n = 0
        for sc, i, c in pairs:
            if sc < min_score or i in used_t or c in used_c:
                continue
            unknown[i].hint(c)
            used_t.add(i)
            used_c.add(c)
            n += 1
        return n

    def _inherit(self, d: Found, now: float, used: set[int]) -> str | None:
        """找一条刚消失的、已认出名号的旧轨迹来继承身份。"""
        best_key, best_dist = None, self.gate * 2.0
        for ti, tr in enumerate(self.tracks):
            if ti in used or not tr.key:
                continue
            if now - tr.t_last > 3.0:      # 消失太久就不能算同一只了
                continue
            dist = norm_dist(tr.nx, tr.ny, d.nx, d.ny)
            if dist <= best_dist:
                best_key, best_dist = tr.key, dist
        return best_key
