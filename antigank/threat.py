"""威胁判定与视野情报。

统一用"预计到达时间 TTA"（敌人走到参考点还要几秒）作为主指标：

  danger  TTA ≤ 2.5 s                       已经贴脸
  warn    TTA ≤ 6 s 且在靠近                 提前预警
  fog     在参考点附近从视野里消失            最实用的一条
  sight   在野区 / 河道 / 资源点被看到         纯情报，不判断威胁

为什么 fog 最重要：小地图只显示有视野的敌人。一个敌人贴着中路从视野里消失，
比它明晃晃走过来更危险 —— 大概率在绕后。

区域判定用 geo.zone_key：河道是副对角线、中路是主对角线、两条线切出四个野区块。
sight 规则带 0.5 秒防抖（先候选、稳定后才切换），避免在区域边界上反复横跳。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .geo import direction_word, norm_dist, time_to_arrive, zone_key, zone_label
from .tracker import Track
from .voice import zone_voice_key

_DIR_KEY = {
    "上": "warn_up", "下": "warn_down", "左": "warn_left", "右": "warn_right",
    "左上": "warn_ul", "右上": "warn_ur", "左下": "warn_dl", "右下": "warn_dr",
    "附近": "warn_close",
}

# 这些区域不值得播报：玩家要的是"谁离开分路进了野区/河道"。
# 分路上的敌人正在被我方看见，中路有 danger 单独管；基地没有情报价值。
_SKIP_ZONE = {"top", "mid", "bot", "base_blue", "base_red"}


@dataclass
class Alert:
    level: str
    seq: tuple[str, ...]
    text: str
    tid: int
    score: float
    tta: float = 0.0
    champion: str = ""


@dataclass
class _ZoneState:
    zone: str = ""
    cand: str = ""
    cand_t: float = 0.0
    hidden_since: float = 0.0


class ThreatEngine:
    LEVEL_CD = {"danger": 9.0, "warn": 11.0, "fog": 16.0, "sight": 22.0}

    def __init__(self, tcfg: dict, scfg: dict, namer=None):
        self.map_units = float(tcfg["map_units"])
        self.move_speed = float(tcfg["move_speed"])
        self.detour = float(tcfg["detour"])
        self.horizon = float(tcfg["alert_horizon"])
        self.danger_tta = float(tcfg.get("danger_tta", 2.5))
        self.fog_radius = float(tcfg["fog_radius"])
        self.fog_delay = float(tcfg["fog_delay"])
        self.max_per_min = int(tcfg["max_alerts_per_min"])
        self.report_jungle = bool(tcfg.get("report_jungle", True))
        # 身份还在解析时的等待上限。情报类播报宁肯晚 1.8 秒，也要带上英雄名。
        self.identity_grace = float(tcfg.get("identity_grace", 1.8))
        self.self_mode = scfg["mode"]
        self.self_point = (float(scfg["point"][0]), float(scfg["point"][1]))
        self.namer = namer or (lambda k: k or "英雄")
        self.my_side: str | None = None
        self._cd: dict[tuple[int, str], float] = {}
        self._sent: list[float] = []
        self._zstate: dict[int, _ZoneState] = {}
        self.last_seen_zone: dict[int, str] = {}

    # ---------- 基础 ----------
    def ref_point(self) -> tuple[float, float] | None:
        if self.self_mode == "mid":
            return (0.5, 0.5)
        if self.self_mode == "fixed":
            return self.self_point
        return None

    def tta_of(self, nx: float, ny: float) -> float:
        return time_to_arrive(nx, ny, ref=self.ref_point(),
                              map_units=self.map_units,
                              move_speed=self.move_speed, detour=self.detour)

    def _label(self, zk: str) -> str:
        return zone_label(zk, self.my_side)

    # ---------- 主流程 ----------
    def evaluate(self, tracks: list[Track], now: float) -> list[Alert]:
        ref = self.ref_point()
        alerts: list[Alert] = []
        alive = {t.tid for t in tracks}
        for tid in list(self._zstate):
            if tid not in alive:
                del self._zstate[tid]

        for tr in tracks:
            # 名字优先用确认身份，其次用排除法/累计相关度推测的名字。
            # 播报里绝不再出现"敌人"这种没有名字的句子。
            key = tr.ident()
            named = bool(tr.key)
            name = self.namer(key) if key else "英雄"
            shown = name if (named or not key) else f"{name}（推测）"
            foe_wav = f"foe_{key}" if key else "foe"
            tta = self.tta_of(tr.nx, tr.ny)
            d_ref = (norm_dist(tr.nx, tr.ny, ref[0], ref[1]) if ref
                     else abs(tr.nx + tr.ny - 1.0) / 1.41421356)
            visible = tr.visible(now, 0.45)
            # 连推测名都还没有（英雄库/阵容都没拿到）→ 先攒一会儿再报，
            # danger 例外，它等不起
            waiting = key is None and (now - tr.t_first) < self.identity_grace
            lead = foe_wav

            # ---- 威胁 ----
            if visible:
                if tta <= self.danger_tta:
                    alerts.append(Alert("danger",
                                        tuple(k for k in (lead, "danger_mid") if k),
                                        f"敌方{shown}已经在中路", tr.tid, 1.0, tta, name))
                elif tta <= self.horizon:
                    word = direction_word(tr.nx - 0.5, tr.ny - 0.5)
                    closing = True
                    if ref is not None:
                        dx, dy = ref[0] - tr.nx, ref[1] - tr.ny
                        n = (dx * dx + dy * dy) ** 0.5 or 1.0
                        closing = (tr.vx * dx + tr.vy * dy) / n > 0.004 or tr.speed <= 0.02
                    if closing and not waiting:
                        dkey = _DIR_KEY.get(word, "warn_close")
                        alerts.append(Alert(
                            "warn", tuple(k for k in (lead, dkey) if k),
                            f"敌方{shown}从{word}方向靠近，约 {tta:.0f} 秒到中路",
                            tr.tid, 1.0 - tta / max(self.horizon, 0.1), tta, name))
            else:
                lost = tr.lost_for(now)
                if (d_ref <= self.fog_radius and self.fog_delay <= lost <= 5.0
                        and tr.hits >= 2 and not waiting):
                    word = direction_word(tr.nx - 0.5, tr.ny - 0.5)
                    alerts.append(Alert(
                        "fog", tuple(k for k in (lead, "fog") if k),
                        f"敌方{shown}在{word}方向消失，可能绕中路", tr.tid, 0.85, 0.0, name))

            # ---- 视野情报 ----
            if self.report_jungle:
                a = self._sighting(tr, now, visible, lead, f"敌方{shown}")
                if a is not None:
                    alerts.append(a)

        return self._throttle(alerts, now)

    def _sighting(self, tr: Track, now: float, visible: bool,
                  lead: str, who: str) -> Alert | None:
        st = self._zstate.setdefault(tr.tid, _ZoneState(cand=zone_key(tr.nx, tr.ny),
                                                       cand_t=now, zone=""))
        zk = zone_key(tr.nx, tr.ny)

        if not visible:
            if st.hidden_since == 0.0:
                st.hidden_since = now
            return None

        reappeared = st.hidden_since and (now - st.hidden_since) >= 2.5
        st.hidden_since = 0.0

        if zk != st.cand:
            st.cand, st.cand_t = zk, now
        committed = None
        if zk != st.zone and (now - st.cand_t) >= 0.5:
            committed = zk

        target = committed or (zk if reappeared else None)
        if not target or target in _SKIP_ZONE:
            return None
        if committed is None and st.zone in _SKIP_ZONE:
            return None
        # 名号还在解析：这里先不提交 st.zone，等下一帧再来一次，别把情报丢掉
        if tr.ident() is None and (now - tr.t_first) < self.identity_grace:
            return None
        if committed:
            st.zone = committed

        label = self._label(target)
        return Alert("sight",
                     tuple(k for k in (lead, zone_voice_key(target, self.my_side)) if k),
                     f"{who}出现在{label}", tr.tid, 0.5, 0.0, who)

    # ---------- 限流 ----------
    def _throttle(self, alerts: list[Alert], now: float) -> list[Alert]:
        self._sent = [t for t in self._sent if now - t <= 60.0]
        alerts.sort(key=lambda a: -a.score)
        out: list[Alert] = []
        for a in alerts:
            k = (a.tid, a.level)
            if now - self._cd.get(k, -1e9) < self.LEVEL_CD.get(a.level, 10.0):
                continue
            if len(self._sent) >= self.max_per_min:
                break
            self._cd[k] = now
            self._sent.append(now)
            out.append(a)
        return out
