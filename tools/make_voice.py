"""预生成语音 wav。只在装机时跑一次。

    python tools/make_voice.py             # 报警短语（约 25 条）
    python tools/make_voice.py --champs    # 再加 173 个英雄名（约 3 分钟）
    python tools/make_voice.py --force     # 改了措辞后强制重录短语

运行时不做实时 TTS：所有短语预先落成 wav，播放延迟 <5 ms。
如果有英雄名缺失，程序启动时还会尝试补生成，或者退化成只播动作短语。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from antigank.voice import PHRASES, synth_wav, write_tone  # noqa: E402

OUT = ROOT / "assets" / "voice"
FORCE = "--force" in sys.argv


def ok(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 3000


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"输出目录 {OUT}")
    made = skipped = 0

    for key, text in PHRASES.items():
        path = OUT / f"{key}.wav"
        if ok(path) and not FORCE:
            skipped += 1
            continue
        if not text:
            write_tone(path, key)
            made += 1
            continue
        if synth_wav(text, path):
            made += 1
        else:
            print(f"  [降级为提示音] {key}: {text}")
            write_tone(path, key)
            made += 1
    print(f"短语：新生成 {made}，已存在 {skipped}")

    if "--champs" in sys.argv:
        idx_path = ROOT / "assets" / "champions" / "index.json"
        if not idx_path.exists():
            print("缺 assets/champions/index.json，先跑 tools/fetch_champions.py")
            return
        champs = json.loads(idx_path.read_text(encoding="utf-8"))["champions"]
        # 念称号（大发明家 / 冰晶凤凰 / 盲僧），与运行时 ChampionBook.speak 一致
        todo = [(k, v.get("epithet") or v.get("name")) for k, v in champs.items()
                if (v.get("epithet") or v.get("name"))
                and not ok(OUT / f"champ_{k}.wav")]
        print(f"英雄名：待生成 {len(todo)} / {len(champs)}")
        done = 0
        for i, (key, name) in enumerate(todo, 1):
            if synth_wav(name, OUT / f"champ_{key}.wav"):
                done += 1
            if i % 20 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)}", flush=True)
        print(f"英雄名：生成 {done} 条")

    files = sorted(OUT.glob("*.wav"))
    total = sum(f.stat().st_size for f in files)
    print(f"完成：{len(files)} 个 wav，共 {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
