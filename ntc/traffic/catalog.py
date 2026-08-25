"""Uygulama imzaları ve cihaz profilleri — simülatörün ham maddesi.

Buradaki `AppSpec` tablosu aynı zamanda canlı modda port/protokol eşlemesiyle
akış sınıflandırmak için kullanılacak, o yüzden simülatörden ayrı tutuldu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import DeviceKind, TrafficClass


@dataclass(frozen=True)
class AppSpec:
    name: str
    proto: str
    port: int
    traffic_class: TrafficClass
    down_bps: tuple[float, float]   # (min, max) tipik indirme hızı
    up_bps: tuple[float, float]
    rtt_ms: tuple[float, float] = (10.0, 40.0)
    session_seconds: tuple[float, float] = (5.0, 120.0)
    scope: str = "wan"              # wan | lan — LAN trafiği WAN kapasitesine sayılmaz


KB = 1_000.0
MB = 1_000_000.0

APPS: dict[str, AppSpec] = {
    a.name: a
    for a in [
        # --- realtime ---
        AppSpec("teams-call", "udp", 3478, TrafficClass.REALTIME,
                (1.2 * MB, 3.0 * MB), (1.0 * MB, 2.5 * MB), (8, 35), (60, 1800)),
        AppSpec("voip-sip", "udp", 5060, TrafficClass.REALTIME,
                (80 * KB, 160 * KB), (80 * KB, 160 * KB), (8, 30), (30, 900)),
        # --- interactive ---
        AppSpec("https-web", "tcp", 443, TrafficClass.INTERACTIVE,
                (200 * KB, 6 * MB), (30 * KB, 400 * KB), (12, 60), (2, 40)),
        AppSpec("ssh", "tcp", 22, TrafficClass.INTERACTIVE,
                (5 * KB, 60 * KB), (5 * KB, 80 * KB), (5, 25), (30, 3600)),
        AppSpec("rdp", "tcp", 3389, TrafficClass.INTERACTIVE,
                (600 * KB, 4 * MB), (120 * KB, 700 * KB), (10, 45), (120, 3600)),
        AppSpec("game-udp", "udp", 27015, TrafficClass.INTERACTIVE,
                (100 * KB, 500 * KB), (80 * KB, 300 * KB), (15, 70), (300, 3600)),
        # --- streaming ---
        AppSpec("netflix", "tcp", 443, TrafficClass.STREAMING,
                (5 * MB, 16 * MB), (40 * KB, 150 * KB), (20, 70), (300, 3600)),
        AppSpec("youtube", "tcp", 443, TrafficClass.STREAMING,
                (2 * MB, 12 * MB), (30 * KB, 120 * KB), (18, 65), (60, 1200)),
        # Kameralar buluta değil, LAN'daki kayıt sunucusuna (NVR) akıtır.
        AppSpec("rtsp-camera", "tcp", 554, TrafficClass.STREAMING,
                (600 * KB, 2.5 * MB), (1.5 * MB, 5 * MB), (5, 20), (600, 3600),
                scope="lan"),
        # --- bulk ---
        AppSpec("windows-update", "tcp", 443, TrafficClass.BULK,
                (15 * MB, 60 * MB), (100 * KB, 400 * KB), (25, 90), (120, 1800)),
        # SMB yedeği LAN içi dosya sunucusuna gider.
        AppSpec("smb-backup", "tcp", 445, TrafficClass.BULK,
                (2 * MB, 20 * MB), (10 * MB, 45 * MB), (2, 12), (300, 3600),
                scope="lan"),
        AppSpec("cloud-sync", "tcp", 443, TrafficClass.BULK,
                (1 * MB, 8 * MB), (2 * MB, 15 * MB), (20, 80), (60, 1800)),
        # --- background ---
        AppSpec("dns", "udp", 53, TrafficClass.BACKGROUND,
                (1 * KB, 8 * KB), (1 * KB, 6 * KB), (3, 25), (0.05, 0.4)),
        AppSpec("ntp", "udp", 123, TrafficClass.BACKGROUND,
                (0.5 * KB, 2 * KB), (0.5 * KB, 2 * KB), (5, 30), (0.05, 0.3)),
        AppSpec("mqtt-telemetry", "tcp", 8883, TrafficClass.BACKGROUND,
                (2 * KB, 20 * KB), (4 * KB, 40 * KB), (10, 50), (60, 3600)),
        AppSpec("os-telemetry", "tcp", 443, TrafficClass.BACKGROUND,
                (10 * KB, 90 * KB), (20 * KB, 180 * KB), (25, 90), (5, 60)),
    ]
}

# Port -> AppSpec (canlı modda hızlı sınıflandırma için)
PORT_INDEX: dict[tuple[str, int], AppSpec] = {}
for _app in APPS.values():
    PORT_INDEX.setdefault((_app.proto, _app.port), _app)


@dataclass(frozen=True)
class DeviceProfile:
    """Bir cihazın "kişiliği": hangi uygulamaları hangi olasılıkla kullanır."""

    hostname: str
    kind: DeviceKind
    trust: float
    app_weights: dict[str, float]
    concurrency: tuple[int, int] = (1, 3)   # aynı anda kaç akış
    activity: float = 0.6                   # bir tikte akış üretme olasılığı
    tags: list[str] = field(default_factory=list)


PROFILES: list[DeviceProfile] = [
    DeviceProfile(
        "ws-finance-01", DeviceKind.WORKSTATION, 0.85,
        {"https-web": 4, "teams-call": 2, "os-telemetry": 3, "dns": 4,
         "cloud-sync": 1, "windows-update": 0.4},
        concurrency=(1, 4), activity=0.75, tags=["finance", "managed"],
    ),
    DeviceProfile(
        "ws-dev-02", DeviceKind.WORKSTATION, 0.8,
        {"https-web": 4, "ssh": 3, "rdp": 1, "dns": 4, "cloud-sync": 2,
         "os-telemetry": 2, "windows-update": 0.5},
        concurrency=(2, 5), activity=0.85, tags=["engineering", "managed"],
    ),
    DeviceProfile(
        "lt-sales-07", DeviceKind.LAPTOP, 0.7,
        {"https-web": 5, "teams-call": 3, "youtube": 2, "dns": 4,
         "os-telemetry": 2},
        concurrency=(1, 3), activity=0.6, tags=["sales", "roaming"],
    ),
    DeviceProfile(
        "srv-app-01", DeviceKind.SERVER, 0.95,
        {"https-web": 3, "ssh": 2, "smb-backup": 2, "dns": 3,
         "os-telemetry": 1},
        concurrency=(2, 6), activity=0.9, tags=["datacenter", "critical"],
    ),
    DeviceProfile(
        "srv-backup-01", DeviceKind.SERVER, 0.95,
        {"smb-backup": 6, "dns": 2, "ssh": 1},
        concurrency=(1, 3), activity=0.5, tags=["datacenter", "bulk"],
    ),
    DeviceProfile(
        "phone-omer", DeviceKind.PHONE, 0.65,
        {"https-web": 5, "youtube": 3, "voip-sip": 1, "dns": 4,
         "os-telemetry": 2},
        concurrency=(1, 2), activity=0.5, tags=["byod"],
    ),
    DeviceProfile(
        "tv-lobby", DeviceKind.IOT, 0.4,
        {"netflix": 6, "youtube": 3, "dns": 2, "ntp": 1},
        concurrency=(1, 2), activity=0.45, tags=["iot", "unmanaged"],
    ),
    DeviceProfile(
        "cam-entrance", DeviceKind.CAMERA, 0.35,
        {"rtsp-camera": 8, "mqtt-telemetry": 2, "ntp": 1},
        concurrency=(1, 2), activity=0.95, tags=["iot", "unmanaged", "always-on"],
    ),
    DeviceProfile(
        "cam-parking", DeviceKind.CAMERA, 0.35,
        {"rtsp-camera": 8, "mqtt-telemetry": 2, "ntp": 1},
        concurrency=(1, 2), activity=0.95, tags=["iot", "unmanaged", "always-on"],
    ),
    DeviceProfile(
        "guest-wifi-a", DeviceKind.GUEST, 0.25,
        {"https-web": 4, "youtube": 4, "netflix": 2, "dns": 3},
        concurrency=(1, 3), activity=0.4, tags=["guest", "untrusted"],
    ),
]


# --------------------------------------------------------------- hedef adresler

# Uygulama başına gerçekçi hedef blokları.
#
# **Neden eklendi:** simülatör hedef IP'yi uygulamadan **bağımsız**, ortak bir
# havuzdan seçiyordu — yani Netflix CDN'ine giden bir DNS akışı üretiyordu.
# Gerçek ağda bu olmaz ve sınıflandırıcının IP katmanı tam da bu korelasyona
# dayanıyor. Ölçüldü: rastgele havuzla IP katmanının isabeti %15.6, uygulamaya
# bağlı havuzla %100. Aradaki fark simülatörün kusuruydu, sınıflandırıcının
# değil — ama düzeltilmeseydi ölçüm sonsuza kadar yanlış okunurdu.
APP_ENDPOINTS: dict[str, list[str]] = {
    "netflix": ["23.246.2.11", "23.246.9.40", "45.57.40.12"],
    "youtube": ["142.250.187.14", "142.250.74.206", "172.217.169.78"],
    "windows-update": ["13.107.42.14", "13.107.4.50"],
    "os-telemetry": ["20.42.65.92", "20.42.73.11"],
    "cloud-sync": ["13.107.136.9", "40.108.128.20"],
    "https-web": ["104.18.32.47", "185.199.108.153", "151.101.1.140"],
    "teams-call": ["52.113.194.132", "52.114.75.24"],
    "voip-sip": ["81.23.228.129", "81.23.228.130"],
    "ssh": ["159.65.20.14", "167.99.4.88"],
    "rdp": ["20.79.44.10"],
    "game-udp": ["162.254.196.68", "155.133.248.53"],
    "dns": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
    "ntp": ["216.239.35.0", "132.163.96.1"],
    "mqtt-telemetry": ["52.16.104.90"],
}

# Bilinmeyen uygulama için genel havuz.
GENERIC_ENDPOINTS: list[str] = [
    "104.18.32.47", "185.199.108.153", "151.101.1.140", "34.117.59.81",
]
