"""下载英雄头像与中文名（Data Dragon）。

    python tools/fetch_champions.py            # 全量，约 170 个图标
    python tools/fetch_champions.py Ahri LeeSin

产物：
    assets/champions/index.json    {Key: {id, cn, title}}
    assets/champions/<Key>.png     120x120 方形头像

运行时会用到这些文件把"红点"和"具体英雄"对上；缺哪个就现场补哪个。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "champions"
UA = {"User-Agent": "Mozilla/5.0 antigank/0.2"}


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_version() -> str:
    return json.loads(_get("https://ddragon.leagueoflegends.com/api/versions.json"))[0]


def fetch_index(version: str) -> dict:
    data = json.loads(_get(
        f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json"
    ))["data"]
    idx = {
        k: {"id": v["key"], "cn": v["name"], "title": v["title"]}
        for k, v in data.items()
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.json").write_text(
        json.dumps({"version": version, "champions": idx}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return idx


def fetch_icon(version: str, key: str) -> tuple[str, bool]:
    path = OUT / f"{key}.png"
    if path.exists() and path.stat().st_size > 1500:
        return key, True
    try:
        blob = _get(f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{key}.png")
        path.write_bytes(blob)
        return key, True
    except Exception as exc:
        return key, False


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ver = latest_version()
    print(f"Data Dragon 版本 {ver}")
    idx = fetch_index(ver)
    print(f"英雄索引 {len(idx)} 个 → assets/champions/index.json")

    want = sys.argv[1:] or list(idx.keys())
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (key, ok) in enumerate(pool.map(lambda k: fetch_icon(ver, k), want), 1):
            done += ok
            if i % 20 == 0 or i == len(want):
                print(f"  图标 {i}/{len(want)}", flush=True)
    print(f"完成，本地图标 {done} 个")
    if done < len(want):
        print("  个别图标下载失败，运行时会重试")


if __name__ == "__main__":
    main()
