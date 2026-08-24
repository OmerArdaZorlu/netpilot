"""Kayan pencere metrikleri: link doluluğu, sınıf/cihaz dağılımı, sağlık sinyalleri."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterable

from ..core.config import LinkConfig
from ..core.models import Direction, Flow, LinkStats, now


@dataclass
class DeviceSignals:
    """Tek cihaz için pencere içi davranış özeti.

    `down_bps` / `up_bps` yalnızca WAN trafiğidir; LAN içi hacim ayrı tutulur.
    Karıştırmak, LAN'a yedek yazan bir sunucuyu "internet hattını tıkıyor" diye
    yanlışlıkla sınırlandırmaya yol açar.
    """

    device_id: str
    down_bps: float = 0.0
    up_bps: float = 0.0
    lan_bps: float = 0.0
    flow_count: int = 0
    unique_dst_ips: int = 0
    unique_dst_ports: int = 0
    lateral_flows: int = 0
    avg_rtt_ms: float = 0.0
    retransmit_rate: float = 0.0
    top_app: str = ""
    class_mix: dict[str, float] = field(default_factory=dict)

    @property
    def total_bps(self) -> float:
        return self.down_bps + self.up_bps

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["total_bps"] = self.total_bps
        return d


class MetricsEngine:
    """Akışları biriktirir, pencere kaydıkça eskileri düşürür."""

    HISTORY_POINTS = 300

    def __init__(self, link: LinkConfig, window_seconds: int = 60) -> None:
        self.link = link
        self.window = float(window_seconds)
        self._flows: deque[Flow] = deque()
        self.history: deque[dict] = deque(maxlen=self.HISTORY_POINTS)
        self._total_flows_seen = 0
        self._total_bytes_seen = 0

    # ------------------------------------------------------------------ ingest

    def add(self, flows: Iterable[Flow]) -> None:
        for flow in flows:
            self._flows.append(flow)
            self._total_flows_seen += 1
            self._total_bytes_seen += flow.total_bytes
        self._evict()

    def _evict(self) -> None:
        cutoff = now() - self.window
        while self._flows and self._flows[0].ts < cutoff:
            self._flows.popleft()

    @property
    def flows(self) -> list[Flow]:
        self._evict()
        return list(self._flows)

    @property
    def totals(self) -> dict:
        return {
            "flows_seen": self._total_flows_seen,
            "bytes_seen": self._total_bytes_seen,
        }

    # ----------------------------------------------------------------- compute

    def link_stats(self) -> LinkStats:
        """WAN linki özeti.

        Doluluk yalnızca WAN'ı geçen trafikten hesaplanır: kameranın NVR'a
        akıttığı veya yedeğin LAN dosya sunucusuna yazdığı trafik anahtarda kalır,
        internet hattını tüketmez. Bunları saymak doluluğu yapay olarak şişirir.
        """
        flows = self.flows
        span = self._effective_span(flows)
        wan = [f for f in flows if f.direction is not Direction.LATERAL]

        down_bps = sum(f.bytes_down for f in wan) * 8 / span
        up_bps = sum(f.bytes_up for f in wan) * 8 / span
        lan_bps = sum(f.total_bytes for f in flows
                      if f.direction is Direction.LATERAL) * 8 / span

        per_class: dict[str, float] = defaultdict(float)
        per_device: dict[str, float] = defaultdict(float)
        for f in wan:
            bits = f.total_bytes * 8
            per_class[f.traffic_class.value] += bits / span
            per_device[f.device_id] += bits / span

        # Hat kalitesi de WAN akışlarından ölçülür — LAN içi RTT (~1 ms) ortalamayı
        # yapay olarak aşağı çeker ve gerçek bozulmayı maskeler.
        weighted_rtt = sum(f.rtt_ms * f.packets for f in wan)
        total_packets = sum(f.packets for f in wan)
        avg_rtt = weighted_rtt / total_packets if total_packets else 0.0
        retrans = sum(f.retransmits for f in wan)
        retrans_rate = retrans / total_packets if total_packets else 0.0

        return LinkStats(
            window_seconds=span,
            down_bps=down_bps,
            up_bps=up_bps,
            lan_bps=lan_bps,
            down_utilization=min(down_bps / self.link.downlink_bps, 4.0),
            up_utilization=min(up_bps / self.link.uplink_bps, 4.0),
            flow_count=len(flows),
            device_count=len({f.device_id for f in flows}),
            avg_rtt_ms=round(avg_rtt, 2),
            retransmit_rate=round(retrans_rate, 4),
            per_class_bps={k: round(v, 1) for k, v in per_class.items()},
            per_device_bps={k: round(v, 1) for k, v in per_device.items()},
        )

    def device_signals(self) -> dict[str, DeviceSignals]:
        flows = self.flows
        span = self._effective_span(flows)
        grouped: dict[str, list[Flow]] = defaultdict(list)
        for f in flows:
            grouped[f.device_id].append(f)

        out: dict[str, DeviceSignals] = {}
        for device_id, dev_flows in grouped.items():
            wan_flows = [f for f in dev_flows if f.direction is not Direction.LATERAL]
            packets = sum(f.packets for f in dev_flows)
            app_bytes: dict[str, int] = defaultdict(int)
            class_bits: dict[str, float] = defaultdict(float)
            # Sınıf karışımı WAN üzerinden — QoS kararları yalnızca hattı
            # tüketen trafiğe uygulanır.
            for f in wan_flows:
                class_bits[f.traffic_class.value] += f.total_bytes * 8
            for f in dev_flows:
                app_bytes[f.app] += f.total_bytes
            total_bits = sum(class_bits.values()) or 1.0

            out[device_id] = DeviceSignals(
                device_id=device_id,
                down_bps=sum(f.bytes_down for f in wan_flows) * 8 / span,
                up_bps=sum(f.bytes_up for f in wan_flows) * 8 / span,
                lan_bps=sum(f.total_bytes for f in dev_flows
                            if f.direction is Direction.LATERAL) * 8 / span,
                flow_count=len(dev_flows),
                unique_dst_ips=len({f.dst_ip for f in dev_flows}),
                unique_dst_ports=len({f.dst_port for f in dev_flows}),
                lateral_flows=sum(1 for f in dev_flows
                                  if f.direction is Direction.LATERAL),
                avg_rtt_ms=round(
                    sum(f.rtt_ms * f.packets for f in dev_flows) / packets, 2
                ) if packets else 0.0,
                retransmit_rate=round(
                    sum(f.retransmits for f in dev_flows) / packets, 4
                ) if packets else 0.0,
                top_app=max(app_bytes, key=app_bytes.get) if app_bytes else "",
                class_mix={k: round(v / total_bits, 3) for k, v in class_bits.items()},
            )
        return out

    def class_shares(self) -> dict[str, float]:
        """Sınıfların toplam bant içindeki payı (0..1)."""
        stats = self.link_stats()
        total = sum(stats.per_class_bps.values()) or 1.0
        return {k: round(v / total, 4) for k, v in stats.per_class_bps.items()}

    def top_talkers(self, limit: int = 5) -> list[tuple[str, float]]:
        stats = self.link_stats()
        ranked = sorted(stats.per_device_bps.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:limit]

    def sample_history(self, stats: LinkStats | None = None) -> dict:
        """Grafik için tek bir zaman noktası kaydeder."""
        stats = stats or self.link_stats()
        point = {
            "ts": now(),
            "down_bps": round(stats.down_bps, 1),
            "up_bps": round(stats.up_bps, 1),
            "down_util": round(stats.down_utilization, 4),
            "up_util": round(stats.up_utilization, 4),
            "rtt_ms": stats.avg_rtt_ms,
            "flows": stats.flow_count,
        }
        self.history.append(point)
        return point

    # ------------------------------------------------------------------ helper

    def _effective_span(self, flows: list[Flow]) -> float:
        """Başlangıçta pencere henüz dolmadığı için gerçek süreyi kullan."""
        if not flows:
            return self.window
        span = flows[-1].ts - flows[0].ts
        return max(min(span, self.window), 1.0)
