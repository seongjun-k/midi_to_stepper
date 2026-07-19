#!/usr/bin/env python3
"""파싱 최적화 벤치마크: legacy(midi_player.parse_midi) vs core.midi.parse_midi.

5000+ 노트짜리 합성 MIDI(모든 노트가 겹치지 않는 단선율)로 두 구현의 실행 시간을 재고,
이벤트 수·total_ms·이벤트 내용까지 동일한지 확인한다.
(단선율이라 겹치는 노트가 없으므로 두 알고리즘의 "활성 노트 선택" 방식 차이와 무관하게
결과가 정확히 일치해야 정상이다.)
"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mido  # noqa: E402

from core import midi as new_midi  # noqa: E402
from legacy import midi_player as legacy  # noqa: E402

NUM_NOTES = 6000


def make_synthetic_midi(path, num_notes=NUM_NOTES, seed=42):
    """단선율(겹치지 않는 note_on/note_off) 5000+개 + 중간 템포 변경 1회."""
    random.seed(seed)
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    tempo_change_at = num_notes // 2
    dur_tick = 60
    for i in range(num_notes):
        if i == tempo_change_at:
            track.append(mido.MetaMessage("set_tempo", tempo=300000, time=0))
        note = random.randint(21, 108)
        vel = random.randint(60, 110)
        track.append(mido.Message("note_on", note=note, velocity=vel, time=0))
        track.append(mido.Message("note_off", note=note, velocity=0, time=dur_tick))
    mid.save(path)
    return path


def main():
    path = "/tmp/bench_synthetic.mid"
    make_synthetic_midi(path)

    bands = legacy.BANDS
    num_motors = legacy.NUM_MOTORS

    t0 = time.perf_counter()
    legacy_events, legacy_total = legacy.parse_midi(path, bands=bands)
    t_legacy = time.perf_counter() - t0

    t0 = time.perf_counter()
    new_events, new_total = new_midi.parse_midi(path, bands=bands, num_motors=num_motors)
    t_new = time.perf_counter() - t0

    print(f"notes             : {NUM_NOTES}")
    print(f"legacy parse_midi : {t_legacy*1000:8.2f} ms  events={len(legacy_events)}  total_ms={legacy_total:.1f}")
    print(f"new    parse_midi : {t_new*1000:8.2f} ms  events={len(new_events)}  total_ms={new_total:.1f}")
    print(f"speedup           : {t_legacy / t_new:.1f}x")

    assert len(legacy_events) == len(new_events), (len(legacy_events), len(new_events))
    assert abs(legacy_total - new_total) < 1e-6, (legacy_total, new_total)
    assert legacy_events == new_events, "이벤트 내용까지 동일해야 함"
    print("RESULT: MATCH (event count, total_ms, content all identical)")


if __name__ == "__main__":
    main()
