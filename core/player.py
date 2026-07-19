"""재생 스레드 (midi_player.py Player 이식).

콜백(on_position/on_freqs/on_stopped/on_log)은 재생 스레드에서 직접 호출된다 —
asyncio 이벤트 루프가 아니므로, 서버 쪽에서 이 콜백을 asyncio 루프로 마샬링해야 한다
(예: asyncio.run_coroutine_threadsafe). Player 자체는 콜백을 순서대로, 참조를
로컬 변수로 캡처해 호출하므로 콜백 재할당 중 레이스에도 안전하다.

타이밍: sleep 기반 대기 + 목표 시각 직전 ~1ms만 스핀 (legacy와 동일한 패턴 유지).
"""
import threading
import time


class Player:
    def __init__(self, writer):
        self.writer = writer
        self.events = []
        self.total_ms = 0.0
        self._pause_ev = threading.Event()
        self._stop_ev = threading.Event()
        self._seek_ms = None
        self._lock = threading.Lock()
        self._thread = None
        self.on_position = None
        self.on_freqs = None
        self.on_stopped = None
        self.on_log = None

    def _log(self, msg):
        cb = self.on_log
        if cb:
            cb(msg)

    def load(self, events, total_ms):
        self.stop()
        self.events, self.total_ms = events, total_ms

    def play(self, start_ms=0.0):
        self.stop()
        self._pause_ev.set()
        self._stop_ev.clear()
        self._seek_ms = start_ms
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log("재생 시작")

    def pause(self):
        self._pause_ev.clear()
        self.writer.stop()
        self._log("일시정지")

    def resume(self):
        self._pause_ev.set()
        self._log("재개")

    @property
    def is_paused(self):
        return not self._pause_ev.is_set()

    @property
    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        self._stop_ev.set()
        self._pause_ev.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.writer.stop()

    def seek(self, ms):
        with self._lock:
            self._seek_ms = ms
        self._pause_ev.set()

    def _run(self):
        if not self.events:
            return
        with self._lock:
            offset_ms, self._seek_ms = self._seek_ms or 0.0, None
        idx = next((i for i, (t, _, _) in enumerate(self.events) if t >= offset_ms), 0)
        origin = time.perf_counter() * 1000 - offset_ms

        while idx < len(self.events) and not self._stop_ev.is_set():
            if not self._pause_ev.is_set():
                t0 = time.perf_counter() * 1000
                self._pause_ev.wait()
                origin += time.perf_counter() * 1000 - t0
            if self._stop_ev.is_set():
                break

            with self._lock:
                if self._seek_ms is not None:
                    offset_ms, self._seek_ms = self._seek_ms, None
                    origin = time.perf_counter() * 1000 - offset_ms
                    idx = next((i for i, (t, _, _) in enumerate(self.events) if t >= offset_ms), 0)
                    continue

            t_ev, freqs, _ = self.events[idx]
            wait = (t_ev - (time.perf_counter() * 1000 - origin)) / 1000.0
            if wait > 0.001:
                time.sleep(wait - 0.001)
            while time.perf_counter() * 1000 - origin < t_ev:  # 마지막 ~1ms만 스핀
                pass
            if self._stop_ev.is_set():
                break

            self.writer.send(freqs)
            cur = time.perf_counter() * 1000 - origin
            on_pos, on_freqs = self.on_position, self.on_freqs
            if on_pos:
                on_pos(cur)
            if on_freqs:
                on_freqs(freqs)
            idx += 1

        self.writer.stop()
        if not self._stop_ev.is_set():
            on_stopped = self.on_stopped
            if on_stopped:
                on_stopped()
            self._log("재생 완료")


if __name__ == "__main__":
    class _StubWriter:
        def __init__(self):
            self.sent = []

        def send(self, freqs):
            self.sent.append(freqs)

        def stop(self):
            pass

    w = _StubWriter()
    p = Player(w)
    positions = []
    p.on_position = lambda ms: positions.append(ms)
    p.load([(0, [440], 10), (10, [220], 10), (20, [0], 10)], 30)
    p.play()
    time.sleep(0.2)
    assert w.sent == [[440], [220], [0]], w.sent
    assert len(positions) == 3
    print("player self-check ok:", w.sent, positions)
