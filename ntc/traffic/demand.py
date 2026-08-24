"""Talep tahmini — doygun hatta ölçülen hız zaten tavandır.

**Sorun.** Çözücü "kim ne kadar istiyor" sorusunun cevabını girdi olarak
alıyor ve şimdiye kadar o cevap **ölçülen hız**dı. Hat boşken bu doğru: bir
cihaz 40 Mbps çekiyorsa 40 Mbps istiyordur. Hat doluyken **yanlış**, üstelik
sessizce yanlış:

    gerçek talep : 200 Mbps
    hat kapasitesi: 100 Mbps, üç cihaz yarışıyor
    ölçülen      :  33 Mbps      ← çözücü bunu "talep" sanıyor
    sonuç        : "33 istedi, 33 verdim, memnun"  → geri çekme listesi BOŞ

Yani sistem tam da tıkanma anında körleşiyor. Herkesi memnun görüyor çünkü
herkesi kendi kısıtlanmış halinden ölçüyor. Bu, optimize edilecek şeyin
kendisini kaybetmek demek.

**Çözüm: hat boş anları hafızada tut.** Doluluk eşiğin altındayken ölçülen
değer *gerçek taleptir* — orada kimse kısıtlanmıyor. O anları cihaz+yön
başına tepe değer olarak saklıyoruz. Hat dolduğunda ölçülen değeri değil,
o tepeyi kullanıyoruz.

**Neden ortalama değil tepe.** Ortalama, cihazın boşta olduğu dakikaları da
sayar ve talebi olduğundan küçük gösterir. Yedekleme sunucusu günün 22
saatinde 0 Mbps, 2 saatinde 400 Mbps çeker; ortalaması 33 Mbps'tir ve o sayı
hiçbir anı temsil etmez. Tepe, "bu cihaz fırsat bulduğunda ne kadar çekiyor"
sorusunun cevabı — kapasite planlamasının sorduğu soru da bu.

**Ama tepeyi her düşük ölçümde kullanamayız.** Tıkanık hatta düşük ölçümün
iki zıt sebebi olabilir:

    a) cihaz kısıtlanıyor  → talebi ölçülenden çok yüksek, tepesine güven
    b) cihaz zaten boşta   → talebi ölçülen kadar, tepesi eski bir iş

Ayırt eden sinyal: **cihaz adil payına dayanmış mı?** Payının tamamını
kullanan cihaz daha fazlasını istiyordur; payının çeyreğinde duran istemiyor.
Bu ayrım olmadan yedeklemesi bitmiş bir sunucu, aylar önceki 400 Mbps'lik
tepesiyle başkalarının payını çalardı.

**Dört güvenlik freni:**

1. **Baskı ayrımı.** Tepe yalnız cihaz payına dayanmışken kullanılıyor.
2. **Şişme tavanı.** Dayanan cihazda ölçülenin 10 katı, boştakinde 1 katı —
   yani boştaki cihaz için tahmin = ölçüm, şişme yok.
3. **Yaşlanma.** Tepe `retain_seconds` sonunda tamamen düşüyor. Değeri
   *kırpılmıyor* — 60 Mbps çeken bir cihazı 45 Mbps ister göstermek ne ölçüm
   ne tahmin olurdu. Olgu yaşlanmaz, ilgisi yaşlanır; yaşlanma güven
   puanında ifade ediliyor.
4. **Güven puanı.** Her tahmin nereden geldiğini ve ne kadar güvenilir
   olduğunu taşıyor. Uydurulmuş bir sayıyı ölçülmüş gibi göstermiyoruz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Tepe değerin tamamen değersizleşme süresi. 24 saat, `db.py`'nin sakladığı
# pencereyle aynı: daha uzunu zaten diskte yok, daha kısası gece yedekleme
# penceresini sabah unutmak demek.
RETAIN_SECONDS = 24 * 3600.0

# Tahminin ölçülenin kaç katına kadar çıkabileceği.
#
# **İki ayrı tavan var çünkü iki ayrı durum var.** Ölçülen değerin düşük
# olmasının iki sebebi olabilir ve bunlar taban tabana zıt:
#
#   a) cihaz kısıtlanıyor  → talebi ölçülenden ÇOK yüksek, tepesine güven
#   b) cihaz zaten boşta   → talebi ölçülen kadar, tepesi eski bir iş
#
# Ayırt eden sinyal: cihaz **adil payına dayanmış mı?** Tıkanık bir hatta
# payının tamamını kullanan cihaz daha fazlasını istiyordur; payının çeyreğini
# kullanan istemiyordur. Bu ayrım olmadan tek bir tavan koymak, (a)'yı
# kısıtlıyor ya da (b)'ye hayali talep uyduruyordu.
INFLATION_CAP_PRESSING = 10.0   # paya dayanmış: tepesine güven
INFLATION_CAP_IDLE = 1.0        # boşta: ölçülen neyse o
# Adil payın bu kadarını kullanan cihaz "dayanmış" sayılıyor.
PRESSING_RATIO = 0.90

# Bunun altındaki ölçüm gürültü sayılıyor; tepe olarak kaydedilmiyor.
MIN_SAMPLE_MBPS = 0.05


@dataclass
class Estimate:
    """Bir talebin tahmini — ve o tahminin nereden geldiği."""

    mbps: float
    measured_mbps: float
    confidence: float          # 0..1
    basis: str                 # olculdu | gecmis-tepe | tavan-alti | veri-yok

    @property
    def inflated(self) -> bool:
        return self.mbps > self.measured_mbps + 1e-6

    def to_dict(self) -> dict[str, Any]:
        return {"mbps": round(self.mbps, 3),
                "measured_mbps": round(self.measured_mbps, 3),
                "confidence": round(self.confidence, 2),
                "basis": self.basis, "inflated": self.inflated}


@dataclass
class Profile:
    """Bir (cihaz, yön) çiftinin geçmişi."""

    key: str
    peak_mbps: float = 0.0
    # `peak_ts = 0.0` sentinel olarak kullanılamaz: sıfır geçerli bir zaman
    # damgası (testlerde t=0'dan başlanıyor) ve tepe sessizce düşüyordu.
    has_peak: bool = False
    peak_ts: float = 0.0
    free_samples: int = 0        # hat boşken kaç kez ölçüldü
    busy_samples: int = 0
    last_mbps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "peak_mbps": round(self.peak_mbps, 3),
                "has_peak": self.has_peak, "peak_ts": self.peak_ts, "free_samples": self.free_samples,
                "busy_samples": self.busy_samples,
                "last_mbps": round(self.last_mbps, 3)}


class DemandEstimator:
    """Ölçülen hızdan gerçek talebi kestirir."""

    def __init__(self, retain_seconds: float = RETAIN_SECONDS,
                 pressing_cap: float = INFLATION_CAP_PRESSING) -> None:
        self.retain_seconds = retain_seconds
        self.pressing_cap = pressing_cap
        self._profiles: dict[str, Profile] = {}

    # ------------------------------------------------------------ yardımcı

    @staticmethod
    def key(host: str, direction: str) -> str:
        return f"{host}|{direction}"

    def profile(self, host: str, direction: str) -> Profile | None:
        return self._profiles.get(self.key(host, direction))

    def _live_peak(self, p: Profile, ts: float) -> float:
        """Hâlâ geçerli sayılan tepe — **değeri kırpılmadan**.

        ⚠️ İlk sürüm tepeyi yaşıyla orantılı küçültüyordu ve bu ölçülebilir
        biçimde yanlıştı: 60 Mbps çektiği gözlenen bir cihaz 6 saat sonra
        45 Mbps talep ediyor sayılıyordu. 45 hiçbir zaman gözlenmedi; ne
        ölçüm ne tahmin, ikisinin arasında anlamsız bir sayı.

        Tepe bir **olgu**: "bu cihaz saat 03:00'te 60 Mbps çekti". Olgu
        yaşlanmaz, *ilgisi* yaşlanır. O yüzden değer olduğu gibi duruyor,
        yaşlanma `confidence` tarafında ifade ediliyor; pencere dolunca da
        kırpılmadan tamamen düşüyor.
        """
        if not p.has_peak or p.peak_mbps <= 0:
            return 0.0
        if (ts - p.peak_ts) >= self.retain_seconds:
            return 0.0
        return p.peak_mbps

    def _freshness(self, p: Profile, ts: float) -> float:
        """Tepenin tazeliği, 0..1."""
        if not p.has_peak:
            return 0.0
        return 1.0 - min(1.0, max(0.0, ts - p.peak_ts) / self.retain_seconds)

    # ------------------------------------------------------------- gözlem

    def observe(self, host: str, direction: str, mbps: float,
                *, congested: bool, ts: float) -> None:
        """Bir ölçümü kaydeder.

        **Yalnız hat boşken tepe güncelleniyor.** Doluyken ölçülen değer
        cihazın isteğini değil, ona düşen payı gösteriyor; onu tepe diye
        saklamak tam da düzeltmeye çalıştığımız hatayı kalıcı hale getirirdi.
        """
        k = self.key(host, direction)
        p = self._profiles.get(k)
        if p is None:
            p = Profile(key=k)
            self._profiles[k] = p
        p.last_mbps = mbps

        if congested:
            p.busy_samples += 1
            return

        p.free_samples += 1
        if mbps < MIN_SAMPLE_MBPS:
            return
        # Tepe ya büyüdüğü için ya da eskisi tamamen düştüğü için güncelleniyor.
        if mbps >= self._live_peak(p, ts):
            p.peak_mbps = mbps
            p.peak_ts = ts
            p.has_peak = True

    # ------------------------------------------------------------- tahmin

    def estimate(self, host: str, direction: str, measured_mbps: float,
                 *, congested: bool, ts: float,
                 fair_share_mbps: float | None = None,
                 capped_at_mbps: float | None = None) -> Estimate:
        """Ölçülen hızdan gerçek talebi kestirir.

        `fair_share_mbps`: bu cihaza tıkanık hatta düşen adil pay. Ölçüm bu
        payın altında kalmışsa cihaz kısıtlandığı için değil, **istemediği
        için** azdır — tepesi geçmişte kalmış bir iştir, bugünkü talebi değil.
        Verilmezse temkinli tarafa düşülüyor (bkz. gövde).

        `capped_at_mbps`: bu cihaza kendi koyduğumuz hız tavanı. Ölçüm tavana
        yapışmışsa cihaz **bastırılıyor** demektir ve talebi en az tavan
        kadardır — tahminin en sağlam dayanağı, çünkü kısıtlayan biziz ve
        kısıtladığımızı biliyoruz.
        """
        p = self._profiles.get(self.key(host, direction))

        # --- hat boş: ölçülen değer zaten gerçek talep
        if not congested:
            return Estimate(mbps=measured_mbps, measured_mbps=measured_mbps,
                            confidence=1.0, basis="olculdu")

        # --- cihaz payına dayanmış mı?
        #
        # Bu ayrım tahminin belkemiği. Tıkanık bir hatta düşük ölçüm iki zıt
        # şey anlatabilir: cihaz *kısıtlanıyordur* (talebi çok yüksek) ya da
        # cihaz *boştadır* (talebi ölçülen kadar). İkisini ayırmadan tek bir
        # şişme tavanı koymak, kısıtlananı eziyor ya da boştakine hayali
        # talep uyduruyordu.
        #
        # Ayırt eden: payının tamamını kullanıyor mu. Payına dayanmış cihaz
        # daha fazlasını istiyordur; payının çeyreğinde duran istemiyordur.
        dayaniyor = False
        if fair_share_mbps is not None and fair_share_mbps > 0:
            dayaniyor = measured_mbps >= fair_share_mbps * PRESSING_RATIO
        # Pay bilgisi yoksa **şişirmiyoruz.** İlk sürümde tersi vardı ("tıkanık
        # hatta ölçüm zaten baskı altındadır") ve ölçümde yakalandı: payının
        # altıda birini kullanan boştaki bir sunucuya, aylar önceki tepesinden
        # 50 Mbps talep uyduruldu. Sinyal yokken iki durumu ayıramıyoruz;
        # ayıramadığımızda hayali talep üretmek, başkasının payını çalmak
        # demek. Ölçülende kalmak en kötü ihtimalle eski davranış — yani
        # hiçbir şey kaybetmiyoruz.

        if (capped_at_mbps is not None and capped_at_mbps > 0
                and measured_mbps >= capped_at_mbps * 0.95):
            # Kısıtlayan biziz ve kısıtladığımızı biliyoruz — en sağlam kanıt.
            dayaniyor = True

        tavan = measured_mbps * (self.pressing_cap if dayaniyor
                                 else INFLATION_CAP_IDLE)
        aday = measured_mbps
        temel = "olculdu" if not dayaniyor else "tavan-alti"
        guven = 0.8 if not dayaniyor else 0.35

        # --- kendi koyduğumuz tavana yapışmışsa: talep ≥ tavan
        if (capped_at_mbps is not None and capped_at_mbps > 0
                and measured_mbps >= capped_at_mbps * 0.95):
            aday = max(aday, capped_at_mbps * 1.25)
            temel = "baski-alti"
            guven = 0.7

        # --- boş saat tepesi
        if p is not None:
            tepe = self._live_peak(p, ts)
            if tepe > aday and dayaniyor:
                aday = tepe
                temel = "gecmis-tepe"
                # Güven, tepenin tazeliğinden ve kaç boş örnek gördüğümüzden.
                # Tek bir ölçüme dayanan tepe, yüz ölçüme dayanandan zayıf.
                ornek = min(1.0, p.free_samples / 20.0)
                guven = 0.4 + 0.5 * self._freshness(p, ts) * ornek
        elif measured_mbps > 0 and dayaniyor:
            temel = "veri-yok"
            guven = 0.2

        # --- şişme freni
        if aday > tavan and measured_mbps > MIN_SAMPLE_MBPS:
            aday = tavan
        return Estimate(mbps=max(aday, measured_mbps),
                        measured_mbps=measured_mbps,
                        confidence=round(guven, 2), basis=temel)

    # -------------------------------------------------------------- sunum

    def to_dict(self, limit: int = 40) -> dict[str, Any]:
        satirlar = sorted(self._profiles.values(),
                          key=lambda p: -p.peak_mbps)[:limit]
        return {"count": len(self._profiles),
                "profiles": [p.to_dict() for p in satirlar]}

    def prune(self, ts: float) -> int:
        """Değerini tamamen yitirmiş ve artık görülmeyen profilleri atar."""
        atilan = []
        for k, p in self._profiles.items():
            if self._live_peak(p, ts) <= 0 and p.last_mbps < MIN_SAMPLE_MBPS:
                atilan.append(k)
        for k in atilan:
            del self._profiles[k]
        return len(atilan)
