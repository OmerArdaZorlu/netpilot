"""Uzlaştırıcı — infazın "ne zaman" katmanı.

Akış çözücüsü 15 saniyede bir yeni bir plan üretiyor. Naif yaklaşım her turda
tüm kuralları silip yeniden kurmak olurdu; üç sebeple yanlış:

1. **Boşluk.** Sil ile ekle arasındaki anda kısıt yok. Tıkanma anında bu,
   saniyede birkaç kez vananın tam açılması demek.
2. **Gürültü.** Değişmeyen kural için cihaza komut göndermek, 40 cihazlık bir
   ağda dakikada yüzlerce gereksiz işlem.
3. **Kayıp.** Cihazdaki gerçek durumu bilmeden yazmak, elle konmuş kuralları
   ezme riski taşıyor.

Bunun yerine **istenen durum ile bilinen durumu karşılaştırıp yalnızca farkı**
uyguluyoruz. Her kural iki kimlik taşıyor (bkz. `policy.Rule`): `key` kimlik,
`fingerprint` kimlik+değer. Fark buradan çıkıyor.

**Varsayılan gölge modu.** `mode="golge"` komutları üretir, hiçbirini
çalıştırmaz. Gerçek çalıştırma açıkça bir `runner` verilmesini şart koşuyor;
çünkü elimizde doğrulayabildiğimiz bir cihaz yok ve doğrulanmamış bir
`subprocess.run` bu dosyaya girerse "infaz hazır" sanılır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .drivers import Command, Driver, UnsupportedRule
from .policy import SCOPE_CORE, PolicySet, Rule

log = logging.getLogger(__name__)

MODE_SHADOW = "golge"
MODE_LIVE = "canli"


@dataclass
class Skipped:
    rule: Rule
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule.to_dict(), "reason": self.reason}


@dataclass
class Reconciliation:
    """Bir uzlaştırma turunun sonucu."""

    added: list[Rule] = field(default_factory=list)
    changed: list[Rule] = field(default_factory=list)
    removed: list[Rule] = field(default_factory=list)
    unchanged: list[Rule] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    executed: bool = False
    mode: str = MODE_SHADOW
    driver: str = ""

    @property
    def touched(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed)

    def summary(self) -> str:
        return (f"{len(self.added)} yeni, {len(self.changed)} değişti, "
                f"{len(self.removed)} kaldırıldı, "
                f"{len(self.unchanged)} aynı, {len(self.skipped)} atlandı "
                f"→ {len(self.commands)} komut "
                f"({'çalıştırıldı' if self.executed else 'çalıştırılmadı'})")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "driver": self.driver,
            "executed": self.executed, "summary": self.summary(),
            "added": [r.to_dict() for r in self.added],
            "changed": [r.to_dict() for r in self.changed],
            "removed": [r.to_dict() for r in self.removed],
            "unchanged": [r.to_dict() for r in self.unchanged],
            "skipped": [s.to_dict() for s in self.skipped],
            "commands": [c.to_dict() for c in self.commands],
        }


class Enforcer:
    """İstenen politika kümesini cihaza taşır — ya da gölgede yazar.

    `runner`, komut listesini alıp çalıştıran bir fonksiyon. Verilmediyse
    `mode="canli"` reddediliyor. Bu bir eksiklik değil bilinçli bir kapı:
    çalıştırma kodu ancak üzerinde doğrulanabileceğimiz bir cihaz olduğunda
    yazılmalı, yoksa test edilmemiş kod "hazır" görünür.
    """

    def __init__(self, driver: Driver | dict[str, Driver],
                 mode: str = MODE_SHADOW,
                 runner: Callable[[list[Command]], None] | None = None) -> None:
        if mode == MODE_LIVE and runner is None:
            raise ValueError(
                "canlı mod için bir `runner` gerekli; gölge modda çalıştırma "
                "yapılmadığı için `runner` istenmez.")
        # **Kapsam başına ayrı sürücü.** Ağın iki farklı yerine iki farklı
        # dille yazıyoruz: çekirdekteki router `tc` konuşuyor, uçtaki Windows
        # domain `New-NetQosPolicy`. Tek sürücüyle çalışsaydık ya uçtaki
        # damgalar ya çekirdekteki indirme kısıtları düşerdi — ikisi de tam
        # olarak infazın var olma sebebi.
        #
        # Tek `Driver` verilirse tüm kapsamlara o bakıyor (test ve tek cihazlı
        # kurulum için).
        self.drivers: dict[str, Driver] = (
            dict(driver) if isinstance(driver, dict) else {"*": driver})
        self.mode = mode
        self.runner = runner
        # Cihazda **bizim koyduğumuzu bildiğimiz** durum. Cihazdan okunan
        # değil: geri okuma sürücüye özgü ve henüz cihazımız yok. Sınırı
        # burada açıkça yazıyoruz ki "cihazın gerçek hali" sanılmasın.
        self._state: dict[str, str] = {}          # key -> fingerprint
        self._rules: dict[str, Rule] = {}         # key -> son bilinen kural
        self.last: Reconciliation | None = None

    # ------------------------------------------------------------------ sorgu

    @property
    def driver(self) -> Driver | None:
        """Birincil sürücü — çekirdek, yoksa genel, yoksa ilk tanımlı."""
        return (self.drivers.get(SCOPE_CORE) or self.drivers.get("*")
                or next(iter(self.drivers.values()), None))

    @property
    def driver_label(self) -> str:
        if set(self.drivers) == {"*"}:
            return self.drivers["*"].name
        return ", ".join(f"{k}={v.name}"
                         for k, v in sorted(self.drivers.items()))

    def _driver_for(self, rule: Rule) -> Driver:
        d = self.drivers.get(rule.scope) or self.drivers.get("*")
        if d is None:
            raise UnsupportedRule(
                f"'{rule.scope}' kapsamı için sürücü tanımlı değil "
                f"(enforce.core_driver / enforce.edge_driver).")
        return d

    @property
    def active(self) -> list[Rule]:
        return list(self._rules.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "driver": self.driver_label,
            "drivers": {k: {"name": v.name, "supports": list(v.supports)}
                        for k, v in sorted(self.drivers.items())},
            "active": [r.to_dict() for r in self.active],
            "last": self.last.to_dict() if self.last else None,
        }

    # ------------------------------------------------------------ uzlaştırma

    def reconcile(self, desired: PolicySet,
                  approved: set[str] | None = None) -> Reconciliation:
        """İstenen durumu bilinen duruma uygular ve farkı döndürür.

        `approved` verilirse yalnız o anahtarlardaki kurallar kurulur;
        gerisi **istenmemiş** sayılır ve aktifse kaldırılır. Operatör onayı
        böyle bağlanıyor: onay kalkınca kural da kalkıyor, ayrı bir "geri al"
        yoluna gerek kalmadan.
        """
        istenen = desired.by_key()
        if approved is not None:
            istenen = {k: v for k, v in istenen.items() if k in approved}

        sonuc = Reconciliation(mode=self.mode, driver=self.driver_label)

        for key, rule in istenen.items():
            onceki = self._state.get(key)
            if onceki is None:
                sonuc.added.append(rule)
            elif onceki != rule.fingerprint:
                sonuc.changed.append(rule)
            else:
                sonuc.unchanged.append(rule)

        for key, rule in self._rules.items():
            if key not in istenen:
                sonuc.removed.append(rule)

        # Komutlar: önce kaldırma, sonra kurma. Ters sırada yapsaydık aynı
        # `classid`'yi hedefleyen bir ekleme, hemen ardından gelen silmeyle
        # iptal olabilirdi.
        for rule in sonuc.removed:
            self._collect(sonuc, rule, "remove")
        for rule in sonuc.added:
            self._collect(sonuc, rule, "add")
        for rule in sonuc.changed:
            self._collect(sonuc, rule, "update")

        atlanan = {s.rule.key for s in sonuc.skipped}

        if self.mode == MODE_LIVE and sonuc.commands:
            assert self.runner is not None
            self.runner(sonuc.commands)
            sonuc.executed = True

        # Durum defterini ancak komut üretimi başarılıysa güncelliyoruz.
        # Desteklenmeyen bir kuralı "kuruldu" diye yazsaydık, bir sonraki
        # turda "değişmedi" görünüp sonsuza kadar uygulanmamış kalırdı.
        for rule in sonuc.removed:
            if rule.key in atlanan:
                continue
            self._state.pop(rule.key, None)
            self._rules.pop(rule.key, None)
        for rule in sonuc.added + sonuc.changed:
            if rule.key in atlanan:
                continue
            self._state[rule.key] = rule.fingerprint
            self._rules[rule.key] = rule

        self.last = sonuc
        log.info("infaz (%s/%s): %s", self.driver.name, self.mode,
                 sonuc.summary())
        return sonuc

    def _collect(self, sonuc: Reconciliation, rule: Rule, islem: str) -> None:
        try:
            sonuc.commands.extend(
                getattr(self._driver_for(rule), islem)(rule))
        except UnsupportedRule as exc:
            sonuc.skipped.append(Skipped(rule=rule, reason=str(exc)))
            for bucket in (sonuc.added, sonuc.changed, sonuc.removed):
                if rule in bucket:
                    bucket.remove(rule)
                    break

    def rollback(self) -> Reconciliation:
        """Kurduğumuz her şeyi kaldırır.

        Kapanışta çağrılmalı. Aksi halde denetleyici durduğunda ağda sahipsiz
        kısıtlar kalır ve kimse onları kaldırmaz — en kötü arıza biçimi,
        çünkü sebebi görünmez.
        """
        return self.reconcile(PolicySet(rules=[]))
