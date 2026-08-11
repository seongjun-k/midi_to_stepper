// MKS Robin Nano v1.2 (STM32F103VET6 @72MHz) — 스텝모터 MIDI 연주 펌웨어
//
// 시리얼 프로토콜은 ESP32판(firmware/stepper_ledc)과 동일:
//   "F<f0>,<f1>,...\n"  모터별 주파수(Hz). 부족분은 REST
//   "S\n"               전체 정지
//
// ── 왜 LEDC 방식을 못 쓰는가 ────────────────────────────────────────────
// ESP32는 핀마다 독립 하드웨어 PWM(LEDC)이 붙는다. STM32F103은 타이머 출력이
// 고정 핀에만 나오고 remap도 타이머 단위라, 이 보드의 STEP 핀 5개 중
// 하드웨어 PWM이 가능한 건 PB5(TIM3_CH2 partial remap)와 PA6(TIM3_CH1 no remap)
// 둘뿐이고 같은 TIM3라 동시 사용도 안 된다. PE3/PE0/PD6은 아예 타이머 채널이 아니다.
// 그래서 타이머 인터럽트 하나로 5채널을 소프트웨어 생성한다(DDS).
//
// ── DDS 방식 ────────────────────────────────────────────────────────────
// ISR_HZ로 도는 인터럽트에서 채널별 32비트 위상누산기에 inc를 더하고,
// 누산기 최상위 비트를 그대로 STEP 핀에 출력한다 → 평균 주파수가 정확한 구형파.
// 에지가 ISR 격자에 걸리므로 최대 1/ISR_HZ 만큼 에지 지터가 생기지만
// 음정(평균 주파수)은 누산기 정밀도만큼 정확하다.

#include <Arduino.h>

// ── 튜닝 노브 ───────────────────────────────────────────────────────────
#define NUM_MOTORS   5      // 드라이버 슬롯 X/Y/Z/E0/E1
#define FREQ_MIN     100    // Hz 하한 (미만은 REST). 60까지 낮추면 저음이 늘지만
                            // 저속에서 토크가 떨어져 탈조하기 쉬우니 실기로 확인할 것
#define FREQ_MAX     4000   // Hz 상한 (스텝모터 물리 한계)
#define ISR_HZ       100000UL  // DDS 샘플링. 높을수록 에지 지터가 줄고 CPU를 더 먹는다
                               // (5채널 기준 72MHz의 15% 안팎 추정)
#define EN_ACTIVE_LOW 1     // A4988/DRV8825/TMC 모두 EN은 액티브 로우

// ── 핀맵 (Marlin pins_MKS_ROBIN_NANO_common.h 기준) ─────────────────────
//        X     Y     Z     E0    E1
// STEP   PE3   PE0   PB5   PD6   PA6
// DIR    PE2   PB9   PB4   PD3   PA1
// EN     PE4   PE1   PB8   PB3   PA3
static const uint8_t dirPins[NUM_MOTORS] = { PE2, PB9, PB4, PD3, PA1 };
static const uint8_t  enPins[NUM_MOTORS] = { PE4, PE1, PB8, PB3, PA3 };
static const uint8_t stepPins[NUM_MOTORS] = { PE3, PE0, PB5, PD6, PA6 };

// ISR 안에서는 digitalWrite()가 너무 느리다(수 us). 포트/마스크를 미리 박아두고
// BSRR에 직접 쓴다 — set은 하위 16비트, reset은 상위 16비트.
static GPIO_TypeDef* const stepPort[NUM_MOTORS] = { GPIOE, GPIOE, GPIOB, GPIOD, GPIOA };
static const uint32_t      stepMask[NUM_MOTORS] = { 1u << 3, 1u << 0, 1u << 5, 1u << 6, 1u << 6 };

// ── DDS 상태 ────────────────────────────────────────────────────────────
// inc는 main이 쓰고 ISR이 읽는다. Cortex-M3에서 32비트 정렬 접근은 단일 명령이라
// 찢어진 값이 읽힐 일이 없어 크리티컬 섹션 없이 volatile로 충분하다.
static volatile uint32_t phaseInc[NUM_MOTORS];
static uint32_t          phaseAcc[NUM_MOTORS];

static HardwareTimer *ddsTimer;

// 누산기 MSB를 STEP 핀에 그대로 출력. inc=0인 채널은 누산기가 멈춰 있으므로
// 분기 없이 자연스럽게 LOW를 유지한다(REST).
static void ddsISR() {
  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    phaseAcc[i] += phaseInc[i];
    if (phaseAcc[i] & 0x80000000UL) stepPort[i]->BSRR = stepMask[i];
    else                            stepPort[i]->BSRR = stepMask[i] << 16;
  }
}

// ── 주파수 적용 ─────────────────────────────────────────────────────────
static void applyFreq(uint8_t m, uint16_t freq) {
  if (freq < FREQ_MIN || freq > FREQ_MAX) {   // 범위 밖은 조용히 REST
    phaseInc[m] = 0;
    phaseAcc[m] = 0;
    stepPort[m]->BSRR = stepMask[m] << 16;    // 핀 LOW 보장
    return;
  }
  // inc = freq * 2^32 / ISR_HZ. 64비트로 계산해야 오버플로가 안 난다.
  phaseInc[m] = (uint32_t)(((uint64_t)freq << 32) / ISR_HZ);
}

// ── 명령 파싱 (ESP32판과 동일) ──────────────────────────────────────────
static char    rxBuf[64];
static uint8_t rxLen = 0;

static void handleCmd(char *cmd) {
  if (cmd[0] == 'S') {
    for (uint8_t i = 0; i < NUM_MOTORS; i++) applyFreq(i, 0);
    return;
  }
  if (cmd[0] == 'F') {
    uint16_t freqs[NUM_MOTORS] = {0};
    uint8_t  idx = 0;
    char    *p   = cmd + 1;
    while (*p && idx < NUM_MOTORS) {
      freqs[idx++] = (uint16_t)atoi(p);
      while (*p && *p != ',') p++;
      if (*p == ',') p++;
    }
    for (uint8_t i = 0; i < NUM_MOTORS; i++) applyFreq(i, freqs[i]);
  }
}

static void readSerial() {
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
  // PB3(E0_EN)와 PB4(Z_DIR)는 기본이 JTAG 핀(JTDO/NJTRST)이다. JTAG를 끄지 않으면
  // 이 둘이 GPIO로 동작하지 않는다. SWD는 남겨 디버깅 여지를 둔다.
  __HAL_RCC_AFIO_CLK_ENABLE();
  __HAL_AFIO_REMAP_SWJ_NOJTAG();

  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    pinMode(stepPins[i], OUTPUT);
    pinMode(dirPins[i], OUTPUT);
    pinMode(enPins[i], OUTPUT);

    digitalWrite(stepPins[i], LOW);
    // ponytail: DIR 고정 — 모터가 한 방향으로 계속 돈다. 축(리드스크류)에 물린
    // 상태면 끝까지 밀고 가 파손되니 노출 모터 전용이다. 제자리 연주가 필요하면
    // 일정 스텝마다 DIR을 뒤집는 로직을 추가할 것.
    digitalWrite(dirPins[i], HIGH);
    digitalWrite(enPins[i], EN_ACTIVE_LOW ? LOW : HIGH);   // 드라이버 활성화

    phaseInc[i] = 0;
    phaseAcc[i] = 0;
  }

  // TIM4를 순수 인터럽트 소스로만 쓴다(출력 채널을 핀에 붙이지 않으므로
  // TIM4 기본 핀 PB6~PB9와 충돌하지 않는다).
  ddsTimer = new HardwareTimer(TIM4);
  ddsTimer->setOverflow(ISR_HZ, HERTZ_FORMAT);
  ddsTimer->attachInterrupt(ddsISR);
  ddsTimer->resume();

  Serial.begin(115200);
  Serial.println("READY");
}

void loop() {
  readSerial();   // 펄스 생성은 전부 ISR이 담당. loop는 수신·파싱만.
}
