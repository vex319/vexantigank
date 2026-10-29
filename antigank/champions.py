"""英雄身份识别。

两条链路，优先用第一条：

1) **官方 Live Client Data API**（127.0.0.1:2999）
   直接给出敌方五个英雄的 championName —— 身份是"读"出来的，不是猜的。
   这个接口不给坐标，所以位置仍靠小地图像素。

2) **头像模板匹配**
   已知 5 个候选（或没有 API 时的全部英雄）时，把检出的红点裁下来，
   和 Data Dragon 头像做归一化互相关。单个候选只要 5 次比对，几十微秒。

  小地图上头像只有 ~19 px，单帧 top-1 准确率不会很高（经验值 70~90%），
  所以不靠单帧：每条轨迹对最近若干帧投票，1 秒内就能收敛到稳定身份。
  同一帧内还会做唯一性约束 —— 每个英雄只出现一次，用贪心指派消歧。
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

DD_BASE = "https://ddragon.leagueoflegends.com/cdn"
UA = {"User-Agent": "Mozilla/5.0 antigank/0.2"}


def _mask(size: int, frac: float = 0.34) -> np.ndarray:
    """只比对圆形内部。

    小地图头像是"方形原画裁成圆 + 外圈红色描边"，描边不携带身份信息，
    甚至会把相关度拉向所有英雄都相似的红区，所以遮罩半径要略小于半个图标。
    """
    yy, xx = np.mgrid[0:size, 0:size]
    c = (size - 1) / 2.0
    r = size * frac
    return (((xx - c) ** 2 + (yy - c) ** 2) <= r * r)


class ChampionBook:
    """头像模板库 + 身份识别器。"""

    def __init__(self, root: str | Path = "assets/champions", size: int = 26,
                 version: str = "", mask_frac: float = 0.34):
        self.root = Path(root)
        self.size = int(size)
        self.version = version
        self.index: dict[str, dict] = {}
        self._tpl: dict[str, np.ndarray] = {}
        self._mask = _mask(self.size, mask_frac)
        self._lock = threading.Lock()
        self._spoken: dict[str, str] = {}
        self._alias: dict[str, str] = {}
        self._load_index()
        self._load_spoken()
        self._build_alias()

    # ---------- 索引 ----------
    def _load_index(self) -> None:
        p = self.root / "index.json"
        if not p.exists():
            return
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            self.index = blob.get("champions", {})
            self.version = self.version or blob.get("version", "")
        except Exception:
            self.index = {}

    def _build_alias(self) -> None:
        """中文名/称号 → 英文 key 的反查表。

        国服的对局接口给的是**本地化中文**（"齐天大圣"、"异画师"），而模板库、
        语音文件、勾选名单统统用 Data Dragon 的英文 key（MonkeyKing）。
        不归一化的话，候选列表里一个都不存在，整条识别链路会静默失效。
        """
        alias: dict[str, str] = {}
        for key, e in self.index.items():
            alias.setdefault(key.lower(), key)
            for field in ("name", "epithet"):
                val = (e.get(field) or "").strip()
                if val:
                    alias.setdefault(val, key)
                if val:
                    alias.setdefault(val.lower(), key)
        self._alias = alias

    def resolve(self, raw: str) -> str:
        """对局接口给的英雄标识 → 英文 key（认不出来就原样返回）。"""
        s = (raw or "").strip()
        if not s:
            return ""
        if s in self.index:
            return s
        hit = self._alias.get(s) or self._alias.get(s.lower())
        if hit:
            return hit
        compact = s.replace(" ", "").replace("_", "").replace("'", "").lower()
        for key in self.index:
            if key.replace(" ", "").replace("_", "").replace("'", "").lower() == compact:
                return key
        return s

    def _load_spoken(self) -> None:
        """可选的播报名覆盖表（key → 念出来的字）。"""
        p = self.root / "spoken.json"
        if not p.exists():
            return
        try:
            self._spoken = {k: str(v) for k, v in
                            json.loads(p.read_text(encoding="utf-8")).items()}
        except Exception:
            self._spoken = {}

    def name(self, key: str) -> str:
        e = self.index.get(key, {})
        return e.get("name") or e.get("epithet") or key

    def speak(self, key: str) -> str:
        """播报用的名字 —— 用称号，不用本名。

        小地图上认出的是"这张脸"，玩家听到"大发明家 / 冰晶凤凰 / 盲僧"
        比听到"黑默丁格 / 艾尼维亚 / 李青"反应快得多，这也是客户端里
        英雄头像下面那行字。

        个别称号不好念/听不清的，可以在 assets/champions/spoken.json
        里写 {"LeeSin": "盲僧"} 之类的覆盖。
        """
        if not key:
            return "英雄"
        custom = self._spoken.get(key)
        if custom:
            return custom
        e = self.index.get(key, {})
        return e.get("epithet") or e.get("name") or key

    def label(self, key: str) -> str:
        """控制台/勾选界面用：称号（本名），两边都能认。"""
        e = self.index.get(key, {})
        ep, nm = e.get("epithet"), e.get("name")
        if ep and nm and ep != nm:
            return f"{ep}（{nm}）"
        return ep or nm or key

    def full(self, key: str) -> str:
        e = self.index.get(key, {})
        return f"{e.get('epithet', '')} {e.get('name', key)}".strip()

    def has_icon(self, key: str) -> bool:
        p = self.root / f"{key}.png"
        return p.exists() and p.stat().st_size > 1500

    # ---------- 模板 ----------
    def template(self, key: str) -> np.ndarray | None:
        with self._lock:
            tpl = self._tpl.get(key)
            if tpl is not None:
                return tpl
        p = self.root / f"{key}.png"
        if not p.exists():
            return None
        try:
            buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            if img is None:
                return None
        except Exception:
            return None
        # 方形头像中心裁成正方形后缩放到统一尺寸，再套圆形遮罩
        h, w = img.shape[:2]
        s = min(h, w)
        y0, x0 = (h - s) // 2, (w - s) // 2
        img = img[y0:y0 + s, x0:x0 + s]
        img = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_AREA)
        v = img.astype(np.float32)
        v[~self._mask] = 0.0
        with self._lock:
            self._tpl[key] = v
        return v

    # ---------- 识别 ----------
    def _prep(self, patch: np.ndarray) -> np.ndarray:
        v = cv2.resize(patch, (self.size, self.size), interpolation=cv2.INTER_LINEAR)
        v = v.astype(np.float32)
        v[~self._mask] = 0.0
        return v

    def _score(self, a: np.ndarray, b: np.ndarray) -> float:
        m = self._mask
        av = a[m].ravel()
        bv = b[m].ravel()
        av = av - av.mean()
        bv = bv - bv.mean()
        den = (float(np.linalg.norm(av)) * float(np.linalg.norm(bv))) + 1e-6
        return float(av @ bv / den)

    def scores(self, patch: np.ndarray, candidates: list[str]) -> list[tuple[str, float]]:
        p = self._prep(patch)
        out: list[tuple[str, float]] = []
        for key in candidates:
            tpl = self.template(key)
            if tpl is None:
                continue
            out.append((key, self._score(p, tpl)))
        out.sort(key=lambda kv: -kv[1])
        return out

    def assign(self, patches: list[np.ndarray], candidates: list[str],
               min_score: float = 0.30) -> list[tuple[str, float] | None]:
        """一帧内给多个红点分配身份，每个英雄最多用一次。"""
        if not patches or not candidates:
            return [None] * len(patches)
        return self.assign_from(self.score_matrix(patches, candidates), min_score)

    def score_matrix(self, patches: list[np.ndarray],
                     candidates: list[str]) -> list[list[tuple[str, float]]]:
        """每个红点对所有候选的相关度（按分数降序）。"""
        return [self.scores(p, candidates) for p in patches]

    def assign_from(self, matrix: list[list[tuple[str, float]]],
                    min_score: float = 0.30) -> list[tuple[str, float] | None]:
        """贪心唯一指派：一个英雄在同一帧里只能出现在一个红点上。"""
        if not matrix:
            return []
        pairs: list[tuple[float, int, str]] = []
        for i, row in enumerate(matrix):
            for key, sc in row:
                pairs.append((sc, i, key))
        pairs.sort(key=lambda x: -x[0])

        result: list[tuple[str, float] | None] = [None] * len(matrix)
        used_key: set[str] = set()
        for sc, i, key in pairs:
            if result[i] is not None or key in used_key or sc < min_score:
                continue
            result[i] = (key, sc)
            used_key.add(key)
        return result
