"""MIDI 파싱 (midi_player.py 이식 + 최적화).

최적화 내역 (docs/PLAN.md §2):
- 템포 변환: 구간별 누적 ms 사전계산(_build_tempo_segments) + bisect 이진탐색
  (기존 _ticks_to_ms는 tick마다 템포맵을 처음부터 순회 -> O(events*tempos))
- 이벤트 빌드: 노트 on/off 스위프 방식으로 O(n log n) 재작성
  (기존은 시간 슬롯마다 전 트랙·전 노트를 재스캔하는 O(n^2))
"""
import bisect

import mido

MIN_STEP_MS = 5


def note_to_freq(note: int) -> int:
    if note == 0:
        return 0
    return round(440.0 * 2.0 ** ((note - 69) / 12.0))


def auto_bands(path, n, note_min=48):
    """노트 개수가 균등하도록 n개 구간(motor별 담당 음역)으로 분할. 높은 음역이 index 0."""
    mid = mido.MidiFile(path)
    notes = sorted(msg.note for track in mid.tracks
                   for msg in track
                   if msg.type == "note_on" and msg.velocity > 0 and msg.note >= note_min)
    if not notes:
        # ponytail: 노트가 없을 때의 최소 fallback — 0..127을 n등분
        step = 128 // n
        bands = [(i * step, (i + 1) * step - 1) for i in range(n)]
        bands[-1] = (bands[-1][0], 127)
        bands.reverse()
        return bands
    total = len(notes)
    cuts = [notes[int(total * i / n)] for i in range(1, n)]
    bands, lo = [], 0
    for cut in cuts:
        bands.append((lo, cut - 1))
        lo = cut
    bands.append((lo, 127))
    bands.reverse()
    return bands


def _build_tempo_segments(mid):
    """트랙 0 기준, 템포 구간별 (tick_start, ms_start, tempo) 리스트를 사전계산.
    이후 _ticks_to_ms에서 매 호출마다 전체 순회하지 않고 bisect로 구간을 바로 찾는다."""
    tempo_map = []
    abs_tick = 0
    for msg in mid.tracks[0]:
        abs_tick += msg.time
        if msg.type == "set_tempo":
            tempo_map.append((abs_tick, msg.tempo))

    tpb = mid.ticks_per_beat
    segments = []
    prev_tick, prev_tempo, prev_ms = 0, 500000, 0.0
    for t, tp in tempo_map:
        segments.append((prev_tick, prev_ms, prev_tempo))
        prev_ms += (t - prev_tick) * prev_tempo / (tpb * 1000.0)
        prev_tick, prev_tempo = t, tp
    segments.append((prev_tick, prev_ms, prev_tempo))
    starts = [s[0] for s in segments]
    return segments, starts


def _ticks_to_ms(tick, segments, starts, tpb):
    idx = bisect.bisect_right(starts, tick) - 1
    tick_start, ms_start, tempo = segments[idx]
    return ms_start + (tick - tick_start) * tempo / (tpb * 1000.0)


def parse_midi(path, bands, num_motors, min_ms=MIN_STEP_MS):
    """출력 형식은 legacy와 동일: ([(t_ms, [freq x num_motors], dur), ...], total_ms)"""
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    segments, starts = _build_tempo_segments(mid)

    all_notes = []
    for track in mid.tracks:
        pending = {}
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            t_ms = _ticks_to_ms(abs_tick, segments, starts, tpb)
            if msg.type == "note_on" and msg.velocity > 0:
                pending[msg.note] = t_ms
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                if msg.note in pending:
                    s = pending.pop(msg.note)
                    if t_ms - s > 5:
                        all_notes.append((s, msg.note, t_ms - s))

    if not all_notes:
        return [], 0.0
    total_ms = max(s + d for s, _, d in all_notes)

    tracks = [[(s, n, d) for s, n, d in all_notes if lo <= n <= hi] for lo, hi in bands]

    # 노트 on/off 스위프: 트랙별 정렬 후 변화 이벤트만 생성 (O(n log n))
    # priority: 같은 시각에 종료(0)가 시작(1)보다 먼저 반영되어야 새 노트가 우선한다.
    changes = []
    for m, tr in enumerate(tracks):
        tr.sort(key=lambda x: x[0])
        for s, note, d in tr:
            freq = note_to_freq(note)
            changes.append((s, 1, m, freq))
            changes.append((s + d, 0, m, 0))
    changes.sort(key=lambda c: (c[0], c[1]))

    times = sorted({c[0] for c in changes})

    events = []
    slot = [0] * num_motors
    ci, nchanges = 0, len(changes)
    for i, t in enumerate(times[:-1]):
        while ci < nchanges and changes[ci][0] == t:
            _, _, m, freq = changes[ci]
            slot[m] = freq
            ci += 1
        dt = times[i + 1] - t
        if dt < min_ms:
            continue
        cur = slot[:]
        if events and events[-1][1] == cur:
            events[-1] = (events[-1][0], cur, events[-1][2] + dt)
        else:
            events.append([t, cur, dt])

    return [(t, s, d) for t, s, d in events], total_ms


if __name__ == "__main__":
    # ponytail: 최소 self-check — 간단한 합성 MIDI로 파싱 결과 형태만 검증
    import tempfile

    m = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    m.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    tr.append(mido.Message("note_on", note=60, velocity=100, time=0))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
    tr.append(mido.Message("note_on", note=64, velocity=100, time=0))
    tr.append(mido.Message("note_off", note=64, velocity=0, time=480))
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
        m.save(f.name)
        events, total_ms = parse_midi(f.name, bands=[(0, 127)], num_motors=1)
    assert len(events) == 2, events
    assert abs(total_ms - 1000.0) < 1.0, total_ms
    assert events[0][1] == [note_to_freq(60)]
    assert events[1][1] == [note_to_freq(64)]
    print("midi self-check ok:", events, total_ms)
