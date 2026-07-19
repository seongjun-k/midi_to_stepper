"""SerialWriter 이식 (midi_player.py) + 포트 자동 탐지 + 데모용 FakeSerial.

시리얼 프로토콜은 불변: "F<f0>,<f1>,...\\n" / "S\\n" (docs/PLAN.md).
"""
import queue
import threading
import time

import serial
import serial.tools.list_ports


def detect_port():
    """/dev/ttyACM*, /dev/ttyUSB* 자동 탐지. 없으면 빈 문자열."""
    ports = sorted(p.device for p in serial.tools.list_ports.comports()
                    if "ttyACM" in p.device or "ttyUSB" in p.device)
    return ports[0] if ports else ""


class FakeSerial:
    """데모 모드용 no-op 시리얼. write/close는 로그만 남긴다."""

    def __init__(self, on_log=None):
        self.is_open = True
        self._on_log = on_log

    def write(self, data):
        if self._on_log:
            self._on_log(f"[FAKE SERIAL] {data!r}")

    def close(self):
        self.is_open = False

    def readline(self):
        return b""


class SerialWriter:
    def __init__(self):
        self.ser = None
        self.q = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def connect(self, port, baud=115200, fake=False, on_log=None):
        if fake:
            self.ser = FakeSerial(on_log)
            return True
        try:
            self.ser = serial.Serial(port, baud, timeout=1)
            time.sleep(2)
            self.ser.readline()
            return True
        except Exception as e:
            self.ser = None
            return str(e)

    def disconnect(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def send(self, freqs):
        self.q.put(("F" + ",".join(str(f) for f in freqs) + "\n").encode())

    def stop(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.q.put(b"S\n")

    def _loop(self):
        while True:
            data = self.q.get()
            try:
                if self.ser and self.ser.is_open:
                    self.ser.write(data)
            except Exception:
                pass


if __name__ == "__main__":
    w = SerialWriter()
    assert w.connect("x", fake=True) is True
    w.send([440, 0, 220])
    w.stop()
    time.sleep(0.1)
    w.disconnect()
    assert w.ser is None
    print("serial_link self-check ok, detect_port() =", detect_port())
