#!/usr/bin/env python3
"""FastAPI 백엔드 + WebSocket. (docs/PLAN.md §1)

실행:
  .venv/bin/python server.py [--demo]

--demo 플래그를 주면 실제 시리얼 대신 FakeSerial을 사용한다 (하드웨어 없이 UI 테스트).

MIDI 업로드(/api/midi)는 python-multipart가 이 venv에 설치되어 있지 않아
FastAPI의 UploadFile(멀티파트)을 쓸 수 없다 — "의존성 추가 금지" 제약 때문에
새 패키지를 넣는 대신 Starlette Request.body()로 원시 바이트를 그대로 받는다
(클라이언트는 FormData 없이 body에 파일 바이트를 그대로 fetch하면 됨).
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

import serial.tools.list_ports
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from core import config as cfg_mod
from core import midi as midi_mod
from core import youtube as yt_mod
from core.player import Player
from core.serial_link import FakeSerial, SerialWriter, detect_port

DEMO_MODE = "--demo" in sys.argv

app = FastAPI()

CONFIG = cfg_mod.load()
writer = SerialWriter()
player = Player(writer)

STATE = {
    "events": [],
    "total_ms": 0.0,
    "bands": CONFIG.get("bands"),
    "loaded_title": "",
    "connected": False,
    "port": CONFIG.get("serial_port", ""),
}

_clients = set()
_loop = None  # startup에서 채워짐 — 플레이어 스레드 콜백을 asyncio로 마샬링하는 데 사용

PUSH_INTERVAL = 1.0 / 30  # 화면 푸시만 ~30Hz 스로틀 (시리얼 전송은 즉시, player.py에서 처리)
_last_push_t = [0.0]
_allow_push = [True]


def _broadcast(msg):
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast_async(msg), _loop)


async def _broadcast_async(msg):
    data = json.dumps(msg)
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _on_position(ms):
    now = time.monotonic()
    _allow_push[0] = (now - _last_push_t[0]) >= PUSH_INTERVAL
    if _allow_push[0]:
        _last_push_t[0] = now
        _broadcast({"type": "position", "ms": ms})


def _on_freqs(freqs):
    # on_position 직후 같은 이벤트에서 호출되므로 같은 틱의 스로틀 판정을 재사용한다.
    if _allow_push[0]:
        _broadcast({"type": "freqs", "freqs": freqs})


def _on_stopped():
    _broadcast({"type": "state", "playing": False})


def _on_log(msg):
    _broadcast({"type": "log", "msg": msg})


player.on_position = _on_position
player.on_freqs = _on_freqs
player.on_stopped = _on_stopped
player.on_log = _on_log


def _resolve_cache_dir():
    return cfg_mod.resolve_cache_dir(CONFIG)


STATIC_DIR = os.path.join(cfg_mod.PROJECT_ROOT, "static")


@app.on_event("startup")
async def on_startup():
    global _loop
    _loop = asyncio.get_event_loop()
    if DEMO_MODE:
        writer.connect("demo", CONFIG.get("baud", 115200), fake=True, on_log=_on_log)
        STATE["connected"] = True
        STATE["port"] = "demo (FakeSerial)"
    # basic-pitch 모델 예열 — TF 임포트+모델 로드(~2s)를 첫 변환 요청 밖으로 이동
    threading.Thread(target=yt_mod.preload_model, daemon=True).start()


# 단일 HTML 앱이라 브라우저(특히 모바일)가 index.html을 캐시하면 갱신이 안 된다.
# no-store로 매 요청 새로 받게 한다. (정적 자산 분리 전까지 이 방식으로 충분)
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers=_NO_CACHE)
    return "<h1>midi_to_stepper</h1><p>static/index.html 준비 중 (프론트 작업 대기)</p>"


@app.get("/api/state")
async def get_state():
    return {
        "connected": STATE["connected"],
        "port": STATE["port"],
        "playing": player.is_alive and not player.is_paused,
        "paused": player.is_paused,
        "total_ms": STATE["total_ms"],
        "loaded_title": STATE["loaded_title"],
        "num_motors": CONFIG["num_motors"],
        "demo": DEMO_MODE,
    }


@app.get("/api/ports")
async def list_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


@app.post("/api/serial/connect")
async def serial_connect(payload: dict = Body(default={})):
    port = payload.get("port") or CONFIG.get("serial_port") or detect_port()
    result = writer.connect(port, CONFIG.get("baud", 115200), fake=DEMO_MODE, on_log=_on_log)
    STATE["connected"] = result is True
    STATE["port"] = port
    _broadcast({"type": "state", "connected": STATE["connected"], "port": port})
    if result is not True:
        raise HTTPException(400, str(result))
    return {"connected": True, "port": port}


@app.post("/api/serial/disconnect")
async def serial_disconnect():
    writer.disconnect()
    STATE["connected"] = False
    _broadcast({"type": "state", "connected": False})
    return {"connected": False}


def _load_events(path):
    n = CONFIG["num_motors"]
    bands = CONFIG.get("bands") or midi_mod.auto_bands(path, n)
    events, total_ms = midi_mod.parse_midi(path, bands, n)
    return events, total_ms, bands


@app.post("/api/midi")
async def upload_midi(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, "빈 파일입니다")
    filename = request.headers.get("x-filename", "upload.mid")
    tmp_path = os.path.join(tempfile.gettempdir(), f"upload_{int(time.time() * 1000)}_{filename}")
    with open(tmp_path, "wb") as f:
        f.write(data)
    try:
        events, total_ms, bands = _load_events(tmp_path)
    except Exception as e:
        raise HTTPException(400, f"MIDI 파싱 실패: {e}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    player.load(events, total_ms)
    STATE.update(events=events, total_ms=total_ms, bands=bands, loaded_title=filename)
    _broadcast({"type": "state", "loaded": True, "title": filename,
                "total_ms": total_ms, "events": len(events)})
    return {"events": len(events), "total_ms": total_ms, "bands": bands}


@app.get("/api/search")
async def api_search(q: str):
    try:
        return await asyncio.to_thread(yt_mod.search, q)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/library")
async def api_library():
    return yt_mod.list_library(_resolve_cache_dir())


@app.get("/api/thumb/{video_id}")
async def api_thumb(video_id: str):
    # 경로 traversal 차단: video_id는 영숫자/_/- 만 허용
    if not video_id or not video_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(404, "not found")
    path = yt_mod._find_thumb(_resolve_cache_dir(), video_id)
    if not path:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(path)


@app.post("/api/load")
async def api_load(payload: dict = Body(...)):
    video_id = payload.get("videoId", "")
    title = payload.get("title", "")
    artist = payload.get("artist", "")
    thumbnail = payload.get("thumbnail", "")
    if not video_id:
        raise HTTPException(400, "videoId 필요")

    def _job():
        try:
            def on_progress(ratio, msg):
                _broadcast({"type": "progress", "ratio": ratio, "msg": msg})

            cache_dir = _resolve_cache_dir()
            midi_path = yt_mod.download_and_convert(video_id, cache_dir, on_progress)
            events, total_ms, bands = _load_events(midi_path)
            player.load(events, total_ms)
            yt_mod.save_library_entry(cache_dir, video_id, title, artist, thumbnail)
            loaded_title = f"{title} - {artist}" if artist else title
            STATE.update(events=events, total_ms=total_ms, bands=bands, loaded_title=loaded_title)
            _broadcast({"type": "state", "loaded": True, "title": loaded_title,
                        "total_ms": total_ms, "events": len(events)})
        except Exception as e:
            _broadcast({"type": "log", "msg": f"변환 실패: {e}"})

    threading.Thread(target=_job, daemon=True).start()
    return {"status": "started"}


@app.post("/api/play")
async def api_play(payload: dict = Body(default={})):
    if not STATE["events"]:
        raise HTTPException(400, "로드된 MIDI가 없습니다")
    player.play(start_ms=payload.get("start_ms", 0.0))
    return {"status": "playing"}


@app.post("/api/pause")
async def api_pause():
    player.pause()
    return {"status": "paused"}


@app.post("/api/resume")
async def api_resume():
    player.resume()
    return {"status": "resumed"}


@app.post("/api/stop")
async def api_stop():
    player.stop()
    return {"status": "stopped"}


@app.post("/api/seek")
async def api_seek(payload: dict = Body(...)):
    player.seek(payload.get("ms", 0.0))
    return {"status": "seeking"}


@app.get("/api/config")
async def get_config():
    return CONFIG


@app.put("/api/config")
async def put_config(payload: dict = Body(...)):
    CONFIG.update(payload)
    cfg_mod.save(CONFIG)
    return CONFIG


@app.post("/api/firmware/compile")
async def firmware_compile(payload: dict = Body(...)):
    ino_path = payload.get("ino_path", "")
    if not ino_path or not os.path.exists(ino_path):
        raise HTTPException(400, "ino 경로가 올바르지 않습니다")
    ino_dir = os.path.dirname(ino_path)
    fqbn = CONFIG.get("fqbn", "esp32:esp32:esp32s3")
    r = subprocess.run(["arduino-cli", "compile", "--fqbn", fqbn, ino_dir],
                        capture_output=True, text=True)
    return {"ok": r.returncode == 0, "stdout": r.stdout, "stderr": r.stderr}


@app.post("/api/firmware/upload")
async def firmware_upload(payload: dict = Body(...)):
    ino_path = payload.get("ino_path", "")
    if not ino_path or not os.path.exists(ino_path):
        raise HTTPException(400, "ino 경로가 올바르지 않습니다")
    ino_dir = os.path.dirname(ino_path)
    fqbn = CONFIG.get("fqbn", "esp32:esp32:esp32s3")
    port = STATE.get("port") or CONFIG.get("serial_port", "")

    r = subprocess.run(["arduino-cli", "compile", "--fqbn", fqbn, ino_dir],
                        capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "stage": "compile", "stdout": r.stdout, "stderr": r.stderr}

    writer.disconnect()
    r2 = subprocess.run(["arduino-cli", "upload", "-p", port, "--fqbn", fqbn, ino_dir],
                         capture_output=True, text=True)
    if r2.returncode != 0:
        return {"ok": False, "stage": "upload", "stdout": r2.stdout, "stderr": r2.stderr}

    time.sleep(2)
    result = writer.connect(port, CONFIG.get("baud", 115200), fake=DEMO_MODE, on_log=_on_log)
    STATE["connected"] = result is True
    return {"ok": True, "reconnected": STATE["connected"]}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()  # 클라 → 서버 메시지는 사용 안 함, 연결 유지용
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
