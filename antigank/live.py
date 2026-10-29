"""官方 Live Client Data API（对局内本地接口）。

     https://127.0.0.1:2999/liveclientdata/allgamedata

这是 Riot 随游戏客户端一起启动的只读本地接口，官方文档公开，
Blitz / Porofessor / OP.GG 这类工具用的就是它。安全性和合规性都比读屏更高。

能拿到：敌我双方的英雄名、等级、装备、召唤师技能、比分、游戏时间。
拿不到：**任何人的坐标** —— 所以位置仍然只能靠小地图视觉。

自签名证书，需要跳过校验；游戏不在进行中时连接会被拒绝（当作"未开局"处理）。
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.request

URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class LiveClient:
    def __init__(self, url: str = URL, interval: float = 4.0, timeout: float = 1.5):
        self.url = url
        self.interval = float(interval)
        self.timeout = float(timeout)
        self._snap: dict | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ctx = _ctx()

    def start(self) -> "LiveClient":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            snap = self._poll()
            with self._lock:
                self._snap = snap
            self._stop.wait(self.interval)

    def _poll(self) -> dict | None:
        try:
            req = urllib.request.Request(self.url, headers={"User-Agent": "antigank"})
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            return self._parse(data)
        except Exception:
            return None

    @staticmethod
    def _parse(data: dict) -> dict | None:
        players = data.get("allPlayers") or []
        active = data.get("activePlayer") or {}
        if not players:
            return None

        me_names = {
            active.get("summonerName"), active.get("riotId"), active.get("riotIdGameName"),
        } - {None, ""}

        my_team = None
        my_champ = active.get("championName") or ""
        for p in players:
            names = {p.get("summonerName"), p.get("riotId"), p.get("riotIdGameName")} - {None, ""}
            if names & me_names:
                my_team = p.get("team")
                my_champ = p.get("championName") or my_champ
                break

        game_time = float((data.get("gameData") or {}).get("gameTime") or 0.0)

        if my_team is None:
            # 认不出自己是谁时，不猜敌我，交给上层走"全英雄模板匹配"
            return {"game_time": game_time, "my_side": None, "my_champion": my_champ,
                    "enemies": [], "allies": []}

        enemies, allies = [], []
        for p in players:
            rec = {
                "champion": p.get("championName") or "",
                "level": p.get("level"),
                "team": p.get("team"),
                "is_bot": bool(p.get("isBot")),
                "position": p.get("position") or "",
            }
            (enemies if p.get("team") != my_team else allies).append(rec)

        return {
            "game_time": game_time,
            "my_side": my_team,
            "my_champion": my_champ,
            "enemies": enemies,
            "allies": allies,
            "stamp": time.time(),
        }

    def snapshot(self) -> dict | None:
        with self._lock:
            return self._snap

    def probe(self) -> dict | None:
        """同步探一次。空闲等待开局时用，不依赖后台线程的轮询节奏。"""
        return self._poll()

    @staticmethod
    def in_game(snap: dict | None) -> bool:
        """接口通了 ≠ 开局了：加载界面也能连上，此时 gameTime 还是 0 或负数。"""
        return bool(snap) and float(snap.get("game_time") or 0.0) > 0.0

    def enemy_champions(self) -> list[str]:
        snap = self.snapshot()
        if not snap:
            return []
        return [e["champion"] for e in snap.get("enemies", []) if e.get("champion")]
