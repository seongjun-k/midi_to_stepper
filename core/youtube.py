"""유튜브 검색 → mp3 다운로드 → MIDI 변환 파이프라인 (stepper_midi_kiosk.py 이식).

basic-pitch는 함수 내부에서만 지연 import한다 — 설치가 안 되어 있어도
서버 자체(검색/재생 등 나머지 기능)는 정상 기동해야 하기 때문이다.
"""
import glob
import os

from ytmusicapi import YTMusic

NON_ORIGINAL_KEYWORDS = [
    "cover", "커버", "live", "라이브", "remix", "리믹스",
    "karaoke", "노래방", "instrumental", "mr", "반주",
    "acoustic", "acoustic ver", "piano ver", "version",
    "tribute", "parody", "패러디", "nightcore",
]

_yt = None


def _client():
    global _yt
    if _yt is None:
        _yt = YTMusic()
    return _yt


def extract_artist(r):
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


def is_non_original(title):
    lower = title.lower()
    return any(kw in lower for kw in NON_ORIGINAL_KEYWORDS)


def original_score(r):
    """원곡 우선 정렬 점수 (낮을수록 상단)."""
    score = 0
    if r.get("resultType") != "song":
        score += 5
    if is_non_original(r.get("title", "")):
        score += 10
    if r.get("album") and isinstance(r.get("album"), dict) and r["album"].get("name"):
        score -= 1
    return score


def fmt_duration(raw):
    if not raw:
        return ""
    if isinstance(raw, int):
        return f"{raw // 60}:{raw % 60:02d}"
    return str(raw)


def search(query, limit=20):
    yt = _client()
    try:
        raw = yt.search(query, filter="songs", limit=limit)
        candidates = [r for r in raw if r.get("videoId")]
    except Exception:
        raw, candidates = [], []

    # ponytail: ytmusicapi 1.12.x 파서가 현 유튜브 응답에서 songs=0건·videos=잡탕만 내는
    # 상류 버그(2026-07) 우회 — 이미 의존 중인 yt-dlp의 일반 유튜브 검색으로 폴백.
    # ytmusicapi가 고쳐지면 이 블록 제거 가능.
    if not candidates:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                               "extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        candidates = [{
            "videoId": e["id"],
            "title": e.get("title", ""),
            "artists": [{"name": e.get("channel") or e.get("uploader") or ""}],
            "duration_seconds": e.get("duration"),
            "thumbnails": e.get("thumbnails") or [],
        } for e in (info.get("entries") or []) if e.get("id")]

    if not candidates:
        raise ValueError(f"'{query}'에 대한 재생 가능한 결과가 없습니다")

    candidates.sort(key=original_score)
    results = candidates[:5]
    return [{
        "videoId": r.get("videoId", ""),
        "title": r.get("title", ""),
        "artist": extract_artist(r),
        "album": r.get("album", {}).get("name", "") if isinstance(r.get("album"), dict) else "",
        "duration": fmt_duration(r.get("duration") or r.get("duration_seconds")),
        "thumbnail": (r.get("thumbnails") or [{}])[-1].get("url", ""),
    } for r in results]


_bp_model = None


def preload_model():
    """basic-pitch 모델을 미리 로드해 재사용 (TF 임포트 ~1.3s + 모델 로드 ~0.5s를
    첫 변환 요청에서 서버 시작 시점으로 이동). 미설치면 조용히 None."""
    global _bp_model
    if _bp_model is None:
        try:
            from basic_pitch.inference import Model
            from basic_pitch import ICASSP_2022_MODEL_PATH
            _bp_model = Model(ICASSP_2022_MODEL_PATH)
        except ImportError:
            return None
    return _bp_model


def _find_cached_audio(cache_dir, video_id):
    hits = glob.glob(os.path.join(cache_dir, f"{video_id}.*"))
    return next((h for h in hits if not h.endswith(".mid")), None)


def download_and_convert(video_id, cache_dir, on_progress=None):
    """오디오 다운로드(yt-dlp) → MIDI 변환(basic-pitch). 둘 다 cache_dir에 캐시.
    on_progress(ratio: float, msg: str) 콜백으로 진행률 통지.
    트랜스코딩 없이 원본 컨테이너(webm/m4a)를 그대로 받아 basic-pitch에 직접 입력
    (mp3 변환 대비 다운로드 단계 실측 4.5s→1.9s)."""
    os.makedirs(cache_dir, exist_ok=True)
    midi_path = os.path.join(cache_dir, f"{video_id}.mid")
    url = f"https://music.youtube.com/watch?v={video_id}"

    def notify(ratio, msg):
        if on_progress:
            on_progress(ratio, msg)

    audio_path = _find_cached_audio(cache_dir, video_id)
    if audio_path is None:
        import yt_dlp

        def _hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    pct = d.get("downloaded_bytes", 0) / total
                    notify(0.05 + pct * 0.40, "다운로드 중...")
            elif d.get("status") == "finished":
                notify(0.45, "다운로드 완료")

        ydl_opts = {
            "format": "bestaudio[abr<=160]/bestaudio/best",
            "concurrent_fragment_downloads": 4,
            "outtmpl": os.path.join(cache_dir, f"{video_id}.%(ext)s"),
            "restrictfilenames": True,
            "quiet": True, "no_warnings": True,
            "progress_hooks": [_hook],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        audio_path = _find_cached_audio(cache_dir, video_id)
        if audio_path is None:
            raise RuntimeError("다운로드 실패: 오디오 파일이 생성되지 않음")
    else:
        notify(0.45, "캐시된 오디오 사용")

    if not os.path.exists(midi_path):
        # ponytail: basic-pitch는 진행률 훅을 제공하지 않아 시작/완료 시점만 통지한다.
        # 세밀한 진행률이 필요해지면 subprocess + 로그 파싱으로 추정.
        notify(0.5, "MIDI 변환 중...")
        model = preload_model()
        if model is None:
            raise RuntimeError("basic-pitch 미설치: pip install basic-pitch")
        from basic_pitch.inference import predict as bp_predict
        _, midi_data, _ = bp_predict(audio_path, model)
        midi_data.write(midi_path)
    else:
        notify(0.95, "캐시된 MIDI 사용")

    notify(1.0, "완료")
    return midi_path


if __name__ == "__main__":
    # ponytail: 네트워크 호출 없이 순수 함수만 self-check
    assert is_non_original("BTS Dynamite (Live)") is True
    assert is_non_original("BTS Dynamite") is False
    assert original_score({"resultType": "song", "title": "x", "album": {"name": "a"}}) == -1
    assert fmt_duration(185) == "3:05"
    assert extract_artist({"artists": [{"name": "IU"}]}) == "IU"
    print("youtube self-check ok")
