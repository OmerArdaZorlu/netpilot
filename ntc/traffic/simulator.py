"""Sentetik ama gerçekçi ağ trafiği üreteci.

Canlı yakalama (scapy) faz 2'de gelecek; o zaman `LiveSource` aynı arayüzü
uygulayıp bunun yerine geçecek. Bu yüzden simülatör sadece `Flow` üretir,
başka hiçbir modülü tanımaz.
"""

from __future__ import annotations

import ipaddress
import random
from dataclasses import dataclass, field

from ..core.models import (
    Device,
    Direction,
    Flow,
    TrafficClass,
    new_id,
    now,
)
from .catalog import APPS, PROFILES, AppSpec, DeviceProfile

LAN_PREFIX = "10.10.0."
EXTERNAL_POOL = [
    "13.107.42.14",    # microsoft
    "52.96.7.34",      # o365
    "142.250.187.14",  # google
    "23.246.2.11",     # netflix cdn
    "104.18.32.47",    # cloudflare
    "185.199.108.153", # github pages
    "20.42.65.92",     # azure
]
# LAN içi hedefler (NVR, dosya/yedek sunucusu) — WAN kapasitesini tüketmezler.
LAN_SERVERS = ["10.10.0.5", "10.10.0.6"]

# Bir keşif taramasının tipik olarak denediği servis portları.
SCAN_PORTS = [
    21, 22, 23, 25, 53, 80, 88, 110, 111, 135, 139, 143, 389, 443, 445,
    464, 514, 587, 623, 636, 873, 993, 995, 1080, 1433, 1521, 2049, 2375,
    3128, 3268, 3306, 3389, 4444, 5000, 5060, 5432, 5601, 5900, 5985, 5986,
    6379, 8000, 8080, 8443, 9092, 9200, 11211, 27017,
]


@dataclass
class Scenario:
    """Elle tetiklenen trafik olayı (demo/talim amaçlı)."""

    name: str
    device_id: str
    started_at: float
    duration: float
    params: dict = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return now() - self.started_at >= self.duration

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "device_id": self.device_id,
            "started_at": self.started_at,
            "duration": self.duration,
            "remaining": max(0.0, self.duration - (now() - self.started_at)),
            "params": self.params,
        }


SCENARIOS = ("congestion", "bandwidth_hog", "port_scan", "exfil", "beacon", "quiet")


class TrafficSimulator:
    """Her `tick()` çağrısında o saniyeye ait akış listesini döndürür."""

    def __init__(self, seed: int | None = None) -> None:
        self.rng = random.Random(seed)
        self.devices: dict[str, Device] = {}
        self._profiles: dict[str, DeviceProfile] = {}
        self.scenarios: list[Scenario] = []
        self._build_devices()

    # ------------------------------------------------------------------ setup

    def _build_devices(self) -> None:
        for idx, profile in enumerate(PROFILES, start=11):
            device_id = new_id("dev")
            device = Device(
                id=device_id,
                ip=f"{LAN_PREFIX}{idx}",
                mac=self._fake_mac(),
                hostname=profile.hostname,
                kind=profile.kind,
                trust=profile.trust,
                tags=list(profile.tags),
            )
            self.devices[device_id] = device
            self._profiles[device_id] = profile

    def _fake_mac(self) -> str:
        return ":".join(f"{self.rng.randint(0, 255):02x}" for _ in range(6))

    def device_by_hostname(self, hostname: str) -> Device | None:
        for device in self.devices.values():
            if device.hostname == hostname:
                return device
        return None

    # -------------------------------------------------------------- scenarios

    def trigger(self, name: str, device_id: str | None = None,
                duration: float = 60.0, **params) -> Scenario:
        if name not in SCENARIOS:
            raise ValueError(f"bilinmeyen senaryo: {name} (geçerli: {', '.join(SCENARIOS)})")
        if device_id is None:
            device_id = self.rng.choice(list(self.devices))
        elif device_id not in self.devices:
            match = self.device_by_hostname(device_id)
            if match is None:
                raise ValueError(f"bilinmeyen cihaz: {device_id}")
            device_id = match.id
        scenario = Scenario(name=name, device_id=device_id,
                            started_at=now(), duration=duration, params=params)
        self.scenarios.append(scenario)
        return scenario

    def clear_scenarios(self) -> int:
        count = len(self.scenarios)
        self.scenarios.clear()
        return count

    def _prune_scenarios(self) -> None:
        self.scenarios = [s for s in self.scenarios if not s.expired]

    def _active(self, name: str) -> list[Scenario]:
        return [s for s in self.scenarios if s.name == name]

    # ------------------------------------------------------------------- tick

    def tick(self, dt: float = 1.0) -> list[Flow]:
        """dt saniyelik dilim için akışlar üretir."""
        self._prune_scenarios()
        flows: list[Flow] = []
        quiet = bool(self._active("quiet"))
        congested = bool(self._active("congestion"))
        hog_devices = {s.device_id for s in self._active("bandwidth_hog")}

        load_multiplier = 1.0
        if quiet:
            load_multiplier = 0.15
        elif congested:
            load_multiplier = 2.6

        for device_id, device in self.devices.items():
            profile = self._profiles[device_id]
            activity = min(0.98, profile.activity * load_multiplier)
            if self.rng.random() > activity:
                continue

            lo, hi = profile.concurrency
            count = self.rng.randint(lo, hi)
            if congested:
                count += 1
            for _ in range(count):
                app = self._pick_app(profile)
                flow = self._make_flow(device, app, dt,
                                       boost=load_multiplier if congested else 1.0)
                flows.append(flow)
            device.last_seen = now()

        # Senaryoya özel akışlar
        for scenario in self.scenarios:
            device = self.devices.get(scenario.device_id)
            if device is None:
                continue
            if scenario.name == "bandwidth_hog":
                flows.extend(self._hog_flows(device, dt, scenario))
            elif scenario.name == "port_scan":
                flows.extend(self._scan_flows(device, dt, scenario))
            elif scenario.name == "exfil":
                flows.extend(self._exfil_flows(device, dt, scenario))
            elif scenario.name == "beacon":
                flows.extend(self._beacon_flows(device, dt, scenario))

        _ = hog_devices  # hog akışları yukarıdaki döngüde ekleniyor
        return flows

    # --------------------------------------------------------------- builders

    def _pick_app(self, profile: DeviceProfile) -> AppSpec:
        names = list(profile.app_weights)
        weights = [profile.app_weights[n] for n in names]
        return APPS[self.rng.choices(names, weights=weights, k=1)[0]]

    def _make_flow(self, device: Device, app: AppSpec, dt: float,
                   boost: float = 1.0) -> Flow:
        down = self.rng.uniform(*app.down_bps) * boost * dt / 8
        up = self.rng.uniform(*app.up_bps) * boost * dt / 8
        rtt = self.rng.uniform(*app.rtt_ms)
        if boost > 1.5:
            rtt *= self.rng.uniform(1.8, 4.0)   # tıkanmada gecikme şişer

        total_bytes = int(down + up)
        packets = max(1, total_bytes // 1200)
        retrans = 0
        if boost > 1.5 and app.proto == "tcp":
            retrans = int(packets * self.rng.uniform(0.01, 0.06))

        if app.scope == "lan":
            dst_ip = self.rng.choice(LAN_SERVERS)
            direction = Direction.LATERAL
        else:
            dst_ip = self.rng.choice(EXTERNAL_POOL)
            direction = Direction.INBOUND if down >= up else Direction.OUTBOUND

        return Flow(
            id=new_id("flw"),
            ts=now(),
            device_id=device.id,
            src_ip=device.ip,
            dst_ip=dst_ip,
            src_port=self.rng.randint(49152, 65535),
            dst_port=app.port,
            proto=app.proto,
            app=app.name,
            traffic_class=app.traffic_class,
            direction=direction,
            bytes_down=int(down),
            bytes_up=int(up),
            packets=packets,
            duration=dt,
            rtt_ms=round(rtt, 2),
            retransmits=retrans,
        )

    def _hog_flows(self, device: Device, dt: float, scenario: Scenario) -> list[Flow]:
        """Tek cihazın hattı doldurması — büyük bir bulk indirme."""
        mbps = float(scenario.params.get("mbps", 120))
        app = APPS["windows-update"]
        down = mbps * 1_000_000 * dt / 8
        total = int(down)
        return [Flow(
            id=new_id("flw"),
            ts=now(),
            device_id=device.id,
            src_ip=device.ip,
            dst_ip="13.107.42.14",
            src_port=self.rng.randint(49152, 65535),
            dst_port=443,
            proto="tcp",
            app=app.name,
            traffic_class=TrafficClass.BULK,
            direction=Direction.INBOUND,
            bytes_down=total,
            bytes_up=int(total * 0.01),
            packets=max(1, total // 1400),
            duration=dt,
            rtt_ms=round(self.rng.uniform(60, 180), 2),
            retransmits=int((total // 1400) * self.rng.uniform(0.02, 0.08)),
            flags=["scenario:bandwidth_hog"],
        )]

    def _scan_flows(self, device: Device, dt: float, scenario: Scenario) -> list[Flow]:
        """Yatay/dikey port taraması — çok sayıda ufak, kısa ömürlü akış."""
        rate = int(scenario.params.get("ports_per_tick", 60))
        target = scenario.params.get("target", "10.10.0.0/24")
        network = list(ipaddress.ip_network(target).hosts())[:64]
        flows = []
        for _ in range(rate):
            dst = str(self.rng.choice(network))
            flows.append(Flow(
                id=new_id("flw"),
                ts=now(),
                device_id=device.id,
                src_ip=device.ip,
                dst_ip=dst,
                src_port=self.rng.randint(49152, 65535),
                dst_port=self.rng.choice(SCAN_PORTS),
                proto="tcp",
                app="unknown",
                traffic_class=TrafficClass.BACKGROUND,
                direction=Direction.LATERAL,
                bytes_down=self.rng.randint(0, 120),
                bytes_up=self.rng.randint(40, 200),
                packets=self.rng.randint(1, 3),
                duration=min(dt, 0.2),
                rtt_ms=round(self.rng.uniform(0.5, 5.0), 2),
                flags=["scenario:port_scan", "syn-only"],
            ))
        return flows

    def _exfil_flows(self, device: Device, dt: float, scenario: Scenario) -> list[Flow]:
        """Alışılmadık saatte, tek hedefe büyük dışarı yükleme."""
        mbps = float(scenario.params.get("mbps", 35))
        up = int(mbps * 1_000_000 * dt / 8)
        return [Flow(
            id=new_id("flw"),
            ts=now(),
            device_id=device.id,
            src_ip=device.ip,
            dst_ip=scenario.params.get("dst", "185.220.101.44"),
            src_port=self.rng.randint(49152, 65535),
            dst_port=int(scenario.params.get("port", 443)),
            proto="tcp",
            app="unknown",
            traffic_class=TrafficClass.BULK,
            direction=Direction.OUTBOUND,
            bytes_down=int(up * 0.005),
            bytes_up=up,
            packets=max(1, up // 1400),
            duration=dt,
            rtt_ms=round(self.rng.uniform(90, 240), 2),
            flags=["scenario:exfil", "rare-destination"],
        )]

    def _beacon_flows(self, device: Device, dt: float, scenario: Scenario) -> list[Flow]:
        """C2 tarzı düzenli aralıklı, sabit boyutlu küçük çağrılar."""
        interval = float(scenario.params.get("interval", 5.0))
        elapsed = now() - scenario.started_at
        if int(elapsed / interval) == int((elapsed - dt) / interval):
            return []
        size = int(scenario.params.get("size", 512))
        return [Flow(
            id=new_id("flw"),
            ts=now(),
            device_id=device.id,
            src_ip=device.ip,
            dst_ip=scenario.params.get("dst", "45.61.136.12"),
            src_port=self.rng.randint(49152, 65535),
            dst_port=443,
            proto="tcp",
            app="unknown",
            traffic_class=TrafficClass.BACKGROUND,
            direction=Direction.OUTBOUND,
            bytes_down=size,
            bytes_up=size,
            packets=4,
            duration=0.3,
            rtt_ms=round(self.rng.uniform(80, 200), 2),
            flags=["scenario:beacon", f"interval:{interval}s"],
        )]
