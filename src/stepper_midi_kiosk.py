#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
STEPPER MIDI KIOSK
박람회용 키오스크 - 유튜브 뮤직 검색 → 다운로드 → MIDI 변환 → 스텝모터 연주

실행 방법:
  python stepper_midi_kiosk.py          # 일반 모드 (아두이노 연결 필요)
  python stepper_midi_kiosk.py --demo   # 데모 모드 (아두이노 없이 UI만 테스트)
"""

import sys, os, io, time, threading, math, urllib.request, tempfile, random
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
    # 데모 모드: 라이브러리 없어도 실행 가능
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
SERIAL_PORT  = "COM4"       # ← 실제 포트로 변경 (Mac: /dev/ttyUSB0 등)
SERIAL_BAUD  = 115200
NUM_MOTORS   = 4
SCREEN_W     = 1280
SCREEN_H     = 720
CACHE_DIR    = os.path.join(tempfile.gettempdir(), "stepper_midi_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

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
    {
        "videoId": "demo_001",
        "title": "밤편지",
        "artists": [{"name": "아이유 (IU)"}],
        "album": {"name": "Palette"},
        "duration": "3:36",
        "thumbnails": [],
    },
    {
        "videoId": "demo_002",
        "title": "Dynamite",
        "artists": [{"name": "BTS"}],
        "album": {"name": "BE"},
        "duration": "3:19",
        "thumbnails": [],
    },
    {
        "videoId": "demo_003",
        "title": "LILAC",
        "artists": [{"name": "아이유 (IU)"}],
        "album": {"name": "LILAC"},
        "duration": "3:37",
        "thumbnails": [],
    },
    {
        "videoId": "demo_004",
        "title": "Butter",
        "artists": [{"name": "BTS"}],
        "album": {"name": "Butter"},
        "duration": "2:44",
        "thumbnails": [],
    },
    {
        "videoId": "demo_005",
        "title": "Celebrity",
        "artists": [{"name": "아이유 (IU)"}],
        "album": {"name": "Celebrity Single"},
        "duration": "3:02",
        "thumbnails": [],
    },
]

def make_demo_song(length_ms=30000):
    """데모용 가짜 MIDI 타임라인 생성 (스텝모터 시뮬레이션)"""
    scale = [262, 294, 330, 349, 392, 440, 494, 523,
             587, 659, 698, 784, 880]
    song, durs = [], []
    t = 0
    while t < length_ms:
        slot = []
        for m in range(NUM_MOTORS):
            if random.random() < 0.3:
                slot.append(0)
            else:
                offset = m * 3
                freq = scale[(t // 400 + offset) % len(scale)]
                freq = int(freq * random.uniform(0.98, 1.02))
                slot.append(freq)
        dur = random.choice([150, 200, 250, 300, 400])
        song.append(slot)
        durs.append(dur)
        t += dur
    return song, durs

# ── 유틸 ──────────────────────────────────────────────────────────────────────
def note_to_freq(note):
    if note <= 0: return 0
    return round(440.0 * (2.0 ** ((note - 69) / 12.0)))

def freq_to_name(f):
    if f == 0: return "REST"
    names = {65:"C2",73:"D2",82:"E2",87:"F2",98:"G2",110:"A2",123:"B2",
             131:"C3",147:"D3",165:"E3",175:"F3",196:"G3",220:"A3",247:"B3",
             262:"C4",294:"D4",330:"E4",349:"F4",392:"G4",440:"A4",494:"B4",
             523:"C5",587:"D5",659:"E5",698:"F5",784:"G5",880:"A5"}
    best = min(names.items(), key=lambda kv: abs(kv[0]-f))
    return best[1]

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
    r = rendered.get_rect(center=(cx, cy))
    surf.blit(rendered, r)

def fetch_album_art(url, size=(180, 180)):
    try:
        req = urllib.request.urlopen(url, timeout=5)
        data = req.read()
        img = pygame.image.load(io.BytesIO(data))
        return pygame.transform.smoothscale(img, size)
    except:
        s = pygame.Surface(size)
        s.fill(SURF2)
        return s

def make_demo_album_art(size=(90, 90)):
    """데모용 컬러 앨범 아트 생성"""
    colors = [
        [(255,80,80),(180,40,40)],
        [(80,160,255),(40,80,180)],
        [(80,220,120),(40,140,60)],
        [(220,160,80),(140,90,30)],
        [(200,80,220),(120,40,140)],
    ]
    s = pygame.Surface(size)
    idx = random.randint(0, len(colors)-1)
    c1, c2 = colors[idx]
    s.fill(c2)
    pygame.draw.rect(s, c1, (size[0]//4, size[1]//4, size[0]//2, size[1]//2),
                     border_radius=8)
    font = pygame.font.SysFont("arial", 28, bold=True)
    note = font.render("♪", True, (255,255,255))
    s.blit(note, (size[0]//2 - note.get_width()//2,
                  size[1]//2 - note.get_height()//2))
    return s

# ── MIDI 파싱 ─────────────────────────────────────────────────────────────────
def parse_midi(path):
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
            elif msg.type in ('note_off',) or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in pending:
                    s = pending.pop(msg.note)
                    dur = tms - s
                    if dur > 10:
                        notes.append((s, note_to_freq(msg.note), int(dur)))
        if notes:
            all_tracks.append(notes)
    return all_tracks

def build_timeline(tracks, min_ms=30):
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
                    slot[m] = freq
                    break
        song.append(slot)
        durs.append(min(dt, 65535))
    return song, durs

# ── 시리얼 연결 ───────────────────────────────────────────────────────────────
class FakeSerial:
    """아두이노 없이 시뮬레이션하는 더미 시리얼 (--demo 또는 pyserial 미설치 시 사용)"""
    def write(self, d): pass   # 실제 전송 안 함
    def close(self): pass

def open_serial():
    if DEMO_MODE:
        print("[데모 모드] FakeSerial 사용 (아두이노 전송 없음)")
        return FakeSerial()
    if not HAS_SERIAL:
        print("[시리얼 없음] pyserial 미설치 → 시뮬레이션 모드")
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

# ── 플레이어 스레드 ───────────────────────────────────────────────────────────
class StepperPlayer:
    def __init__(self, song, durs, ser):
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
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

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
            try: self.ser.write(cmd)   # FakeSerial이면 아무 동작 안 함
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

    def get_state(self):
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
            self.yt = YTMusic()
        self.ser = open_serial()

        self.state = self.STATE_SEARCH
        self.query = ""
        self.cursor_blink = 0
        self.search_results = []
        self.selected_idx = 0
        self.album_arts = {}
        self.player = None
        self.smooth_freqs = [0.0] * NUM_MOTORS
        self.osc_buffers = [[0.0]*120 for _ in range(NUM_MOTORS)]
        self.now_playing = None
        self.error_msg = ""
        self.error_timer = 0
        self.loading_msg = ""
        self.loading_progress = 0.0

    def _init_fonts(self):
        korean_fonts = [
            "malgunbd.ttf", "malgun.ttf",
            "NanumGothic.ttf", "NanumGothicBold.ttf",
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        ]
        kf = None
        for fp in korean_fonts:
            if os.path.exists(fp):
                kf = fp; break
        if kf:
            self.font_lg   = pygame.font.Font(kf, 26)
            self.font_md   = pygame.font.Font(kf, 18)
            self.font_sm   = pygame.font.Font(kf, 14)
            self.font_xs   = pygame.font.Font(kf, 11)
            self.font_hero = pygame.font.Font(kf, 42)
        else:
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
                    elif ev.unicode and len(self.query) < 40:
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

            if ev.type == pygame.MOUSEBUTTONDOWN:
                self._handle_click(ev.pos)

    def _handle_click(self, pos):
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
                        vid = r["videoId"]
                        self.album_arts[vid] = make_demo_album_art((90, 90))
                else:
                    results = self.yt.search(self.query, filter="songs", limit=5)
                    self.search_results = results[:5]
                    for r in self.search_results:
                        vid = r.get("videoId","")
                        thumbs = r.get("thumbnails", [])
                        if thumbs and vid:
                            art = fetch_album_art(thumbs[-1]["url"], (90, 90))
                            self.album_arts[vid] = art
                self.loading_progress = 1.0
                self.state = self.STATE_RESULTS
                self.selected_idx = 0
            except Exception as e:
                self.error_msg = f"검색 실패: {e}"
                self.error_timer = 180
                self.state = self.STATE_SEARCH

        threading.Thread(target=_search, daemon=True).start()

    # ── 로딩 + 변환 ───────────────────────────────────────────────────────────
    def _start_loading(self, result):
        self.state = self.STATE_LOADING
        self.loading_progress = 0.0
        video_id = result.get("videoId", "")
        title    = result.get("title", "Unknown")
        artists  = result.get("artists", [])
        artist   = artists[0]["name"] if artists else "Unknown"
        art_surf = self.album_arts.get(video_id)

        def _convert():
            try:
                if DEMO_MODE:
                    # 데모: 실제 다운로드/변환 없이 가짜 진행률만 표시
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
                    duration_str = result.get("duration", "3:00")
                    parts = duration_str.split(":")
                    length_ms = (int(parts[0])*60 + int(parts[1])) * 1000 if len(parts)==2 else 30000
                    song, durs = make_demo_song(length_ms)
                else:
                    # 실제 모드
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

                if art_surf is None:
                    disp_art = make_demo_album_art((180, 180))
                else:
                    disp_art = pygame.transform.smoothscale(art_surf, (180, 180))

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

    def _start_player(self, song, durs):
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
                self.osc_buffers[i] = self.osc_buffers[i][-120:]

    # ── 그리기 ────────────────────────────────────────────────────────────────
    def draw(self):
        self.screen.fill(BG)
        if   self.state == self.STATE_SEARCH:  self._draw_search()
        elif self.state == self.STATE_RESULTS: self._draw_results()
        elif self.state == self.STATE_LOADING: self._draw_loading()
        elif self.state == self.STATE_PLAYING: self._draw_playing()

        # 데모 모드 배지
        if DEMO_MODE:
            badge = self.font_xs.render("  DEMO MODE  ", True, BG)
            bw = badge.get_width() + 4
            draw_rounded_rect(self.screen, BLUE, (SCREEN_W - bw - 12, 8, bw, 22), 4)
            self.screen.blit(badge, (SCREEN_W - bw - 10, 12))

        # 에러 토스트
        if self.error_timer > 0:
            alpha = min(255, self.error_timer * 3)
            draw_rounded_rect(self.screen, RED, (SCREEN_W//2-300, 20, 600, 44), 8, alpha)
            err_surf = self.font_sm.render(self.error_msg, True, WHITE)
            self.screen.blit(err_surf, (SCREEN_W//2 - err_surf.get_width()//2, 32))

        pygame.display.flip()

    def _draw_search(self):
        draw_text_centered(self.screen, "STEPPER MIDI KIOSK",
                           self.font_hero, AMBER, SCREEN_W//2, 140)
        subtitle = "노래를 검색하면 스텝모터로 연주합니다" + (" — 데모 모드" if DEMO_MODE else "")
        draw_text_centered(self.screen, subtitle,
                           self.font_md, TEXT_MUTED, SCREEN_W//2, 195)

        box = pygame.Rect(SCREEN_W//2 - 340, 270, 680, 64)
        pygame.draw.rect(self.screen, SURF2, box, border_radius=12)
        pygame.draw.rect(self.screen, AMBER if self.cursor_blink < 30 else BORDER, box, 2, border_radius=12)

        display_text = self.query + ("|" if self.cursor_blink < 30 else " ")
        t_surf = self.font_lg.render(display_text, True, WHITE)
        self.screen.blit(t_surf, (box.x + 20, box.y + 16))
        if not self.query:
            hint = self.font_md.render("예: 아이유 밤편지  /  BTS Dynamite ...", True, TEXT_FAINT)
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
            card_y  = 120 + i * 108
            selected = (i == self.selected_idx)
            pygame.draw.rect(self.screen, SURF2 if selected else SURF,
                             (40, card_y, SCREEN_W-80, 100), border_radius=10)
            pygame.draw.rect(self.screen, AMBER if selected else BORDER,
                             (40, card_y, SCREEN_W-80, 100), 2, border_radius=10)

            vid = r.get("videoId","")
            art = self.album_arts.get(vid)
            if art:
                self.screen.blit(art, (60, card_y + 5))
            else:
                pygame.draw.rect(self.screen, AMBER_FAINT,
                                 (60, card_y+5, 90, 90), border_radius=6)
                draw_text_centered(self.screen, "♪", self.font_lg,
                                   AMBER_DIM, 105, card_y + 50)

            tx = 170
            title   = r.get("title","")[:48]
            artists = r.get("artists",[])
            artist  = artists[0]["name"] if artists else "Unknown"
            album   = r.get("album",{}).get("name","") if isinstance(r.get("album"),dict) else ""
            duration= r.get("duration","")

            self.screen.blit(self.font_lg.render(title, True, WHITE if selected else TEXT),
                             (tx, card_y + 12))
            self.screen.blit(self.font_sm.render(artist, True, AMBER if selected else TEXT_MUTED),
                             (tx, card_y + 46))
            if album:
                self.screen.blit(self.font_xs.render(album, True, TEXT_FAINT),
                                 (tx, card_y + 70))
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
            pygame.gfxdraw.filled_circle(self.screen, x1, y1, 4,
                                         (*AMBER, int(255 * i / 12)))

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
            s = ms//1000; return f"{s//60:02d}:{s%60:02d}"
        self.screen.blit(self.font_xs.render(fmt(elapsed_ms), True, TEXT_MUTED), (tx, prog_y+12))
        tr = self.font_xs.render(fmt(total_ms), True, TEXT_MUTED)
        self.screen.blit(tr, (tx + prog_w - tr.get_width(), prog_y+12))

        pygame.draw.line(self.screen, BORDER, (30, 232), (SCREEN_W-30, 232))

        # 모터 바 그래프
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

        # 오실로스코프
        osc_y, osc_h = 500, 75
        pygame.draw.rect(self.screen, SURF, (30, osc_y, SCREEN_W-60, osc_h), border_radius=8)
        pygame.draw.rect(self.screen, BORDER, (30, osc_y, SCREEN_W-60, osc_h), 1, border_radius=8)
        for m in range(NUM_MOTORS):
            buf = self.osc_buffers[m]
            pts = [(30 + int(i*(SCREEN_W-60)/len(buf)),
                    osc_y + osc_h//2 - int((v/max_freq)*(osc_h*0.45)))
                   for i, v in enumerate(buf)]
            if len(pts) > 2:
                pygame.draw.lines(self.screen, MOTOR_COLS[m%len(MOTOR_COLS)], False, pts, 1)
        self.screen.blit(self.font_xs.render("오실로스코프 (실시간 주파수)", True, TEXT_FAINT), (42, osc_y+4))

        # STOP 버튼
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
