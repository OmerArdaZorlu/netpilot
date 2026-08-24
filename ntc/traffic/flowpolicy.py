"""Akış politikası — çözücünün "neye göre optimal" sorusunun cevabı.

**Bu dosyanın var olma sebebi.** `flowopt.py` bir doğrusal program çözüyor ve
verilen hedefe göre matematiksel olarak en iyi cevabı buluyor. Ama *hedefin
kendisi* bir olgu değil, bir karar — ve o karar şimdiye kadar dosyanın içinde
sabit tablolar olarak gömülüydü:

    realtime her zaman bulk'u yener        ← gece 03:00'te yanlış
    taban paylar hep aynı                  ← olay anında yanlış
    gecikme maliyetten baskın              ← sayaçlı hat devredeyken yanlış
    bozuk hat cezası sabit                 ← tek sağlam bacak kalınca yanlış

Sabit bir tablo, sabit bir ağ ve sabit bir gün varsayıyor. Gerçekte mimari,
koşullar ve öncelikler değişiyor; hedef de onlarla değişmeli.

**İş bölümü:**

    durum (ölçüm + bağlam) ──► AI ──► FlowPolicy ──► LP ──► optimal akış
                                      (hedef)              (sayılar)

AI **sayı üretmiyor** — sayıyı LP üretiyor. AI sıralama ve ağırlık üretiyor:
"şu an yedekleme penceresi, bulk'u yukarı al", "yedek bacak sayaçlı, para
ağırlığını artır". Bu, küçük bir modelin gerçekten yapabildiği iş; aritmetik
değil, durum yargısı. (Ölçüldü: phi-4-mini %17.5 doluluğu "critical" diyor —
sayı karşılaştıramıyor. Ama beş sınıfı yeniden sıralayabiliyor.)

**Her alan doğrulanabilir.** Sıralama beş sınıfın permütasyonu olmak zorunda,
tabanlar tavanı aşamaz, ağırlıklar aralık dışına çıkamaz. Geçersiz çıktı
sessizce kabul edilmiyor: gerekçesiyle reddedilip varsayılana düşülüyor.
Modelin ağa dokunabileceği tek kapı bu ve kapı dar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.models import TrafficClass

# Sınıf adları — doğrulamanın referansı.
CLASSES = [c.value for c in TrafficClass]

# Tabanların toplamına tavan. Tamamı taban olursa öncelik sırasının hiçbir
# anlamı kalmaz: herkes garantisini alır, artık kalmaz, sıralama boşa döner.
MAX_FLOOR_TOTAL = 0.60
MAX_FLOOR_ONE = 0.40

# Ağırlık aralıkları. Üst sınırlar keyfi değil: `path_weight` amaç
# fonksiyonunda karşılanan talebin (katsayı 1.0) yanında **küçük kalmalı**,
# yoksa çözücü talebi karşılamak yerine kısa yol seçmeye başlar — yani
# optimize edilecek şeyi kaybederiz.
WEIGHT_LIMITS = {
    "path_weight":    (0.0, 1e-2),
    "latency_weight": (0.0, 10.0),
    "cost_weight":    (0.0, 100.0),
    "health_weight":  (0.0, 1000.0),
}

# ---------------------------------------------------- kategorik seçenekler
#
# **Neden sayı değil kelime.** Modelden sayı istediğimizde ölçtük (4 durum,
# gerçek phi-4-mini):
#
#   sıralama (kelime işi) → 1/4 doğru, 2/4 korudu, 1/4 bozdu
#   ağırlıklar (sayı işi) → 4/4 HİÇ dokunmadı, varsayılanı aynen geri yazdı
#   tabanlar (sayı işi)   → hepsini %14.6'da eşitledi veya üç sınıfı sıfırladı
#
# "Sayaçlı hat devrede" diye yazdığı halde para ağırlığını 10'da bıraktı.
# Yani niyeti anlıyor, sayıya çeviremiyor. Aynı kusur %17.5'i "critical"
# demesinde ve bir payı %122 raporlamasında da vardı.
#
# Çözüm: sayıyı ondan hiç istememek. Model "yüksek" diyor, sayıyı kod
# koyuyor. Bu, modelin gösterilebilir şekilde yapabildiği işe indirgiyor —
# yapamadığı işi daha iyi istemekle çözmeye çalışmaktansa.

WEIGHT_LEVELS: dict[str, dict[str, float]] = {
    "latency_weight": {"dusuk": 0.2, "normal": 1.0, "yuksek": 5.0},
    "cost_weight":    {"dusuk": 1.0, "normal": 10.0, "yuksek": 60.0},
    "health_weight":  {"dusuk": 20.0, "normal": 100.0, "yuksek": 500.0},
}

# Taban profilleri — sayıları burada tutuyoruz, model yalnız adını seçiyor.
# Her profil toplamı MAX_FLOOR_TOTAL altında ve her sınıfa sıfırdan büyük
# taban veriyor: "düşük öncelikli" ile "feda edilebilir" aynı şey değil.
FLOOR_PROFILES: dict[str, dict[str, float]] = {
    # Normal mesai: insan bekliyor, ama hiçbir sınıf aç kalmıyor.
    "dengeli": {"realtime": 0.12, "interactive": 0.12, "streaming": 0.08,
                "bulk": 0.04, "background": 0.02},
    # Görüşme yoğun (toplantı saati, çağrı merkezi).
    "gorusme-oncelikli": {"realtime": 0.24, "interactive": 0.16,
                          "streaming": 0.04, "bulk": 0.02, "background": 0.02},
    # Gece yedekleme penceresi: ofis boş, transferin bitmesi lazım.
    "yedekleme-penceresi": {"realtime": 0.06, "interactive": 0.05,
                            "streaming": 0.02, "bulk": 0.32,
                            "background": 0.03},
    # Olay/kriz: haberleşme ve yönetim ayakta kalsın, gerisi beklesin.
    "kriz": {"realtime": 0.26, "interactive": 0.22, "streaming": 0.02,
             "bulk": 0.01, "background": 0.04},
}


@dataclass
class FlowPolicy:
    """Çözücünün optimize edeceği hedef."""

    # Sınıflar bu sırayla doyuruluyor; baştakiler önce.
    class_order: list[str] = field(
        default_factory=lambda: list(CLASSES))
    # Sınıf başına asgari garanti — **kapasitenin** yüzdesi.
    #
    # Neden kapasitenin, talebin değil: ilk sürümde talebin oranıydı ve
    # baskı altında çöktü. Talep büyüyünce tabanlar da büyüyor, üst
    # sınıfların tabanları kapasiteyi bitiriyor, en alttakine yine bir şey
    # kalmıyordu (1245 Mbps talep / 350 kapasite ile ölçüldü).
    floors: dict[str, float] = field(default_factory=lambda: {
        "realtime": 0.12, "interactive": 0.12, "streaming": 0.08,
        "bulk": 0.04, "background": 0.02,
    })
    # Yol tercihinin toplam ağırlığı. Talebi karşılamanın önüne geçmemeli.
    path_weight: float = 1e-4
    # Yol tercihi içindeki karışım: gecikme mi, para mı, sağlık mı?
    latency_weight: float = 1.0
    cost_weight: float = 10.0
    health_weight: float = 100.0

    # Kim koydu ve neden — panelde ve kayıtta görünür.
    source: str = "varsayilan"        # varsayilan | ai | operator
    rationale: str = ""
    situation: str = ""               # AI'ın okuduğu durum etiketi

    # ---------------------------------------------------------- doğrulama

    def priority_of(self, traffic_class: str) -> int:
        try:
            return self.class_order.index(traffic_class)
        except ValueError:
            # Sırada olmayan sınıf en sona: bilinmeyeni öne almak,
            # bilinen bir sınıfı sessizce aç bırakmak demek olurdu.
            return len(self.class_order)

    def floor_of(self, traffic_class: str) -> float:
        return max(0.0, float(self.floors.get(traffic_class, 0.0)))

    @classmethod
    def validate(cls, raw: Any) -> tuple["FlowPolicy | None", list[str]]:
        """Ham sözlüğü politikaya çevirir; geçersizse gerekçeyle reddeder.

        **Sessiz düzeltme yok.** Bir alanı kırpıp geri kalanını kabul etmek,
        modelin yarım anladığı bir kararı ağa taşımak olurdu. Yapısal hata
        (eksik sınıf, sıralama değil) reddediliyor; yalnız sayısal taşmalar
        (taban toplamı, ağırlık aralığı) kırpılıyor ve **kırpıldığı
        yazılıyor** — o da geri bildirim, gizlenecek bir şey değil.
        """
        sorunlar: list[str] = []
        if not isinstance(raw, dict):
            return None, ["politika bir sözlük değil"]

        # --- sıralama: beş sınıfın permütasyonu olmak zorunda
        order = raw.get("class_order")
        if not isinstance(order, list):
            return None, ["class_order bir liste değil"]
        order = [str(x).strip().lower() for x in order]
        if sorted(order) != sorted(CLASSES):
            eksik = sorted(set(CLASSES) - set(order))
            fazla = sorted(set(order) - set(CLASSES))
            return None, [f"class_order beş sınıfın sıralaması değil "
                          f"(eksik: {eksik or '-'}, tanınmayan: {fazla or '-'})"]

        # --- tabanlar: önce profil adı, yoksa ham sayılar
        #
        # İki yol var çünkü iki farklı yazar var. Model **profil adı**
        # veriyor (sayıyı beceremediği ölçüldü); operatör ve config dosyası
        # doğrudan sayı yazabiliyor. Sayı yolunu kapatmak, insanın ince
        # ayarını da engellerdi.
        profil = raw.get("floor_profile")
        if profil is not None:
            ad = str(profil).strip().lower()
            if ad not in FLOOR_PROFILES:
                return None, [f"floor_profile tanınmadı: {ad!r} "
                              f"(seçenekler: {', '.join(FLOOR_PROFILES)})"]
            ham_floors: Any = dict(FLOOR_PROFILES[ad])
        else:
            ham_floors = raw.get("floors") or {}
        if not isinstance(ham_floors, dict):
            return None, ["floors bir sözlük değil"]
        floors: dict[str, float] = {}
        for c in CLASSES:
            try:
                v = float(ham_floors.get(c, 0.0))
            except (TypeError, ValueError):
                return None, [f"floors.{c} sayı değil: {ham_floors.get(c)!r}"]
            if v < 0:
                v = 0.0
            if v > MAX_FLOOR_ONE:
                sorunlar.append(f"{c} tabanı %{v*100:.0f} → "
                                f"%{MAX_FLOOR_ONE*100:.0f}'e kırpıldı")
                v = MAX_FLOOR_ONE
            floors[c] = v

        toplam = sum(floors.values())
        if toplam > MAX_FLOOR_TOTAL:
            # Orantılı küçültüyoruz: modelin kurduğu *denge* korunuyor,
            # yalnız ölçek düşüyor. Tek tek kırpmak dengeyi bozardı.
            k = MAX_FLOOR_TOTAL / toplam
            floors = {c: round(v * k, 4) for c, v in floors.items()}
            sorunlar.append(f"taban toplamı %{toplam*100:.0f} → "
                            f"%{MAX_FLOOR_TOTAL*100:.0f}'e orantılı küçültüldü")

        # --- ağırlıklar: "dusuk/normal/yuksek" ya da ham sayı
        agirliklar: dict[str, float] = {}
        varsayilan = cls()
        for ad, (alt, ust) in WEIGHT_LIMITS.items():
            ham = raw.get(ad, getattr(varsayilan, ad))
            seviyeler = WEIGHT_LEVELS.get(ad)
            if isinstance(ham, str) and seviyeler:
                anahtar = ham.strip().lower()
                # Türkçe karakterle yazılmış olabilir; ikisini de kabul et.
                anahtar = (anahtar.replace("ü", "u").replace("ş", "s")
                                  .replace("ı", "i").replace("ö", "o")
                                  .replace("ç", "c").replace("ğ", "g"))
                if anahtar not in seviyeler:
                    return None, [f"{ad} tanınmadı: {ham!r} "
                                  f"(seçenekler: {', '.join(seviyeler)})"]
                agirliklar[ad] = seviyeler[anahtar]
                continue
            try:
                v = float(ham)
            except (TypeError, ValueError):
                return None, [f"{ad} sayı ya da seviye değil: {ham!r}"]
            if not (alt <= v <= ust):
                kirpik = min(max(v, alt), ust)
                sorunlar.append(f"{ad} {v:g} → {kirpik:g} aralığa çekildi")
                v = kirpik
            agirliklar[ad] = v

        return cls(
            class_order=order, floors=floors,
            source=str(raw.get("source", "ai")),
            rationale=str(raw.get("rationale", "")).strip()[:400],
            situation=str(raw.get("situation", "")).strip()[:120],
            **agirliklar,
        ), sorunlar

    # ------------------------------------------------------------- sunum

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_order": list(self.class_order),
            "floors": {k: round(v, 4) for k, v in self.floors.items()},
            "path_weight": self.path_weight,
            "latency_weight": self.latency_weight,
            "cost_weight": self.cost_weight,
            "health_weight": self.health_weight,
            "floor_total": round(sum(self.floors.values()), 4),
            "source": self.source,
            "rationale": self.rationale,
            "situation": self.situation,
        }

    def describe(self) -> str:
        sira = " > ".join(self.class_order)
        return (f"[{self.source}] {self.situation or 'genel'} · sıra: {sira} · "
                f"taban %{sum(self.floors.values())*100:.0f} · "
                f"gecikme {self.latency_weight:g} / para {self.cost_weight:g} / "
                f"sağlık {self.health_weight:g}")

    def diff(self, other: "FlowPolicy") -> list[str]:
        """İki politika arasındaki farkı insan diliyle söyler."""
        out = []
        if self.class_order != other.class_order:
            out.append(f"sıra: {' > '.join(other.class_order)} → "
                       f"{' > '.join(self.class_order)}")
        for c in CLASSES:
            a, b = other.floor_of(c), self.floor_of(c)
            if abs(a - b) > 0.005:
                out.append(f"{c} tabanı %{a*100:.0f} → %{b*100:.0f}")
        for ad in ("path_weight", "latency_weight", "cost_weight",
                   "health_weight"):
            a, b = getattr(other, ad), getattr(self, ad)
            if a != b:
                out.append(f"{ad} {a:g} → {b:g}")
        return out


DEFAULT_POLICY = FlowPolicy()
