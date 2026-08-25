"""AI'ın akışı **doğrudan** ürettiği katman.

Önceki tasarımda AI çözücünün hedefini kuruyordu (sınıf sırası, taban
profili) ve akışı doğrusal program hesaplıyordu. Kullanıcının istediği bu
değildi: **akışı AI bulacak.** Aradaki yol serbest, ama zincirin başında AI
duracak.

Buradaki tasarım:

    topoloji + talepler ──► AI ──► tahsis önerisi ──► DOĞRULAYICI ──► akış
                                                          │
                                                          └─ geçersizse onar
                                                             ya da LP'ye düş

LP artık karar verici değil **hakem**: AI'ın planını ölçüyor, gerekirse
onarıyor, ve AI hiç geçerli bir şey üretemezse yedek olarak devreye giriyor.

**Neden doğrulayıcı zorunlu.** Model aritmetik yapamıyor (ölçüldü: %17.5
doluluğu "critical" dedi, bir sınıf payını %122 raporladı). Kapasiteyi aşan
ya da talepten fazla veren bir tahsis, uygulanırsa ağı bozar. Bu yüzden her
öneri üç kısıttan geçiyor:

  1. hiçbir tahsis talebi aşamaz
  2. hiçbir bacak kapasitesini aşamaz
  3. adı geçen cihaz/bacak gerçekten var olmalı

İhlal sessizce kabul edilmiyor: ya orantılı onarılıyor (kapasite aşımı) ya
da düşürülüyor (uydurma cihaz). Ne yapıldığı `issues` içinde yazılı kalıyor.

**Ölçülebilirlik.** `score()` AI'ın planını LP optimumuna karşı ölçüyor.
"AI optimize ediyor mu" sorusunun cevabı bir orandır, bir iddia değil.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Modele gidecek en fazla talep satırı. Küçük model uzun listede kayboluyor;
# üstelik istem uzunluğu ONNX Runtime'ın bellek ayırmasını şişiriyor (bir kez
# 1.2 GB ayırmaya çalışıp düştü). En büyük N talep gidiyor, gerisi LP'ye.
MAX_ROWS = 10
EPS = 1e-6


@dataclass
class Grant:
    """AI'ın tek bir talebe verdiği pay."""

    device: str
    direction: str
    traffic_class: str
    grant_mbps: float
    egress: str = ""

    @property
    def key(self) -> str:
        return f"{self.device}|{self.direction}|{self.traffic_class}"

    def to_dict(self) -> dict[str, Any]:
        return {"device": self.device, "direction": self.direction,
                "traffic_class": self.traffic_class,
                "grant_mbps": round(self.grant_mbps, 3), "egress": self.egress}


@dataclass
class AIFlowPlan:
    """AI'ın ürettiği akış — doğrulanmış hali."""

    grants: list[Grant] = field(default_factory=list)
    rationale: str = ""
    situation: str = ""
    issues: list[str] = field(default_factory=list)
    valid: bool = False
    # Doğrulayıcının ne kadar müdahale ettiği: 0.0 hiç, 1.0 tamamen yeniden
    # yazıldı. AI'ın gerçekten karar verip vermediğini bu sayı gösteriyor.
    repair_ratio: float = 0.0

    def by_key(self) -> dict[str, float]:
        return {g.key: g.grant_mbps for g in self.grants}

    @property
    def total_mbps(self) -> float:
        return sum(g.grant_mbps for g in self.grants)

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "situation": self.situation,
                "rationale": self.rationale, "issues": self.issues,
                "repair_ratio": round(self.repair_ratio, 3),
                "total_mbps": round(self.total_mbps, 2),
                "grants": [g.to_dict() for g in self.grants]}


# --------------------------------------------------------------- istem girdisi


def demand_rows(demands: list[Any], limit: int = MAX_ROWS
                ) -> tuple[list[dict[str, Any]], list[Any]]:
    """Talepleri modele gidecek satırlara indirger.

    Cihaz+yön+sınıf başına toplanıyor: model 50 satırlık ham listede
    kayboluyor, üstelik aynı cihazın aynı sınıftaki iki akışını ayrı ayrı
    yönetmesinin bir anlamı da yok — kısıt zaten cihaz düzeyinde konuyor.

    **LAN talepleri modele gitmiyor.** Onların hedefi internet değil (NVR,
    dosya sunucusu); çıkış bacağı seçilecek bir şey yok ve kapasiteleri de
    WAN'dan bağımsız. Gönderince model yönü `down` sanıp satırı bozuyordu
    (ölçüldü: 3 satır bu yüzden tanınmadı). LAN'ı LP çözüyor.

    Her satır kısa bir **kimlik** taşıyor (`r1`, `r2`…) ve model kimlikle
    cevap veriyor. Cihaz adını, yönü ve sınıfı yeniden yazdırmak ölçülebilir
    hata kaynağıydı: model `lan`'ı `down` yazıyor, bacak alanına `indirme`
    koyuyordu. Yazdırmadığın şeyi yanlış yazamaz.

    Dönen ikili: (modele giden satırlar, modele gitmeyen talepler).
    """
    toplu: dict[str, dict[str, Any]] = {}
    lan_talepleri: list[Any] = []
    for d in demands:
        if d.direction not in ("down", "up"):
            lan_talepleri.append(d)
            continue
        k = f"{d.device}|{d.direction}|{d.traffic_class.value}"
        row = toplu.setdefault(k, {
            "device": d.device, "direction": d.direction,
            "class": d.traffic_class.value, "want_mbps": 0.0,
            "_demands": [],
        })
        row["want_mbps"] += d.mbps
        row["_demands"].append(d)

    sirali = sorted(toplu.values(), key=lambda r: -r["want_mbps"])
    gidenler = sirali[:limit]
    kalanlar = [d for r in sirali[limit:] for d in r["_demands"]]
    kalanlar += lan_talepleri
    for i, r in enumerate(gidenler, start=1):
        r["id"] = f"r{i}"
        r["want_mbps"] = round(r["want_mbps"], 1)
        r.pop("_demands", None)
    return gidenler, kalanlar


def egress_rows(topology: Any) -> list[dict[str, Any]]:
    """Çıkış bacakları — yön başına kapasiteleriyle.

    Yön ayrımı şart: indirme ve yükleme ayrı kaynaklar ve gerçek hatlarda
    aralarında 10 kat fark olabiliyor. Tek bir "kapasite" sayısı vermek
    modeli yükleme hattını indirme sanmaya iter.
    """
    from ..traffic.topology import INTERNET

    bacaklar: dict[str, dict[str, Any]] = {}
    for e in getattr(topology, "edges", []):
        if getattr(e, "kind", "") != "wan":
            continue
        if e.src == INTERNET:
            b = bacaklar.setdefault(e.dst, {"name": e.dst})
            b["down_mbps"] = round(e.effective_mbps, 1)
            b["latency_ms"] = e.latency_ms
            b["metered"] = e.cost_per_gb > 0
            b["healthy"] = e.health >= 0.99
        elif e.dst == INTERNET:
            b = bacaklar.setdefault(e.src, {"name": e.src})
            b["up_mbps"] = round(e.effective_mbps, 1)
            b.setdefault("latency_ms", e.latency_ms)
            b.setdefault("metered", e.cost_per_gb > 0)
            b.setdefault("healthy", e.health >= 0.99)
    return sorted(bacaklar.values(), key=lambda b: -(b.get("down_mbps", 0.0)))


# ---------------------------------------------------------------- doğrulama


def _normalize(raw: Any) -> dict[str, Any] | None:
    """Modelin ürettiği şema varyantlarını tek biçime indirir.

    **Neden var:** rastgele mimarilerde ölçüldü ki phi-4-mini şemayı bazı
    ağlarda kendiliğinden değiştiriyor — üst düzeyde bir *liste* döndürüyor
    ve her elemanın içine kendi `allocations` alanını koyuyor:

        [{"id": "r1", "rationale": "...", "allocations": {...}}, ...]

    İçerik doğruydu (10 satır, makul sayılar, bacaklar seçilmiş) ama
    `raw.get("allocations")` bulunamadığı için plan tümden reddediliyordu.
    Yani modelin verdiği iyi cevabı **biz** çöpe atıyorduk. Ölçümde bu tek
    başına 10 ağın 1'ini geçersizden 10 tahsise çeviriyor.

    Kabul edilen biçimler: üst düzey liste, `allocations` alanı tek sözlük,
    ve satırların içine gömülmüş `allocations`.
    """
    if isinstance(raw, dict) and isinstance(raw.get("allocations"), list):
        duz = _flatten(raw["allocations"])
        if duz:
            return {"situation": raw.get("situation", ""),
                    "rationale": raw.get("rationale", ""), "allocations": duz}
    if isinstance(raw, dict) and isinstance(raw.get("allocations"), dict):
        return {"situation": raw.get("situation", ""),
                "rationale": raw.get("rationale", ""),
                "allocations": [raw["allocations"]]}
    if isinstance(raw, list):
        duz = _flatten(raw)
        if not duz:
            return None
        # Gerekçe satırlara dağılmış oluyor; ilk dolu olanı alıyoruz.
        durum = next((str(x.get("situation", "")) for x in raw
                      if isinstance(x, dict) and x.get("situation")), "")
        gerekce = next((str(x.get("rationale", "")) for x in raw
                        if isinstance(x, dict) and x.get("rationale")), "")
        return {"situation": durum, "rationale": gerekce, "allocations": duz}
    return None


def _flatten(items: Any) -> list[dict[str, Any]]:
    """Sarmalanmış tahsis satırlarını düz listeye açar."""
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        ic = it.get("allocations")
        if isinstance(ic, dict):
            out.append(ic)
        elif isinstance(ic, list):
            out.extend(x for x in ic if isinstance(x, dict))
        elif "grant_mbps" in it or "id" in it:
            out.append(it)
    return out


def validate(raw: Any, rows: list[dict[str, Any]],
             legs: list[dict[str, Any]]) -> AIFlowPlan:
    """Modelin önerisini kısıtlara karşı doğrular ve gerekirse onarır.

    **Sessiz kabul yok, sessiz ret de yok.** Her müdahale `issues` içine
    yazılıyor ve `repair_ratio` ne kadarının yeniden yazıldığını söylüyor.
    O sayı olmadan "AI karar verdi" demek ölçülemez bir iddia olurdu —
    doğrulayıcı planın yarısını yeniden yazmışsa karar AI'ın değildir.
    """
    plan = AIFlowPlan()
    duz = _normalize(raw)
    if duz is None:
        plan.issues.append("öneri tanınan bir biçimde değil")
        return plan
    if not isinstance(raw, dict) or not isinstance(raw.get("allocations"), list):
        plan.issues.append("şema düzeltildi: model alışılmış biçimi vermedi")
    raw = duz

    # Kimlik → satır. Model adı yeniden yazmıyor, kimlik veriyor.
    by_id = {r["id"]: r for r in rows if "id" in r}
    istek = {f"{r['device']}|{r['direction']}|{r['class']}": r["want_mbps"]
             for r in rows}
    bacak_adlari = {b["name"] for b in legs}
    ham = raw.get("allocations")
    if not isinstance(ham, list) or not ham:
        plan.issues.append("allocations listesi yok ya da boş")
        return plan

    plan.situation = str(raw.get("situation", "")).strip()[:120]
    plan.rationale = str(raw.get("rationale", "")).strip()[:400]

    onerilen_toplam = 0.0
    duzeltilen_toplam = 0.0
    grants: list[Grant] = []
    gorulen: set[str] = set()

    for item in ham:
        if not isinstance(item, dict):
            plan.issues.append("tahsis satırı sözlük değil, atlandı")
            continue
        # Önce kimlik; yoksa eski biçime düş (model kimliği unutursa).
        rid = str(item.get("id", "")).strip().lower()
        satir = by_id.get(rid)
        if satir is not None:
            dev, yon, sinif = satir["device"], satir["direction"], satir["class"]
        else:
            dev = str(item.get("device", "")).strip()
            yon = str(item.get("direction", "")).strip().lower()
            sinif = str(item.get("class",
                                 item.get("traffic_class", ""))).strip().lower()
        k = f"{dev}|{yon}|{sinif}"
        if k not in istek:
            plan.issues.append(
                f"tanınmayan talep: {rid or k}")
            continue
        if k in gorulen:
            plan.issues.append(f"aynı talep iki kez: {k}")
            continue
        gorulen.add(k)

        try:
            ver = float(item.get("grant_mbps", 0.0))
        except (TypeError, ValueError):
            plan.issues.append(f"{k}: grant_mbps sayı değil, 0 sayıldı")
            ver = 0.0
        ver = max(0.0, ver)
        onerilen_toplam += ver

        # 1. kısıt: talebi aşamaz
        if ver > istek[k] + EPS:
            duzeltilen_toplam += ver - istek[k]
            plan.issues.append(
                f"{k}: talepten fazla ({ver:.1f} > {istek[k]:.1f}), kırpıldı")
            ver = istek[k]

        bacak = str(item.get("egress", "")).strip()
        if bacak and bacak not in bacak_adlari:
            plan.issues.append(f"{k}: olmayan bacak '{bacak}', boş bırakıldı")
            bacak = ""

        grants.append(Grant(device=dev, direction=yon, traffic_class=sinif,
                            grant_mbps=ver, egress=bacak))

    eksik = set(istek) - gorulen
    if eksik:
        # Atlanan talebe 0 vermek, modelin unuttuğunu "kes" diye okumak olur.
        # Unutmak bir karar değil; bunları LP'ye bırakıyoruz.
        plan.issues.append(f"{len(eksik)} talep hiç yanıtlanmadı")

    # 2. kısıt: yön başına toplam kapasiteyi aşamaz
    for yon, alan in (("down", "down_mbps"), ("up", "up_mbps")):
        kap = sum(b.get(alan, 0.0) for b in legs)
        if kap <= 0:
            continue
        toplam = sum(g.grant_mbps for g in grants if g.direction == yon)
        if toplam > kap + 0.5:
            k = kap / toplam
            duzeltilen_toplam += toplam - kap
            plan.issues.append(
                f"{yon}: toplam {toplam:.1f} > kapasite {kap:.1f}, "
                f"%{(1 - k) * 100:.0f} orantılı kısıldı")
            for g in grants:
                if g.direction == yon:
                    g.grant_mbps *= k

    plan.grants = grants
    plan.valid = bool(grants)
    plan.repair_ratio = (duzeltilen_toplam / onerilen_toplam
                         if onerilen_toplam > EPS else 0.0)
    return plan


# ------------------------------------------------------------------- ölçüm


def score(plan: AIFlowPlan, lp_total_mbps: float,
          demands_total_mbps: float) -> dict[str, Any]:
    """AI planını LP optimumuna karşı ölçer.

    `vs_optimum` 1.0 ise AI, LP kadar iyi bir akış buldu. 0.6 ise LP'nin
    bulduğunun %60'ını geçirebildi — yani ağın %40'ı boşa gitti.

    Bu sayı olmadan "AI optimize ediyor" demek doğrulanamaz bir iddiadır.
    """
    ai = plan.total_mbps
    return {
        "ai_total_mbps": round(ai, 2),
        "lp_total_mbps": round(lp_total_mbps, 2),
        "demand_mbps": round(demands_total_mbps, 2),
        "vs_optimum": round(ai / lp_total_mbps, 4) if lp_total_mbps > 0 else 0.0,
        "vs_demand": round(ai / demands_total_mbps, 4)
        if demands_total_mbps > 0 else 0.0,
        "repair_ratio": round(plan.repair_ratio, 3),
        "issues": len(plan.issues),
    }


def pins_for(plan: AIFlowPlan, demands: list[Any]) -> dict[str, float]:
    """AI tahsislerini `Demand.key` anahtarlarına çevirir.

    Model cihaz+yön+sınıf düzeyinde cevap veriyor; çözücü ise her (kaynak,
    hedef) çifti için ayrı bir talep tutuyor. Aynı üçlüye birden çok talep
    düşerse modelin verdiği pay **büyüklükleriyle orantılı** bölünüyor —
    modelden o ayrıntıyı istemek, zaten zorlandığı aritmetiği artırmak
    olurdu.
    """
    by_triple: dict[str, list[Any]] = {}
    for d in demands:
        k = f"{d.device}|{d.direction}|{d.traffic_class.value}"
        by_triple.setdefault(k, []).append(d)

    pins: dict[str, float] = {}
    for g in plan.grants:
        grup = by_triple.get(g.key)
        if not grup:
            continue
        toplam = sum(d.mbps for d in grup)
        if toplam <= EPS:
            continue
        for d in grup:
            pins[d.key] = g.grant_mbps * (d.mbps / toplam)
    return pins
