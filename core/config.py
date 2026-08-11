"""config.json 로드/저장. (docs/PLAN.md §1)"""
import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

DEFAULT_CONFIG = {
    "num_motors": 5,             # Robin Nano 드라이버 슬롯 X/Y/Z/E0/E1 기준. ESP32(최대 8)면 늘려도 된다
    "serial_port": "",          # 빈 문자열 = 자동 탐지
    "baud": 115200,
    "fqbn": "esp32:esp32:esp32s3",
    "bands": None,               # null = auto_bands()로 자동 산출
    "motor_names": ["고음", "중고음", "중음", "중저음", "저음"],
    "cache_dir": "cache",        # 상대경로면 PROJECT_ROOT 기준
}


def load():
    """config.json이 없으면 기본값으로 생성 후 반환."""
    if not os.path.exists(CONFIG_PATH):
        save(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def resolve_cache_dir(cfg):
    cd = cfg.get("cache_dir") or "cache"
    return cd if os.path.isabs(cd) else os.path.join(PROJECT_ROOT, cd)


if __name__ == "__main__":
    c = load()
    assert len(c["motor_names"]) == c["num_motors"], "motor_names 개수가 num_motors와 다르다"
    assert c["baud"] == 115200
    assert os.path.exists(CONFIG_PATH)
    print("config self-check ok:", c)
