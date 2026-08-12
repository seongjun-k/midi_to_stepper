"""유튜브 검색 → mp3 다운로드 → MIDI 변환 파이프라인 (stepper_midi_kiosk.py 이식).

basic-pitch는 함수 내부에서만 지연 import한다 — 설치가 안 되어 있어도
서버 자체(검색/재생 등 나머지 기능)는 정상 기동해야 하기 때문이다.
"""
import glob
import json
import os
import urllib.parse
import urllib.request

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


# ── 주크박스 라이브러리 ────────────────────────────────────────────────
# 캐시 루트의 {vid}.* 는 _find_cached_audio가 오디오로 잡으므로, 메타(json)·
# 썸네일은 반드시 하위폴더 library/ 에 분리해 오인식을 막는다.
def _lib_dir(cache_dir):
    return os.path.join(cache_dir, "library")


def _find_thumb(cache_dir, video_id):
    hits = glob.glob(os.path.join(_lib_dir(cache_dir), f"{video_id}.*"))
    return next((h for h in hits if not h.endswith(".json")), None)


def save_library_entry(cache_dir, video_id, title, artist, thumbnail_url=""):
    """변환 완료된 곡의 메타(제목/아티스트/썸네일 URL)를 library/{vid}.json에 저장하고,
    썸네일 이미지를 library/ 에 로컬 캐시한다(오프라인 대비). 썸네일 실패는 치명적 아님."""
    d = _lib_dir(cache_dir)
    os.makedirs(d, exist_ok=True)
    meta = {"videoId": video_id, "title": title, "artist": artist, "thumbnail": thumbnail_url}
    with open(os.path.join(d, f"{video_id}.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    if thumbnail_url and not _find_thumb(cache_dir, video_id):
        try:
            ext = os.path.splitext(urllib.parse.urlparse(thumbnail_url).path)[1] or ".jpg"
            req = urllib.request.Request(thumbnail_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = resp.read()
            with open(os.path.join(d, f"{video_id}{ext}"), "wb") as f:
                f.write(data)
        except Exception:
            pass  # 원격 URL로 폴백 (frontend onerror)
    return meta


def list_library(cache_dir):
    """재생 가능한(=.mid 존재) 저장곡 목록. 최근 추가순."""
    out = []
    for meta_path in glob.glob(os.path.join(_lib_dir(cache_dir), "*.json")):
        try:
            with open(meta_path, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        vid = m.get("videoId") or os.path.splitext(os.path.basename(meta_path))[0]
        mid = os.path.join(cache_dir, f"{vid}.mid")
        if not os.path.exists(mid):
            continue
        out.append({
            "videoId": vid,
            "title": m.get("title", ""),
            "artist": m.get("artist", ""),
            "thumbnail": m.get("thumbnail", ""),
            "hasThumb": _find_thumb(cache_dir, vid) is not None,
            "_mtime": os.path.getmtime(mid),
        })
    out.sort(key=lambda x: x["_mtime"], reverse=True)
    for x in out:
        del x["_mtime"]
    return out


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

    # 라이브러리 저장/조회 라운드트립 (네트워크 없이 — thumbnail_url="" 로 다운로드 스킵)
    import tempfile
    tmp = tempfile.mkdtemp()
    open(os.path.join(tmp, "abc.mid"), "w").close()  # 재생 가능 표식
    save_library_entry(tmp, "abc", "Song", "Artist", "")
    open(os.path.join(tmp, "noconv.json"), "w").close()  # noise
    lib = list_library(tmp)
    assert len(lib) == 1 and lib[0]["videoId"] == "abc" and lib[0]["title"] == "Song"
    save_library_entry(tmp, "zzz", "NoMidi", "X", "")  # .mid 없으면 목록 제외
    assert len(list_library(tmp)) == 1
    print("youtube self-check ok")
