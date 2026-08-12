#!/usr/bin/env python3
"""주크박스 라이브러리를 대중적인 곡으로 미리 채운다 (박람회 사전 준비용).

레포 루트에서, 반드시 .venv311로:
    .venv311/Scripts/python.exe scripts/populate_library.py

각 곡: 유튜브 검색 → 첫 결과 다운로드(yt-dlp) → MIDI 변환(basic-pitch) → 라이브러리 저장.
네트워크와 basic-pitch가 필요하다. 실패한 곡은 건너뛰고 계속하며, 이미 받은 곡은 캐시를 재사용한다.
표시 제목/아티스트는 아래에서 고정한다 — 검색은 videoId만 얻는 용도(유튜브 업로드 제목이 지저분해도 깔끔하게 뜬다).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as cfg_mod
from core import youtube as yt

# (검색어, 표시 제목, 표시 아티스트). 스텝모터로 잘 들리는 단선율 위주의 대중곡.
SONGS = [
    ("Tetris theme Korobeiniki",       "테트리스",         "Korobeiniki"),
    ("Super Mario Bros theme",         "슈퍼 마리오",       "Koji Kondo"),
    ("Legend of Zelda main theme",     "젤다의 전설",       "Koji Kondo"),
    ("Pokemon anime opening theme",    "포켓몬",           "Pokemon"),
    ("Pachelbel Canon in D",           "캐논 변주곡",       "Pachelbel"),
    ("Beethoven Fur Elise",            "엘리제를 위하여",    "Beethoven"),
    ("Mozart Turkish March Rondo",     "터키 행진곡",       "Mozart"),
    ("도라에몽 주제가",                 "도라에몽",         "도라에몽"),
]


def main():
    cfg = cfg_mod.load()
    cache_dir = cfg_mod.resolve_cache_dir(cfg)
    ok = 0
    for q, title, artist in SONGS:
        print(f"\n▶ {title}  (검색: {q})")
        try:
            results = yt.search(q)
            if not results:
                print("  건너뜀: 검색 결과 없음")
                continue
            r = results[0]

            def prog(ratio, msg):
                print(f"  {int(ratio * 100):3d}% {msg}    ", end="\r")

            yt.download_and_convert(r["videoId"], cache_dir, prog)
            yt.save_library_entry(cache_dir, r["videoId"], title, artist, r.get("thumbnail", ""))
            print(f"\n  저장 완료: {title} [{r['videoId']}]")
            ok += 1
        except Exception as e:
            print(f"\n  실패: {e}")
    print(f"\n완료: {ok}/{len(SONGS)}곡 저장. cache_dir={cache_dir}")


if __name__ == "__main__":
    main()
