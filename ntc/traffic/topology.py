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

import hashlib
import random
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
    # Çok siteli ağda birden çok giriş noktası; boşsa `default_access`.
    access_nodes: list[str] = field(default_factory=list)

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
        """Cihazın hangi düğümden ağa girdiği.

        Cihaz adı bir düğümse doğrudan o (LAN hedefleri: nvr, srv-file).
        Değilse erişim düğümlerinden birine **kararlı hash** ile dağıtılıyor:
        aynı cihaz her zaman aynı yerden giriyor, ayrı bir envanter tablosu
        tutmadan. Çok siteli bir ağda bütün cihazları tek anahtara toplamak,
        gerçekte var olmayan bir darboğaz uydurmak demekti.
        """
        if self.has_node(hostname):
            return hostname
        girisler = self.access_nodes or [self.default_access]
        if len(girisler) == 1:
            return girisler[0]
        h = int(hashlib.blake2b(hostname.encode("utf-8"),
                                digest_size=4).hexdigest(), 16)
        return girisler[h % len(girisler)]

    # ------------------------------------------------------- kapasite özeti

    def wan_capacity(self) -> tuple[float, float]:
        """(indirme, yükleme) toplam WAN kapasitesi.

        **Panel ile çözücünün ayrışmasını burası engelliyor.** Doluluk
        `link:` ayarından, darboğaz topolojiden hesaplanıyordu; ikisi elle
        tutulduğu için bir kez ayrıştılar ve panel "%92 dolu" derken çözücü
        "darboğaz yok" dedi. Artık tek kaynak topoloji; `link:` buradan
        türetiliyor.

        İndirme = internetten ağa giren kenarlar, yükleme = ağdan internete
        çıkanlar. Yön fiziksel olarak doğru modellendiği için toplamlar da
        doğru ayrışıyor.
        """
        down = sum(e.capacity_mbps for e in self.edges
                   if e.kind == "wan" and e.src == INTERNET)
        up = sum(e.capacity_mbps for e in self.edges
                 if e.kind == "wan" and e.dst == INTERNET)
        return down, up

    def to_dict(self) -> dict[str, Any]:
        down, up = self.wan_capacity()
        return {
            "nodes": self.nodes,
            "edges": [e.to_dict() for e in self.edges],
            "default_access": self.default_access,
            "access_nodes": self.access_nodes or [self.default_access],
            "wan_downlink_mbps": round(down, 1),
            "wan_uplink_mbps": round(up, 1),
        }

    # ------------------------------------------------------------ kurulum

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None) -> "Topology":
        """`config.yaml` içindeki `topology:` bloğundan kurar.

        Blok yoksa varsayılan topoloji kullanılıyor — böylece mevcut kurulum
        yapılandırma değiştirmeden çalışmaya devam ediyor.
        """
        if raw and raw.get("generate"):
            g = raw["generate"] or {}
            return cls.generate(
                seed=int(g.get("seed", 7)),
                sites=int(g.get("sites", 3)),
                egresses=int(g.get("egresses", 2)),
                downlink_mbps=float(g.get("downlink_mbps", 300.0)),
                uplink_mbps=float(g.get("uplink_mbps", 40.0)),
            )
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
                   default_access=str(raw.get("default_access", "sw-access")),
                   access_nodes=[str(x) for x in raw.get("access_nodes", [])])

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

    # ---------------------------------------------------------------- üretim

    @classmethod
    def generate(cls, seed: int = 7, sites: int = 3, egresses: int = 2,
                 downlink_mbps: float = 300.0,
                 uplink_mbps: float = 40.0) -> "Topology":
        """Rastgele ama gerçekçi bir ağ üretir.

        **Neden var:** elle yazılmış tek bir topolojide çalışmak hiçbir şey
        kanıtlamıyor. Sistemin işi *ne bulursa onda* çalışmak — birkaç router
        kesişimi, uçlarda LAN'lar, birden çok çıkış. Bu üreteç o mimariyi
        tohumlu üretiyor: aynı tohum aynı ağı verir (tekrarlanabilirlik),
        farklı tohum farklı şekil (genellik). Çözücü, çevirici ve infaz
        katmanlarının hiçbiri düğüm adı varsaymıyor; doğrulama da bunu
        birkaç tohum üzerinde ölçüyor.

        Şekil:

            cihazlar ─► access-1 ─► dist-1 ─┐
                                            ├─► core ─┬─► cikis-1 ─► internet
            cihazlar ─► access-2 ─► dist-2 ─┘         └─► cikis-2 ─► internet

        Çıkış kapasitelerinin toplamı **tam olarak** `downlink_mbps` /
        `uplink_mbps` — panel doluluğu ile çözücünün darboğazı ayrışmasın
        diye (bkz. `wan_capacity`).
        """
        rnd = random.Random(seed)
        sites = max(1, sites)
        egresses = max(1, egresses)
        e: list[Edge] = []
        access: list[str] = []

        # --- uçlar: her site bir erişim anahtarı + bir dağıtım router'ı
        for i in range(1, sites + 1):
            acc, dist = f"access-{i}", f"dist-{i}"
            access.append(acc)
            # İç bacaklar bol: gerçek ağlarda darboğaz iç anahtarlamada
            # değil, WAN çıkışında olur. Buraya dar kapasite koymak
            # optimizasyonun ölçtüğü şeyi değiştirirdi.
            ic = rnd.choice([1000.0, 1000.0, 2500.0])
            e += [
                Edge(acc, dist, ic, 0.2, kind="access"),
                Edge(dist, acc, ic, 0.2, kind="access"),
                Edge(dist, "core", ic, round(rnd.uniform(0.3, 0.9), 2),
                     kind="access"),
                Edge("core", dist, ic, round(rnd.uniform(0.3, 0.9), 2),
                     kind="access"),
            ]

        # --- LAN hedefleri: kamera kaydı ve dosya sunucusu. WAN'a çıkmıyor,
        #     o yüzden internet hattı doluluğuna da sayılmıyor (3. ilke).
        for hedef in ("nvr", "srv-file"):
            e += [Edge("core", hedef, 1000.0, 0.3, kind="lan"),
                  Edge(hedef, "core", 1000.0, 0.3, kind="lan")]

        # --- çıkışlar: paylar rastgele, toplamları verilen kapasiteye eşit
        paylar = [rnd.uniform(1.0, 3.0) for _ in range(egresses)]
        toplam = sum(paylar)
        kalan_down, kalan_up = downlink_mbps, uplink_mbps
        for i, pay in enumerate(paylar, start=1):
            ad = f"cikis-{i}"
            son = (i == egresses)
            # Son bacak kalanı alıyor: yuvarlama artığı kapasite
            # kaybettirmesin, `wan_capacity()` toplamı birebir tutsun.
            d = round(kalan_down if son else downlink_mbps * pay / toplam, 1)
            u = round(kalan_up if son else uplink_mbps * pay / toplam, 1)
            kalan_down = round(kalan_down - d, 1)
            kalan_up = round(kalan_up - u, 1)
            gecikme = round(rnd.uniform(6.0, 40.0), 1)
            # Bacakların bir kısmı sayaçlı (LTE gibi): çözücü eşit koşulda
            # ucuz olanı seçsin diye — gerçek ağlarda olan durum.
            ucret = round(rnd.choice([0.0, 0.0, 0.0, rnd.uniform(1.0, 5.0)]), 2)
            e += [
                Edge("core", ad, u, gecikme, ucret, kind="wan"),
                Edge(ad, INTERNET, u, gecikme, ucret, kind="wan"),
                Edge(INTERNET, ad, d, gecikme, ucret, kind="wan"),
                Edge(ad, "core", d, gecikme, ucret, kind="wan"),
            ]

        return cls(edges=e, default_access=access[0], access_nodes=access)
