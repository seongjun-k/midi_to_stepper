# 계획서: midi_to_stepper 웹 전환 + 최적화 + UI 재설계 + MCU 교체

작성: 2026-07-19 · 브랜치: `web-ui`

> **진행 현황 (2026-07-20)**: 1~6단계 완료. 파싱 12.9배 개선(499→39ms, 결과 동일성 검증), 데모 모드 REST·WS·브라우저 렌더링 확인, ESP32-S3 컴파일 통과. 미완: 실기 하드웨어 테스트(보드 확정 대기), 유튜브 변환 경로 종단 테스트(네트워크 필요), 커밋.

## 확정된 결정

| 항목 | 결정 |
|------|------|
| 전환 대상 | `midi_player.py` + `stepper_midi_kiosk.py` 두 GUI를 웹 앱 하나로 통합, 기존 GUI는 `legacy/`로 이동 |
| 접속 형태 | LAN 접속 (우분투 PC에서 서버 실행, 태블릿·폰·노트북 브라우저에서 사용 — 박람회 태블릿 키오스크 겸용) |
| 프론트 스택 | 순수 HTML/JS 단일 파일 (`static/index.html`), 빌드 도구 없음 |
| 모터 개수 | config로 가변 (기본 6) |
| MCU | Mega 폐기 → ESP32-S3 등으로 교체 예정 (보드 미확정, 펌웨어를 보드 독립적으로 재작성) |
| 개발 OS | Windows → Ubuntu |

## 목표

- tkinter/pygame GUI 폐기, FastAPI 백엔드 + 단일 HTML 프론트로 통합
- MIDI 파싱·재생 타이밍 최적화 (수치 측정 포함)
- UI 전면 재설계 (다크 "하드웨어 콘솔" 테마, 태블릿 대응)
- 펌웨어를 ESP32 LEDC 하드웨어 PWM 기반으로 재작성, 시리얼 프로토콜은 유지

## 1. 아키텍처

```
midi_to_stepper/
├── server.py              # FastAPI 앱 + WebSocket
├── core/
│   ├── config.py          # config.json 로드: num_motors, serial, baud, fqbn, bands
│   ├── midi.py            # parse_midi / auto_bands / 템포맵 (midi_player.py에서 이식+최적화)
│   ├── player.py          # 재생 스레드 (콜백 → WebSocket 브로드캐스트)
│   ├── serial_link.py     # SerialWriter + /dev/ttyACM*·ttyUSB* 자동 탐지 + FakeSerial(데모)
│   └── youtube.py         # 검색/yt-dlp/basic-pitch/캐시/원곡 우선 정렬 (키오스크에서 이식)
├── static/index.html      # 프론트 전체 (HTML+CSS+JS 인라인)
├── firmware/
│   └── stepper_ledc/stepper_ledc.ino   # ESP32용 신규 펌웨어
├── legacy/                # 기존 midi_player.py, stepper_midi_kiosk.py
└── docs/PLAN.md           # 이 문서
```

### 시리얼 프로토콜 (불변 — PC/펌웨어 간 계약)

- `F<f0>,<f1>,...\n` : 모터별 주파수(Hz). 개수가 펌웨어 채널보다 적으면 나머지 REST
- `S\n` : 전체 정지
- 주파수 가드(100~4000Hz 밖 REST)는 펌웨어에서 유지

이 계약 덕에 보드 교체·모터 수 변경이 PC 코드에 영향 없음.

### API / WebSocket

- REST: MIDI 업로드·파싱, 유튜브 검색·로드(백그라운드 작업), play/pause/stop/seek, 시리얼 연결/해제, arduino-cli 컴파일·업로드, config 조회/수정
- WS `/ws`: 재생 위치, 모터별 주파수, 변환 진행률, 상태 — ~30Hz 스로틀

## 2. 최적화 (코드에서 확인한 지점)

| 대상 | 현재 문제 (file:line은 legacy 기준) | 개선 |
|------|------|------|
| 템포 변환 | `_ticks_to_ms`가 메시지마다 템포맵 전체 순회 (`midi_player.py:112`) | 템포 구간별 누적 ms 사전계산 + 이진탐색 |
| 이벤트 빌드 | 시간 슬롯마다 전 트랙·전 노트 재스캔 (`midi_player.py:155`) — O(n²) | 노트 on/off 스위프 한 번으로 O(n log n) |
| 재생 타이밍 | `while … pass` busy-wait (`midi_player.py:270`)가 코어 점유 | sleep 기반 + 마지막 ~1ms만 스핀 |
| 변환 파이프라인 | basic-pitch(10~30초) 블로킹, 진행률 하드코딩 | 백그라운드 작업 + 실제 진행률 WS 푸시, mp3/mid 캐시 유지 |
| 상태 푸시 | 이벤트마다 UI 갱신 | 시리얼은 즉시, 화면 푸시만 ~30Hz 스로틀 |
| 펌웨어 지터 | Mega `micros()` 소프트웨어 토글 — 6모터 동시 구동 시 지터 | ESP32 LEDC 하드웨어 PWM (아래 §4) |

검증: 파싱 최적화는 동일 MIDI 파일로 전후 시간 측정해 수치 보고.

## 3. UI 재설계 (자유 개선 위임)

현재 문제: 라이트 tkinter 카드 UI와 앰버 레트로 pygame이 따로 놀고, 데스크톱 고정 레이아웃.

새 방향 — **"하드웨어 콘솔" 다크 테마 단일 디자인**:

- 다크 배경 + 모터별 고정 액센트 컬러, 주파수·시간은 모노스페이스 — 장비 패널 느낌
- 메인 화면에 모터별 실시간 시각화 (스크롤링 피아노롤/파형 스타일, Canvas 직접 그리기, 라이브러리 없음) — 박람회 "보는 재미" 담당
- 관람객 흐름: 검색 → 결과(원곡 우선, 앨범아트) → 진행률 → 연주 화면. 터치 타겟 크게 (태블릿 기준)
- 반응형: 태블릿 세로/가로, 데스크톱 대응
- 관리 기능(시리얼 연결, MIDI 업로드, 펌웨어 업로드, config, 로그)은 설정 패널로 분리 — 관람객 화면에서 숨김

구현 후 데모 모드로 먼저 시연 → 피드백 반영.

## 4. 펌웨어: Mega → ESP32

- **LEDC 하드웨어 PWM**: 채널당 주파수 설정만 하면 펄스를 하드웨어가 생성. `micros()` 폴링 제거 → 지터 제로, 음 변경은 `ledcChangeFrequency()` 한 줄, loop에는 시리얼 수신만 남음
- ESP32-S3 기준 LEDC 8채널 → 최대 8모터 (config 가변과 맞물림)
- 핀맵·채널 수·보드 정보는 펌웨어 상단 상수 — 보드 확정 시 핀맵만 채움
- 시리얼 프로토콜·주파수 가드는 기존과 동일하게 유지
- arduino-cli: fqbn 하드코딩(`arduino:avr:mega`) → config 이동, `esp32:esp32` 코어 설치를 준비 단계에 포함
- S3 네이티브 USB → `/dev/ttyACM*` 자동 탐지로 커버
- **배선 주의**: ESP32는 3.3V 로직. A4988/DRV8825의 로직 전원(VDD)을 3.3V로 급전할 것 (5V VDD + 3.3V STEP 신호는 A4988에서 마지널)
- WiFi 전송은 보류 — 박람회장 무선 불안정, USB 시리얼 유지. 필요 시 전송 계층만 교체

## 5. 실행 단계

1. **준비**: ~~클론, `web-ui` 브랜치~~(완료), venv 생성(3.12 우선), 패키지 설치: `fastapi uvicorn mido pyserial ytmusicapi yt-dlp basic-pitch`. basic-pitch 설치 실패 시 Python 3.11 venv 또는 onnx 대체 후 결정 기록. `arduino-cli core install esp32:esp32`
2. **코어 이식 + 최적화**: 로직을 `core/`로 분리하며 §2 파싱·타이밍 최적화 적용, 파싱 벤치마크 기록
3. **백엔드**: FastAPI 라우트 + WS + 데모 모드(FakeSerial), 변환 백그라운드 작업화
4. **프론트**: §3 디자인으로 `static/index.html` — 관람객 화면 + 설정 패널
5. **펌웨어**: `firmware/stepper_ledc/` ESP32 LEDC 버전 작성 (보드 확정 전엔 핀맵 placeholder + 컴파일 확인까지)
6. **정리**: 기존 GUI를 `legacy/`로 이동, README 갱신 (우분투 기준 설치·실행법)
7. **검증**:
   - 데모 모드: 브라우저에서 MIDI 재생·시크·모터 시각화 동작 확인
   - 파싱 전후 시간 비교 수치
   - 펌웨어: ESP32 fqbn으로 컴파일 통과
   - 실기 테스트(보드+드라이버 배선)는 하드웨어 준비 후 사용자와 진행

## 6. 리스크 / 보류

- ~~basic-pitch ↔ Python 3.12 호환 불확실~~ → **실측 결과 3.12 불가** (구버전 numpy 소스 빌드 실패). 해결: conda로 Python 3.11 venv(`.venv311`) 구성 + `setuptools<81` 고정(resampy가 pkg_resources 요구) → import 확인 완료. 서버 런타임은 3.11 venv 기준.
- 보드 미확정 — 펌웨어는 핀맵 상수만 비워두고 진행, 실기 검증은 보드 확정 후
- 원격 push는 요청 시에만
