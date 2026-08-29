"""Hacim beslemesi — kablodan geçen baytı 5'li başına sayar.

**Neden ayrı bir besleme:** Windows'ta kimlik ile hacim aynı kaynaktan
gelmiyor ve bu, canlı modun bütün şeklini belirliyor:

    Sysmon Event 3 / bağlantı tablosu → bağlantıyı KİM açtı (süreç)
    paket yakalama                    → o bağlantıdan KAÇ BAYT aktı

Sysmon bağlantı olayında bayt alanı yok, yakalamada süreç yok. `live.py`
ikisini 5'li üzerinden birleştiriyor; bu dosya birleştirmenin hacim tarafı.

**Sayaç tutuluyor, paket saklanmıyor.** `store=False` ve geri çağırma
yalnız birkaç tamsayı artırıyor. 1 Gbps'lik bir hatta saniyede yüz binlerce
paket geçiyor; paket nesnelerini biriktirmek belleği dakikalar içinde
bitirirdi ve bize gereken zaten toplam.

**Yön yerel uca göre tanımlı.** Aynı bağlantının iki yönü tek kovaya
düşüyor: `local → remote` baytı `up`, `remote → local` baytı `down`.
Ayrı kovalara koysaydık tek bir TCP oturumu iki "akış" gibi görünür ve
cihaz başına hız iki katına çıkardı.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class CaptureUnavailable(RuntimeError):
    """Yakalama başlatılamıyor: scapy/Npcap yok ya da yetki yetmiyor."""


# Katman sınıfları burada saklanıyor; `_katmanlar()` bir kez dolduruyor.
_KATMAN: dict[str, Any] = {}


def _katmanlar() -> dict[str, Any]:
    """scapy katmanlarını yükler ve döndürür.

    **Bu erken yükleme kritik ve ölçümle öğrenildi.** Önce katmanlar
    `feed()` içinde, yani ilk paket geldiğinde import ediliyordu. Sonuç:
    scapy paketi **çözümlerken** `IP` sınıfı henüz yüklü olmadığı için
    bağlantı katmanını tanıyamıyor ("Unable to guess datalink type") ve her
    paketi ham `Packet` olarak kuruyordu; `IP in pkt` hiçbir zaman tutmuyor,
    yakalama 45 paket teslim etmesine rağmen sayaçlar **0** kalıyordu.

    Çözümleme geriye dönük düzeltilemiyor — paket zaten yanlış tipte
    kurulmuş oluyor. O yüzden katmanlar yakalama başlamadan **önce**
    yükleniyor.
    """
    if not _KATMAN:
        from scapy.layers.inet import IP, TCP, UDP
        from scapy.layers.l2 import Ether            # bağlantı katmanı eşlemesi
        _KATMAN.update({"IP": IP, "TCP": TCP, "UDP": UDP, "Ether": Ether})
        try:
            from scapy.layers.inet6 import IPv6
            _KATMAN["IPv6"] = IPv6
        except ImportError:                          # IPv6 katmanı yoksa
            _KATMAN["IPv6"] = None
    return _KATMAN


@dataclass
class VolumeRecord:
    """Bir 5'linin son boşaltmadan bu yana biriktirdiği hacim."""

    proto: str
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    bytes_down: int = 0          # remote -> local
    bytes_up: int = 0            # local -> remote
    packets: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0

    @property
    def key(self) -> tuple:
        return (self.proto, self.local_ip, self.local_port,
                self.remote_ip, self.remote_port)


def local_addresses() -> set[str]:
    """Bu makinenin IP adresleri — yön kararı buna dayanıyor."""
    import psutil
    out: set[str] = set()
    for adres_listesi in psutil.net_if_addrs().values():
        for adres in adres_listesi:
            # AF_INET / AF_INET6 sayısal değerleri platforma göre değişiyor;
            # aile numarası yerine metnin ayrıştırılabilirliğine bakıyoruz.
            ham = (adres.address or "").split("%")[0]
            try:
                ipaddress.ip_address(ham)
            except ValueError:
                continue          # MAC adresi ve benzeri
            out.add(ham)
    return out


def guess_interface() -> str | None:
    """Trafiğin gerçekten aktığı arayüzü seçer.

    **Ölçümle eklendi.** scapy'nin varsayılan arayüzü bu makinede bir TAP
    adaptörüydü: yakalama "başladı", `running` True döndü ve **10 saniyede
    0 paket** geldi. Yönetici hakkı da yoktu ama asıl sebep o değildi —
    doğru arayüzde (Wi-Fi) yönetici olmadan 33 paket yakalandı.

    Seçim yöntemi: kurulu dış bağlantıların **yerel uç adreslerine** bak,
    o adresi taşıyan arayüzü seç. "Arayüz ayakta mı" ya da "IP'si var mı"
    yetmiyor; VPN/VirtualBox adaptörleri de ayakta ve IP'li duruyor.
    Trafiğin aktığı yeri, akan trafik söylüyor.
    """
    try:
        import psutil
        from scapy.arch.windows import get_windows_if_list
    except ImportError:
        return None

    kullanilan: dict[str, int] = {}
    try:
        for c in psutil.net_connections(kind="inet"):
            if not (c.raddr and c.laddr):
                continue
            uzak = c.raddr.ip
            if uzak.startswith("127.") or uzak == "::1":
                continue
            kullanilan[c.laddr.ip] = kullanilan.get(c.laddr.ip, 0) + 1
    except Exception:
        log.debug("Bağlantı tablosu okunamadı", exc_info=True)
        return None

    if not kullanilan:
        return None

    en_iyi, en_iyi_puan = None, 0
    for arayuz in get_windows_if_list():
        puan = sum(kullanilan.get(ip, 0) for ip in (arayuz.get("ips") or []))
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = arayuz.get("name"), puan
    if en_iyi:
        log.info("Yakalama arayüzü seçildi: %s (%d bağlantı)", en_iyi, en_iyi_puan)
    return en_iyi


class PacketVolumeFeed:
    """scapy/Npcap ile 5'li başına bayt sayar.

    `start()` arka planda bir yakalama iş parçacığı açıyor; `drain()` o ana
    kadar birikeni döndürüp sayaçları sıfırlıyor. Toplayıcı döngüsü saniyede
    bir `drain()` çağırdığı için sayaçlar o pencerenin hacmini veriyor.
    """

    def __init__(self, iface: str | None = None, bpf: str = "ip or ip6",
                 promiscuous: bool = False,
                 yerel_adresler: set[str] | None = None) -> None:
        # Arayüz verilmediyse tahmin ediyoruz. scapy'nin varsayılanına
        # güvenmek bu makinede 0 paketle sonuçlanmıştı (bkz. guess_interface).
        self.iface = iface or guess_interface()
        self.secilen_arayuz_tahmini = iface is None
        self.bpf = bpf
        self.promiscuous = promiscuous
        self._yerel = yerel_adresler if yerel_adresler is not None else local_addresses()
        self._kilit = threading.Lock()
        self._kovalar: dict[tuple, VolumeRecord] = {}
        self._sniffer: Any = None
        # Yakalanan ama yerel uç bulunamayan paket sayısı. Sıfırdan farklıysa
        # ya yansıtma portundayız ya da adres listesi eksik — sessizce
        # düşürmek yerine sayıp raporluyoruz.
        self.yabanci_paket = 0
        self.toplam_paket = 0
        self._basladi_ts = 0.0
        self._sessizlik_uyarildi = False

    # ------------------------------------------------------------------ yaşam

    def start(self) -> None:
        try:
            # Katmanlar yakalamadan ÖNCE yüklenmeli — bkz. `_katmanlar()`.
            _katmanlar()
            from scapy.sendrecv import AsyncSniffer
        except ImportError as exc:
            raise CaptureUnavailable(
                "scapy kurulu değil. `pip install scapy` (Npcap da gerekli)."
            ) from exc

        try:
            self._sniffer = AsyncSniffer(
                iface=self.iface, filter=self.bpf, store=False,
                prn=self._paket, promisc=self.promiscuous)
            self._sniffer.start()
        except Exception as exc:                      # yetki, sürücü, arayüz
            raise CaptureUnavailable(
                f"paket yakalama başlatılamadı ({exc.__class__.__name__}: {exc}). "
                "Npcap kurulu mu ve süreç yönetici olarak mı çalışıyor?"
            ) from exc

        # AsyncSniffer hatayı kendi iş parçacığında yutabiliyor; kısa bir
        # bekleyişten sonra hâlâ ayakta değilse başlamamış sayıyoruz.
        # (Sessizce "yakalıyorum" demek, sıfır trafikle dolu bir panel demek.)
        self._sniffer.thread.join(0.5)
        if not self._sniffer.running:
            raise CaptureUnavailable(
                "yakalama iş parçacığı başlar başlamaz düştü — büyük ihtimalle "
                "yetki yok (yönetici olarak çalıştırın) ya da arayüz adı yanlış.")
        self._basladi_ts = time.time()
        log.info("Paket yakalama başladı (arayüz=%s, filtre=%r)",
                 self.iface or "varsayılan", self.bpf)

    # Bu kadar saniye boyunca tek paket görülmezse sessizlik bildiriliyor.
    # "running=True ama 0 paket" gerçekten yaşandı (yanlış arayüz) ve
    # dışarıdan sağlıklı görünüyordu: panel sıfır trafikle dolar, kimse
    # sebebini aramaz.
    SESSIZLIK_ESIGI = 15.0

    @property
    def sessiz(self) -> bool:
        """Yakalama ayakta ama hiç paket görmedi mi?"""
        if not self.running or self.toplam_paket > 0 or not self._basladi_ts:
            return False
        return (time.time() - self._basladi_ts) >= self.SESSIZLIK_ESIGI

    def aclose(self) -> None:
        if self._sniffer is not None:
            try:
                self._sniffer.stop()
            except Exception:
                log.exception("Yakalama durdurulamadı")
            self._sniffer = None

    @property
    def running(self) -> bool:
        return bool(self._sniffer is not None and self._sniffer.running)

    # --------------------------------------------------------------- yakalama

    def _paket(self, pkt: Any) -> None:
        """scapy iş parçacığından çağrılıyor — burada iş yapmak yakalamayı
        yavaşlatır ve paket düşürtür, o yüzden yalnız sayaç artırıyoruz."""
        try:
            self.feed(pkt)
        except Exception:                              # tek paket yüzünden
            log.debug("Paket ayrıştırılamadı", exc_info=True)  # yakalama düşmesin

    def feed(self, pkt: Any) -> None:
        """Bir paketi sayaçlara işler. Test bunu doğrudan çağırıyor."""
        k = _katmanlar()
        IP, TCP, UDP, IPv6 = k["IP"], k["TCP"], k["UDP"], k["IPv6"]

        if IP in pkt:
            ip_katman = pkt[IP]
        elif IPv6 is not None and IPv6 in pkt:
            ip_katman = pkt[IPv6]
        else:
            return

        self.toplam_paket += 1
        src, dst = str(ip_katman.src), str(ip_katman.dst)

        if TCP in pkt:
            proto, sport, dport = "tcp", int(pkt[TCP].sport), int(pkt[TCP].dport)
        elif UDP in pkt:
            proto, sport, dport = "udp", int(pkt[UDP].sport), int(pkt[UDP].dport)
        else:
            proto, sport, dport = "ip", 0, 0

        boyut = len(pkt)
        zaman = float(getattr(pkt, "time", 0.0) or 0.0)

        src_yerel, dst_yerel = src in self._yerel, dst in self._yerel
        if src_yerel and not dst_yerel:
            yerel_ip, yerel_port, uzak_ip, uzak_port = src, sport, dst, dport
            asagi, yukari = 0, boyut
        elif dst_yerel and not src_yerel:
            yerel_ip, yerel_port, uzak_ip, uzak_port = dst, dport, src, sport
            asagi, yukari = boyut, 0
        elif src_yerel and dst_yerel:
            # Loopback / makine içi. Hat kapasitesini tüketmiyor, saymıyoruz.
            return
        else:
            # İki uç da yabancı: yansıtma portu ya da eksik adres listesi.
            # Sessizce düşürmüyoruz, sayıyoruz ki `/api/status` görebilsin.
            self.yabanci_paket += 1
            return

        anahtar = (proto, yerel_ip, yerel_port, uzak_ip, uzak_port)
        with self._kilit:
            kova = self._kovalar.get(anahtar)
            if kova is None:
                kova = VolumeRecord(proto, yerel_ip, yerel_port,
                                    uzak_ip, uzak_port, first_ts=zaman)
                self._kovalar[anahtar] = kova
            kova.bytes_down += asagi
            kova.bytes_up += yukari
            kova.packets += 1
            kova.last_ts = zaman

    # ---------------------------------------------------------------- boşaltma

    def drain(self) -> list[VolumeRecord]:
        """Biriken hacmi döndürür ve sayaçları sıfırlar."""
        with self._kilit:
            kovalar = list(self._kovalar.values())
            self._kovalar = {}
        return kovalar
