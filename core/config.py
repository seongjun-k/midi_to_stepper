"""config.json 로드/저장. (docs/PLAN.md §1)"""
import json
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")

DEFAULT_CONFIG = {
    "num_motors": 6,
    "serial_port": "",          # 빈 문자열 = 자동 탐지
    "baud": 115200,
    "fqbn": "esp32:esp32:esp32s3",
    "bands": None,               # null = auto_bands()로 자동 산출
    "motor_names": ["고음1", "고음2", "중고음", "중저음", "저음1", "저음2"],
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
    assert c["num_motors"] == 6 and c["baud"] == 115200
    assert os.path.exists(CONFIG_PATH)
    print("config self-check ok:", c)
