"""config.yaml -> tip güvenli yapılandırma nesneleri."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

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
class FlowConfig:
    """Akış optimize edici (`flowopt.py`) ayarları."""

    enabled: bool = True
    # Optimizer'ın 5 sn'lik eşik döngüsünden ayrı ve daha seyrek: LP çözümü
    # ucuz ama bedava değil, ve sonucu her saniye değişmiyor.
    interval_seconds: float = 15.0
    # Bu kadarın altındaki eksik "geri çek" listesine girmiyor — ölçüm
    # gürültüsünü aksiyona çevirmenin anlamı yok.
    min_pullback_mbps: float = 0.5


@dataclass
class EnforceConfig:
    """İnfaz katmanı ayarları.

    ⚠️ `mode` varsayılanı `golge` ve öyle kalmalı. Canlı mod ancak üzerinde
    doğrulama yapılabilecek gerçek bir cihaz varken açılmalı; o cihaz
    olmadan üretilen komutların doğruluğu yalnız *metin* olarak sınandı,
    davranış olarak değil.
    """

    enabled: bool = True
    mode: str = "golge"                 # golge | canli
    # **Kapsam başına ayrı sürücü.** Ağın iki yerine iki farklı dille
    # yazıyoruz: çekirdekteki router `tc`, uçtaki Windows domain
    # `New-NetQosPolicy`. Tek sürücü seçseydik biri sessizce düşerdi.
    #   core = router kesişimi  → indirme kısıtı + yol seçimi
    #   edge = Windows domain uç → yükleme kısıtı + DSCP damgası
    core_driver: str = ""               # boş = `driver` alanına düş
    edge_driver: str = ""
    # Her ikisi de boşsa bu tek sürücü tüm kapsamlara bakar.
    driver: str = "anlat"               # anlat | linux | windows
    # Kısan her kural operatör onayı bekler. Kapatmak, halüsinasyon değil
    # ama yine de denetimsiz bir kısıt akışı demek — bilerek açık bırakıldı.
    require_approval: bool = True
    # Linux sürücüsü için arayüz adları ve çıkış → yönlendirme tablosu.
    wan_if: str = "eth1"
    lan_if: str = "eth0"
    tables: dict[str, str] = field(default_factory=dict)


@dataclass
class AIConfig:
    provider: str = "auto"          # auto | foundry | ollama | mock
    model: str = "phi-4-mini"       # Foundry Local takma adı
    base_url: str = ""              # boş = sağlayıcı uç noktayı kendi keşfeder
    temperature: float = 0.2
    timeout_seconds: float = 120.0
    analysis_interval_seconds: float = 30.0
    # **Modelin sisteme dokunduğu tek yol.** Duruma göre çözücünün hedefini
    # kuruyor: sınıf sırası, taban profili, ağırlık seviyeleri. Sayı üretmiyor;
    # sayıyı LP hesaplıyor. Kapatılırsa hedef sabit varsayılanda kalır ve
    # sistem çalışmaya devam eder.
    policy_enabled: bool = True
    # Analizden seyrek: hedefi her 30 saniyede savurmak ağı sallar.
    policy_interval_seconds: float = 120.0
    # **Akışı doğrudan modele kurdurma.** Politika yolunda model hedefi
    # kuruyor, sayıyı LP hesaplıyordu; burada sayıyı model veriyor ve LP
    # hakem oluyor: kalanı dolduruyor, kapasiteyi aştırmıyor.
    # Ölçüldü: model tek başına optimumun %22'si, hibritte kayıp 0 ve akışın
    # %16-24'ü modelin kararı.
    flow_enabled: bool = True
    max_snapshot_flows: int = 25
    # İstem uzunluğuna sert tavan. Model bağlamı 4096 token; `lm_head` çıktısı
    # istemle birlikte büyüyor ve yük altında ONNX Runtime 1.2 GB ayırmaya
    # çalışıp düştü. ~3500 karakter kabaca 1100 token — analiz için yeterli,
    # bağlamı patlatmaktan uzak.
    max_snapshot_chars: int = 3500

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
class ClassifyConfig:
    """Trafik sınıflandırma — akışın sınıfını **biz** üretiyoruz.

    Simülasyonda etiket zaten doğru geliyor; orada sınıflandırıcıyı devreye
    almak %100'ü %97.7'ye düşürmek olurdu. O yüzden varsayılan **gölge**:
    sınıflandırıcı her akışı okuyor, kararını simülatörün etiketiyle
    karşılaştırıyor, ama **hiçbir şeyi değiştirmiyor**. Uyum oranı
    `/api/classify` ucunda görünüyor.

    `mode: "canli"` sınıfı gerçekten yazar — canlı yakalamada etiket
    olmadığı için orada tek kaynak bu.
    """

    enabled: bool = True
    mode: str = "golge"             # golge | canli
    # Sysmon Event 3 süreç adını veriyor ve en sağlam katman o. Kapalıyken
    # sistem yalnız ağ görünümüne bakıyor — ölçüldü: %100 yerine %97.7.
    use_process: bool = True
    # Uyum örneklemesi kaç akışta bir tutulacak. Hepsini saklamak gereksiz;
    # oran birkaç yüz örnekte oturuyor.
    sample_size: int = 500


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
    flow: FlowConfig = field(default_factory=FlowConfig)
    enforce: EnforceConfig = field(default_factory=EnforceConfig)
    classify: ClassifyConfig = field(default_factory=ClassifyConfig)
    # Topoloji düz bir dataclass değil (kenar listesi), o yüzden ham
    # haliyle taşınıp `Topology.from_config` tarafından ayrıştırılıyor.
    topology_raw: dict[str, Any] | None = None


def _build(cls: type, raw: Any) -> Any:
    """İç içe dataclass'ları dict'ten kurar; bilinmeyen anahtarları yok sayar.

    `from __future__ import annotations` yüzünden `f.type` bir **string**;
    `is_dataclass(f.type)` hiçbir zaman doğru olmuyordu ve iç içe dataclass
    yolu sessizce ölüydü. Üst seviyede `_NESTED` işi görüyor diye fark
    edilmiyordu, ama `AIConfig` gibi bir sınıfa iç içe alan eklendiğinde
    hatasız şekilde yanlış çalışacaktı: dict olduğu gibi atanacaktı.

    Çözüm: anotasyonları `get_type_hints` ile gerçek tiplere çöz.
    """
    if not isinstance(raw, dict):
        return cls()
    try:
        hints = get_type_hints(cls)
    except Exception:          # ileriye dönük referans çözülemezse
        hints = {}
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        value = raw[f.name]
        hint = hints.get(f.name)
        if isinstance(hint, type) and is_dataclass(hint):
            kwargs[f.name] = _build(hint, value)
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
    "flow": FlowConfig,
    "enforce": EnforceConfig,
    "classify": ClassifyConfig,
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
    cfg.topology_raw = raw.get("topology")
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
