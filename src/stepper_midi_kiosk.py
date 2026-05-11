#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STEPPER MIDI KIOSK
박람회용 키오스크 - 유튜브 뮤직 검색 → 다운로드 → MIDI 변환 → 스텝모터 연주

실행 방법:
  python stepper_midi_kiosk.py          # 일반 모드 (아두이노 연결 필요)
  python stepper_midi_kiosk.py --demo   # 데모 모드 (아두이노 없이 UI만 테스트)
"""

import sys, os, io, time, threading, math, urllib.request, tempfile, random, re
from collections import deque
import pygame
import pygame.gfxdraw

# ── 데모 모드 감지 ────────────────────────────────────────────────────────────
DEMO_MODE = "--demo" in sys.argv
if DEMO_MODE:
    print("[데모 모드] 아두이노 연결 없이 UI 테스트 실행")

# ── 라이브러리 임포트 (없으면 설치 안내) ─────────────────────────────────────
def require(pkg, import_name=None):
    import importlib
    try:
        return importlib.import_module(import_name or pkg)
    except ImportError:
        print(f"[설치 필요] pip install {pkg}")
        sys.exit(1)

if not DEMO_MODE:
    ytmusic_mod = require("ytmusicapi")
    yt_dlp_mod  = require("yt_dlp", "yt_dlp")
    mido_mod    = require("mido")
else:
    try:
        from ytmusicapi import YTMusic
        import yt_dlp, mido
        HAS_FULL_LIBS = True
    except ImportError:
        HAS_FULL_LIBS = False

try:
    import serial as serial_mod
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

try:
    from basic_pitch.inference import predict as bp_predict
    HAS_BP = True
except ImportError:
    HAS_BP = False

if not DEMO_MODE:
    from ytmusicapi import YTMusic
    import yt_dlp, mido

# ── 설정 ──────────────────────────────────────────────────────────────────────
SERIAL_PORT  = "COM4"
SERIAL_BAUD  = 115200
NUM_MOTORS   = 4
SCREEN_W     = 1280
SCREEN_H     = 720
OSC_BUF_LEN  = 120
CACHE_DIR    = os.path.join(tempfile.gettempdir(), "stepper_midi_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# YouTube URL에서 videoId(11자) 추출
URL_PATTERN = re.compile(r"(?:v=|youtu\.be/|music\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})")

# 커버/라이브/리믹스 판별 키워드 (소문자)
NON_ORIGINAL_KEYWORDS = [
    "cover", "커버", "live", "라이브", "remix", "리믹스",
    "karaoke", "노래방", "instrumental", "mr", "반주",
    "acoustic", "acoustic ver", "piano ver", "version",
    "tribute", "parody", "패러디", "nightcore",
]

# ── 색상 팔레트 ───────────────────────────────────────────────────────────────
BG          = (8,  8,  7)
SURF        = (14, 13, 11)
SURF2       = (20, 19, 16)
BORDER      = (35, 33, 28)
AMBER       = (255, 170,  0)
AMBER_DIM   = ( 80,  50,  0)
AMBER_FAINT = ( 30,  20,  0)
ORANGE      = (255, 120,  0)
TEXT        = (220, 200, 150)
TEXT_MUTED  = (120, 110,  80)
TEXT_FAINT  = ( 60,  55,  40)
WHITE       = (240, 235, 220)
GREEN       = ( 80, 200,  80)
RED         = (200,  60,  60)
BLUE        = ( 80, 160, 255)
MOTOR_COLS  = [
    (255, 170,  0),
    (255, 120,  0),
    (255, 220,  0),
    (220,  80, 255),
]

# ── 데모용 가짜 데이터 ────────────────────────────────────────────────────────
DEMO_SONGS = [
    {"videoId": "demo_001", "title": "밤편지",
     "artists": [{"name": "아이유 (IU)"}], "album": {"name": "Palette"},
     "duration": "3:36", "thumbnails": []},
    {"videoId": "demo_002", "title": "Dynamite",
     "artists": [{"name": "BTS"}], "album": {"name": "BE"},
     "duration": "3:19", "thumbnails": []},
    {"videoId": "demo_003", "title": "LILAC",
     "artists": [{"name": "아이유 (IU)"}], "album": {"name": "LILAC"},
     "duration": "3:37", "thumbnails": []},
    {"videoId": "demo_004", "title": "Butter",
     "artists": [{"name": "BTS"}], "album": {"name": "Butter"},
     "duration": "2:44", "thumbnails": []},
    {"videoId": "demo_005", "title": "Celebrity",
     "artists": [{"name": "아이유 (IU)"}], "album": {"name": "Celebrity Single"},
     "duration": "3:02", "thumbnails": []},
]

def make_demo_song(length_ms=30000):
    scale = [262, 294, 330, 349, 392, 440, 494, 523, 587, 659, 698, 784, 880]
    song, durs = [], []
    t = 0
    while t < length_ms:
        slot = []
        for m in range(NUM_MOTORS):
            if random.random() < 0.3:
                slot.append(0)
            else:
                freq = scale[(t // 400 + m * 3) % len(scale)]
                freq = int(freq * random.uniform(0.98, 1.02))
                slot.append(freq)
        dur = random.choice([150, 200, 250, 300, 400])
        song.append(slot); durs.append(dur); t += dur
    return song, durs

# ── 유틸 ──────────────────────────────────────────────────────────────────────
def extract_video_id(query: str) -> str | None:
    """YouTube/YouTube Music URL에서 videoId 추출. 없으면 None 반환."""
    m = URL_PATTERN.search(query)
    return m.group(1) if m else None

def note_to_freq(note: int) -> int:
    if note <= 0: return 0
    return round(440.0 * (2.0 ** ((note - 69) / 12.0)))

def freq_to_name(f: int) -> str:
    if f == 0: return "REST"
    names = {65:"C2",73:"D2",82:"E2",87:"F2",98:"G2",110:"A2",123:"B2",
             131:"C3",147:"D3",165:"E3",175:"F3",196:"G3",220:"A3",247:"B3",
             262:"C4",294:"D4",330:"E4",349:"F4",392:"G4",440:"A4",494:"B4",
             523:"C5",587:"D5",659:"E5",698:"F5",784:"G5",880:"A5"}
    best = min(names.items(), key=lambda kv: abs(kv[0]-f))
    return best[1]

def fmt_duration(raw) -> str:
    """duration 필드를 '분:초' 문자열로 변환.
    - '데:30' 형태 문자열 → 그대로 반환
    - 정수(초) → 변환
    - None/빈값 → '' 반환
    """
    if not raw:
        return ""
    if isinstance(raw, int):
        return f"{raw // 60}:{raw % 60:02d}"
    return str(raw)

def extract_artist(r: dict) -> str:
    """
    ytmusicapi 결과에서 아티스트명 추출.
    타입별 우선순위: artists[0].name → author → channel → channelId → ''
    """
    artists = r.get("artists", [])
    if artists and isinstance(artists, list):
        name = artists[0].get("name", "") if isinstance(artists[0], dict) else str(artists[0])
        if name:
            return name
    for key in ("author", "channel", "channelId"):
        val = r.get(key, "")
        if val:
            return str(val)
    return ""

def is_non_original(title: str) -> bool:
    """제목에 커버/라이브/리믹스 키워드가 포함되면 True 반환."""
    lower = title.lower()
    return any(kw in lower for kw in NON_ORIGINAL_KEYWORDS)

def original_score(r: dict) -> int:
    """
    오리지널 곡 우선 정렬 점수 (낮을수록 상단).
    - resultType == 'song' → 0점 보너스
    - 제목에 non-original 키워드 → +10점 페널티
    - album 정보 있음 → -1점 보너스 (정식 발매 가능성)
    """
    score = 0
    if r.get("resultType") != "song":
        score += 5
    if is_non_original(r.get("title", "")):
        score += 10
    if r.get("album") and isinstance(r.get("album"), dict) and r["album"].get("name"):
        score -= 1
    return score

def draw_rounded_rect(surf, color, rect, radius, alpha=255):
    x, y, w, h = rect
    if alpha < 255:
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(s, (*color, alpha), (0, 0, w, h), border_radius=radius)
        surf.blit(s, (x, y))
    else:
        pygame.draw.rect(surf, color, rect, border_radius=radius)

def draw_text_centered(surf, text, font, color, cx, cy):
    rendered = font.render(text, True, color)
    surf.blit(rendered, rendered.get_rect(center=(cx, cy)))

def fetch_album_art(url: str, size=(180, 180)):
    try:
        req = urllib.request.urlopen(url, timeout=5)
        data = req.read()
        img = pygame.image.load(io.BytesIO(data))
        return pygame.transform.smoothscale(img, size)
    except:
        s = pygame.Surface(size); s.fill(SURF2); return s

def make_demo_album_art(size=(90, 90)):
    colors = [
        [(255,80,80),(180,40,40)], [(80,160,255),(40,80,180)],
        [(80,220,120),(40,140,60)], [(220,160,80),(140,90,30)],
        [(200,80,220),(120,40,140)],
    ]
    s = pygame.Surface(size)
    c1, c2 = random.choice(colors)
    s.fill(c2)
    pygame.draw.rect(s, c1, (size[0]//4, size[1]//4, size[0]//2, size[1]//2), border_radius=8)
    font = pygame.font.SysFont("arial", 28, bold=True)
    note = font.render("♪", True, (255,255,255))
    s.blit(note, (size[0]//2 - note.get_width()//2, size[1]//2 - note.get_height()//2))
    return s

# ── MIDI 파싱 ─────────────────────────────────────────────────────────────────
def parse_midi(path: str) -> list:
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    all_tracks = []
    for track in mid.tracks[:NUM_MOTORS]:
        notes, pending = [], {}
        tempo, tms = 500000, 0.0
        for msg in track:
            tms += msg.time * tempo / tpb / 1000.0
            if msg.type == 'set_tempo':
                tempo = msg.tempo
            elif msg.type == 'note_on' and msg.velocity > 0:
                pending[msg.note] = tms
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in pending:
                    s = pending.pop(msg.note)
                    dur = tms - s
                    if dur > 10:
                        notes.append((s, note_to_freq(msg.note), int(dur)))
        if notes:
            all_tracks.append(notes)
    return all_tracks

def build_timeline(tracks: list, min_ms=30) -> tuple:
    times = set()
    for tr in tracks:
        for s, f, d in tr:
            times.add(s); times.add(s + d)
    times = sorted(times)
    song, durs = [], []
    for i, t in enumerate(times[:-1]):
        dt = int(times[i+1] - t)
        if dt < min_ms: continue
        slot = [0] * NUM_MOTORS
        for m, tr in enumerate(tracks):
            for s, freq, d in tr:
                if s <= t < s + d:
                    slot[m] = freq; break
        song.append(slot)
        durs.append(min(dt, 65535))
    return song, durs

# ── 시리얼 ────────────────────────────────────────────────────────────────────
class FakeSerial:
    def write(self, d): pass
    def close(self): pass

def open_serial():
    if DEMO_MODE:
        print("[데모 모드] FakeSerial 사용")
        return FakeSerial()
    if not HAS_SERIAL:
        print("[pyserial 미설치] → 시뮬레이션 모드")
        return FakeSerial()
    try:
        import serial
        s = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)
        time.sleep(2)
        print(f"[시리얼 연결] {SERIAL_PORT}")
        return s
    except Exception as e:
        print(f"[시리얼 실패] {e} → 시뮬레이션 모드")
        return FakeSerial()

# ── 플레이어 ───────────────────────────────────────────────────────────────────
class StepperPlayer:
    def __init__(self, song: list, durs: list, ser):
        self.song, self.durs, self.ser = song, durs, ser
        self.step = 0
        self.total = len(song)
        self.cur_freqs = [0] * NUM_MOTORS
        self.running = False
        self.finished = False
        self.elapsed_ms = 0
        self.total_ms = sum(durs)
        self.lock = threading.Lock()

    def play(self):
        self.running = True
        self.finished = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        acc = 0
        for idx in range(self.total):
            if not self.running: break
            slot = self.song[idx]
            dur  = self.durs[idx] / 1000.0
            with self.lock:
                self.step = idx
                self.cur_freqs = list(slot)
                self.elapsed_ms = acc
            cmd = (",".join(str(f) for f in slot) + "\n").encode()
            try: self.ser.write(cmd)
            except: pass
            time.sleep(dur)
            acc += self.durs[idx]
        with self.lock:
            self.cur_freqs = [0] * NUM_MOTORS
            self.running = False
            self.finished = True
        try: self.ser.write((",".join(["0"]*NUM_MOTORS)+"\n").encode())
        except: pass

    def stop(self):
        self.running = False
        try: self.ser.write((",".join(["0"]*NUM_MOTORS)+"\n").encode())
        except: pass

    def get_state(self) -> tuple:
        with self.lock:
            return self.step, list(self.cur_freqs), self.elapsed_ms

# ── 메인 앱 ───────────────────────────────────────────────────────────────────
class KioskApp:
    STATE_SEARCH  = "search"
    STATE_RESULTS = "results"
    STATE_LOADING = "loading"
    STATE_PLAYING = "playing"

    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        mode_label = " [DEMO MODE]" if DEMO_MODE else ""
        pygame.display.set_caption(f"STEPPER MIDI KIOSK{mode_label}")
        self._init_fonts()
        self.clock = pygame.time.Clock()

        if not DEMO_MODE:
            # browser.json이 있으면 인증 모드로 실행 (검색 품질 향상)
            auth_file = os.path.join(os.path.dirname(__file__), "browser.json")
            if os.path.exists(auth_file):
                self.yt = YTMusic(auth_file)
                print(f"[YTMusic] 인증 모드로 실행 ({auth_file})")
            else:
                self.yt = YTMusic()
                print("[YTMusic] 비인증 모드로 실행 (browser.json 없음)")
        self.ser = open_serial()

        self.state = self.STATE_SEARCH
        self.query = ""
        self.composing = ""
        self.cursor_blink = 0
        self.search_results = []
        self.selected_idx = 0
        self.album_arts = {}
        self.player = None
        self.smooth_freqs = [0.0] * NUM_MOTORS
        self.osc_buffers = [deque([0.0] * OSC_BUF_LEN, maxlen=OSC_BUF_LEN)
                            for _ in range(NUM_MOTORS)]
        self.now_playing = None
        self.error_msg = ""
        self.error_timer = 0
        self.loading_msg = ""
        self.loading_progress = 0.0

    def _init_fonts(self):
        windir = os.environ.get("WINDIR", "C:\\Windows")
        korean_fonts = [
            os.path.join(windir, "Fonts", "malgunbd.ttf"),
            os.path.join(windir, "Fonts", "malgun.ttf"),
            os.path.join(windir, "Fonts", "gulim.ttc"),
            os.path.join(windir, "Fonts", "batang.ttc"),
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        ]
        kf = None
        for fp in korean_fonts:
            if os.path.exists(fp): kf = fp; break
        if kf is None:
            for name in ["malgun gothic", "nanum gothic", "gulim", "batang"]:
                f = pygame.font.match_font(name)
                if f: kf = f; break
        if kf:
            try:
                self.font_lg   = pygame.font.Font(kf, 26)
                self.font_md   = pygame.font.Font(kf, 18)
                self.font_sm   = pygame.font.Font(kf, 14)
                self.font_xs   = pygame.font.Font(kf, 11)
                self.font_hero = pygame.font.Font(kf, 42)
                print(f"[폰트] 한글 폰트 로드: {kf}")
                return
            except Exception as e:
                print(f"[폰트 오류] {e}")
        print("[폰트 경고] 한글 폰트를 찾지 못했습니다.")
        self.font_lg   = pygame.font.SysFont("arial", 26, bold=True)
        self.font_md   = pygame.font.SysFont("arial", 18)
        self.font_sm   = pygame.font.SysFont("arial", 14)
        self.font_xs   = pygame.font.SysFont("arial", 11)
        self.font_hero = pygame.font.SysFont("arial", 42, bold=True)

    # ── 이벤트 ────────────────────────────────────────────────────────────────
    def handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self._cleanup(); sys.exit()

            if ev.type == pygame.TEXTEDITING:
                self.composing = ev.text

            if ev.type == pygame.TEXTINPUT:
                self.composing = ""
                if self.state == self.STATE_SEARCH and len(self.query) < 80:
                    self.query += ev.text

            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    if self.state == self.STATE_PLAYING:
                        if self.player: self.player.stop()
                        self.state = self.STATE_SEARCH; self.query = ""
                    elif self.state in (self.STATE_RESULTS, self.STATE_LOADING):
                        self.state = self.STATE_SEARCH
                    else:
                        self._cleanup(); sys.exit()

                elif self.state == self.STATE_SEARCH:
                    if ev.key == pygame.K_RETURN and self.query.strip():
                        self._do_search()
                    elif ev.key == pygame.K_BACKSPACE:
                        self.query = self.query[:-1]
                    elif ev.unicode and ev.unicode.isprintable() and len(self.query) < 80:
                        if not ('\uAC00' <= ev.unicode <= '\uD7A3') \
                           and not ('\x00'  <= ev.unicode <= '\x7A'):
                            self.query += ev.unicode

                elif self.state == self.STATE_RESULTS:
                    if ev.key == pygame.K_UP:
                        self.selected_idx = max(0, self.selected_idx - 1)
                    elif ev.key == pygame.K_DOWN:
                        self.selected_idx = min(len(self.search_results)-1, self.selected_idx+1)
                    elif ev.key == pygame.K_RETURN:
                        self._start_loading(self.search_results[self.selected_idx])

                elif self.state == self.STATE_PLAYING:
                    if ev.key == pygame.K_SPACE:
                        if self.player and self.player.running:
                            self.player.stop()
                        elif self.player and self.player.finished:
                            self._replay()

            if ev.type == pygame.MOUSEWHEEL:
                if self.state == self.STATE_RESULTS:
                    self.selected_idx = max(
                        0, min(len(self.search_results) - 1, self.selected_idx - ev.y)
                    )

            if ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button not in (4, 5):
                    self._handle_click(ev.pos)

    def _handle_click(self, pos: tuple):
        x, y = pos
        if self.state == self.STATE_RESULTS:
            for i in range(len(self.search_results)):
                card_y = 120 + i * 108
                if card_y <= y <= card_y + 100 and 40 <= x <= SCREEN_W - 40:
                    self.selected_idx = i
                    self._start_loading(self.search_results[i])
                    return
        if self.state == self.STATE_PLAYING:
            btn = pygame.Rect(SCREEN_W//2 - 90, SCREEN_H - 68, 180, 46)
            if btn.collidepoint(pos):
                if self.player: self.player.stop()
                self.state = self.STATE_SEARCH; self.query = ""

    # ── 검색 ──────────────────────────────────────────────────────────────────
    def _do_search(self):
        self.state = self.STATE_LOADING
        self.loading_msg = f"'{self.query}' 검색 중..."
        self.loading_progress = 0.1
        self.search_results = []
        self.album_arts = {}

        def _search():
            try:
                if DEMO_MODE:
                    time.sleep(0.8)
                    self.search_results = DEMO_SONGS[:]
                    for r in self.search_results:
                        self.album_arts[r["videoId"]] = make_demo_album_art((90, 90))
                else:
                    # URL 직접 입력 처리
                    vid = extract_video_id(self.query)
                    if vid:
                        self.loading_msg = "URL에서 곡 정보 가져오는 중..."
                        self.search_results = [{
                            "videoId": vid,
                            "title": self.query,
                            "artists": [],
                            "thumbnails": [],
                        }]
                        self.loading_progress = 1.0
                        self.state = self.STATE_RESULTS
                        self.selected_idx = 0
                        return

                    # 1단계: filter="songs" 로 오리지널 우선 검색
                    try:
                        raw = self.yt.search(self.query, filter="songs", limit=20)
                        candidates = [r for r in raw if r.get("videoId")]
                    except Exception:
                        raw = []
                        candidates = []

                    # 2단계: filter 없이 전체 검색 fallback
                    if not candidates:
                        raw = self.yt.search(self.query, limit=20)
                        candidates = [r for r in raw
                                      if r.get("resultType") in ("song", "video")
                                      and r.get("videoId")]

                    # 3단계: videoId만 있으면 다 허용 fallback
                    if not candidates:
                        candidates = [r for r in raw if r.get("videoId")]

                    if not candidates:
                        raise ValueError(f"'{self.query}'에 대한 재생 가능한 결과가 없습니다")

                    # 오리지널 곡 우선 정렬: 커버/라이브/리믹스를 하단으로
                    candidates.sort(key=lambda r: original_score(r))
                    results = candidates[:5]

                    self.search_results = results
                    for r in self.search_results:
                        vid = r.get("videoId", "")
                        thumbs = r.get("thumbnails", [])
                        if thumbs and vid:
                            self.album_arts[vid] = fetch_album_art(thumbs[-1]["url"], (90, 90))
                self.loading_progress = 1.0
                self.state = self.STATE_RESULTS
                self.selected_idx = 0
            except Exception as e:
                self.error_msg = f"검색 실패: {e}"
                self.error_timer = 180
                self.state = self.STATE_SEARCH

        threading.Thread(target=_search, daemon=True).start()

    # ── 로딩 + 변환 ───────────────────────────────────────────────────────────
    def _start_loading(self, result: dict):
        self.state = self.STATE_LOADING
        self.loading_progress = 0.0
        video_id = result.get("videoId", "")
        title    = result.get("title", "Unknown")
        artist   = extract_artist(result)
        art_surf = self.album_arts.get(video_id)

        def _convert():
            try:
                if DEMO_MODE:
                    steps = [
                        (0.2, "⬇  다운로드 중... (데모)", 0.6),
                        (0.5, "MIDI 변환 중... (데모)", 0.4),
                        (0.8, "준비 중...", 0.3),
                        (1.0, "완료!", 0.1),
                    ]
                    for prog, msg, delay in steps:
                        self.loading_progress = prog
                        self.loading_msg = msg
                        time.sleep(delay)
                    duration_raw = result.get("duration", "3:00")
                    dur_str = fmt_duration(duration_raw)
                    parts = dur_str.split(":")
                    length_ms = (int(parts[0])*60 + int(parts[1])) * 1000 if len(parts)==2 else 30000
                    song, durs = make_demo_song(length_ms)
                else:
                    audio_path = os.path.join(CACHE_DIR, f"{video_id}.mp3")
                    midi_path  = os.path.join(CACHE_DIR, f"{video_id}.mid")
                    url = f"https://music.youtube.com/watch?v={video_id}"

                    self.loading_msg = "⬇  다운로드 중..."
                    self.loading_progress = 0.15
                    if not os.path.exists(audio_path):
                        ydl_opts = {
                            "format": "bestaudio/best",
                            "postprocessors": [{"key": "FFmpegExtractAudio",
                                                "preferredcodec": "mp3",
                                                "preferredquality": "128"}],
                            "outtmpl": audio_path.replace(".mp3",""),
                            "restrictfilenames": True,
                            "windowsfilenames": True,
                            "quiet": True, "no_warnings": True,
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                    self.loading_progress = 0.45

                    self.loading_msg = "MIDI 변환 중... (약 10~30초)"
                    self.loading_progress = 0.50
                    if not os.path.exists(midi_path):
                        if not HAS_BP:
                            raise RuntimeError("basic-pitch 미설치: pip install basic-pitch")
                        from basic_pitch.inference import predict as bp_predict
                        _, midi_data, _ = bp_predict(audio_path)
                        midi_data.write(midi_path)
                    self.loading_progress = 0.85

                    self.loading_msg = "준비 중..."
                    tracks = parse_midi(midi_path)
                    if not tracks:
                        raise RuntimeError("MIDI 트랙을 찾을 수 없습니다")
                    song, durs = build_timeline(tracks)
                    self.loading_progress = 1.0

                disp_art = pygame.transform.smoothscale(art_surf, (180, 180)) \
                           if art_surf else make_demo_album_art((180, 180))

                self.now_playing = {
                    "title": title, "artist": artist,
                    "art": disp_art, "song": song, "durs": durs,
                    "video_id": video_id,
                }
                self.state = self.STATE_PLAYING
                self._start_player(song, durs)

            except Exception as e:
                self.error_msg = f"오류: {e}"
                self.error_timer = 240
                self.state = self.STATE_RESULTS

        threading.Thread(target=_convert, daemon=True).start()

    def _start_player(self, song: list, durs: list):
        if self.player: self.player.stop()
        self.player = StepperPlayer(song, durs, self.ser)
        self.player.play()

    def _replay(self):
        if self.now_playing:
            self._start_player(self.now_playing["song"], self.now_playing["durs"])

    # ── 업데이트 ──────────────────────────────────────────────────────────────
    def update(self):
        self.cursor_blink = (self.cursor_blink + 1) % 60
        if self.error_timer > 0: self.error_timer -= 1
        if self.state == self.STATE_PLAYING and self.player:
            _, freqs, _ = self.player.get_state()
            for i in range(NUM_MOTORS):
                target = float(freqs[i]) if i < len(freqs) else 0.0
                self.smooth_freqs[i] = self.smooth_freqs[i] * 0.85 + target * 0.15
                self.osc_buffers[i].append(self.smooth_freqs[i])

    # ── 그리기 ────────────────────────────────────────────────────────────────
    def draw(self):
        self.screen.fill(BG)
        if   self.state == self.STATE_SEARCH:  self._draw_search()
        elif self.state == self.STATE_RESULTS: self._draw_results()
        elif self.state == self.STATE_LOADING: self._draw_loading()
        elif self.state == self.STATE_PLAYING: self._draw_playing()

        if DEMO_MODE:
            badge = self.font_xs.render("  DEMO MODE  ", True, BG)
            bw = badge.get_width() + 4
            draw_rounded_rect(self.screen, BLUE, (SCREEN_W - bw - 12, 8, bw, 22), 4)
            self.screen.blit(badge, (SCREEN_W - bw - 10, 12))

        if self.error_timer > 0:
            alpha = min(255, self.error_timer * 3)
            draw_rounded_rect(self.screen, RED, (SCREEN_W//2-300, 20, 600, 44), 8, alpha)
            err_surf = self.font_sm.render(self.error_msg, True, WHITE)
            self.screen.blit(err_surf, (SCREEN_W//2 - err_surf.get_width()//2, 32))

        pygame.display.flip()

    def _draw_search(self):
        draw_text_centered(self.screen, "STEPPER MIDI KIOSK",
                           self.font_hero, AMBER, SCREEN_W//2, 140)
        subtitle = "노래를 검색하거나 YouTube URL을 붙여넣으세요" + (" — 데모 모드" if DEMO_MODE else "")
        draw_text_centered(self.screen, subtitle, self.font_md, TEXT_MUTED, SCREEN_W//2, 195)

        box = pygame.Rect(SCREEN_W//2 - 340, 270, 680, 64)
        pygame.draw.rect(self.screen, SURF2, box, border_radius=12)
        pygame.draw.rect(self.screen, AMBER if self.cursor_blink < 30 else BORDER, box, 2, border_radius=12)

        display_text = self.query + self.composing + ("|" if self.cursor_blink < 30 else " ")
        t_surf = self.font_lg.render(display_text[:48], True, WHITE)
        self.screen.blit(t_surf, (box.x + 20, box.y + 16))
        if not self.query and not self.composing:
            hint = self.font_md.render("예: 아이유 밤편지  /  https://music.youtube.com/watch?v=...", True, TEXT_FAINT)
            self.screen.blit(hint, (box.x + 20, box.y + 20))

        btn = pygame.Rect(SCREEN_W//2 - 110, 360, 220, 52)
        draw_rounded_rect(self.screen, AMBER if self.query.strip() else AMBER_DIM, btn, 10)
        draw_text_centered(self.screen, "검색  (Enter)", self.font_md,
                           BG if self.query.strip() else TEXT_FAINT, SCREEN_W//2, 386)
        draw_text_centered(self.screen, "↑↓ 결과 선택  /  Enter 변환 시작  /  ESC 종료",
                           self.font_xs, TEXT_FAINT, SCREEN_W//2, SCREEN_H - 30)

    def _draw_results(self):
        back = self.font_sm.render("← ESC  검색으로 돌아가기", True, TEXT_MUTED)
        self.screen.blit(back, (40, 20))
        draw_text_centered(self.screen, f"'{self.query}' 검색 결과",
                           self.font_lg, TEXT, SCREEN_W//2, 75)
        pygame.draw.line(self.screen, BORDER, (40, 105), (SCREEN_W-40, 105))

        for i, r in enumerate(self.search_results):
            card_y   = 120 + i * 108
            selected = (i == self.selected_idx)
            pygame.draw.rect(self.screen, SURF2 if selected else SURF,
                             (40, card_y, SCREEN_W-80, 100), border_radius=10)
            pygame.draw.rect(self.screen, AMBER if selected else BORDER,
                             (40, card_y, SCREEN_W-80, 100), 2, border_radius=10)

            vid = r.get("videoId", "")
            art = self.album_arts.get(vid)
            if art:
                self.screen.blit(art, (60, card_y + 5))
            else:
                pygame.draw.rect(self.screen, AMBER_FAINT, (60, card_y+5, 90, 90), border_radius=6)
                draw_text_centered(self.screen, "♪", self.font_lg, AMBER_DIM, 105, card_y + 50)

            tx = 170
            title    = r.get("title", "")[:48]
            artist   = extract_artist(r)
            album    = r.get("album", {}).get("name", "") if isinstance(r.get("album"), dict) else ""
            duration = fmt_duration(r.get("duration") or r.get("duration_seconds"))

            self.screen.blit(self.font_lg.render(title, True, WHITE if selected else TEXT),
                             (tx, card_y + 12))
            self.screen.blit(self.font_sm.render(artist, True, AMBER if selected else TEXT_MUTED),
                             (tx, card_y + 46))
            if album:
                self.screen.blit(self.font_xs.render(album, True, TEXT_FAINT), (tx, card_y + 70))
            if duration:
                d_surf = self.font_sm.render(duration, True, TEXT_MUTED)
                self.screen.blit(d_surf, (SCREEN_W - 140, card_y + 40))
            if selected:
                arrow = self.font_md.render("▶  클릭 또는 Enter", True, AMBER)
                self.screen.blit(arrow, (SCREEN_W - arrow.get_width() - 60, card_y + 15))

        draw_text_centered(self.screen, "↑↓ 선택  /  Enter 또는 클릭으로 변환 시작",
                           self.font_xs, TEXT_FAINT, SCREEN_W//2, SCREEN_H - 25)

    def _draw_loading(self):
        cx, cy = SCREEN_W//2, SCREEN_H//2
        t = time.time()
        for i in range(12):
            angle = i * 30 - (t * 360) % 360
            rad = math.radians(angle)
            x1 = int(cx + 50 * math.cos(rad))
            y1 = int(cy - 120 + 50 * math.sin(rad))
            pygame.gfxdraw.filled_circle(self.screen, x1, y1, 4, (*AMBER, int(255 * i / 12)))

        draw_text_centered(self.screen, self.loading_msg, self.font_md, TEXT, cx, cy - 30)
        bar_w = 500
        bx, by = cx - bar_w//2, cy + 10
        pygame.draw.rect(self.screen, SURF2, (bx, by, bar_w, 10), border_radius=5)
        fill_w = int(bar_w * self.loading_progress)
        if fill_w > 0:
            pygame.draw.rect(self.screen, AMBER, (bx, by, fill_w, 10), border_radius=5)
        pct = self.font_sm.render(f"{int(self.loading_progress*100)}%", True, TEXT_MUTED)
        self.screen.blit(pct, (cx - pct.get_width()//2, by + 22))
        draw_text_centered(self.screen, "ESC  취소", self.font_xs, TEXT_FAINT, cx, SCREEN_H - 30)

    def _draw_playing(self):
        if not self.player or not self.now_playing: return
        step, freqs, elapsed_ms = self.player.get_state()
        total_ms = self.player.total_ms
        np = self.now_playing

        ART_SIZE = 180
        art_x, art_y = 50, 30
        art = np.get("art")
        if art:
            self.screen.blit(art, (art_x, art_y))
        else:
            pygame.draw.rect(self.screen, SURF2, (art_x, art_y, ART_SIZE, ART_SIZE), border_radius=12)

        tx = art_x + ART_SIZE + 30
        self.screen.blit(self.font_hero.render(np["title"][:30], True, WHITE), (tx, art_y + 20))
        self.screen.blit(self.font_md.render(np["artist"], True, AMBER), (tx, art_y + 70))

        status = "▶  재생 중" if self.player.running else ("⏹  종료됨" if self.player.finished else "⏸  정지")
        col = GREEN if self.player.running else TEXT_MUTED
        self.screen.blit(self.font_sm.render(status, True, col), (tx, art_y + 105))

        if DEMO_MODE:
            demo_note = self.font_xs.render("데모 모드: 아두이노 신호 전송 없음 (FakeSerial)", True, BLUE)
            self.screen.blit(demo_note, (tx, art_y + 130))

        prog_w = SCREEN_W - tx - 50
        prog_y = art_y + 155
        pygame.draw.rect(self.screen, SURF2, (tx, prog_y, prog_w, 8), border_radius=4)
        ratio = elapsed_ms / max(total_ms, 1)
        fill  = int(prog_w * ratio)
        if fill > 0:
            pygame.draw.rect(self.screen, AMBER, (tx, prog_y, fill, 8), border_radius=4)

        def fmt(ms):
            s = ms // 1000; return f"{s//60:02d}:{s%60:02d}"
        self.screen.blit(self.font_xs.render(fmt(elapsed_ms), True, TEXT_MUTED), (tx, prog_y+12))
        tr = self.font_xs.render(fmt(total_ms), True, TEXT_MUTED)
        self.screen.blit(tr, (tx + prog_w - tr.get_width(), prog_y+12))

        pygame.draw.line(self.screen, BORDER, (30, 232), (SCREEN_W-30, 232))

        bar_area_y, bar_area_h = 245, 210
        bar_w = (SCREEN_W - 80) // NUM_MOTORS - 20
        max_freq = 900
        for m in range(NUM_MOTORS):
            bx = 50 + m * ((SCREEN_W - 80) // NUM_MOTORS)
            freq = self.smooth_freqs[m]
            ratio2 = min(freq / max_freq, 1.0) if freq > 0 else 0.0
            bar_h_act = int(bar_area_h * ratio2)
            col = MOTOR_COLS[m % len(MOTOR_COLS)]

            pygame.draw.rect(self.screen, SURF2, (bx, bar_area_y, bar_w, bar_area_h), border_radius=6)
            if bar_h_act > 4:
                dim = tuple(max(0, c//5) for c in col)
                pygame.draw.rect(self.screen, dim, (bx, bar_area_y, bar_w, bar_area_h), border_radius=6)
                pygame.draw.rect(self.screen, col,
                                 (bx, bar_area_y + bar_area_h - bar_h_act, bar_w, bar_h_act),
                                 border_radius=6)

            self.screen.blit(self.font_sm.render(f"M{m+1}", True, col),
                             (bx + bar_w//2 - 12, bar_area_y + bar_area_h + 5))
            n = self.font_xs.render(freq_to_name(int(freq)), True, TEXT_MUTED)
            self.screen.blit(n, (bx + bar_w//2 - n.get_width()//2, bar_area_y + bar_area_h + 22))
            h = self.font_xs.render(f"{int(freq)}Hz" if freq > 0 else "--", True, TEXT_FAINT)
            self.screen.blit(h, (bx + bar_w//2 - h.get_width()//2, bar_area_y + bar_area_h + 36))

        osc_y, osc_h = 500, 75
        pygame.draw.rect(self.screen, SURF, (30, osc_y, SCREEN_W-60, osc_h), border_radius=8)
        pygame.draw.rect(self.screen, BORDER, (30, osc_y, SCREEN_W-60, osc_h), 1, border_radius=8)
        for m in range(NUM_MOTORS):
            buf = list(self.osc_buffers[m])
            pts = [(30 + int(i*(SCREEN_W-60)/len(buf)),
                    osc_y + osc_h//2 - int((v/max_freq)*(osc_h*0.45)))
                   for i, v in enumerate(buf)]
            if len(pts) > 2:
                pygame.draw.lines(self.screen, MOTOR_COLS[m%len(MOTOR_COLS)], False, pts, 1)
        self.screen.blit(self.font_xs.render("오실로스코프 (실시간 주파수)", True, TEXT_FAINT), (42, osc_y+4))

        btn = pygame.Rect(SCREEN_W//2 - 90, SCREEN_H - 68, 180, 46)
        draw_rounded_rect(self.screen, RED, btn, 10)
        draw_text_centered(self.screen, "정지 & 홈으로", self.font_sm, WHITE, SCREEN_W//2, SCREEN_H - 45)
        draw_text_centered(self.screen, "ESC / 클릭 → 정지   SPACE → 다시 재생",
                           self.font_xs, TEXT_FAINT, SCREEN_W//2, SCREEN_H - 12)

    def _cleanup(self):
        if self.player: self.player.stop()
        try: self.ser.close()
        except: pass
        pygame.quit()

    def run(self):
        while True:
            self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(60)

if __name__ == "__main__":
    KioskApp().run()
