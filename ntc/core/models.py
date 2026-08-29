"""Sistemin tamamında dolaşan ortak veri tipleri."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class TrafficClass(str, Enum):
    """QoS sınıfları — önceliği yüksekten düşüğe."""

    REALTIME = "realtime"        # VoIP, video konferans — gecikmeye çok duyarlı
    INTERACTIVE = "interactive"  # SSH, RDP, web, oyun
    STREAMING = "streaming"      # Netflix, YouTube, kamera — tamponlu
    BULK = "bulk"                # yedek, güncelleme, büyük indirme
    BACKGROUND = "background"    # telemetri, DNS, keepalive

    @property
    def priority(self) -> int:
        return _CLASS_PRIORITY[self]


_CLASS_PRIORITY = {
    TrafficClass.REALTIME: 0,
    TrafficClass.INTERACTIVE: 1,
    TrafficClass.STREAMING: 2,
    TrafficClass.BULK: 3,
    TrafficClass.BACKGROUND: 4,
}


class DeviceKind(str, Enum):
    WORKSTATION = "workstation"
    LAPTOP = "laptop"
    PHONE = "phone"
    SERVER = "server"
    IOT = "iot"
    CAMERA = "camera"
    GUEST = "guest"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class Direction(str, Enum):
    INBOUND = "inbound"    # dışarıdan içeriye (download)
    OUTBOUND = "outbound"  # içeriden dışarıya (upload)
    LATERAL = "lateral"    # LAN içi


@dataclass
class Device:
    id: str
    ip: str
    mac: str
    hostname: str
    kind: DeviceKind = DeviceKind.UNKNOWN
    trust: float = 0.5           # 0.0 (şüpheli) .. 1.0 (tam güvenilir)
    first_seen: float = field(default_factory=now)
    last_seen: float = field(default_factory=now)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class Flow:
    """Tek bir ağ akışının özeti (5-tuple + hacim)."""

    id: str
    ts: float
    device_id: str
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    proto: str                   # tcp | udp | icmp
    app: str                     # "netflix", "ssh", "windows-update" ...
    traffic_class: TrafficClass
    direction: Direction
    bytes_down: int = 0
    bytes_up: int = 0
    packets: int = 0
    duration: float = 1.0
    rtt_ms: float = 0.0
    retransmits: int = 0
    flags: list[str] = field(default_factory=list)
    # Bağlantıyı açan süreç (canlı modda bağlantı tablosundan / Sysmon Event
    # 3'ten gelir; simülasyonda boş). Sınıflandırıcının **1. katmanı** bu
    # alanı okuyor — `ClassifyAudit` zaten `flow.process` bekliyordu ama alan
    # yoktu, yani canlı modda en sağlam katman hiç ateşlenmeyecekti.
    process: str = ""
    # Akış çözücüsünün bu akışa atadığı çıkış düğümü. Boşsa henüz plan yok
    # ya da tek yol var. **Akış başına** atanır ve akış boyunca değişmez —
    # paketleri iki yola serpiştirmek TCP'yi yavaşlatır (sırasız gelen paket
    # kayıp sanılır, tıkanma penceresi çöker).
    egress: str = ""

    @property
    def total_bytes(self) -> int:
        return self.bytes_down + self.bytes_up

    @property
    def bps(self) -> float:
        return (self.total_bytes * 8) / max(self.duration, 0.001)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["traffic_class"] = self.traffic_class.value
        d["direction"] = self.direction.value
        d["total_bytes"] = self.total_bytes
        return d


@dataclass
class Alert:
    id: str
    ts: float
    severity: Severity
    source: str                  # "optimizer", "ai-analyst", "metrics" ...
    title: str
    detail: str
    device_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


class ActionKind(str, Enum):
    RATE_LIMIT = "rate_limit"          # cihaz/akışa bant sınırı
    PRIORITIZE = "prioritize"          # QoS önceliğini yükselt
    DEPRIORITIZE = "deprioritize"      # QoS önceliğini düşür
    DEFER = "defer"                    # yoğunluk bitene kadar ertele
    REBALANCE = "rebalance"            # sınıflar arası bant payı yeniden dağıt
    REROUTE = "reroute"                # trafiği başka bir çıkıştan akıt
    ADVISE = "advise"                  # sadece öneri, uygulanabilir kural yok


@dataclass
class OptimizationAction:
    id: str
    ts: float
    kind: ActionKind
    target: str                  # device_id, traffic_class veya "link"
    params: dict[str, Any]
    reason: str
    confidence: float            # 0..1
    source: str = "rules"        # "rules" | "ai"
    applied: bool = False
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class LinkStats:
    """Belirli bir pencere için WAN linki özeti."""

    window_seconds: float
    down_bps: float              # yalnızca WAN'ı geçen trafik
    up_bps: float
    down_utilization: float      # 0..1
    up_utilization: float
    lan_bps: float               # LAN içi trafik — WAN kapasitesini tüketmez
    flow_count: int
    device_count: int
    avg_rtt_ms: float
    retransmit_rate: float
    per_class_bps: dict[str, float] = field(default_factory=dict)
    per_device_bps: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
