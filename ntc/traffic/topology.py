"""Ağ topolojisi — akış optimizasyonunun üzerinde çalıştığı grafik.

Faz 1'de topoloji yoktu: tek bir WAN hattı vardı ve cihazlar doğrudan ona
bağlıydı. Bu yüzden "optimizasyon" yapılabilecek bir şey de yoktu — tek yol
varken seçilecek yol yok, sadece kısıtlanacak hız var.

Burada ağı yönlü bir grafik olarak modelliyoruz. Varsayılan hali
`config.yaml` içindeki `link:` ile birebir — tek çıkış:

    cihazlar ──► sw-access ──► sw-core ──┬─► wan ──► internet
                                         └─► nvr / srv-file   (LAN, WAN'a çıkmaz)

Çoklu çıkış (yedek hat, LTE) `topology:` bloğuyla tanımlanır; varsayılanda
**yok**, çünkü olmayan kapasiteyi varsaymak çözücüyü yanıltıyor.

Her kenarın kapasitesi, gecikmesi, maliyeti ve sağlığı var. Optimize edici
bunları kısıt olarak alıp trafiği kenarlara dağıtıyor.

**Yön önemli.** İndirme ve yükleme ayrı kapasitelere sahip (200/20 gibi), o
yüzden grafik yönlü ve her fiziksel hat iki kenar olarak duruyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

    def edge(self, src: str, dst: str) -> Edge | None:
        for e in self.edges:
            if e.src == src and e.dst == dst:
                return e
        return None

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
        """`link:` ayarındaki **tek** hattı modelleyen topoloji.

        ⚠️ Burada birden çok WAN çıkışı uydurmuyoruz. İlk sürüm üç çıkış
        (fiber + yedek + LTE) kuruyordu ve sonuç yanlıştı: gerçek hat %92
        doluyken çözücü "darboğaz yok" diyordu, çünkü var olmayan 150 Mbps'lik
        ek kapasiteyi kullanılabilir sanıyordu. Panel ile ölçüm birbirini
        yalanlıyordu.

        Kural: **varsayılan topoloji yapılandırmadaki kapasiteyi aşmaz.**
        Çoklu çıkış gerçek bir yetenek, ama var olduğunu kullanıcı söyler —
        `config.yaml` içindeki `topology:` bloğuyla.
        """
        e = [
            # Erişim katmanı: cihazların girdiği yer, iç anahtarlama bol.
            Edge("sw-access", "sw-core", 1000.0, 0.2, kind="access"),
            Edge("sw-core", "sw-access", 1000.0, 0.2, kind="access"),

            # LAN hedefleri — kamera kaydı ve dosya sunucusu. WAN'a çıkmıyor,
            # o yüzden internet hattı doluluğuna da sayılmıyor (3. ilke).
            Edge("sw-core", "nvr", 1000.0, 0.3, kind="lan"),
            Edge("nvr", "sw-core", 1000.0, 0.3, kind="lan"),
            Edge("sw-core", "srv-file", 1000.0, 0.3, kind="lan"),
            Edge("srv-file", "sw-core", 1000.0, 0.3, kind="lan"),

            # Tek WAN çıkışı. İndirme ve yükleme ayrı kenar: asimetri
            # (200/20) gerçek hatların belirleyici özelliği.
            #
            # ⚠️ Yön fiziksel olarak doğru olmak zorunda. İlk sürümde tersti:
            # indirme kapasitesi `cihaz → internet` kenarlarına konmuştu.
            # Tek yön modellendiği sürece fark etmiyordu ama yükleme talebi
            # eklenince yükleme, indirme kapasitesini tüketmeye başlıyordu.
            #
            # YÜKLEME: veri cihazdan çıkar → sw-core → wan → internet
            Edge("sw-core", "wan", uplink_mbps, 8.0, kind="wan"),
            Edge("wan", INTERNET, uplink_mbps, 8.0, kind="wan"),
            # İNDİRME: veri internetten gelir → wan → sw-core → cihaz
            Edge(INTERNET, "wan", downlink_mbps, 8.0, kind="wan"),
            Edge("wan", "sw-core", downlink_mbps, 8.0, kind="wan"),
        ]
        return cls(edges=e)
