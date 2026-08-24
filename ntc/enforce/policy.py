"""Cihazdan bağımsız politika nesneleri — infazın "ne" katmanı.

Akış çözücüsü bir plan üretiyor: kime ne kadar hız, hangi trafik hangi
bacaktan. O plan bir **hesap sonucu**; içinde `tc`, `New-NetQosPolicy` veya
Cisco komutu yok, olmamalı da. Burası araya giren çeviri katmanı: planı
"hangi eşleşmeye hangi kısıt" biçimindeki nötr kurallara dönüştürüyor.
Somut komutu sürücüler (`ntc/enforce/drivers.py`) üretiyor.

Neden ayrı katman: aynı karar iki farklı yerde uygulanacak. Yükleme kısıtı
Windows domain'indeki uç makinede (`New-NetQosPolicy`), indirme kısıtı ve
yol seçimi çekirdekteki router'da (`tc` + `ip rule`). Tek bir "komut üret"
fonksiyonu yazsaydık bu ayrım koda gömülü kalırdı; ayrı durunca kural nesnesi
*nerede* uygulanacağını kendi taşıyor (`scope`).

**Kapsam (`scope`) bir tercih değil, fizik.**

* `edge`  — uç makine / erişim anahtarı. Buradan yüklemeyi kısabilirsin
            (paket henüz çıkmadı) ve DSCP damgası vurabilirsin.
* `core`  — router kesişimi. İndirme kısıtı ancak burada anlamlı: paket
            uca vardığında dar boğazı çoktan geçmiş olur, orada kısmak
            bandı geri getirmez. Yol seçimi de yalnız burada mümkün.

`scope` **"en erken nerede yapılabilir"** demek, "yalnız orada yapılabilir"
değil. Router yüklemenin de yukarısında durduğu için `edge` etiketli bir
kuralı da uygulayabiliyor; bu yüzden Linux sürücüsü kapsam ayrımı gözetmeden
hepsini kabul ediyor. Ayrım asıl Windows tarafında ısırıyor: uç makine
`core` işini yapamaz ve `UnsupportedRule` fırlatır. Bir gün "tutarlılık için"
uzlaştırıcıya kapsam filtresi eklenirse, router'ın yükleme kısıtları sessizce
düşer — o yüzden burada yazılı.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- DSCP eşlemesi
#
# RFC 4594'ün önerdiği standart kod noktaları. Uydurmuyoruz, çünkü damganın
# tek işlevi *başka birinin* onu tanıması: yol üzerindeki router, ISP'nin
# kenar cihazı, Wi-Fi erişim noktasının WMM kuyruğu. Kendi sayımızı
# koysaydık damga bizden sonra hiçbir şey ifade etmezdi.
DSCP_BY_CLASS = {
    "realtime": 46,       # EF  — Expedited Forwarding
    "interactive": 26,    # AF31
    "streaming": 18,      # AF21
    "bulk": 10,           # AF11
    "background": 8,      # CS1 — "scavenger", boşta kalan bantla yetinir
}

DSCP_NAME = {46: "EF", 26: "AF31", 18: "AF21", 10: "AF11", 8: "CS1"}

SCOPE_EDGE = "edge"
SCOPE_CORE = "core"

DIRECTION_LABEL = {"down": "indirme", "up": "yükleme", "lan": "LAN içi"}


def class_selectors(traffic_class: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Bir sınıfı somut port/uygulama seçicilerine indirger.

    Dönen ikili: (kesin portlar, porttan ayırt edilemeyen uygulamalar).

    **443 sorunu.** Uygulama kataloğunda beş uygulama aynı portta oturuyor —
    web, Netflix, YouTube, Windows Update, telemetri — ve dördü farklı
    sınıfta. Yani "443 → interactive" diye bir damga kuralı yazmak, tıkanmayı
    yaratan Windows Update trafiğine de en yüksek etkileşimli önceliği vermek
    demek. Tam ters etki.

    Bu yüzden belirsiz portu **hiç** üretmiyoruz; o sınıfın uygulamaları
    ayrı bir listede dönüyor ve sürücü onları uygulama-yolu eşleşmesi
    gerektiren, operatörün tamamlaması gereken kural olarak işaretliyor.
    Eksik bilgiyi tahminle doldurmak burada sessiz bir yanlışa dönüşürdü.
    """
    from ..traffic.catalog import APPS

    sinif_by_port: dict[tuple[str, int], set[str]] = {}
    for app in APPS.values():
        sinif_by_port.setdefault((app.proto, app.port), set()).add(
            app.traffic_class.value)

    kesin: list[dict[str, Any]] = []
    belirsiz: list[str] = []
    for app in APPS.values():
        if app.traffic_class.value != traffic_class:
            continue
        anahtar = (app.proto, app.port)
        if len(sinif_by_port[anahtar]) == 1:
            satir = {"proto": app.proto, "port": app.port}
            if satir not in kesin:
                kesin.append(satir)
        else:
            belirsiz.append(app.name)

    kesin.sort(key=lambda d: (d["proto"], d["port"]))
    return kesin, sorted(set(belirsiz))


def _short(text: str) -> str:
    """Kural anahtarından kısa, kararlı bir kimlik.

    Cihaz adları uzun ve Türkçe karakterli olabiliyor; `tc` sınıf kimliği ve
    Windows politika adı ikisi de dar. Hash kararlı olmak zorunda: aynı kural
    her turda aynı kimliği almalı, yoksa uzlaştırıcı her seferinde "yeni kural"
    görüp siler-ekler.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=4).hexdigest()


@dataclass(frozen=True)
class Match:
    """Bir kuralın hangi trafiğe değdiği.

    Boş alan "önemsiz" demek. `host` insan tarafı, `ip` makine tarafı:
    sürücüler ip varsa onu kullanıyor, yoksa host adıyla üretilen komut
    operatörün elle tamamlaması için işaretleniyor.
    """

    host: str = ""
    ip: str = ""
    direction: str = ""          # up | down | lan
    traffic_class: str = ""

    def describe(self) -> str:
        parts = []
        if self.host:
            parts.append(self.host)
        if self.ip:
            parts.append(f"({self.ip})")
        if self.direction:
            parts.append(DIRECTION_LABEL.get(self.direction, self.direction))
        if self.traffic_class:
            parts.append(self.traffic_class)
        return " ".join(parts) or "tüm trafik"

    @property
    def token(self) -> str:
        return f"{self.host}|{self.ip}|{self.direction}|{self.traffic_class}"

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "ip": self.ip,
                "direction": self.direction, "traffic_class": self.traffic_class}


@dataclass
class Rule:
    """Tüm politika türlerinin ortak tabanı.

    İki ayrı kimlik var ve ikisi de uzlaştırma için şart:

    * `key`         — kuralın **kimliği**. Değeri değişse de aynı kalır.
    * `fingerprint` — kimlik + **değer**. Değişimi buradan görüyoruz.

    Sadece `key` olsaydı 45 Mbps'ten 30 Mbps'e düşen bir tavan "değişmedi"
    sayılırdı. Sadece `fingerprint` olsaydı her değer değişimi sil-ekle
    olurdu ve aradaki boşlukta kural yokken trafik serbest kalırdı.
    """

    match: Match
    scope: str = SCOPE_CORE
    reason: str = ""

    kind: str = "rule"

    @property
    def key(self) -> str:
        return f"{self.kind}:{_short(self.match.token)}"

    @property
    def fingerprint(self) -> str:
        return self.key

    @property
    def name(self) -> str:
        """Cihaz üzerinde görünecek ad.

        `netpilot-` öneki kritik: uzlaştırıcı **yalnızca bu önekli nesneleri**
        siliyor. Operatörün elle koyduğu bir kuralı temizlememizin başka bir
        güvencesi yok.
        """
        return f"netpilot-{self.key.replace(':', '-')}"

    def describe(self) -> str:
        return self.key

    def to_dict(self) -> dict[str, Any]:
        d = {"kind": self.kind, "key": self.key, "name": self.name,
             "scope": self.scope, "match": self.match.to_dict(),
             "reason": self.reason, "describe": self.describe()}
        for alan in ("cap_mbps", "shares", "branch_node", "dscp", "apps"):
            if hasattr(self, alan):
                d[alan] = getattr(self, alan)
        return d


@dataclass
class RateLimit(Rule):
    """Hız tavanı — "vanayı kıs" tarafı."""

    cap_mbps: float = 0.0
    kind: str = field(default="rate", init=False)

    @property
    def fingerprint(self) -> str:
        # 0.1 Mbps'e yuvarlıyoruz. Çözücü her turda birkaç kbit oynayan
        # sayılar üretiyor; yuvarlamasak uzlaştırıcı durmadan "değişti" deyip
        # cihaza gereksiz komut yağdırırdı.
        return f"{self.key}@{self.cap_mbps:.1f}"

    def describe(self) -> str:
        return f"{self.match.describe()} → en fazla {self.cap_mbps:.1f} Mbps"


@dataclass
class PathPin(Rule):
    """Yol seçimi — "farklı yola yönlendir" tarafı.

    `shares` = çıkış düğümü → pay (toplamı 1.0). Paketi değil **akışı**
    bölüyoruz; hangi akışın hangi çıkışa düştüğünü `PathAssigner` hash ile
    belirliyor, burada yalnız oranlar duruyor.
    """

    shares: dict[str, float] = field(default_factory=dict)
    branch_node: str = ""
    kind: str = field(default="path", init=False)

    @property
    def fingerprint(self) -> str:
        pay = ",".join(f"{k}:{v:.2f}" for k, v in sorted(self.shares.items()))
        return f"{self.key}@{self.branch_node}/{pay}"

    def describe(self) -> str:
        pay = ", ".join(f"{k} %{v * 100:.0f}"
                        for k, v in sorted(self.shares.items(),
                                           key=lambda kv: -kv[1]))
        return f"{self.match.describe()} → {self.branch_node} üzerinden {pay}"


@dataclass
class Mark(Rule):
    """DSCP damgası — sınıfı yol üzerindeki diğer cihazlara duyurur.

    Kısıtlamıyor, sıraya sokuyor. Bizim kısıtlarımız kendi cihazlarımızda
    çalışıyor; damga ise aradaki her cihazın kendi kuyruğunda doğru kararı
    vermesini sağlıyor — bizim yönetmediğimiz hoplar dahil.
    """

    dscp: int = 0
    # Damganın vurulacağı somut seçiciler. Sınıf soyut bir kavram; cihaz
    # "realtime" diye bir şey bilmiyor, port ve uygulama biliyor.
    selectors: list[dict[str, Any]] = field(default_factory=list)
    # Porttan ayırt edilemeyen uygulamalar (hepsi 443'te). Bunlar için
    # uygulama yolu eşleşmesi gerekiyor ve yolu operatör dolduruyor.
    apps: list[str] = field(default_factory=list)
    kind: str = field(default="mark", init=False)

    @property
    def fingerprint(self) -> str:
        return f"{self.key}@{self.dscp}"

    def describe(self) -> str:
        ad = DSCP_NAME.get(self.dscp, str(self.dscp))
        kuyruk = ", ".join(f"{s['proto']}/{s['port']}" for s in self.selectors)
        ek = f" [{kuyruk}]" if kuyruk else ""
        if self.apps:
            ek += f" + uygulama eşleşmesi gereken {len(self.apps)} uygulama"
        return f"{self.match.describe()} → DSCP {ad} ({self.dscp}){ek}"


@dataclass
class PolicySet:
    """Bir uzlaştırma turunun **istenen** durumu."""

    rules: list[Rule] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rules)

    def by_key(self) -> dict[str, Rule]:
        return {r.key: r for r in self.rules}

    def scoped(self, scope: str) -> list[Rule]:
        return [r for r in self.rules if r.scope == scope]

    def to_dict(self) -> dict[str, Any]:
        return {"count": len(self.rules),
                "rules": [r.to_dict() for r in self.rules]}


# ------------------------------------------------------------------- çeviri


def policies_from_plan(plan: Any, devices: dict[str, Any] | None = None,
                       min_pullback_mbps: float = 0.5,
                       min_split_mbps: float = 1.0,
                       with_marks: bool = True) -> PolicySet:
    """Akış planını politika kurallarına çevirir.

    Aksiyonlardan (`actions_from_plan`) değil **plandan** çeviriyoruz.
    Aksiyon operatöre gösterilen, gerekçeli, onaylanabilir bir kayıt;
    politika ise cihaza gidecek makine okunur kısıt. İkisini tek nesne
    yapmak, panelde okunaklı olsun diye eklenen her alanı cihaz komutuna
    taşımak demekti.

    Kapsam ataması fizikten geliyor, tercihten değil — modül başlığındaki
    nota bak.
    """
    ip_by_host = {}
    for d in (devices or {}).values():
        host = getattr(d, "hostname", None)
        if host:
            ip_by_host[host] = getattr(d, "ip", "") or ""

    rules: list[Rule] = []

    # --- 1. Hız tavanları -------------------------------------------------
    for row in plan.pullbacks(min_pullback_mbps):
        host = row["device"]
        yon = row["direction"]
        # LAN içi trafiğin kısıtı da çekirdekte: kaynak uç kısabilir ama
        # LAN'da dar boğaz genelde hedef taraf (NVR'ın disk yazma hızı,
        # dosya sunucusunun bağlantısı), uç makine onu göremiyor.
        scope = SCOPE_EDGE if yon == "up" else SCOPE_CORE
        rules.append(RateLimit(
            match=Match(host=host, ip=ip_by_host.get(host, ""), direction=yon),
            scope=scope,
            cap_mbps=round(row["granted_mbps"], 2),
            reason=(f"{host} {DIRECTION_LABEL.get(yon, yon)} yönünde "
                    f"{row['demand_mbps']:.1f} Mbps istiyor, ağ "
                    f"{row['granted_mbps']:.1f} Mbps verebiliyor."),
        ))

    # --- 2. Yol atamaları -------------------------------------------------
    for a in plan.allocations:
        if a.granted_mbps <= min_split_mbps:
            continue
        # Gerçek bölünme = tek düğümden birden çok kenara çıkış. Yol
        # üzerindeki ardışık duraklar (sw-core → wan → internet) bölünme
        # değil; ilk sürüm bunu karıştırıp tek yollu topolojide bile
        # "yönlendir" diyordu.
        dallar: dict[str, dict[str, float]] = {}
        for (src, dst), v in a.edge_usage.items():
            if v > min_split_mbps:
                dallar.setdefault(src, {})[dst] = v
        dugum, cikislar = max(dallar.items(), key=lambda kv: len(kv[1]),
                              default=(None, {}))
        if len(cikislar) < 2:
            continue
        toplam = sum(cikislar.values())
        host = a.demand.device
        rules.append(PathPin(
            match=Match(host=host, ip=ip_by_host.get(host, ""),
                        direction=a.demand.direction,
                        traffic_class=a.demand.traffic_class.value),
            scope=SCOPE_CORE,
            branch_node=dugum or "",
            shares={k: round(v / toplam, 4) for k, v in cikislar.items()},
            reason=(f"{host} trafiği tek bacağa sığmıyor; "
                    f"{dugum} üzerinden bölünüyor."),
        ))

    # --- 3. DSCP damgaları ------------------------------------------------
    #
    # Cihaz başına değil **sınıf başına**. Damganın işi trafiği tanıtmak;
    # hangi cihazdan geldiği damgayı değiştirmiyor. Cihaz başına üretseydik
    # 40 cihazlık bir ağda 200 kural çıkardı, hepsi aynı beş değeri taşıyan.
    if with_marks:
        siniflar = {a.demand.traffic_class.value for a in plan.allocations
                    if a.granted_mbps > 0}
        for sinif in sorted(siniflar):
            dscp = DSCP_BY_CLASS.get(sinif)
            if not dscp:
                continue
            kesin, belirsiz = class_selectors(sinif)
            if not kesin and not belirsiz:
                continue
            rules.append(Mark(
                match=Match(traffic_class=sinif),
                scope=SCOPE_EDGE, dscp=dscp,
                selectors=kesin, apps=belirsiz,
                reason=f"{sinif} trafiği yol boyunca doğru kuyruğa düşsün.",
            ))

    return PolicySet(rules=rules)


def approved_keys(actions: list[Any], policies: PolicySet) -> set[str]:
    """Operatörün onayladığı aksiyonları politika anahtarlarına bağlar.

    Aksiyon ve politika birebir aynı şey değil: aksiyon operatöre gösterilen
    gerekçeli kayıt, politika cihaza giden kısıt. Aralarındaki köprü
    (tür, makine, yön) üçlüsü — ikisi de aynı plandan çıktığı için bu üçlü
    ikisinde de aynı.

    **Damgalar onay istemiyor ve bu bilinçli.** DSCP kimseyi kısmıyor,
    yalnız trafiği tanıtıyor; en kötü ihtimalle yol üstündeki cihaz damgayı
    yok sayar. Kısan her kural (hız tavanı, yol ataması) onay bekliyor.
    """
    onayli = set()
    for a in actions:
        if not getattr(a, "applied", False):
            continue
        params = getattr(a, "params", {}) or {}
        tur = {"rate_limit": "rate", "reroute": "path"}.get(
            getattr(getattr(a, "kind", None), "value", ""), "")
        if not tur:
            continue
        onayli.add((tur, params.get("hostname", ""), params.get("direction", "")))

    out = set()
    for r in policies.rules:
        if r.kind == "mark":
            out.add(r.key)
            continue
        if (r.kind, r.match.host, r.match.direction) in onayli:
            out.add(r.key)
    return out
