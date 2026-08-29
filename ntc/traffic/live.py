"""Canlı kaynak — gerçek trafiği `Flow` nesnelerine çeviren birleştirici.

Faz 2. Simülatörün yerine aynı `FlowSource` arayüzünden geçiyor; toplayıcı
hangisinin takılı olduğunu bilmiyor.

**Tasarımın tek cümlelik özeti: kimlik ile hacim ayrı beslemelerden gelir ve
5'li üzerinden birleşir.**

    capture.PacketVolumeFeed   → 5'li başına bayt/paket  (süreç bilmiyor)
    ConnectionOwners           → 5'li başına süreç/PID   (bayt bilmiyor)
                     ⋈ (proto, yerel ip:port, uzak ip:port)
                          → Flow (hacim + süreç adı)

Windows'ta bunları tek yerden alamıyoruz: Sysmon Event 3 bağlantıyı kimin
açtığını söylüyor ama bayt alanı yok; yakalama baytı tam veriyor ama süreç
yok. Sysmon ileride `ConnectionOwners`'ın yerine geçebilir — birleştirmenin
şekli değişmez, yalnız kimlik beslemesinin kaynağı değişir.

**Bilerek doldurulmayan alanlar.** `rtt_ms` ve `retransmits` **0** kalıyor:
paket sayaçlarından ikisi de çıkarılamaz (RTT için el sıkışma/ACK eşlemesi,
yeniden gönderim için sıra numarası takibi gerekir). Uydurmak yerine sıfır
bırakılıyor ve bu bir borç olarak duruyor — hat *kalitesi* kurallarının
canlı modda kör olduğu anlamına gelir; hat *doluluğu* kuralları etkilenmez.

**Sınıf etiketi üretmiyoruz** (`labels_traffic_class = False`). Simülatör
akışın sınıfını biliyordu çünkü kendisi üretiyordu; kabloda öyle bir alan
yok. Sınıfı `classify.py` verecek — kontrolcü, etiketlemeyen bir kaynak
takılıysa sınıflandırıcıyı canlı moda alıyor.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from typing import Any

from ..core.models import (
    Device,
    DeviceKind,
    Direction,
    Flow,
    TrafficClass,
    new_id,
    now,
)
from .capture import CaptureUnavailable, PacketVolumeFeed, local_addresses

log = logging.getLogger(__name__)

# Kimlik beslemesi bu kadar saniye boyunca hatırlanıyor. Yoklama arası
# kapanan bağlantılar bağlantı tablosunda görünmüyor — hacimleri elimizde
# ama sahibi yok. Kısa ömürlü bağlantı (DNS, tek istek) gerçek ağda
# çoğunluk; hafızasız çalışsaydık en çok onları kaybederdik.
OWNER_TTL = 120.0

# Süreç adı çözülemeyen akışlarda kullanılan işaret. Boş bırakmak yerine
# açık bir değer: sınıflandırıcının "süreç katmanı yok" kararını ölçebilmek
# için ayırt edilebilir olması gerekiyor.
BILINMEYEN_SUREC = ""


def _ozel_mi(ip: str) -> bool:
    """LAN adresi mi? Yön kararı (LATERAL vs WAN) buna dayanıyor."""
    try:
        adres = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(adres.is_private or adres.is_link_local or adres.is_loopback)


class ConnectionOwners:
    """Kimlik beslemesi: 5'li → (pid, süreç adı).

    `psutil.net_connections` yoklaması. Yönetici olmadan da çalışıyor ama
    **yalnız bu kullanıcının süreçlerini** görüyor; başka kullanıcının
    süreci için PID geliyor, ad gelmiyor. Bu bir kusur değil işletim
    sisteminin sınırı, ama ölçülebilir olması gerekiyor: `hit_rate`
    kaç akışın sahibinin bulunduğunu söylüyor.
    """

    def __init__(self, ttl: float = OWNER_TTL) -> None:
        self.ttl = ttl
        self._tablo: dict[tuple, tuple[int, str, float]] = {}
        self.son_yoklama = 0.0
        self.cozulen = 0
        self.cozulemeyen = 0

    def refresh(self, ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        try:
            import psutil
        except ImportError:
            log.warning("psutil yok — akışlara süreç adı eklenemeyecek")
            return 0

        eklenen = 0
        try:
            baglantilar = psutil.net_connections(kind="inet")
        except Exception:                     # yetki / geçici hata
            log.debug("Bağlantı tablosu okunamadı", exc_info=True)
            return 0

        for c in baglantilar:
            if not c.laddr or not c.pid:
                continue
            proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"

            # **İki anahtarla indeksliyoruz ve sebebi ölçüldü.** Windows'ta
            # `psutil` UDP soketlerinde uzak ucu **hiç** vermiyor (ölçüm:
            # 71 UDP kaydının 71'inde `raddr` boş). Yalnız 5'li anahtarla
            # çalışırken bütün QUIC/DNS trafiği sahipsiz kalıyordu — süreç
            # çözülme oranı %52'de takılmıştı.
            #
            # Yerel uç anahtarı daha zayıf bir eşleşme: port kapanıp başka
            # bir sürece yeniden verilebilir. O yüzden **yedek** olarak
            # duruyor; 5'li eşleşmesi varsa önce o kullanılıyor.
            # Üçüncü ve en zayıf seviye: yalnız port. Soketlerin çoğu joker
            # adrese bağlanıyor (ölçüm: 60 soket `0.0.0.0`/`::`) ama paket
            # somut yerel IP taşıyor — bu ikisi asla eşleşmiyordu.
            anahtarlar = [(proto, c.laddr.ip, c.laddr.port, "", 0),
                          (proto, "", c.laddr.port, "", 0)]
            if c.raddr:
                anahtarlar.insert(
                    0, (proto, c.laddr.ip, c.laddr.port, c.raddr.ip, c.raddr.port))

            ad = None
            for anahtar in anahtarlar:
                mevcut = self._tablo.get(anahtar)
                if mevcut is not None and mevcut[0] == c.pid:
                    self._tablo[anahtar] = (mevcut[0], mevcut[1], ts)   # tazele
                    ad = mevcut[1]
                    continue
                if ad is None:
                    try:
                        import psutil as _ps
                        ad = _ps.Process(c.pid).name()
                    except Exception:         # süreç kapandı ya da erişim yok
                        ad = BILINMEYEN_SUREC
                self._tablo[anahtar] = (c.pid, ad, ts)
                eklenen += 1

        self.son_yoklama = ts
        self._buda(ts)
        return eklenen

    def _buda(self, ts: float) -> None:
        olu = [k for k, (_, _, gorulme) in self._tablo.items() if ts - gorulme > self.ttl]
        for k in olu:
            del self._tablo[k]

    def lookup(self, proto: str, yerel_ip: str, yerel_port: int,
               uzak_ip: str, uzak_port: int) -> tuple[int | None, str]:
        kayit = self._tablo.get((proto, yerel_ip, yerel_port, uzak_ip, uzak_port))
        if kayit is None:
            # Yedek: yalnız yerel uç. UDP'de tek yol bu (bkz. `refresh`),
            # TCP'de de iki yoklama arası açılıp kapanan bağlantıyı kurtarır.
            kayit = self._tablo.get((proto, yerel_ip, yerel_port, "", 0))
        if kayit is None:
            # Son çare: yalnız port (joker adrese bağlı soketler).
            kayit = self._tablo.get((proto, "", yerel_port, "", 0))
        if kayit is None:
            self.cozulemeyen += 1
            return None, BILINMEYEN_SUREC
        self.cozulen += 1
        return kayit[0], kayit[1]

    @property
    def hit_rate(self) -> float | None:
        toplam = self.cozulen + self.cozulemeyen
        return (self.cozulen / toplam) if toplam else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tracked": len(self._tablo),
            "resolved": self.cozulen,
            "unresolved": self.cozulemeyen,
            "hit_rate": round(self.hit_rate, 3) if self.hit_rate is not None else None,
            "last_poll_age_s": round(time.time() - self.son_yoklama, 1)
            if self.son_yoklama else None,
        }


class LiveSource:
    """Gerçek trafikten `Flow` üreten kaynak (`FlowSource` sözleşmesi)."""

    name = "live"
    supports_scenarios = False
    #: Kaynak akışın sınıfını **bilmiyor**; sınıfı `classify.py` verecek.
    labels_traffic_class = False

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.devices: dict[str, Device] = {}
        self._yerel = local_addresses()
        self.volume = PacketVolumeFeed(
            iface=getattr(cfg, "interface", "") or None,
            bpf=getattr(cfg, "bpf_filter", "ip or ip6"),
            promiscuous=bool(getattr(cfg, "promiscuous", False)),
            yerel_adresler=self._yerel,
        )
        self.owners = ConnectionOwners(
            ttl=float(getattr(cfg, "owner_ttl_seconds", OWNER_TTL)))
        self._yoklama_araligi = float(getattr(cfg, "owner_poll_seconds", 1.0))
        self._son_yoklama = 0.0
        self._hostname = socket.gethostname()
        self.dusen_akis = 0
        self._sessizlik_bildirildi = False

    # ------------------------------------------------------------------ yaşam

    async def start(self) -> None:
        self.volume.start()
        self.owners.refresh()

    async def aclose(self) -> None:
        self.volume.aclose()

    # ------------------------------------------------------------------ cihaz

    def _cihaz(self, ip: str) -> Device:
        """IP'yi cihaza çevirir; yoksa kurar.

        Canlı modda cihaz envanteri **gözlemden** doğuyor: simülatörde
        cihazlar baştan tanımlıydı, burada trafiği görünce öğreniyoruz.
        Kimlik (tür, güven) AD entegrasyonuna kadar bilinmiyor ve
        uydurulmuyor — `trust` alanı sabit 0.5, `kind` UNKNOWN.
        """
        for d in self.devices.values():
            if d.ip == ip:
                d.last_seen = now()
                return d
        kendisi = ip in self._yerel
        cihaz = Device(
            id=new_id("dev"), ip=ip, mac="",
            hostname=self._hostname if kendisi else ip,
            kind=DeviceKind.WORKSTATION if kendisi else DeviceKind.UNKNOWN,
            tags=["yerel"] if kendisi else ["gozlemlendi"],
        )
        self.devices[cihaz.id] = cihaz
        return cihaz

    # ------------------------------------------------------------------ üretim

    def tick(self, dt: float = 1.0) -> list[Flow]:
        simdi = time.time()
        if simdi - self._son_yoklama >= self._yoklama_araligi:
            self.owners.refresh(simdi)
            self._son_yoklama = simdi

        # Yakalama ayakta ama hiç paket görmüyorsa bir kez yüksek sesle
        # söylüyoruz. Bu durum ölçümde yaşandı (yanlış arayüz seçilmişti) ve
        # dışarıdan sağlıklı görünüyordu — panel sıfır trafikle doluydu.
        if self.volume.sessiz and not self._sessizlik_bildirildi:
            self._sessizlik_bildirildi = True
            log.warning(
                "Yakalama ayakta ama %.0f saniyedir tek paket görülmedi "
                "(arayüz=%s). Yanlış arayüz ya da yetersiz yetki olabilir; "
                "config.yaml → live.interface ile arayüzü açıkça yazın.",
                self.volume.SESSIZLIK_ESIGI, self.volume.iface or "varsayılan")

        akislar: list[Flow] = []
        for kayit in self.volume.drain():
            if kayit.bytes_down == 0 and kayit.bytes_up == 0:
                continue
            _pid, surec = self.owners.lookup(
                kayit.proto, kayit.local_ip, kayit.local_port,
                kayit.remote_ip, kayit.remote_port)

            lan_ici = _ozel_mi(kayit.remote_ip)
            if lan_ici:
                yon = Direction.LATERAL
            else:
                yon = (Direction.INBOUND if kayit.bytes_down >= kayit.bytes_up
                       else Direction.OUTBOUND)

            cihaz = self._cihaz(kayit.local_ip)
            akislar.append(Flow(
                id=new_id("flw"), ts=now(), device_id=cihaz.id,
                src_ip=kayit.local_ip, dst_ip=kayit.remote_ip,
                src_port=kayit.local_port, dst_port=kayit.remote_port,
                proto=kayit.proto,
                # `app` ve `traffic_class` sınıflandırıcının işi; burada
                # boş/varsayılan bırakılıyor. Tahmin yazmak, ölçtüğümüz
                # sınıflandırma isabetini kendi tahminimizle kirletirdi.
                app="",
                traffic_class=TrafficClass.INTERACTIVE,
                direction=yon,
                bytes_down=kayit.bytes_down, bytes_up=kayit.bytes_up,
                packets=kayit.packets, duration=dt,
                rtt_ms=0.0, retransmits=0,
                process=surec,
            ))
        return akislar

    # ------------------------------------------------------------------ durum

    def to_dict(self) -> dict[str, Any]:
        """`/api/status` için — canlı kaynağın kendini ne kadar gördüğü."""
        return {
            "capture_running": self.volume.running,
            "capture_silent": self.volume.sessiz,
            "interface": self.volume.iface or "",
            "interface_guessed": self.volume.secilen_arayuz_tahmini,
            "packets_seen": self.volume.toplam_paket,
            "foreign_packets": self.volume.yabanci_paket,
            "local_addresses": len(self._yerel),
            "devices": len(self.devices),
            "owners": self.owners.to_dict(),
        }


def build_live_source(cfg: Any) -> LiveSource:
    """Canlı kaynağı kurar. Yakalama başlatılamıyorsa gerekçeli hata verir."""
    kaynak = LiveSource(cfg)
    return kaynak


__all__ = ["ConnectionOwners", "LiveSource", "CaptureUnavailable",
           "build_live_source"]
