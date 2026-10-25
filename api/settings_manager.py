import json
import os
from pathlib import Path

SETTINGS_FILE = Path(__file__).parent / "settings.json"

PROVIDER_PRESETS = {
    "deepseek": {
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-ai/DeepSeek-V3.2"],
    },
    "doubao": {
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-3-4k", "doubao-3-8k"],
    },
    "minimax": {
        "api_base": "https://api.minimaxi.com/anthropic",
        "models": ["abab6-chat", "abab6.5-chat"],
    },
    "kimi": {
        "api_base": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k"],
    },
    "other": {
        "api_base": "",
        "models": ["custom"],
    },
}

DEFAULT_SETTINGS = {
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "api_key": os.getenv("deepseek_API_KEY", ""),
    "api_base": "https://api.deepseek.com",
    "temperature": 0.0,
    "custom_model": "",
    "custom_api_base": "",
}


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            merged = {**DEFAULT_SETTINGS, **saved}
            return merged
        except (json.JSONDecodeError, KeyError):
            pass
    save_settings(DEFAULT_SETTINGS)
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def get_provider_presets() -> dict:
    return PROVIDER_PRESETS