#!/usr/bin/env python3
"""
MIDI → Stepper Motor  실시간 플레이어  [QHD 최적화]
pip install mido pyserial
"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
import tkinter.font as tkfont
import mido, serial, threading, time, os, math, queue, subprocess

# ═══════════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════════
PORT        = "COM5"
BAUD        = 115200
NUM_MOTORS  = 4
MIN_STEP_MS = 20
BANDS       = [(72,127),(60,71),(48,59),(0,47)]

# ═══════════════════════════════════════════════════════════════
#  QHD DPI 스케일 감지
# ═══════════════════════════════════════════════════════════════
def _detect_scale():
    root = tk.Tk(); root.withdraw()
    try:    dpi = root.winfo_fpixels("1i")
    except: dpi = 96
    root.destroy()
    return max(1.0, dpi / 96.0)

SCALE = _detect_scale()
def sp(pt): return max(1, round(pt * SCALE))
def dp(px): return max(1, round(px * SCALE))

# ═══════════════════════════════════════════════════════════════
#  토스 디자인 토큰
# ═══════════════════════════════════════════════════════════════
T = {
    "bg":        "#F9FAFB",
    "card":      "#FFFFFF",
    "border":    "#E5E7EB",
    "text_main": "#111827",
    "text_sub":  "#6B7280",
    "text_hint": "#9CA3AF",
    "primary":   "#3182F6",
    "primary_h": "#1B64DA",
    "success":   "#00C471",
    "danger":    "#F04452",
    "warn":      "#FF8C00",
    "pause":     "#F59E0B",
    "track_bg":  "#E5E7EB",
    "track_fg":  "#3182F6",
}

MOTOR_COLORS = ["#3182F6","#00C471","#F59E0B","#8B5CF6"]
MOTOR_NAMES  = ["고음","중고음","중저음","저음"]

F     = {}
_SANS = "Malgun Gothic"
_MONO = "Consolas"

# ═══════════════════════════════════════════════════════════════
#  유틸
# ═══════════════════════════════════════════════════════════════
def note_to_freq(note):
    if note == 0: return 0
    return round(440.0 * 2.0 ** ((note - 69) / 12.0))

def freq_to_name(freq):
    if freq == 0: return "REST"
    NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
    n = round(12 * math.log2(freq / 440.0) + 69)
    return f"{NAMES[n%12]}{n//12-1}"

def ms_to_str(ms):
    s = int(ms) // 1000
    return f"{s//60:02d}:{s%60:02d}.{int(ms)%1000//10:02d}"

# ═══════════════════════════════════════════════════════════════
#  MIDI 파싱
# ═══════════════════════════════════════════════════════════════
def auto_bands(path, n=NUM_MOTORS, note_min=48):
    mid = mido.MidiFile(path)
    notes = sorted([msg.note for track in mid.tracks
                    for msg in track
                    if msg.type == "note_on"
                    and msg.velocity > 0
                    and msg.note >= note_min])
    if not notes:
        return BANDS
    total = len(notes)
    cuts  = [notes[int(total * i / n)] for i in range(1, n)]
    bands, lo = [], 0
    for cut in cuts:
        bands.append((lo, cut - 1)); lo = cut
    bands.append((lo, 127))
    bands.reverse()
    return bands

def parse_midi(path, bands=BANDS, min_ms=MIN_STEP_MS):
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    all_notes = []
    for track in mid.tracks:
        pending, tempo, t_ms = {}, 500000, 0.0
        for msg in track:
            t_ms += msg.time * tempo / (tpb * 1000.0)
            if msg.type == "set_tempo": tempo = msg.tempo
            if msg.type == "note_on" and msg.velocity > 0:
                pending[msg.note] = t_ms
            elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0):
                if msg.note in pending:
                    s = pending.pop(msg.note)
                    if t_ms - s > 5:
                        all_notes.append((s, msg.note, t_ms - s))
    if not all_notes: return [], 0.0
    total_ms = max(s+d for s,_,d in all_notes)
    tracks   = [[(s,n,d) for s,n,d in all_notes if lo<=n<=hi]
                for lo,hi in bands]
    times    = sorted({t for tr in tracks for s,n,d in tr
                       for t in (s, s+d)})
    events = []
    for i,t in enumerate(times[:-1]):
        dt = times[i+1]-t
        if dt < min_ms: continue
        slot = [0]*NUM_MOTORS
        for m,tr in enumerate(tracks):
            for s,note,d in tr:
                if s<=t<s+d: slot[m]=note; break
        if events and events[-1][1]==slot:
            events[-1]=(events[-1][0],slot,events[-1][2]+dt)
        else:
            events.append([t,slot,dt])
    return [(t,[note_to_freq(n) for n in slot],dur)
            for t,slot,dur in events], total_ms

# ═══════════════════════════════════════════════════════════════
#  Serial Writer
# ═══════════════════════════════════════════════════════════════
class SerialWriter:
    def __init__(self):
        self.ser = None
        self.q   = queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def connect(self, port, baud=115200):
        try:
            self.ser = serial.Serial(port, baud, timeout=1)
            time.sleep(2); self.ser.readline(); return True
        except Exception as e:
            self.ser = None; return str(e)

    def send(self, freqs):
        self.q.put(("F"+",".join(str(f) for f in freqs)+"\n").encode())

    def stop(self): self.q.put(b"S\n")

    def _loop(self):
        while True:
            data = self.q.get()
            try:
                if self.ser and self.ser.is_open:
                    self.ser.write(data)
            except: pass

# ═══════════════════════════════════════════════════════════════
#  Player
# ═══════════════════════════════════════════════════════════════
class Player:
    def __init__(self, writer):
        self.writer    = writer
        self.events    = []; self.total_ms = 0.0
        self._pause_ev = threading.Event()
        self._stop_ev  = threading.Event()
        self._seek_ms  = None
        self._lock     = threading.Lock()
        self._thread   = None
        self.on_position = self.on_freqs = self.on_stopped = None

    def load(self, ev, tm):
        self.stop(); self.events, self.total_ms = ev, tm

    def play(self, start_ms=0.0):
        self.stop()
        self._pause_ev.set(); self._stop_ev.clear()
        self._seek_ms = start_ms
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):  self._pause_ev.clear(); self.writer.stop()
    def resume(self): self._pause_ev.set()

    @property
    def is_paused(self): return not self._pause_ev.is_set()
    @property
    def is_alive(self):  return self._thread and self._thread.is_alive()

    def stop(self):
        self._stop_ev.set(); self._pause_ev.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.writer.stop()

    def seek(self, ms):
        with self._lock: self._seek_ms = ms
        self._pause_ev.set()

    def _run(self):
        if not self.events: return
        with self._lock:
            offset_ms, self._seek_ms = self._seek_ms or 0.0, None
        idx    = next((i for i,(t,_,_) in enumerate(self.events)
                       if t >= offset_ms), 0)
        origin = time.perf_counter()*1000 - offset_ms

        while idx < len(self.events) and not self._stop_ev.is_set():
            if not self._pause_ev.is_set():
                t0 = time.perf_counter()*1000
                self._pause_ev.wait()
                origin += time.perf_counter()*1000 - t0
            if self._stop_ev.is_set(): break

            with self._lock:
                if self._seek_ms is not None:
                    offset_ms, self._seek_ms = self._seek_ms, None
                    origin = time.perf_counter()*1000 - offset_ms
                    idx = next((i for i,(t,_,_) in enumerate(self.events)
                                if t >= offset_ms), 0)
                    continue

            t_ev, freqs, _ = self.events[idx]
            wait = (t_ev - (time.perf_counter()*1000 - origin)) / 1000.0
            if wait > 0.001: time.sleep(wait - 0.001)
            while time.perf_counter()*1000 - origin < t_ev: pass
            if self._stop_ev.is_set(): break

            self.writer.send(freqs)
            cur = time.perf_counter()*1000 - origin
            if self.on_position: self.on_position(cur)
            if self.on_freqs:    self.on_freqs(freqs)
            idx += 1

        self.writer.stop()
        if self.on_stopped and not self._stop_ev.is_set():
            self.on_stopped()

# ═══════════════════════════════════════════════════════════════
#  카드 / 버튼 컴포넌트
# ═══════════════════════════════════════════════════════════════
class Card(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent,
            bg=T["card"],
            highlightbackground=T["border"],
            highlightthickness=dp(1),
            relief="flat", **kw)

class PillButton(tk.Label):
    def __init__(self, parent, text, command=None,
                 bg=None, fg="#FFFFFF", width=10, **kw):
        self._bg  = bg or T["primary"]
        self._hbg = T["primary_h"] if bg in (None, T["primary"]) else bg
        self._cmd = command
        super().__init__(parent,
            text=text, bg=self._bg, fg=fg,
            font=F["h3"], width=width,
            cursor="hand2",
            pady=dp(10), padx=dp(4),
            relief="flat", **kw)
        self.bind("<Enter>",         lambda _: self.config(bg=self._hbg))
        self.bind("<Leave>",         lambda _: self.config(bg=self._bg))
        self.bind("<ButtonPress-1>", lambda _: self._cmd() if self._cmd else None)

    def set_style(self, bg, hbg=None):
        self._bg = bg; self._hbg = hbg or bg; self.config(bg=bg)

# ═══════════════════════════════════════════════════════════════
#  메인 앱
# ═══════════════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()

        # ── 폰트 감지 ────────────────────────────────────────────
        def _ff(name, fallback):
            return name if name in tkfont.families() else fallback

        global _SANS, _MONO, F
        _SANS = _ff("Pretendard",  "Malgun Gothic" if os.name=="nt" else "AppleGothic")
        _MONO = _ff("D2Coding",    "Consolas"      if os.name=="nt" else "Menlo")

        F = {
            "display": (_SANS, sp(26), "bold"),
            "h1":      (_SANS, sp(20), "bold"),
            "h2":      (_SANS, sp(17), "bold"),
            "h3":      (_SANS, sp(15), "bold"),
            "body":    (_SANS, sp(14)),
            "sub":     (_SANS, sp(13)),
            "hint":    (_SANS, sp(12)),
            "mono":    (_MONO, sp(14)),
            "mono_b":  (_MONO, sp(18), "bold"),
            "mono_s":  (_MONO, sp(13)),
            "time":    (_MONO, sp(32), "bold"),
            "time_s":  (_MONO, sp(17)),
        }

        self.title("Stepper Player")
        self.configure(bg=T["bg"])
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(2)
        except: pass

        W, H = dp(1000), dp(820)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{W}x{H}+{(sw-W)//2}+{(sh-H)//2}")
        self.minsize(W, H)

        self.writer = SerialWriter()
        self.player = Player(self.writer)
        self.player.on_position = lambda ms: self.after(0, self._upd_pos, ms)
        self.player.on_freqs    = lambda f:  self.after(0, self._upd_freqs, f)
        self.player.on_stopped  = lambda:    self.after(0, self._on_end)

        self.events   = []; self.total_ms = 0.0
        self.cur_ms   = 0.0; self._seeking = False

        self._build()
        self.after(200, self._poll)

    # ── 레이아웃 ─────────────────────────────────────────────────
    def _build(self):
        canvas = tk.Canvas(self, bg=T["bg"], highlightthickness=0)
        vsb    = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self.main = tk.Frame(canvas, bg=T["bg"])
        wid = canvas.create_window((0,0), window=self.main, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(wid, width=e.width))
        self.main.bind("<Configure>",
                    lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        P = dp(32)
        self._build_header(P)
        self._build_connect_card(P)
        self._build_upload_card(P)
        self._build_file_card(P)
        self._build_player_card(P)
        self._build_motor_card(P)
        self._build_log_card(P)

    # ── ① 헤더 ───────────────────────────────────────────────────
    def _build_header(self, P):
        f = tk.Frame(self.main, bg=T["bg"])
        f.pack(fill="x", padx=P, pady=(dp(32), dp(4)))
        tk.Label(f, text="Stepper Player",
                 bg=T["bg"], fg=T["text_main"],
                 font=F["display"]).pack(side="left")
        tk.Label(f, text="MIDI → Motor  실시간 연주",
                 bg=T["bg"], fg=T["text_sub"],
                 font=F["body"]).pack(side="left", padx=dp(16), pady=dp(6))

    # ── ② 연결 카드 ──────────────────────────────────────────────
    def _build_connect_card(self, P):
        card  = Card(self.main); card.pack(fill="x", padx=P, pady=dp(6))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(20))

        tk.Label(inner, text="Arduino 연결",
                 bg=T["card"], fg=T["text_main"],
                 font=F["h2"]).pack(side="left")

        self.conn_dot = tk.Label(inner, text="●",
                                 bg=T["card"], fg=T["danger"],
                                 font=(_SANS, sp(18)))
        self.conn_dot.pack(side="right", padx=(0, dp(6)))
        self.conn_txt = tk.Label(inner, text="미연결",
                                 bg=T["card"], fg=T["text_sub"],
                                 font=F["body"])
        self.conn_txt.pack(side="right", padx=(0, dp(16)))

        PillButton(inner, "연결", command=self._connect,
                   width=7).pack(side="right", padx=dp(10))

        self.port_var = tk.StringVar(value=PORT)
        tk.Entry(inner, textvariable=self.port_var,
                 width=9, font=F["mono"],
                 bg=T["bg"], fg=T["text_main"],
                 relief="flat",
                 highlightbackground=T["border"],
                 highlightthickness=dp(1)
                 ).pack(side="right", padx=dp(8), ipady=dp(6))
        tk.Label(inner, text="포트",
                 bg=T["card"], fg=T["text_sub"],
                 font=F["sub"]).pack(side="right")

    # ── ③ 펌웨어 업로드 카드 ─────────────────────────────────────
    def _build_upload_card(self, P):
        card  = Card(self.main); card.pack(fill="x", padx=P, pady=dp(6))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(20))

        tk.Label(inner, text="펌웨어 업로드",
                 bg=T["card"], fg=T["text_main"],
                 font=F["h2"]).pack(side="left")

        PillButton(inner, "업로드 🚀",
                   command=self._upload_firmware,
                   bg=T["success"], width=10).pack(side="right", padx=dp(6))
        PillButton(inner, "컴파일만",
                   command=self._compile_only,
                   bg=T["primary"], width=9).pack(side="right", padx=dp(6))

        path_row = tk.Frame(card, bg=T["card"])
        path_row.pack(fill="x", padx=dp(24), pady=(0, dp(16)))
        tk.Label(path_row, text="ino 경로:",
                 bg=T["card"], fg=T["text_sub"],
                 font=F["sub"]).pack(side="left")
        self.ino_path_var = tk.StringVar(value="")
        tk.Entry(path_row, textvariable=self.ino_path_var,
                 width=52, font=F["mono"],
                 bg=T["bg"], fg=T["text_main"],
                 relief="flat",
                 highlightbackground=T["border"],
                 highlightthickness=dp(1)
                 ).pack(side="left", padx=dp(8), ipady=dp(5))
        PillButton(path_row, "…", width=3,
                   command=self._browse_ino,
                   bg=T["border"], fg=T["text_sub"]
                   ).pack(side="left")

    # ── ④ 파일 카드 ──────────────────────────────────────────────
    def _build_file_card(self, P):
        card  = Card(self.main); card.pack(fill="x", padx=P, pady=dp(6))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(20))

        top = tk.Frame(inner, bg=T["card"]); top.pack(fill="x")
        tk.Label(top, text="MIDI 파일",
                 bg=T["card"], fg=T["text_main"],
                 font=F["h2"]).pack(side="left")
        PillButton(top, "파일 선택", command=self._browse,
                   width=9).pack(side="right")

        self.path_var = tk.StringVar(value="파일을 선택하세요")
        tk.Label(inner, textvariable=self.path_var,
                 bg=T["card"], fg=T["text_hint"],
                 font=F["sub"], anchor="w"
                 ).pack(fill="x", pady=(dp(8), 0))
        self.file_info = tk.Label(inner, text="",
                                  bg=T["card"], fg=T["text_sub"],
                                  font=F["sub"], anchor="w")
        self.file_info.pack(fill="x")

    # ── ⑤ 플레이어 카드 ──────────────────────────────────────────
    def _build_player_card(self, P):
        card  = Card(self.main); card.pack(fill="x", padx=P, pady=dp(6))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(24))

        trow = tk.Frame(inner, bg=T["card"]); trow.pack(fill="x")
        self.cur_lbl = tk.Label(trow, text="00:00.00",
                                bg=T["card"], fg=T["text_main"],
                                font=F["time"])
        self.cur_lbl.pack(side="left")
        self.tot_lbl = tk.Label(trow, text="/ 00:00.00",
                                bg=T["card"], fg=T["text_sub"],
                                font=F["time_s"])
        self.tot_lbl.pack(side="left", padx=dp(12), pady=dp(8))

        seek_wrap = tk.Frame(inner, bg=T["card"])
        seek_wrap.pack(fill="x", pady=dp(16))
        self.seek_var    = tk.DoubleVar(value=0)
        self.seek_canvas = tk.Canvas(seek_wrap, height=dp(10),
                                     bg=T["card"], highlightthickness=0,
                                     cursor="hand2")
        self.seek_canvas.pack(fill="x")
        self.seek_canvas.bind("<Configure>",       self._draw_seek)
        self.seek_canvas.bind("<ButtonPress-1>",   self._seek_click)
        self.seek_canvas.bind("<B1-Motion>",       self._seek_drag)
        self.seek_canvas.bind("<ButtonRelease-1>", self._seek_release)

        brow = tk.Frame(inner, bg=T["card"]); brow.pack(pady=(dp(4), 0))
        self.btn_play  = PillButton(brow, "▶  재생",   command=self._play,
                                    bg=T["primary"], width=11)
        self.btn_pause = PillButton(brow, "⏸  일시정지", command=self._pause,
                                    bg=T["pause"],   width=13)
        self.btn_stop  = PillButton(brow, "■  정지",   command=self._stop,
                                    bg=T["danger"],  width=9)
        for b in (self.btn_play, self.btn_pause, self.btn_stop):
            b.pack(side="left", padx=dp(8))

    # ── ⑥ 모터 상태 카드 ─────────────────────────────────────────
    def _build_motor_card(self, P):
        card  = Card(self.main); card.pack(fill="x", padx=P, pady=dp(6))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(20))

        tk.Label(inner, text="모터 상태",
                 bg=T["card"], fg=T["text_main"],
                 font=F["h2"]).pack(anchor="w", pady=(0, dp(14)))

        self.motor_wrap = tk.Frame(inner, bg=T["card"])
        self.motor_wrap.pack(fill="x")
        self.motor_wrap.bind("<Configure>", self._relayout_motors)

        self.motor_note_lbl = []
        self.motor_freq_lbl = []
        self.motor_bars     = []
        self._motor_cols    = []

        for i in range(NUM_MOTORS):
            col = tk.Frame(self.motor_wrap, bg=T["card"],
                           highlightbackground=T["border"],
                           highlightthickness=dp(1))
            self._motor_cols.append(col)

            th = tk.Frame(col, bg=T["card"]); th.pack(fill="x")
            tk.Label(th, text="●", bg=T["card"], fg=MOTOR_COLORS[i],
                     font=(_SANS, sp(16))).pack(side="left")
            tk.Label(th, text=f"M{i}  {MOTOR_NAMES[i]}",
                     bg=T["card"], fg=T["text_sub"],
                     font=F["sub"]).pack(side="left", padx=dp(6))

            nl = tk.Label(col, text="—", bg=T["card"], fg=T["text_main"],
                          font=F["mono_b"])
            nl.pack(anchor="w", pady=(dp(8), 0))
            self.motor_note_lbl.append(nl)

            fl = tk.Label(col, text="", bg=T["card"], fg=T["text_hint"],
                          font=F["mono_s"])
            fl.pack(anchor="w")
            self.motor_freq_lbl.append(fl)

            bar = tk.Canvas(col, height=dp(6), bg=T["card"], highlightthickness=0)
            bar.pack(fill="x", pady=(dp(10), 0))
            self.motor_bars.append(bar)

    def _relayout_motors(self, event=None):
        w = self.motor_wrap.winfo_width()
        if w < 10: return
        MIN_CARD_W   = dp(180)
        cols_per_row = max(1, min(NUM_MOTORS, w // MIN_CARD_W))
        for col in self._motor_cols:
            col.grid_forget()
        for i, col in enumerate(self._motor_cols):
            col.grid(row=i//cols_per_row, column=i%cols_per_row,
                     sticky="nsew", padx=dp(6), pady=dp(4),
                     ipadx=dp(12), ipady=dp(12))
        for c in range(cols_per_row):
            self.motor_wrap.columnconfigure(c, weight=1)
        for c in range(cols_per_row, NUM_MOTORS):
            self.motor_wrap.columnconfigure(c, weight=0)

    # ── ⑦ 로그 카드 ──────────────────────────────────────────────
    def _build_log_card(self, P):
        card  = Card(self.main)
        card.pack(fill="x", padx=P, pady=(dp(6), dp(32)))
        inner = tk.Frame(card, bg=T["card"])
        inner.pack(fill="x", padx=dp(24), pady=dp(20))

        top = tk.Frame(inner, bg=T["card"]); top.pack(fill="x")
        tk.Label(top, text="로그", bg=T["card"], fg=T["text_main"],
                 font=F["h2"]).pack(side="left")
        PillButton(top, "지우기",
                   command=lambda: self.log.delete("1.0","end"),
                   bg=T["border"], fg=T["text_sub"],
                   width=7).pack(side="right")

        self.log = scrolledtext.ScrolledText(
            inner, height=10,
            bg=T["bg"], fg=T["text_main"],
            font=F["mono"], relief="flat",
            borderwidth=0, insertbackground=T["text_main"]
        )
        self.log.pack(fill="x", pady=(dp(12), 0))
        self.log.tag_config("ok",   foreground=T["success"])
        self.log.tag_config("err",  foreground=T["danger"])
        self.log.tag_config("info", foreground=T["primary"])
        self.log.tag_config("warn", foreground=T["warn"])

    # ── 시크바 ───────────────────────────────────────────────────
    def _draw_seek(self, _=None):
        c = self.seek_canvas
        w = c.winfo_width(); h = dp(10)
        if w < 2: return
        ratio = self.seek_var.get() / 1000.0
        fx = max(0, int(w * ratio)); r = h // 2
        c.delete("all")
        c.create_oval(0, 0, h, h,   fill=T["track_bg"], outline="")
        c.create_oval(w-h, 0, w, h, fill=T["track_bg"], outline="")
        c.create_rectangle(r, 0, w-r, h, fill=T["track_bg"], outline="")
        if fx > r:
            c.create_oval(0, 0, h, h,       fill=T["track_fg"], outline="")
            c.create_rectangle(r, 0, fx, h, fill=T["track_fg"], outline="")
        hx = max(r, fx)
        c.create_oval(hx-r-dp(2), -dp(2), hx+r+dp(2), h+dp(2),
                      fill="#BFDBFE", outline="")
        c.create_oval(hx-r, 0, hx+r, h, fill=T["primary"], outline="")

    def _seek_click(self, e):   self._seeking=True;  self._seek_set(e.x)
    def _seek_drag(self, e):
        if self._seeking: self._seek_set(e.x)
    def _seek_release(self, e):
        self._seeking = False
        if self.total_ms > 0:
            ms = self.seek_var.get()/1000*self.total_ms
            self.cur_ms = ms; self.player.seek(ms)
            self._log(f"Seek → {ms_to_str(ms)}", "info")

    def _seek_set(self, x):
        w = self.seek_canvas.winfo_width()
        r = max(0.0, min(1.0, x/w))
        self.seek_var.set(r*1000)
        if self.total_ms > 0:
            self.cur_lbl.config(text=ms_to_str(r*self.total_ms))
        self._draw_seek()

    # ── 파일 / 연결 / 펌웨어 ─────────────────────────────────────
    def _browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("MIDI","*.mid *.midi"),("All","*.*")])
        if not path: return
        self.path_var.set(os.path.basename(path))
        self._log(f"파싱 중: {os.path.basename(path)}", "info")
        try:
            bands = auto_bands(path)
            self.events, self.total_ms = parse_midi(path, bands=bands)
            self.player.load(self.events, self.total_ms)
            self.file_info.config(
                text=f"이벤트 {len(self.events)}개  ·  {ms_to_str(self.total_ms)}")
            self.tot_lbl.config(text=f"/ {ms_to_str(self.total_ms)}")
            band_names = ["고음","중고음","중저음","저음"]
            for i, (lo, hi) in enumerate(bands):
                self._log(
                    f"  M{i} [{band_names[i]}]  "
                    f"노트 {lo}({freq_to_name(note_to_freq(lo))}) "
                    f"~ {hi}({freq_to_name(note_to_freq(hi))})", "info")
            self._log(f"로드 완료  ({len(self.events)}개)", "ok")
        except Exception as e:
            self._log(f"파싱 오류: {e}", "err")

    def _connect(self):
        r = self.writer.connect(self.port_var.get(), BAUD)
        if r is True:
            self.conn_dot.config(fg=T["success"])
            self.conn_txt.config(text="연결됨")
            self._log(f"연결 완료  {self.port_var.get()}", "ok")
        else:
            self.conn_dot.config(fg=T["danger"])
            self.conn_txt.config(text="연결 실패")
            self._log(f"실패: {r}", "err")

    def _browse_ino(self):
        path = filedialog.askopenfilename(
            filetypes=[("Arduino","*.ino"),("All","*.*")])
        if path: self.ino_path_var.set(path)

    def _compile_only(self):
        ino = self.ino_path_var.get()
        if not ino or not os.path.exists(ino):
            self._log("ino 파일 경로를 먼저 선택하세요.", "warn"); return
        threading.Thread(target=self._run_arduino_cli,
                         args=(ino, False), daemon=True).start()

    def _upload_firmware(self):
        ino = self.ino_path_var.get()
        if not ino or not os.path.exists(ino):
            self._log("ino 파일 경로를 먼저 선택하세요.", "warn"); return
        threading.Thread(target=self._run_arduino_cli,
                         args=(ino, True), daemon=True).start()

    def _run_arduino_cli(self, ino_path, upload):
        def log(msg, tag=""): self.after(0, self._log, msg, tag)
        ino_dir = os.path.dirname(ino_path)
        board   = "arduino:avr:mega"
        port    = self.port_var.get()

        log("컴파일 중...", "info")
        r = subprocess.run(
            ["arduino-cli", "compile", "--fqbn", board, ino_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stderr   = r.stderr.decode("utf-8", errors="replace")
        real_err = [l for l in stderr.splitlines()
                    if l.strip() and "nRFMicro" not in l]
        if r.returncode != 0:
            log("컴파일 실패 ✗", "err")
            for l in real_err: log("  "+l, "err")
            return
        log("컴파일 성공 ✓", "ok")
        for l in real_err: log("  "+l)

        if not upload:
            log("업로드 건너뜀 (컴파일 전용 모드)", "warn"); return

        if self.writer.ser and self.writer.ser.is_open:
            log("Serial 일시 해제 중...", "info")
            try: self.writer.ser.close()
            except: pass

        log(f"업로드 중 ({port})...", "info")
        r = subprocess.run(
            ["arduino-cli", "upload", "-p", port, "--fqbn", board, ino_dir],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stderr = r.stderr.decode("utf-8", errors="replace")
        if r.returncode != 0:
            log("업로드 실패 ✗", "err")
            for l in stderr.splitlines():
                if l.strip(): log("  "+l, "err")
            return
        log("업로드 완료 🚀  Arduino가 재시작됩니다.", "ok")

        log("Serial 재연결 중...", "info")
        time.sleep(2)
        r = self.writer.connect(port, BAUD)
        if r is True:
            self.after(0, self.conn_dot.config, {"fg": T["success"]})
            self.after(0, self.conn_txt.config, {"text": "연결됨"})
            log("재연결 완료 ✓", "ok")
        else:
            log(f"재연결 실패: {r}", "err")

    # ── 재생 제어 ─────────────────────────────────────────────────
    def _play(self):
        if not self.events:
            self._log("MIDI 파일을 먼저 선택하세요.", "warn"); return
        if self.player.is_paused:
            self.player.resume(); self._log("재개 ▶", "info")
        else:
            self.player.play(start_ms=self.cur_ms if self.cur_ms>0 else 0.0)
            self._log("재생 시작 ▶", "ok")

    def _pause(self):
        self.player.pause(); self._log("일시정지 ⏸", "info")

    def _stop(self):
        self.player.stop()
        self.cur_ms = 0.0; self.seek_var.set(0)
        self._draw_seek(); self.cur_lbl.config(text="00:00.00")
        for i in range(NUM_MOTORS):
            self.motor_note_lbl[i].config(text="—", fg=T["text_main"])
            self.motor_freq_lbl[i].config(text="")
            self._draw_bar(i, 0)
        self._log("정지 ■", "info")

    def _on_end(self):
        self._stop(); self._log("재생 완료 ✓", "ok")

    # ── 실시간 갱신 ──────────────────────────────────────────────
    def _upd_pos(self, ms):
        self.cur_ms = ms
        self.cur_lbl.config(text=ms_to_str(ms))
        if self.total_ms > 0 and not self._seeking:
            self.seek_var.set(ms/self.total_ms*1000)
            self._draw_seek()

    def _upd_freqs(self, freqs):
        MAX_F = 1000.0
        for i, f in enumerate(freqs):
            self.motor_note_lbl[i].config(
                text=freq_to_name(f),
                fg=MOTOR_COLORS[i] if f else T["text_hint"])
            self.motor_freq_lbl[i].config(text=f"{f} Hz" if f else "REST")
            self._draw_bar(i, min(f/MAX_F, 1.0))

    def _draw_bar(self, i, ratio):
        c = self.motor_bars[i]
        w = c.winfo_width()
        if w < 2: return
        c.delete("all")
        c.create_rectangle(0, 0, w, dp(6), fill=T["border"], outline="")
        if ratio > 0:
            c.create_rectangle(0, 0, int(w*ratio), dp(6),
                               fill=MOTOR_COLORS[i], outline="")

    def _poll(self):
        for bar in self.motor_bars:
            bar.event_generate("<Configure>")
        self.after(200, self._poll)

    def _log(self, msg, tag=""):
        ts = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{ts}]  {msg}\n", tag)
        self.log.see("end")


if __name__ == "__main__":
    app = App()
    app.mainloop()