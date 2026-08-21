"""config.yaml -> tip güvenli yapılandırma nesneleri."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class LinkConfig:
    downlink_mbps: float = 200.0
    uplink_mbps: float = 20.0
    congestion_threshold: float = 0.80
    critical_threshold: float = 0.94

    @property
    def downlink_bps(self) -> float:
        return self.downlink_mbps * 1_000_000

    @property
    def uplink_bps(self) -> float:
        return self.uplink_mbps * 1_000_000


@dataclass
class CollectorConfig:
    tick_seconds: float = 1.0
    window_seconds: int = 60


@dataclass
class OptimizerConfig:
    enabled: bool = True
    interval_seconds: float = 5.0
    auto_apply: bool = False
    hog_share_threshold: float = 0.35
    min_confidence_to_apply: float = 0.7


@dataclass
class AIConfig:
    provider: str = "auto"          # auto | foundry | ollama | mock
    model: str = "phi-4-mini"       # Foundry Local takma adı
    base_url: str = ""              # boş = sağlayıcı uç noktayı kendi keşfeder
    temperature: float = 0.2
    timeout_seconds: float = 120.0
    analysis_interval_seconds: float = 30.0
    max_snapshot_flows: int = 25

    # Ollama geliştirme sırasında yedek olarak duruyor; model adlandırması
    # Foundry'den farklı (phi4-mini ↔ phi-4-mini), o yüzden ayrı alanlar.
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "phi4-mini"


@dataclass
class APIConfig:
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class StorageConfig:
    path: str = "data/ntc.db"
    retain_hours: int = 24

    def resolved_path(self) -> Path:
        p = Path(self.path)
        return p if p.is_absolute() else REPO_ROOT / p


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    mode: str = "simulation"
    link: LinkConfig = field(default_factory=LinkConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    api: APIConfig = field(default_factory=APIConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _build(cls: type, raw: Any) -> Any:
    """İç içe dataclass'ları dict'ten kurar; bilinmeyen anahtarları yok sayar."""
    if not isinstance(raw, dict):
        return cls()
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if is_dataclass(f.type) if isinstance(f.type, type) else False:
            kwargs[f.name] = _build(f.type, value)  # type: ignore[arg-type]
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


_NESTED = {
    "link": LinkConfig,
    "collector": CollectorConfig,
    "optimizer": OptimizerConfig,
    "ai": AIConfig,
    "api": APIConfig,
    "storage": StorageConfig,
    "logging": LoggingConfig,
}


def load_config(path: str | Path | None = None) -> Config:
    """config.yaml'ı okur. Dosya yoksa saf varsayılanlarla döner.

    NTC_ ön ekli ortam değişkenleri dosyayı ezer, örn:
      NTC_AI__MODEL=llama3.2   NTC_API__PORT=9000   NTC_MODE=live
    """
    cfg_path = Path(path) if path else REPO_ROOT / "config.yaml"
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    _apply_env_overrides(raw)

    cfg = Config()
    if "mode" in raw:
        cfg.mode = str(raw["mode"])
    for key, cls in _NESTED.items():
        if key in raw:
            setattr(cfg, key, _build(cls, raw[key]))
    return cfg


def _coerce(text: str) -> Any:
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _apply_env_overrides(raw: dict[str, Any]) -> None:
    for env_key, env_val in os.environ.items():
        if not env_key.startswith("NTC_"):
            continue
        path_parts = [p.lower() for p in env_key[4:].split("__")]
        cursor = raw
        for part in path_parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path_parts[-1]] = _coerce(env_val)
