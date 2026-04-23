// serial_stepper.ino
// PC에서 "F262,440,131,196\n" 형식으로 주파수 전송
// 한 번만 업로드하면 끝

#define NUM_MOTORS   4
#define FREQ_MIN     100   // Hz 하한 (100Hz 미만은 REST 처리)
#define FREQ_MAX     4000  // Hz 상한 (스텝모터 물리 한계)

const byte stepPins[NUM_MOTORS] = { 2,  3,  4, 12};
const byte  dirPins[NUM_MOTORS] = { 5,  6,  7, 13};

struct Motor {
  unsigned long lastToggle;
  unsigned long halfPeriod;   // 0 = REST
  bool          stepState;
};
Motor motors[NUM_MOTORS];

// ── Serial 수신 버퍼 ─────────────────────────────────────────
char    rxBuf[64];
uint8_t rxLen = 0;

// ── 주파수 검증 + 즉시 적용 ──────────────────────────────────
void applyFreq(byte m, uint16_t freq) {
  digitalWrite(stepPins[m], LOW);
  motors[m].stepState  = false;
  motors[m].lastToggle = micros();

  // 하한/상한 범위 벗어나면 REST
  if (freq < FREQ_MIN || freq > FREQ_MAX) {
    motors[m].halfPeriod = 0;
    return;
  }

  motors[m].halfPeriod = 500000UL / freq;
}

// ── 명령 파싱 ────────────────────────────────────────────────
// "F262,440,131,196"  → 4모터 주파수 설정
// "S"                 → 전체 정지
void handleCmd(char* cmd) {
  if (cmd[0] == 'S') {
    for (byte i = 0; i < NUM_MOTORS; i++) applyFreq(i, 0);
    return;
  }
  if (cmd[0] == 'F') {
    uint16_t freqs[NUM_MOTORS] = {0, 0, 0, 0};
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

// ── 논블로킹 모터 토글 ───────────────────────────────────────
void updateMotors() {
  unsigned long now = micros();
  for (byte i = 0; i < NUM_MOTORS; i++) {
    if (motors[i].halfPeriod == 0) continue;
    if ((unsigned long)(now - motors[i].lastToggle)
        >= motors[i].halfPeriod) {
      motors[i].stepState = !motors[i].stepState;
      digitalWrite(stepPins[i], motors[i].stepState);
      motors[i].lastToggle += motors[i].halfPeriod;  // 오차 누적 방지
    }
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
    motors[i] = {0, 0, false};
    pinMode(stepPins[i], OUTPUT);
    pinMode(dirPins[i],  OUTPUT);
    digitalWrite(stepPins[i], LOW);
    digitalWrite(dirPins[i],  HIGH);
  }
  Serial.begin(115200);
  Serial.println("READY");
}

void loop() {
  updateMotors();   // 항상 최우선
  readSerial();
}