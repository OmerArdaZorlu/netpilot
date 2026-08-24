"""Akış optimize edici — trafiğin ağdan en iyi nasıl geçeceğini hesaplar.

Faz 1'deki `optimizer.py` bir **eşik denetçisi**: doluluk %80'i geçince not
yazıyor. Burada yapılan farklı bir iş — bir **optimizasyon**: ölçülen talepleri
topolojinin kenarlarına dağıtıp her akışa ne kadar bant verileceğini ve hangi
yoldan gideceğini çözüyor.

Problem: **çok mallı akış** (multi-commodity flow). Her (cihaz, sınıf, hedef)
üçlüsü bir "mal". Her malın bir talebi var, her kenarın kapasitesi var, ve bir
kenarı bütün mallar paylaşıyor.

Çözüm: doğrusal program, `scipy.optimize.linprog` (HiGHS) ile. Kesir kabul
ediyoruz — bant genişliği zaten bölünebilir bir kaynak, o yüzden tam sayı
kısıtı gerekmiyor ve problem polinom zamanda çözülüyor.

**Politika: önce öncelik, sonra adalet.** Sınıflar sırayla çözülüyor
(realtime → interactive → streaming → bulk → background). Her sınıf için iki
aşama:

  1. `t`'yi büyüt: o sınıftaki *en kötü durumdaki* akışın karşılanma oranını
     maksimize et. Bu, tek bir akışın aç kalmasını engelliyor — düz "toplamı
     maksimize et" formülasyonu, kısa yolu olan bir akışa her şeyi verip
     uzaktakini sıfırda bırakabiliyor.
  2. `t` sabitken toplamı büyüt: kalan kapasiteyi boşa bırakma.

Yüksek öncelikli sınıfın çözümü sonrakiler için kısıt olarak sabitleniyor.

⚠️ **Sınır:** bu tam sözlüksel max-min adalet değil, sınıf içinde tek turlu bir
yaklaşım. İkinci aşama, birinci aşamada eşitlenmiş akışlardan bazılarını
diğerlerinden daha fazla besleyebilir. Gerçek sözlüksel adalet turlu darboğaz
sabitlemesi ister; bu sürüm bilinçli olarak daha basit ve çok daha hızlı.

⚠️ **Ne yapmıyor:** kurulu bir TCP oturumunu taşımıyor. Çıktı bir *hedef
durum* — yeni akışlar için yol ve mevcut akışlar için hız tavanı. Oturum
sürekliliği infaz katmanının problemi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

from ..core.models import TrafficClass
from .topology import INTERNET, Edge, Topology

log = logging.getLogger(__name__)

# Eşit koşulda ucuz ve hızlı kenarı seçtiren ağırlık. Amaç fonksiyonunda
# karşılanan talebin yanında çok küçük kalmalı — yol tercihi, talebi karşılamanın
# önüne geçmemeli.
COST_WEIGHT = 1e-4
# Bozulan kenara ceza. `health` tek başına yalnız kapasiteyi daraltıyordu; o
# zaman sağlam hat yeterken bile çözücü bozuk hatta trafik döküyordu (iki
# çözüm de talebi karşıladığı için LP açısından eşitler). Ceza, eşitliği
# operasyonel olarak doğru tarafa bozuyor. Gecikme cezasından baskın seçildi:
# %20 sağlıklı bir hat (ceza 80), 45 ms'lik sağlam bir hattan (ceza 45) daha
# az tercih edilmeli.
HEALTH_PENALTY = 100.0
# Sayısal gürültüyü sıfıra yuvarlama sınırı (Mbps).
EPS = 1e-6

# Sınıf başına asgari garanti — **kapasitenin** yüzdesi olarak.
#
# Neden gerekiyor: katı öncelikle çözünce en alt sınıf tamamen aç kalıyordu
# (ölçüldü: background %0). DNS ve keepalive o sınıfta; hacimleri ihmal
# edilebilir ama kesilmeleri ağı çalışmaz hale getirir. "Düşük öncelikli"
# ile "feda edilebilir" aynı şey değil.
#
# İlk denemede taban **talebin** oranıydı ve işe yaramadı: talep büyüyünce
# tabanlar da büyüyor, üst sınıfların tabanları kapasiteyi bitiriyor ve en
# alttakine yine bir şey kalmıyordu (1245 Mbps talep / 350 kapasite ile
# ölçüldü). Kapasiteye bağlamak bunu kesiyor — toplam taban her zaman
# kapasitenin sabit bir dilimi.
CLASS_FLOOR_SHARE = {
    TrafficClass.REALTIME: 0.12,
    TrafficClass.INTERACTIVE: 0.12,
    TrafficClass.STREAMING: 0.08,
    TrafficClass.BULK: 0.04,
    TrafficClass.BACKGROUND: 0.02,
}
# Toplam %38; kalan %62 katı öncelik sırasına göre dağıtılıyor.

@dataclass
class Demand:
    """Tek bir malın talebi: nereden nereye, hangi sınıfta, ne kadar.

    `src` verilmezse cihazın topolojideki bağlanma noktası kullanılıyor —
    yani cihazdan **çıkan** trafik. İndirme için `src` açıkça `internet`
    olmalı, çünkü veri fiziksel olarak oradan geliyor ve indirme kapasitesi
    o yöndeki kenarlarda tanımlı.
    """

    device: str                  # hostname — raporlama ve "kimden kıs" için
    dst: str                     # hedef düğüm: "internet", "nvr", "srv-file"…
    traffic_class: TrafficClass
    mbps: float                  # ölçülen / istenen hız
    src: str | None = None       # None = cihazın bağlanma noktası
    direction: str = "up"        # up | down | lan — yalnız raporlama için

    @property
    def key(self) -> str:
        # Yön anahtarda olmalı: aynı cihazın aynı sınıftaki indirme ve
        # yükleme talebi ayrı mallardır, birbirine karışmamalı.
        return (f"{self.device}|{self.direction}|{self.src or '-'}"
                f"|{self.dst}|{self.traffic_class.value}")


@dataclass
class Allocation:
    """Bir talebin çözümdeki karşılığı."""

    demand: Demand
    granted_mbps: float
    # Kenar bazında taşınan miktar — yol ayrıştırması yerine kenar kullanımı.
    # Çoklu yol kullanıldığında tek bir "yol" zaten yok.
    edge_usage: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def shortfall_mbps(self) -> float:
        return max(0.0, self.demand.mbps - self.granted_mbps)

    @property
    def satisfaction(self) -> float:
        if self.demand.mbps <= 0:
            return 1.0
        return min(1.0, self.granted_mbps / self.demand.mbps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.demand.device,
            "dst": self.demand.dst,
            "traffic_class": self.demand.traffic_class.value,
            "demand_mbps": round(self.demand.mbps, 3),
            "granted_mbps": round(self.granted_mbps, 3),
            "shortfall_mbps": round(self.shortfall_mbps, 3),
            "satisfaction": round(self.satisfaction, 3),
            "edges": {f"{a}->{b}": round(v, 3)
                      for (a, b), v in sorted(self.edge_usage.items())
                      if v > EPS},
        }


@dataclass
class FlowPlan:
    """Optimize edicinin ürettiği hedef durum."""

    allocations: list[Allocation]
    edge_load_mbps: dict[tuple[str, str], float]
    topology: Topology
    feasible: bool = True
    note: str = ""

    # -------------------------------------------------------------- türevler

    def pullbacks(self, min_mbps: float = 0.5) -> list[dict[str, Any]]:
        """"Şu makineden şu kadarını geri çek" listesi.

        Talebi karşılanamayan her akış, ağın o akışa veremediği kadar geri
        çekilmeli. Cihaz bazında toplanıyor çünkü uygulanacak kısıt cihaz
        düzeyinde (hız tavanı) konuyor.
        """
        per_device: dict[str, dict[str, Any]] = {}
        for a in self.allocations:
            if a.shortfall_mbps < min_mbps:
                continue
            # Yön ayrı satır: indirmeyi kısmakla yüklemeyi kısmak farklı
            # aksiyonlar ve farklı yerde uygulanıyor.
            row_key = (a.demand.device, a.demand.direction)
            row = per_device.setdefault(row_key, {
                "device": a.demand.device,
                "direction": a.demand.direction,
                "dst": a.demand.dst,
                "demand_mbps": 0.0, "granted_mbps": 0.0,
                "pullback_mbps": 0.0, "classes": [],
            })
            row["demand_mbps"] += a.demand.mbps
            row["granted_mbps"] += a.granted_mbps
            row["pullback_mbps"] += a.shortfall_mbps
            row["classes"].append(a.demand.traffic_class.value)

        rows = list(per_device.values())
        for r in rows:
            for k in ("demand_mbps", "granted_mbps", "pullback_mbps"):
                r[k] = round(r[k], 3)
            r["classes"] = sorted(set(r["classes"]))
        rows.sort(key=lambda r: r["pullback_mbps"], reverse=True)
        return rows

    def bottlenecks(self, threshold: float = 0.98) -> list[dict[str, Any]]:
        """Doymuş kenarlar — sistemi asıl sınırlayan yer."""
        out = []
        for e in self.topology.edges:
            cap = e.effective_mbps
            if cap <= 0:
                continue
            load = self.edge_load_mbps.get(e.key, 0.0)
            if load / cap >= threshold:
                out.append({
                    "edge": f"{e.src}->{e.dst}", "kind": e.kind,
                    "load_mbps": round(load, 2),
                    "capacity_mbps": round(cap, 2),
                    "utilization": round(load / cap, 4),
                })
        out.sort(key=lambda r: r["load_mbps"], reverse=True)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "note": self.note,
            "total_demand_mbps": round(
                sum(a.demand.mbps for a in self.allocations), 3),
            "total_granted_mbps": round(
                sum(a.granted_mbps for a in self.allocations), 3),
            "allocations": [a.to_dict() for a in self.allocations],
            "pullbacks": self.pullbacks(),
            "bottlenecks": self.bottlenecks(),
            "edge_load": {f"{a}->{b}": round(v, 3)
                          for (a, b), v in sorted(self.edge_load_mbps.items())
                          if v > EPS},
        }


# --------------------------------------------------------------------- çözücü


class FlowOptimizer:
    """Çok mallı akış problemini sınıf önceliğine göre çözer."""

    def __init__(self, topology: Topology) -> None:
        self.topo = topology
        self._edges: list[Edge] = list(topology.edges)
        self._edge_index = {e.key: i for i, e in enumerate(self._edges)}
        self._nodes: list[str] = topology.nodes
        self._node_index = {n: i for i, n in enumerate(self._nodes)}

    # ------------------------------------------------------------- genel akış

    def solve(self, demands: list[Demand]) -> FlowPlan:
        usable, skipped = self._filter(demands)
        if not usable:
            return FlowPlan(allocations=[Allocation(d, 0.0) for d in skipped],
                            edge_load_mbps={}, topology=self.topo,
                            feasible=True,
                            note="çözülecek talep yok" if not skipped
                                 else "hiçbir talep ulaşılabilir değil")

        # Sınıflar önceliğe göre; yüksek öncelikli çözüldükten sonra sabitlenir.
        by_class: dict[TrafficClass, list[Demand]] = {}
        for d in usable:
            by_class.setdefault(d.traffic_class, []).append(d)
        order = sorted(by_class, key=lambda c: c.priority)

        granted: dict[str, float] = {d.key: 0.0 for d in usable}
        edge_usage: dict[str, dict[tuple[str, str], float]] = {}
        note_parts: list[str] = []

        # 1. tur — asgari garantiler. Her sınıf yalnız tabanı kadar talep
        # edebiliyor, o yüzden en alt sınıf da payını öncelik sırası devreye
        # girmeden alıyor.
        floors = self._floors(by_class)
        # Taban turu küçükten büyüğe: büyük bir sınıfın tabanı, küçük ama
        # hayati bir sınıfın tabanını yiyemesin.
        floor_order = sorted(
            order, key=lambda c: sum(floors.get(d.key, 0.0) for d in by_class[c]))
        for cls in floor_order:
            if not self._solve_class(by_class[cls], floors, granted, edge_usage):
                note_parts.append(f"{cls.value}: taban turu çözülemedi")

        # 2. tur — kalan kapasite, katı öncelik sırasıyla.
        residual = {d.key: max(0.0, d.mbps - granted.get(d.key, 0.0))
                    for d in usable}
        for cls in order:
            if not self._solve_class(by_class[cls], residual, granted, edge_usage):
                note_parts.append(f"{cls.value}: çözülemedi")

        allocations = [
            Allocation(demand=d,
                       granted_mbps=granted.get(d.key, 0.0),
                       edge_usage=edge_usage.get(d.key, {}))
            for d in usable
        ]
        allocations += [Allocation(d, 0.0) for d in skipped]

        edge_load: dict[tuple[str, str], float] = {}
        for a in allocations:
            for key, val in a.edge_usage.items():
                edge_load[key] = edge_load.get(key, 0.0) + val

        if skipped:
            note_parts.append(
                f"{len(skipped)} talep ulaşılamaz hedefe gidiyordu")

        return FlowPlan(allocations=allocations, edge_load_mbps=edge_load,
                        topology=self.topo, feasible=not any(
                            "çözülemedi" in p for p in note_parts),
                        note="; ".join(note_parts))

    def _filter(self, demands: list[Demand]) -> tuple[list[Demand], list[Demand]]:
        """Anlamsız veya ulaşılamaz talepleri ayırır."""
        usable, skipped = [], []
        for d in demands:
            if d.mbps <= EPS:
                continue
            src = d.src or self.topo.attach_point(d.device)
            if not self.topo.has_node(src) or not self.topo.has_node(d.dst):
                skipped.append(d)
                continue
            if d.dst not in self.topo.reachable(src):
                skipped.append(d)
                continue
            usable.append(d)
        return usable, skipped

    # ------------------------------------------------------- sınıf başına LP

    def _egress_capacity(self) -> float:
        """İnternete çıkan toplam kapasite — taban bütçesinin ölçeği."""
        return sum(e.effective_mbps for e in self._edges if e.dst == INTERNET)

    def _floors(self, by_class: dict[TrafficClass, list[Demand]]
                ) -> dict[str, float]:
        """Talep başına asgari garanti.

        Sınıfın bütçesi kapasitenin sabit bir dilimi; o bütçe sınıf içindeki
        taleplere büyüklükleriyle orantılı dağıtılıyor. Hiçbir talep kendi
        istediğinden fazla taban almıyor.
        """
        capacity = self._egress_capacity()
        floors: dict[str, float] = {}
        for cls, group in by_class.items():
            total = sum(d.mbps for d in group)
            if total <= EPS:
                continue
            budget = min(total, CLASS_FLOOR_SHARE.get(cls, 0.0) * capacity)
            for d in group:
                floors[d.key] = min(d.mbps, budget * (d.mbps / total))
        return floors

    def _solve_class(self, group: list[Demand], caps: dict[str, float],
                     granted: dict[str, float],
                     edge_usage: dict[str, dict[tuple[str, str], float]]) -> bool:
        """Bir öncelik sınıfını iki aşamada çözer: önce adalet, sonra doluluk.

        `caps` bu turda her talebe verilebilecek en fazla miktar — taban
        turunda garanti, artık turunda kalan talep. Sonuçlar `granted` ve
        `edge_usage` üzerine **eklenir**, üzerine yazılmaz; iki tur aynı
        talebi besliyor.
        """
        active = [d for d in group if caps.get(d.key, 0.0) > EPS]
        if not active:
            return True

        reserved = self._reserved_load(edge_usage)

        # 1. aşama — en kötü durumdaki akışın oranını maksimize et.
        res = self._lp(active, caps, reserved, maximize="fairness")
        if res is None:
            return False
        t_star = float(res["t"])

        # 2. aşama — o oranı taban yapıp toplamı maksimize et.
        res2 = self._lp(active, caps, reserved, maximize="throughput",
                        floor_ratio=t_star)
        final = res2 if res2 is not None else res

        for d in active:
            granted[d.key] = granted.get(d.key, 0.0) + float(final["granted"][d.key])
            acc = edge_usage.setdefault(d.key, {})
            for key, val in final["edges"][d.key].items():
                acc[key] = acc.get(key, 0.0) + val
        return True

    def _reserved_load(self, edge_usage) -> np.ndarray:
        """Yüksek öncelikli sınıfların halihazırda tükettiği kapasite."""
        used = np.zeros(len(self._edges))
        for per_demand in edge_usage.values():
            for key, val in per_demand.items():
                idx = self._edge_index.get(key)
                if idx is not None:
                    used[idx] += val
        return used

    def _lp(self, group: list[Demand], caps: dict[str, float],
            reserved: np.ndarray, *, maximize: str,
            floor_ratio: float = 0.0) -> dict[str, Any] | None:
        """Tek bir doğrusal programı kurar ve çözer.

        Değişkenler:
            x[k, e]  malın k, kenar e üzerinde taşıdığı Mbps
            f[k]     malın k karşılanan toplam hızı
            t        (yalnız adalet aşamasında) en düşük karşılanma oranı
        """
        n_e = len(self._edges)
        n_k = len(group)
        n_x = n_k * n_e
        fair = maximize == "fairness"
        n_var = n_x + n_k + (1 if fair else 0)

        def xi(k: int, e: int) -> int:
            return k * n_e + e

        def fi(k: int) -> int:
            return n_x + k

        ti = n_x + n_k          # yalnız fair modunda geçerli

        # --- amaç (linprog minimize eder, o yüzden işaretler ters) ---
        c = np.zeros(n_var)
        if fair:
            c[ti] = -1.0
        else:
            for k in range(n_k):
                c[fi(k)] = -1.0
        for k in range(n_k):
            for e, edge in enumerate(self._edges):
                # Gecikme ve para maliyetini küçük bir ceza olarak ekliyoruz:
                # eşit koşulda kısa ve ucuz kenar seçilsin.
                c[xi(k, e)] += COST_WEIGHT * (
                    edge.latency_ms
                    + 10.0 * edge.cost_per_gb
                    + HEALTH_PENALTY * (1.0 - edge.health))

        # --- kapasite kısıtları: Σ_k x[k,e] ≤ kalan kapasite ---
        rows, cols, vals, b_ub = [], [], [], []
        for e, edge in enumerate(self._edges):
            for k in range(n_k):
                rows.append(e); cols.append(xi(k, e)); vals.append(1.0)
            b_ub.append(max(0.0, edge.effective_mbps - reserved[e]))
        n_ub = n_e

        # --- adalet tabanı: f[k] ≥ oran · talep  →  −f[k] + oran·talep ≤ 0 ---
        if fair:
            for k, d in enumerate(group):
                rows.append(n_ub); cols.append(fi(k)); vals.append(-1.0)
                rows.append(n_ub); cols.append(ti); vals.append(caps[d.key])
                b_ub.append(0.0)
                n_ub += 1
        elif floor_ratio > EPS:
            for k, d in enumerate(group):
                rows.append(n_ub); cols.append(fi(k)); vals.append(-1.0)
                b_ub.append(-floor_ratio * caps[d.key])
                n_ub += 1

        A_ub = csr_matrix((vals, (rows, cols)), shape=(n_ub, n_var))

        # --- korunum kısıtları ---
        eq_rows, eq_cols, eq_vals, b_eq = [], [], [], []
        row = 0
        for k, d in enumerate(group):
            src = d.src or self.topo.attach_point(d.device)
            dst = d.dst
            for node in self._nodes:
                for e, edge in enumerate(self._edges):
                    if edge.dst == node:
                        eq_rows.append(row); eq_cols.append(xi(k, e)); eq_vals.append(1.0)
                    if edge.src == node:
                        eq_rows.append(row); eq_cols.append(xi(k, e)); eq_vals.append(-1.0)
                if node == src:
                    # kaynakta net çıkış = f[k]  →  giren − çıkan + f[k] = 0
                    eq_rows.append(row); eq_cols.append(fi(k)); eq_vals.append(1.0)
                elif node == dst:
                    # hedefte net giriş = f[k]  →  giren − çıkan − f[k] = 0
                    eq_rows.append(row); eq_cols.append(fi(k)); eq_vals.append(-1.0)
                b_eq.append(0.0)
                row += 1
        A_eq = csr_matrix((eq_vals, (eq_rows, eq_cols)), shape=(row, n_var))

        # --- sınırlar ---
        bounds: list[tuple[float, float | None]] = []
        for k in range(n_k):
            for e, edge in enumerate(self._edges):
                bounds.append((0.0, max(0.0, edge.effective_mbps - reserved[e])))
        for d in group:
            bounds.append((0.0, caps[d.key]))     # bu turun tavanı
        if fair:
            bounds.append((0.0, 1.0))

        out = linprog(c, A_ub=A_ub, b_ub=np.array(b_ub),
                      A_eq=A_eq, b_eq=np.array(b_eq),
                      bounds=bounds, method="highs")
        if not out.success:
            log.warning("Akış LP'si çözülemedi (%s): %s", maximize, out.message)
            return None

        x = out.x
        granted = {d.key: max(0.0, float(x[fi(k)])) for k, d in enumerate(group)}
        edges: dict[str, dict[tuple[str, str], float]] = {}
        for k, d in enumerate(group):
            per: dict[tuple[str, str], float] = {}
            for e, edge in enumerate(self._edges):
                val = float(x[xi(k, e)])
                if val > EPS:
                    per[edge.key] = val
            edges[d.key] = per
        return {"granted": granted, "edges": edges,
                "t": float(x[ti]) if fair else 1.0}


# ------------------------------------------------------------------ yardımcı


# Cihaz türüne göre varsayılan LAN hedefi. Kameranın kaydı NVR'a, sunucunun
# yedeği dosya sunucusuna gider; ikisi de internet hattını kullanmaz.
DEFAULT_LAN_TARGETS = {
    "camera": "nvr",
    "iot": "nvr",
    "server": "srv-file",
}
LAN_FALLBACK = "srv-file"


def demands_from_signals(signals: dict[str, Any], devices: dict[str, Any],
                         topology: Topology,
                         lan_targets: dict[str, str] | None = None
                         ) -> list[Demand]:
    """Ölçülen cihaz sinyallerini talebe çevirir — **üç yönü de**.

    ⚠️ İlk sürüm yalnız `down_bps` okuyordu ve bu ölçülebilir bir körlüktü:
    572 Mbps'lik trafiğin 182'sini görüyordu. Görmedikleri, hattın en dar
    yerindeydi — yükleme 20 Mbps'lik hatta %292 doluyken çözücü "darboğaz
    yok" diyordu, LAN'daki 331 Mbps ise topolojide karşılığı olmasına rağmen
    hiç modellenmiyordu.

    Üretilen üç tür:

    * **indirme** — internet → cihaz. Kaynak `internet`, çünkü indirme
      kapasitesi o yöndeki kenarlarda.
    * **yükleme** — cihaz → internet.
    * **LAN** — cihaz → iç hedef (NVR / dosya sunucusu). WAN'a hiç dokunmaz;
      3. mimari ilkenin karşılığı budur.

    `class_mix` cihazın trafiğinin sınıflara dağılımını veriyor; her yönün
    hacmi bu oranlarla bölünüyor.

    ⚠️ **Sınırı:** `class_mix` yön ayrımı yapmıyor — indirme ve yükleme aynı
    karışımla bölünüyor. Gerçekte yükleme profili farklı olabilir (yedekleme
    yüklemede baskındır). Yön başına sınıf dağılımı `metrics.py` tarafında
    ayrılmadan bu düzelmez.
    """
    lan_targets = lan_targets or {}
    out: list[Demand] = []
    have_internet = topology.has_node(INTERNET)

    def split(host: str, mbps: float, mix: dict[str, float],
              *, dst: str, src: str | None, direction: str) -> None:
        if mbps <= EPS:
            return
        if not mix:
            out.append(Demand(host, dst, TrafficClass.BULK, mbps,
                              src=src, direction=direction))
            return
        for name, share in mix.items():
            if share <= 0:
                continue
            try:
                cls = TrafficClass(name)
            except ValueError:
                continue
            out.append(Demand(host, dst, cls, mbps * float(share),
                              src=src, direction=direction))

    for device_id, sig in signals.items():
        device = devices.get(device_id)
        host = getattr(device, "hostname", None) or str(device_id)
        mix = getattr(sig, "class_mix", None) or {}

        if have_internet:
            # İndirme: kaynak internet, hedef cihazın bağlanma noktası.
            split(host, (getattr(sig, "down_bps", 0.0) or 0.0) / 1e6, mix,
                  dst=topology.attach_point(host), src=INTERNET,
                  direction="down")
            # Yükleme: cihazdan çıkıyor, kaynak varsayılan (bağlanma noktası).
            split(host, (getattr(sig, "up_bps", 0.0) or 0.0) / 1e6, mix,
                  dst=INTERNET, src=None, direction="up")

        lan_mbps = (getattr(sig, "lan_bps", 0.0) or 0.0) / 1e6
        if lan_mbps > EPS:
            kind = getattr(getattr(device, "kind", None), "value", "")
            target = (lan_targets.get(host)
                      or DEFAULT_LAN_TARGETS.get(kind, LAN_FALLBACK))
            if topology.has_node(target):
                split(host, lan_mbps, mix, dst=target, src=None,
                      direction="lan")
    return out
