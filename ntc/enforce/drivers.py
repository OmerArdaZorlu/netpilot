"""Sürücüler — infazın "nasıl" katmanı.

Politika nesnesi nötr: "cam-entrance indirmesi en fazla 45 Mbps". Sürücü onu
somut komuta çeviriyor. Ayrı durmalarının sebebi, aynı kararın iki farklı
dünyada tamamen farklı görünmesi:

    RateLimit(cam-entrance, down, 45)
      Linux router  →  tc class replace ... htb rate 45mbit
      Windows QoS   →  **imkânsız** — aşağıdaki nota bak

**Sürücüler komut üretir, çalıştırmaz.** Çalıştırma kararı `engine.py`'de ve
varsayılan olarak kapalı. Üretim ile çalıştırmanın ayrı olması bu katmanın
elimizde cihaz yokken de doğrulanabilmesini sağlıyor: komut metni
deterministik, teste karşı ölçülebilir.

**Desteklenmeyen kural sessizce atlanmıyor.** `UnsupportedRule` fırlatıyor ve
uzlaştırıcı bunu gerekçesiyle birlikte "atlandı" listesine yazıyor. Windows
tarafında indirmeyi kısma isteğine yaklaşık bir komut üretip "oldu" demek,
operatöre uygulanmayan bir kısıtı uygulanmış gibi göstermek olurdu.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .policy import (
    DSCP_NAME,
    Mark,
    PathPin,
    RateLimit,
    Rule,
)


class UnsupportedRule(Exception):
    """Bu sürücü bu kuralı uygulayamaz — gerekçesiyle."""


@dataclass
class Command:
    """Tek bir cihaz komutu.

    `destructive` bayrağı silme komutlarını işaretliyor; kuru çalıştırmada
    panelde ayrı renkte gösterilebilsin ve gerçek çalıştırmada ek onay
    istenebilsin diye ayrı duruyor.
    """

    text: str
    destructive: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "destructive": self.destructive,
                "note": self.note}


class Driver:
    """Sürücü arayüzü."""

    name = "base"
    # Operatöre "bu sürücü neyi yapabilir" diye göstermek için.
    supports = ("rate", "path", "mark")

    def add(self, rule: Rule) -> list[Command]:          # pragma: no cover
        raise NotImplementedError

    def remove(self, rule: Rule) -> list[Command]:       # pragma: no cover
        raise NotImplementedError

    def update(self, rule: Rule) -> list[Command]:
        """Değeri değişen kural.

        Varsayılan davranış `add` — çünkü hem `tc` hem Windows QoS tarafında
        kullandığımız komutlar `replace` / idempotent biçimde. Sil-ekle
        yapsaydık ikisinin arasındaki kısa boşlukta kısıt kalkardı ve tam da
        tıkanma anında trafik serbest kalırdı.
        """
        return self.add(rule)


# --------------------------------------------------------------------- anlatı


class DescribeDriver(Driver):
    """Hiçbir cihaza dokunmayan, Türkçe anlatan sürücü.

    Elimizde henüz fiziksel cihaz yok. Bu sürücü gölge modun varsayılanı:
    kararın ne olduğunu ve nereye uygulanacağını okunur biçimde yazıyor,
    böylece uzlaştırıcı ve panel gerçek cihaz olmadan da doğrulanabiliyor.
    """

    name = "anlat"

    def add(self, rule: Rule) -> list[Command]:
        nerede = "uç makinede" if rule.scope == "edge" else "çekirdek router'da"
        return [Command(f"[{nerede}] KUR  {rule.describe()}", note=rule.reason)]

    def remove(self, rule: Rule) -> list[Command]:
        return [Command(f"KALDIR  {rule.name}  ({rule.describe()})",
                        destructive=True)]


# ---------------------------------------------------------------------- Linux


class LinuxTcDriver(Driver):
    """Linux router: `tc` (hız), `ip rule` + `iptables mangle` (yol, damga).

    Hız için HTB seçildi çünkü tavan koymanın yanında **ödünç verebiliyor**:
    bir sınıf payını kullanmıyorsa diğerleri onu kullanabiliyor. Sert tavan
    (TBF) koysaydık boşta duran kapasiteyi kimse alamazdı — optimize edicinin
    hesapladığı payların anlamı da kalmazdı, çünkü hesap "şu an" için doğru,
    bir saniye sonrası için değil.

    Sınıf kimliği kural anahtarından türüyor ve kararlı: aynı kural her turda
    aynı `classid`'yi alıyor, bu yüzden `replace` idempotent çalışıyor.
    """

    name = "linux"

    def __init__(self, wan_if: str = "eth1", lan_if: str = "eth0",
                 mangle_chain: str = "NETPILOT",
                 table_by_egress: dict[str, str] | None = None) -> None:
        self.wan_if = wan_if
        self.lan_if = lan_if
        self.mangle_chain = mangle_chain
        # Çıkış düğümü → `ip route` tablo adı. Eşleşme yoksa kural
        # desteklenmiyor sayılıyor: olmayan bir tabloya yönlendiren
        # `ip rule` sessizce hiçbir şey yapmaz, ki bu en kötü sonuç.
        self.table_by_egress = table_by_egress or {}

    # İndirme kısıtı **giden** yönde uygulanır: router'ın LAN bacağından
    # cihaza doğru. Paket WAN'dan içeri girdikten sonra kısmak dar boğazı
    # geçmiş olur ama router'ın kuyruğunu kısaltmak yine de işe yarıyor —
    # gecikmeyi burada azaltıyoruz, bandı değil.
    def _iface(self, rule: Rule) -> str:
        return self.lan_if if rule.match.direction == "down" else self.wan_if

    @staticmethod
    def _classid(rule: Rule) -> str:
        # blake2b özeti 8 onaltılık hane; ilk dördü 0x1000..0xffff aralığına
        # taşıyoruz (0x0 ve 0x1 kök qdisc'e ait).
        ham = int(rule.key.split(":")[1][:4], 16)
        return f"1:{max(0x1000, ham):x}"

    @staticmethod
    def _mark(rule: Rule) -> str:
        return f"0x{int(rule.key.split(':')[1][:4], 16):x}"

    def _hedef(self, rule: Rule) -> str:
        ip = rule.match.ip
        if not ip:
            raise UnsupportedRule(
                f"{rule.match.host} için IP bilinmiyor; `tc` filtresi ada göre "
                f"eşleşemez. Cihaz envanterinde IP'yi doldurun.")
        return ip

    def add(self, rule: Rule) -> list[Command]:
        if isinstance(rule, RateLimit):
            return self._rate(rule)
        if isinstance(rule, PathPin):
            return self._path(rule)
        if isinstance(rule, Mark):
            return self._mark_rule(rule)
        raise UnsupportedRule(f"bilinmeyen kural türü: {rule.kind}")

    def _rate(self, rule: RateLimit) -> list[Command]:
        ip = self._hedef(rule)
        dev = self._iface(rule)
        cid = self._classid(rule)
        kbit = max(8, int(rule.cap_mbps * 1000))
        # İndirme yönünde hedef IP cihaz, yükleme yönünde kaynak IP cihaz.
        yon = "dst" if rule.match.direction == "down" else "src"
        return [
            Command(f"tc class replace dev {dev} parent 1: classid {cid} "
                    f"htb rate {kbit}kbit ceil {kbit}kbit",
                    note=rule.reason),
            Command(f"tc filter replace dev {dev} protocol ip parent 1: "
                    f"prio 1 handle {cid.split(':')[1]}::1 u32 "
                    f"match ip {yon} {ip}/32 flowid {cid}"),
        ]

    def _path(self, rule: PathPin) -> list[Command]:
        ip = self._hedef(rule)
        eksik = [e for e in rule.shares if e not in self.table_by_egress]
        if eksik:
            raise UnsupportedRule(
                f"{', '.join(eksik)} çıkışı için yönlendirme tablosu "
                f"tanımlı değil (enforce.linux.tables).")
        mark = self._mark(rule)
        cmds = [
            Command(f"iptables -t mangle -C {self.mangle_chain} -s {ip} "
                    f"-j MARK --set-mark {mark} "
                    f"|| iptables -t mangle -A {self.mangle_chain} -s {ip} "
                    f"-j MARK --set-mark {mark}",
                    note=rule.reason),
        ]
        # Payı en büyük çıkış birincil kural; kalanlar `PathAssigner`'ın
        # akış bazlı ataması sayesinde farklı işaretlerle geliyor. Burada
        # yalnız tabloları bağlıyoruz.
        for i, (egress, pay) in enumerate(sorted(rule.shares.items(),
                                                 key=lambda kv: -kv[1])):
            tablo = self.table_by_egress[egress]
            cmds.append(Command(
                f"ip rule replace fwmark {mark} lookup {tablo} "
                f"priority {200 + i}",
                note=f"{egress} payı %{pay * 100:.0f}"))
        return cmds

    def _mark_rule(self, rule: Mark) -> list[Command]:
        if not rule.selectors and not rule.apps:
            raise UnsupportedRule(
                f"{rule.match.traffic_class} sınıfı için eşleşecek port yok.")
        cmds = []
        for sel in rule.selectors:
            cmds.append(Command(
                f"iptables -t mangle -A {self.mangle_chain} "
                f"-p {sel['proto']} --dport {sel['port']} "
                f"-j DSCP --set-dscp {rule.dscp}",
                note=f"{rule.match.traffic_class} → "
                     f"{DSCP_NAME.get(rule.dscp, rule.dscp)}"))
        if rule.apps:
            # Port yetmiyor. Linux tarafında uygulamayı tanımanın yolu
            # `NFQUEUE`/DPI ya da uç makinedeki `cgroup` işareti — ikisi de
            # bu katmanın işi değil. Yorum satırı olarak bırakıp operatöre
            # görünür kılıyoruz; sessizce eksik bırakmıyoruz.
            cmds.append(Command(
                f"# {rule.match.traffic_class}: {', '.join(rule.apps)} "
                f"aynı portu (443) paylaşıyor, porttan ayrılamaz — "
                f"damga uç makinede uygulama eşleşmesiyle vurulmalı",
                note="uygulanmadı, bilgi"))
        return cmds

    def remove(self, rule: Rule) -> list[Command]:
        if isinstance(rule, RateLimit):
            cid = self._classid(rule)
            dev = self._iface(rule)
            return [
                Command(f"tc filter del dev {dev} parent 1: "
                        f"handle {cid.split(':')[1]}::1 prio 1 u32",
                        destructive=True),
                Command(f"tc class del dev {dev} classid {cid}",
                        destructive=True),
            ]
        if isinstance(rule, PathPin):
            mark = self._mark(rule)
            out = [Command(f"ip rule del fwmark {mark}", destructive=True)]
            if rule.match.ip:
                out.append(Command(
                    f"iptables -t mangle -D {self.mangle_chain} "
                    f"-s {rule.match.ip} -j MARK --set-mark {mark}",
                    destructive=True))
            return out
        if isinstance(rule, Mark):
            return [Command(
                f"iptables -t mangle -D {self.mangle_chain} "
                f"-p {s['proto']} --dport {s['port']} "
                f"-j DSCP --set-dscp {rule.dscp}", destructive=True)
                for s in rule.selectors]
        raise UnsupportedRule(f"bilinmeyen kural türü: {rule.kind}")


# -------------------------------------------------------------------- Windows


class WindowsQosDriver(Driver):
    """Windows Server domain'indeki uç makine: `New-NetQosPolicy`.

    **Ne yapabildiği dar ve bu bir kusur değil, konum sorunu.** Uç makinede
    durup şunları yapabiliyorsun:

    * yüklemeyi kısmak — paket henüz çıkmadı, kuyruk senin elinde
    * DSCP damgası vurmak — sonraki hoplar okusun diye

    Şunları yapamıyorsun:

    * **indirmeyi kısmak** — veri sana ulaştıysa dar boğazı çoktan geçmiştir;
      uçta atmak hattı boşaltmaz, sadece bandı çöpe atar
    * **yol seçmek** — Windows hedefe göre yönlendirir; kaynağa, uygulamaya
      veya sınıfa göre politika yönlendirmesi yok

    İkisi de `UnsupportedRule` fırlatıyor. Bu yüzden asıl infaz router'da;
    uç makine yalnız yükleme ve damga için var.
    """

    name = "windows"
    supports = ("rate:up", "mark")

    # Politika adları alıntılanmıyor çünkü alıntılanacak bir şey yok:
    # ad her zaman `netpilot-<tür>-<onaltılık>` biçiminde ve boşluk,
    # tırnak veya PowerShell operatörü içermiyor (bkz. `policy.Rule.name`).
    # Buraya POSIX alıntılaması (`shlex.quote`) koymak yanlış kabuk için
    # doğru görünen bir koruma olurdu.

    def __init__(self, use_remoting: bool = True) -> None:
        # Denetleyici merkezde, politika uçta. Uzaktan çalıştırma olmadan
        # bu sürücünün ürettiği komut yanlış makinede koşar.
        self.use_remoting = use_remoting

    def _wrap(self, host: str, ps: str) -> str:
        if not self.use_remoting:
            return ps
        return f"Invoke-Command -ComputerName {host} -ScriptBlock {{ {ps} }}"

    def add(self, rule: Rule) -> list[Command]:
        if isinstance(rule, RateLimit):
            if rule.match.direction != "up":
                raise UnsupportedRule(
                    "Windows QoS yalnız giden trafiği kısabilir; "
                    f"'{rule.match.direction}' yönü çekirdek router'da "
                    "uygulanmalı.")
            if not rule.match.host:
                raise UnsupportedRule("politika uygulanacak makine adı yok.")
            bps = int(rule.cap_mbps * 1_000_000)
            ps = (f"New-NetQosPolicy -Name {rule.name} "
                  f"-ThrottleRateActionBitsPerSecond {bps} "
                  f"-PolicyStore ActiveStore")
            return [Command(self._wrap(rule.match.host, ps), note=rule.reason)]

        if isinstance(rule, PathPin):
            raise UnsupportedRule(
                "Windows hedefe göre yönlendirir; kaynağa/sınıfa göre yol "
                "seçimi yok. Yol atamaları çekirdek router'da uygulanmalı.")

        if isinstance(rule, Mark):
            if not rule.selectors:
                raise UnsupportedRule(
                    f"{rule.match.traffic_class} sınıfı porttan ayırt "
                    f"edilemiyor ({', '.join(rule.apps)}); uygulama yolu "
                    f"eşleşmesi gerekiyor ve yolu operatör vermeli.")
            cmds = []
            for sel in rule.selectors:
                ps = (f"New-NetQosPolicy "
                      f"-Name {rule.name}-{sel['port']} "
                      f"-IPDstPortMatchCondition {sel['port']} "
                      f"-IPProtocolMatchCondition {sel['proto'].upper()} "
                      f"-DSCPAction {rule.dscp} -PolicyStore ActiveStore")
                cmds.append(Command(ps, note=rule.reason))
            if rule.apps:
                cmds.append(Command(
                    f"# {rule.match.traffic_class}: {', '.join(rule.apps)} "
                    f"için -AppPathNameMatchCondition '<exe yolu>' eklenmeli",
                    note="uygulanmadı, operatör tamamlamalı"))
            return cmds
        raise UnsupportedRule(f"bilinmeyen kural türü: {rule.kind}")

    def remove(self, rule: Rule) -> list[Command]:
        adlar = [rule.name]
        if isinstance(rule, Mark):
            adlar = [f"{rule.name}-{s['port']}" for s in rule.selectors]
        host = rule.match.host
        out = []
        for ad in adlar:
            ps = (f"Remove-NetQosPolicy -Name {ad} "
                  f"-PolicyStore ActiveStore -Confirm:$false")
            out.append(Command(self._wrap(host, ps) if host else ps,
                               destructive=True))
        return out


DRIVERS: dict[str, type[Driver]] = {
    "anlat": DescribeDriver,
    "linux": LinuxTcDriver,
    "windows": WindowsQosDriver,
}


def build_driver(name: str, **kwargs: Any) -> Driver:
    cls = DRIVERS.get(name)
    if cls is None:
        raise ValueError(f"bilinmeyen sürücü: {name} "
                         f"(seçenekler: {', '.join(DRIVERS)})")
    return cls(**kwargs)
