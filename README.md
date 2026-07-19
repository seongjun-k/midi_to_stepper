# MIDI to Stepper — Web Player

MIDI를 스텝모터 구동음으로 연주하는 시스템입니다. 유튜브 뮤직에서 곡을 검색하면 음원을 내려받아 AI(basic-pitch)로 MIDI를 추출하고, 음역대별로 모터에 분배해 실시간 연주합니다. MIDI 파일 직접 업로드도 지원합니다.

- **서버**: FastAPI + WebSocket (우분투, 아두이노/ESP32가 USB로 연결된 PC에서 실행)
- **프론트**: `static/index.html` 단일 파일 — LAN의 브라우저(태블릿 키오스크 포함)에서 접속
- **펌웨어**: ESP32 LEDC 하드웨어 PWM (`firmware/stepper_ledc/`)
- 구 tkinter/pygame GUI는 `legacy/`에 보존 (설계 문서: `docs/PLAN.md`)

## 폴더 구조

```text
midi_to_stepper/
├── server.py               # FastAPI 서버 (REST + WebSocket)
├── core/                   # 파싱·재생·시리얼·유튜브 파이프라인
├── static/index.html       # 웹 UI 전체 (빌드 불필요)
├── firmware/stepper_ledc/  # ESP32용 펌웨어 (LEDC PWM)
├── src/serial_stepper/     # (구) Arduino Mega 펌웨어
├── legacy/                 # (구) 데스크톱 GUI 2종
├── bench/                  # 파싱 벤치마크
└── docs/PLAN.md            # 웹 전환 계획서·결정 기록
```

## 요구 사항

- Ubuntu, **Python 3.11** (3.12는 basic-pitch 미지원), `ffmpeg`, `arduino-cli`
- ESP32-S3 등 ESP32 계열 보드 + A4988/DRV8825 드라이버 + NEMA17 스텝모터
  - ESP32는 3.3V 로직 — 드라이버 로직 전원(VDD)은 3.3V로 급전할 것

## 설치

```bash
cd midi_to_stepper

# Python 3.11 venv (시스템이 3.12뿐이면 conda로 3.11 확보)
conda create -n stepper311 python=3.11 -y
~/miniconda3/envs/stepper311/bin/python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# ESP32 보드 코어
arduino-cli core install esp32:esp32

# 시리얼 권한 (최초 1회, 재로그인 필요)
sudo usermod -aG dialout $USER
```

## 실행

```bash
.venv/bin/python server.py          # 실제 모드
.venv/bin/python server.py --demo   # 데모 모드 (하드웨어 없이 UI 테스트)
```

- 같은 PC: http://localhost:8000
- LAN(태블릿 등): `http://<서버IP>:8000`
- 시리얼 포트는 `/dev/ttyACM*`, `/dev/ttyUSB*` 자동 탐지. 설정은 우측 상단 기어(⚙) 패널에서: 시리얼 연결, MIDI 업로드, 펌웨어 컴파일/업로드, 모터 수 변경, 로그
- 설정 파일 `config.json`은 첫 실행 시 자동 생성 (모터 수 기본 6)

## 펌웨어

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3 firmware/stepper_ledc
arduino-cli upload -p /dev/ttyACM0 --fqbn esp32:esp32:esp32s3 firmware/stepper_ledc
```

핀맵은 `stepper_ledc.ino` 상단 상수 — **보드 확정 후 수정 필요** (현재 placeholder).
웹 UI 설정 패널에서도 컴파일/업로드 가능합니다.

### 시리얼 프로토콜 (PC ↔ 보드, 115200 baud)

- `F262,440,131,196,330,523\n` — 모터별 주파수(Hz), 개수 부족분은 정지
- `S\n` — 전체 정지
- 100~4000Hz 범위 밖은 펌웨어가 자동 REST 처리 (모터 보호)

## 라이선스

MIT
