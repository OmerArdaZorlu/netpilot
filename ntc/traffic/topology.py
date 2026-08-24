"""Ağ topolojisi — akış optimizasyonunun üzerinde çalıştığı grafik.

Faz 1'de topoloji yoktu: tek bir WAN hattı vardı ve cihazlar doğrudan ona
bağlıydı. Bu yüzden "optimizasyon" yapılabilecek bir şey de yoktu — tek yol
varken seçilecek yol yok, sadece kısıtlanacak hız var.

Burada ağı yönlü bir grafik olarak modelliyoruz:

    cihazlar ──► erişim anahtarı ──► çekirdek ──┬─► wan-fiber ──┐
                                                ├─► wan-lte    ─┼─► internet
                                                └─► wan-yedek  ─┘
                                                │
                                                └─► nvr / dosya sunucusu  (LAN)

Her kenarın kapasitesi, gecikmesi, maliyeti ve sağlığı var. Optimize edici
bunları kısıt olarak alıp trafiği kenarlara dağıtıyor.

**Yön önemli.** İndirme ve yükleme ayrı kapasitelere sahip (200/20 gibi), o
yüzden grafik yönlü ve her fiziksel hat iki kenar olarak duruyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

INTERNET = "internet"


@dataclass
class Edge:
    """Yönlü bir bağlantı: `src` üzerinden `dst`'ye giden kapasite."""

    src: str
    dst: str
    capacity_mbps: float
    latency_ms: float = 1.0
    # Sayaçlı hatlar için (LTE gibi). 0 = ücretsiz. Optimize edici eşit
    # koşulda ucuz kenarı tercih etsin diye var.
    cost_per_gb: float = 0.0
    # 0.0 kopuk, 1.0 sağlam. Etkin kapasite bununla çarpılıyor: bozulan bir
    # hattı grafikten silmek yerine daraltmak, kısmi bozulmayı da modelliyor.
    health: float = 1.0
    kind: str = "lan"            # lan | wan | access

    @property
    def key(self) -> tuple[str, str]:
        return (self.src, self.dst)

    @property
    def effective_mbps(self) -> float:
        return max(0.0, self.capacity_mbps * max(0.0, min(1.0, self.health)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "src": self.src, "dst": self.dst,
            "capacity_mbps": round(self.capacity_mbps, 3),
            "effective_mbps": round(self.effective_mbps, 3),
            "latency_ms": self.latency_ms,
            "cost_per_gb": self.cost_per_gb,
            "health": self.health,
            "kind": self.kind,
        }


@dataclass
class Topology:
    """Yönlü kapasiteli grafik.

    Düğüm adları serbest metin; cihazlar `Device.hostname` ile eşleşiyor.
    """

    edges: list[Edge] = field(default_factory=list)
    # Cihazların bağlandığı düğüm. Eşleşmeyen cihaz varsayılana bağlanır.
    default_access: str = "sw-access"

    # ------------------------------------------------------------ sorgular

    @property
    def nodes(self) -> list[str]:
        seen: dict[str, None] = {}
        for e in self.edges:
            seen.setdefault(e.src, None)
            seen.setdefault(e.dst, None)
        return list(seen)

    def out_edges(self, node: str) -> list[Edge]:
        return [e for e in self.edges if e.src == node]

    def in_edges(self, node: str) -> list[Edge]:
        return [e for e in self.edges if e.dst == node]

    def edge(self, src: str, dst: str) -> Edge | None:
        for e in self.edges:
            if e.src == src and e.dst == dst:
                return e
        return None

    def wan_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind == "wan"]

    def has_node(self, node: str) -> bool:
        return any(e.src == node or e.dst == node for e in self.edges)

    def reachable(self, start: str) -> set[str]:
        """`start`'tan gidilebilen düğümler — kapasitesi sıfır olanlar hariç.

        Talebi hiç ulaşamayacağı bir hedefe göndermeye çalışmak, çözücüde
        anlaşılmaz bir "çözümsüz" hatası olarak dönüyor; önce burada eleniyor.
        """
        seen = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for e in self.out_edges(node):
                if e.effective_mbps <= 0 or e.dst in seen:
                    continue
                seen.add(e.dst)
                stack.append(e.dst)
        return seen

    def attach_point(self, hostname: str) -> str:
        """Cihazın hangi düğümden ağa girdiği."""
        return hostname if self.has_node(hostname) else self.default_access

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": [e.to_dict() for e in self.edges],
            "default_access": self.default_access,
        }

    # ------------------------------------------------------------ kurulum

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None) -> "Topology":
        """`config.yaml` içindeki `topology:` bloğundan kurar.

        Blok yoksa varsayılan topoloji kullanılıyor — böylece mevcut kurulum
        yapılandırma değiştirmeden çalışmaya devam ediyor.
        """
        if not raw or not raw.get("edges"):
            return cls.default()

        edges = []
        for item in raw["edges"]:
            edges.append(Edge(
                src=str(item["src"]),
                dst=str(item["dst"]),
                capacity_mbps=float(item["capacity_mbps"]),
                latency_ms=float(item.get("latency_ms", 1.0)),
                cost_per_gb=float(item.get("cost_per_gb", 0.0)),
                health=float(item.get("health", 1.0)),
                kind=str(item.get("kind", "lan")),
            ))
        return cls(edges=edges,
                   default_access=str(raw.get("default_access", "sw-access")))

    @classmethod
    def default(cls, downlink_mbps: float = 200.0,
                uplink_mbps: float = 20.0) -> "Topology":
        """Tek fiberli, iki yedek çıkışlı ofis ağı.

        Kapasiteler `link:` ayarındaki hat ile uyumlu tutuluyor ki mevcut
        eşik mantığıyla aynı dünyayı anlatsınlar.
        """
        e = []

        # Erişim katmanı — cihazların girdiği yer. İç anahtarlama bol.
        e.append(Edge("sw-access", "sw-core", 1000.0, 0.2, kind="access"))
        e.append(Edge("sw-core", "sw-access", 1000.0, 0.2, kind="access"))

        # LAN hedefleri: kamera kaydı ve dosya sunucusu. Bunlar WAN'a çıkmıyor.
        e.append(Edge("sw-core", "nvr", 1000.0, 0.3, kind="lan"))
        e.append(Edge("sw-core", "srv-file", 1000.0, 0.3, kind="lan"))

        # WAN çıkışları. Üçü de internete gidiyor; optimize edici hangisinden
        # ne kadar akıtacağına karar veriyor.
        e.append(Edge("sw-core", "wan-fiber", downlink_mbps, 8.0, kind="wan"))
        e.append(Edge("wan-fiber", INTERNET, downlink_mbps, 8.0, kind="wan"))
        e.append(Edge("sw-core", "wan-lte", 50.0, 45.0,
                      cost_per_gb=4.0, kind="wan"))
        e.append(Edge("wan-lte", INTERNET, 50.0, 45.0,
                      cost_per_gb=4.0, kind="wan"))
        e.append(Edge("sw-core", "wan-yedek", 100.0, 14.0, kind="wan"))
        e.append(Edge("wan-yedek", INTERNET, 100.0, 14.0, kind="wan"))

        # Yükleme yönü — ayrı ve çok daha dar. Asimetri gerçek hatların
        # belirleyici özelliği; simetrik modellemek yanlış sonuç verir.
        e.append(Edge(INTERNET, "wan-fiber", uplink_mbps, 8.0, kind="wan"))
        e.append(Edge("wan-fiber", "sw-core", uplink_mbps, 8.0, kind="wan"))
        e.append(Edge(INTERNET, "wan-lte", 10.0, 45.0,
                      cost_per_gb=4.0, kind="wan"))
        e.append(Edge("wan-lte", "sw-core", 10.0, 45.0,
                      cost_per_gb=4.0, kind="wan"))
        e.append(Edge(INTERNET, "wan-yedek", 5.0, 14.0, kind="wan"))
        e.append(Edge("wan-yedek", "sw-core", 5.0, 14.0, kind="wan"))

        e.append(Edge("nvr", "sw-core", 1000.0, 0.3, kind="lan"))
        e.append(Edge("srv-file", "sw-core", 1000.0, 0.3, kind="lan"))

        return cls(edges=e)


def summarize_edges(edges: Iterable[Edge]) -> str:
    """Log ve panel için tek satırlık özet."""
    return ", ".join(f"{e.src}->{e.dst} {e.effective_mbps:.0f}M" for e in edges)
