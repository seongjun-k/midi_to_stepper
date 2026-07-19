// stepper_ledc.ino
// ESP32 LEDC 하드웨어 PWM 기반 스텝모터 드라이버
// PC에서 "F262,440,131,196,330,523\n" 형식으로 주파수 전송 (기존 serial_stepper.ino와 프로토콜 동일)
// arduino-esp32 core 3.3.10 (LEDC v3 API: ledcAttach / ledcChangeFrequency / ledcWrite)

#define NUM_MOTORS   6      // 최대 8
#define FREQ_MIN     100    // Hz 하한 (100Hz 미만은 REST 처리)
#define FREQ_MAX     4000   // Hz 상한 (스텝모터 물리 한계)

#define LEDC_RES     8      // duty 분해능 (bit) — 4000Hz까지 충분한 여유
#define DUTY_50      128    // 50% duty (2^LEDC_RES / 2)

// TODO: 보드 확정 후 핀맵 수정 (ESP32-S3 안전 GPIO로 임시 배치, strapping/USB 핀 회피)
const byte stepPins[NUM_MOTORS] = { 1,  2,  4,  5,  6,  7};
const byte  dirPins[NUM_MOTORS] = { 8,  9, 10, 11, 12, 13};

bool motorActive[NUM_MOTORS];  // false = REST(duty 0)

// ── Serial 수신 버퍼 ─────────────────────────────────────────
char    rxBuf[64];
uint8_t rxLen = 0;

// ── 주파수 검증 + 즉시 적용 ──────────────────────────────────
void applyFreq(byte m, uint16_t freq) {
  // 하한/상한 범위 벗어나면 REST (duty 0으로 핀 LOW 보장)
  if (freq < FREQ_MIN || freq > FREQ_MAX) {
    ledcWrite(stepPins[m], 0);
    motorActive[m] = false;
    return;
  }

  ledcChangeFrequency(stepPins[m], freq, LEDC_RES);
  ledcWrite(stepPins[m], DUTY_50);
  motorActive[m] = true;
}

// ── 명령 파싱 ────────────────────────────────────────────────
// "F262,440,131,196,330,523"  → 모터별 주파수 설정 (부족분은 REST)
// "S"                         → 전체 정지
void handleCmd(char* cmd) {
  if (cmd[0] == 'S') {
    for (byte i = 0; i < NUM_MOTORS; i++) applyFreq(i, 0);
    return;
  }
  if (cmd[0] == 'F') {
    uint16_t freqs[NUM_MOTORS] = {0};
    byte     idx = 0;
    char*    p   = cmd + 1;
    while (*p && idx < NUM_MOTORS) {
      freqs[idx++] = (uint16_t)atoi(p);
      while (*p && *p != ',') p++;
      if (*p == ',') p++;
    }
    for (byte i = 0; i < NUM_MOTORS; i++) applyFreq(i, freqs[i]);
  }
}

// ── 논블로킹 Serial 수신 ─────────────────────────────────────
void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rxBuf[rxLen] = '\0';
      if (rxLen > 0) handleCmd(rxBuf);
      rxLen = 0;
    } else if (rxLen < sizeof(rxBuf) - 1) {
      rxBuf[rxLen++] = c;
    }
  }
}

void setup() {
  for (byte i = 0; i < NUM_MOTORS; i++) {
    pinMode(dirPins[i], OUTPUT);
    digitalWrite(dirPins[i], HIGH);

    ledcAttach(stepPins[i], FREQ_MIN, LEDC_RES);  // 채널은 코어가 핀별로 자동 할당
    ledcWrite(stepPins[i], 0);                    // 부팅 시 REST
    motorActive[i] = false;
  }
  Serial.begin(115200);
  Serial.println("READY");
}

void loop() {
  readSerial();   // 하드웨어 PWM이 토글을 전담하므로 loop는 수신·파싱만
}
