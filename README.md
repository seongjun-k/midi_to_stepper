# MIDI to Stepper Motor Player

MIDI 파일을 실시간으로 4채널 스텝모터 소리로 변환해서 연주하는 프로젝트입니다.  
Python GUI에서 MIDI를 재생하면, 주파수로 변환된 신호가 Arduino로 전송되고 CNC 쉴드(또는 드라이버)로 모터를 구동합니다.

---

## 폴더 구조

```text
midi_to_stepper/
├── src/
│   ├── midi_player.py        # Python GUI 플레이어
│   └── serial_stepper/
│       └── serial_stepper.ino  # Arduino 펌웨어
├── .gitignore
└── .venv/                    # Python 가상환경 (Git에서 제외)
```

---

## 요구 사항

### 하드웨어

- Arduino Mega 2560 (또는 UNO, 보드 이름만 수정하면 됨)
- A4988/DRV8825 등 스텝모터 드라이버 × 4
- NEMA17 스텝모터 × 4
- 12V SMPS 전원 (모터용)
- CNC 쉴드 또는 브레드보드 배선

### 소프트웨어

- Python 3.10 이상
- `arduino-cli`
- 다음 Python 패키지:
  - `mido`
  - `pyserial`
  - (기본 내장: `tkinter`)

---

## 설치

### 1. 가상환경 생성 및 패키지 설치

```bash
cd midi_to_stepper

# (선택) 가상환경
python -m venv .venv
# Windows
.\.venv\Scripts\activate

pip install mido pyserial
```

### 2. arduino-cli 설치

- https://arduino.github.io/arduino-cli/latest/ 에서 설치 방법 참고
- 첫 설정:

```bash
arduino-cli config init
arduino-cli core update-index
arduino-cli core install arduino:avr
```

---

## 핀 배선

`serial_stepper.ino` 기준 핀맵입니다.

| 모터 | STEP 핀 | DIR 핀 |
|------|---------|--------|
| M0   | 2       | 5      |
| M1   | 3       | 6      |
| M2   | 4       | 7      |
| M3   | 12      | 13     |

- DIR 핀은 모두 같은 방향(HIGH)으로 설정되어 있습니다.
- 필요하면 펌웨어에서 보드에 맞게 수정할 수 있습니다.

---

## Arduino 펌웨어

소스 위치: `src/serial_stepper/serial_stepper.ino`

### 프로토콜

- Python → Arduino: `"F262,440,131,196\n"` 형식으로 4개 모터의 주파수(Hz)를 전송
- `"S\n"`: 모든 모터 정지 (REST)

### 주파수 가드

- `FREQ_MIN = 100Hz` 미만, `FREQ_MAX = 4000Hz` 초과는 자동으로 REST 처리
- 너무 낮은/높은 주파수에서 모터가 무리하지 않도록 보호

---

## Python GUI 플레이어

소스 위치: `src/midi_player.py`

### 기능

- MIDI 파일 선택 및 파싱
- 곡의 실제 음역을 분석해서 4개 모터에 **자동으로 음역대 분배**
- 재생 / 일시정지 / 정지
- 시크바 드래그로 구간 이동
- 모터별 현재 음표 / 주파수 표시
- 창 크기에 따라 모터 카드가 자동으로 줄 바꿈
- `arduino-cli`를 통해 GUI에서 바로 **컴파일 / 업로드**

### 실행 방법

```bash
cd midi_to_stepper
.\.venv\Scripts\activate        # 가상환경 활성화 (선택)
python .\src\midi_player.py
```

실행 후 순서:

1. 왼쪽 상단에서 Arduino 포트(예: `COM5`) 입력 후 **연결** 버튼 클릭
2. **펌웨어 업로드** 카드에서 `serial_stepper/serial_stepper.ino` 선택
3. 필요하면 `컴파일만` 또는 `업로드 🚀` 버튼 사용
4. `MIDI 파일` 카드에서 `.mid` 파일 선택
5. 하단 **재생** 버튼으로 연주 시작

---

## 주의사항

- `midi/` 폴더의 원본 MIDI 파일은 용량을 줄이기 위해 Git에 포함하지 않았습니다.
- OneDrive 같은 동기화 폴더에 있을 경우, 경로에 공백/한글이 포함되어 있어  
  `arduino-cli` 사용 시 따옴표로 감싸는 것이 안전합니다.
- 스텝모터와 드라이버는 발열이 생길 수 있으니, 처음에는 낮은 전류로 테스트하세요.

---

## 라이선스

MIT
