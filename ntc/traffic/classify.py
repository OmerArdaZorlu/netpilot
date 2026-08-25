"""Trafik sınıflandırma — DPI'sız, katmanlı.

**Neden gerekli:** sistemin bütün öncelik mantığı `traffic_class` alanına
dayanıyor. Simülasyonda o etiket hazır geliyor; gerçek ağda gelmiyor. Yanlış
etiket, doğru çalışan bir çözücünün yanlış şeyi öne almasıdır — yani bu katman
olmadan geri kalan her şey doğru cevabı yanlış soruya veriyor.

**Neden DPI yok:** trafiğin ezici çoğunluğu TLS. İçeriğe bakmak hem teknik
olarak mümkün değil hem de kurumsal ortamda araya girme (MITM) gerektirir.
Bunun yerine dışarıdan görülebilen şeye bakıyoruz: hangi süreç açtı, nereye
gidiyor, hangi port, akış neye benziyor.

Katmanlar, güvenilirlikten aşağıya:

    1. SÜREÇ ADI    Sysmon Event 3 akışı açan süreci veriyor. En sağlam
                    kaynak: `Teams.exe`'nin ne yaptığı tartışmasız.
    2. PORT         Yalnız **tek sınıfa ait** portlar. `tcp/443` altı farklı
                    uygulamada ve dört farklı sınıfta — orada port kullanmak
                    tıkanmayı yaratan Windows Update'e en yüksek etkileşimli
                    önceliği vermek olurdu.
    3. HEDEF IP     Bilinen servis blokları (Netflix CDN, Windows Update).
                    **Yalnız belirsiz portta** devreye giriyor: `udp/53`'e
                    giden akış, çözücü hangi bulutta durursa dursun DNS'tir.
                    Ölçüldü — IP'yi portun önüne almak doğruluğu %72.4'ten
                    %64.2'ye düşürüyordu.
    4. AKIŞ ŞEKLİ   Hız, yön dengesi, paket boyu. Belirsiz portun tek çaresi.
    5. VARSAYILAN   `interactive`. Bilmediğimizi arka plana atmak, tanımadığımız
                    bir görüşme uygulamasını sessizce boğmak olurdu; en üste
                    atmak da her bilinmeyene ayrıcalık verirdi. Ortası.

Her sonuç **hangi katmandan geldiğini** taşıyor (`basis`). Bu olmadan
"sınıflandırma çalışıyor" ölçülemez bir iddia olurdu: şekil tahminiyle süreç
eşleşmesi aynı kutuya girerse hangisinin ne kadar taşıdığı görünmez.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

from ..core.models import TrafficClass
from .catalog import APPS

# --------------------------------------------------------------------- 1. süreç

# Süreç görüntü adı → katalog uygulaması. Küçük harfe indirilmiş yol sonu
# ile eşleşiyor (`C:\...\Teams.exe` → `teams.exe`).
PROCESS_MAP: dict[str, str] = {
    "teams.exe": "teams-call",
    "ms-teams.exe": "teams-call",
    "zoom.exe": "teams-call",
    "lync.exe": "teams-call",
    "softphone.exe": "voip-sip",
    "linphone.exe": "voip-sip",
    # Tarayıcılar BİLEREK boş. `chrome.exe → https-web` yazmak, tarayıcıdan
    # izlenen Netflix'i `interactive` yapardı — yani en yüksek etkileşimli
    # önceliği bir video akışına vermek. Tarayıcı `svchost` gibi konak süreç:
    # ne yaptığını süreç adı değil, nereye gittiği (IP) ve neye benzediği
    # (şekil) söyler. IP katmanının asıl varlık sebebi bu.
    "chrome.exe": "",
    "msedge.exe": "",
    "firefox.exe": "",
    "ssh.exe": "ssh",
    "putty.exe": "ssh",
    "mstsc.exe": "rdp",
    "steam.exe": "game-udp",
    "netflix.exe": "netflix",
    "svchost.exe": "",              # belirsiz: çok iş yapıyor, şekle düşür
    "usoclient.exe": "windows-update",
    "wuauclt.exe": "windows-update",
    "tiworker.exe": "windows-update",
    "onedrive.exe": "cloud-sync",
    "dropbox.exe": "cloud-sync",
    "backup-agent.exe": "smb-backup",
    "compattelrunner.exe": "os-telemetry",
    "dnscache.exe": "dns",
    "w32time.exe": "ntp",
}

# --------------------------------------------------------------------- 2. IP

# Bilinen servis blokları. Süreç adı yoksa (tarayıcı, `svchost`) belirsiz
# porttaki tek gerçek sinyal bu.
#
# İlk sürümde simülatör hedef IP'yi uygulamadan bağımsız seçtiği için bu
# katman ölçülemiyordu; `catalog.APP_ENDPOINTS` ile düzeltildi. Blokların
# ikisi aynı gerçeği anlatmak zorunda: burada Netflix bloğu yazıp
# simülatörde Netflix'i başka bir adrese göndermek, ölçümü sessizce
# yalanlar.
IP_RANGES: list[tuple[str, str]] = [
    ("23.246.0.0/18", "netflix"),
    ("45.57.0.0/17", "netflix"),
    ("208.75.76.0/22", "netflix"),
    ("142.250.0.0/15", "youtube"),
    ("172.217.0.0/16", "youtube"),
    ("13.107.4.0/22", "windows-update"),
    ("13.107.42.0/24", "windows-update"),
    ("13.107.136.0/22", "cloud-sync"),
    ("40.108.128.0/17", "cloud-sync"),
    ("20.42.64.0/18", "os-telemetry"),
    ("52.113.192.0/18", "teams-call"),
    # Genel bulut blokları BILEREK yok. `52.96.0.0/12` ya da `20.42.0.0/16`
    # yazmak, aynı sağlayıcının onlarca farklı servisini tek sınıfa
    # bağlamak olurdu — ölçümde `os-telemetry` ve `cloud-sync` bu yüzden
    # `interactive` çıkıyordu. IP katmanı yalnız **tek servise ait**
    # bloklar için.
]

_NETS: list[tuple[Any, str]] = [
    (ipaddress.ip_network(cidr), app) for cidr, app in IP_RANGES]

# --------------------------------------------------------------------- 3. port


def _port_index() -> tuple[dict[tuple[str, int], str],
                           dict[tuple[str, int], list[str]]]:
    """Katalogdan **tek sınıflı** ve **belirsiz** port tablolarını çıkarır.

    Tablo elle yazılmıyor: katalog değişince (yeni uygulama, sınıf değişimi)
    belirsizlik kümesi kendiliğinden güncelleniyor. Elle yazılsaydı katalogla
    sessizce ayrışırdı ve ayrışma tam da yanlış sınıflandırma demek olurdu.
    """
    gruplar: dict[tuple[str, int], list[str]] = {}
    for a in APPS.values():
        gruplar.setdefault((a.proto, a.port), []).append(a.name)
    tekil: dict[tuple[str, int], str] = {}
    belirsiz: dict[tuple[str, int], list[str]] = {}
    for anahtar, adlar in gruplar.items():
        siniflar = {APPS[n].traffic_class for n in adlar}
        if len(siniflar) == 1:
            tekil[anahtar] = adlar[0]
        else:
            belirsiz[anahtar] = sorted(adlar)
    return tekil, belirsiz


PORT_SINGLE, PORT_AMBIGUOUS = _port_index()

# --------------------------------------------------------------------- 4. şekil

MB = 1_000_000.0
KB = 1_000.0

# `tcp/443` eşikleri. Sayılar katalogdaki hız aralıklarından geliyor ve
# **ayrışabilir** yerlerden seçildi:
#
#   os-telemetry  down  10K–90K   up  20K–180K   -> toplam en küçük
#   cloud-sync    down   1M–8M    up   2M–15M    -> tek yukarı-ağır olan
#   windows-update down 15M–60M   up 100K–400K   -> tek 16M üstü
#   netflix       down   5M–16M   up  40K–150K
#   youtube       down   2M–12M   up  30K–120K
#   https-web     down 200K–6M    up  30K–400K   -> tek 6M altı kalan
#
# `SHAPE_STREAM_DOWN_BPS` de süpürüldü ve 6 Mbps optimumda çıktı — yani
# katalogdaki https-web tavanı. 5'te %95.3, 6'da %98.3, 7'de %97.2.
#
# 2–6 Mbps bandında https-web, youtube ve netflix üst üste biniyor ve şekil
# onları AYIRAMIYOR. Bu bir eksiklik değil, sinyalin sınırı: orada gerçek
# ayrım süreç adından gelir. Ölçüm bunu olduğu gibi gösteriyor.
SHAPE_TINY_BPS = 300 * KB          # bunun altı telemetri
SHAPE_UPLOAD_HEAVY_BPS = 1.0 * MB  # bu kadar yükleyen tek uygulama cloud-sync
SHAPE_BULK_DOWN_BPS = 16.0 * MB    # bu kadar indiren tek uygulama güncelleme
SHAPE_STREAM_DOWN_BPS = 6.0 * MB   # https-web'in tavanı; üstü akış demek

BASIS_PROCESS = "surec"
BASIS_IP = "ip"
BASIS_PORT = "port"
BASIS_SHAPE = "sekil"
BASIS_DEFAULT = "varsayilan"

# Katman başına güven. Sayılar sıralamayı ifade ediyor, olasılık değil —
# ve panelde "neden böyle sınıflandırdı" sorusunun cevabı bu alan.
CONFIDENCE = {BASIS_PROCESS: 0.95, BASIS_IP: 0.80, BASIS_PORT: 0.75,
              BASIS_SHAPE: 0.55, BASIS_DEFAULT: 0.20}

DEFAULT_CLASS = TrafficClass.INTERACTIVE


@dataclass
class Signals:
    """Sınıflandırıcıya giden ham gözlem — akışın etiketi burada YOK.

    Bilerek `Flow`'dan ayrı: `Flow.traffic_class` ve `Flow.app` cevabın
    kendisi. Aynı nesneyi girdi olarak almak, ölçümde cevabı kopya çekmeye
    açık kapı bırakırdı.
    """

    proto: str = "tcp"
    dst_port: int = 0
    dst_ip: str = ""
    process: str = ""              # Sysmon Event 3; canlı modda dolu
    down_bps: float = 0.0
    up_bps: float = 0.0
    rtt_ms: float = 0.0
    packets: int = 0
    duration: float = 1.0

    @property
    def total_bps(self) -> float:
        return self.down_bps + self.up_bps


@dataclass
class Classification:
    traffic_class: TrafficClass
    app: str = ""
    basis: str = BASIS_DEFAULT
    confidence: float = CONFIDENCE[BASIS_DEFAULT]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"traffic_class": self.traffic_class.value, "app": self.app,
                "basis": self.basis, "confidence": round(self.confidence, 2),
                "notes": list(self.notes)}


# ------------------------------------------------------------------ katmanlar


def _from_process(s: Signals) -> tuple[str, str]:
    if not s.process:
        return "", ""
    ad = s.process.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    uygulama = PROCESS_MAP.get(ad)
    if not uygulama:
        # Bilinmeyen süreç bir cevap değil. `svchost.exe` gibi bilerek boş
        # bırakılanlar da buraya düşüyor: çok iş yapan bir konak süreci
        # tek sınıfa bağlamak, telemetriyle güncellemeyi aynı kutuya atardı.
        return "", ad
    return uygulama, ad


def _from_ip(s: Signals) -> str:
    if not s.dst_ip:
        return ""
    try:
        adres = ipaddress.ip_address(s.dst_ip)
    except ValueError:
        return ""
    for ag, uygulama in _NETS:
        if adres in ag:
            return uygulama
    return ""


def _from_port(s: Signals) -> str:
    return PORT_SINGLE.get((s.proto, s.dst_port), "")


def _from_shape(s: Signals) -> tuple[TrafficClass | None, str]:
    """Belirsiz portta akışın kendisine bakar.

    Sıra en ayrışabilirden en bulanığa: önce hacim (telemetri), sonra yön
    dengesi (senkron), sonra mutlak hız. Bulanık banda kalan `interactive`
    oluyor çünkü orada https-web en olası aday.
    """
    if s.total_bps <= 0:
        return None, ""
    if s.total_bps < SHAPE_TINY_BPS:
        return TrafficClass.BACKGROUND, "hacim çok küçük: telemetri/keepalive"
    if s.up_bps >= SHAPE_UPLOAD_HEAVY_BPS and s.up_bps >= s.down_bps * 0.5:
        return TrafficClass.BULK, "yukarı ağır: bulut senkronu"
    if s.down_bps >= SHAPE_BULK_DOWN_BPS:
        return TrafficClass.BULK, "sürekli yüksek indirme: büyük transfer"
    if s.down_bps >= SHAPE_STREAM_DOWN_BPS:
        return TrafficClass.STREAMING, "yüksek tek yönlü indirme: video"
    # **"Tek yönlü akış → streaming" kuralı KALDIRILDI.** Sezgisel olarak
    # doğru görünüyordu (video indirir, yüklemez) ama ölçüm tersini
    # söyledi: `https-web` de tek yönlü olabiliyor (30 KB yükleme / 6 Mbps
    # indirme) ve kural onu akış sayıyordu. Eşik süpürüldü:
    #
    #     yukari/asagi orani   IP yokken   IP varken
    #     0.00 (kural kapali)      %98.3       %99.8
    #     0.01                     %98.0       %99.2
    #     0.02                     %97.0       %97.6
    #     0.05                     %92.9       %92.9
    #
    # Her eşikte zarar veriyor. 2–6 Mbps bandındaki youtube'u yakalamak
    # için https-web'i feda etmeye değmiyor; o bandın doğru çözümü IP
    # katmanı, şekil değil.
    return None, ""


# -------------------------------------------------------------------- giriş


def classify(s: Signals) -> Classification:
    """Bir akışı sınıflandırır ve **hangi katmandan** geldiğini söyler."""
    notlar: list[str] = []

    uygulama, surec_adi = _from_process(s)
    if uygulama and uygulama in APPS:
        return Classification(APPS[uygulama].traffic_class, uygulama,
                              BASIS_PROCESS, CONFIDENCE[BASIS_PROCESS],
                              [f"süreç: {surec_adi}"])
    if surec_adi:
        notlar.append(f"süreç tanınmadı: {surec_adi}")

    # **Tek sınıflı port, IP'den önce gelir.** İlk sürümde tersiydi ve
    # ölçümde yakalandı: DNS `udp/53`'ten gidiyor ama çözücü bir bulut
    # adresinde durduğu için IP katmanı onu `https-web` sayıyordu. Sonuç
    # genel doğruluğu %72.4'ten %64.2'ye düşürüyordu — yani IP katmanı
    # eklemek sistemi **kötüleştiriyordu**.
    #
    # Sebep basit: `udp/53`'e giden akış DNS'tir, hedefin kim olduğundan
    # bağımsız. IP bloğu ise yalnız bir tahmin — üstelik bloklar geniş ve
    # aynı sağlayıcı onlarca servis barındırıyor. IP'nin yeri, portun
    # cevap veremediği yer.
    uygulama = _from_port(s)
    if uygulama and uygulama in APPS:
        return Classification(APPS[uygulama].traffic_class, uygulama,
                              BASIS_PORT, CONFIDENCE[BASIS_PORT],
                              notlar + [f"tek sınıflı port: "
                                        f"{s.proto}/{s.dst_port}"])

    adaylar = PORT_AMBIGUOUS.get((s.proto, s.dst_port))
    if adaylar:
        notlar.append(f"{s.proto}/{s.dst_port} belirsiz: {', '.join(adaylar)}")
        uygulama = _from_ip(s)
        if uygulama and uygulama in APPS:
            return Classification(APPS[uygulama].traffic_class, uygulama,
                                  BASIS_IP, CONFIDENCE[BASIS_IP],
                                  notlar + [f"hedef bloğu: {s.dst_ip}"])
    sinif, gerekce = _from_shape(s)
    if sinif is not None:
        return Classification(sinif, "", BASIS_SHAPE, CONFIDENCE[BASIS_SHAPE],
                              notlar + [gerekce])

    return Classification(DEFAULT_CLASS, "", BASIS_DEFAULT,
                          CONFIDENCE[BASIS_DEFAULT],
                          notlar + ["hiçbir katman karar veremedi"])


def signals_from_flow(flow: Any, process: str = "") -> Signals:
    """Bir `Flow`'dan gözlemlenebilir sinyalleri çıkarır.

    `app` ve `traffic_class` **bilerek okunmuyor**: gerçek ağda o alanlar yok,
    onları kullanmak ölçümde kendi cevabımızı kopya çekmek olurdu.
    """
    sure = max(float(getattr(flow, "duration", 1.0) or 1.0), 0.001)
    return Signals(
        proto=getattr(flow, "proto", "tcp"),
        dst_port=int(getattr(flow, "dst_port", 0) or 0),
        dst_ip=getattr(flow, "dst_ip", ""),
        process=process,
        down_bps=float(getattr(flow, "bytes_down", 0)) * 8 / sure,
        up_bps=float(getattr(flow, "bytes_up", 0)) * 8 / sure,
        rtt_ms=float(getattr(flow, "rtt_ms", 0.0) or 0.0),
        packets=int(getattr(flow, "packets", 0) or 0),
        duration=sure,
    )


# ------------------------------------------------------------------ denetim

MODE_SHADOW = "golge"
MODE_LIVE = "canli"


class ClassifyAudit:
    """Sınıflandırıcıyı canlı akışa bağlar — varsayılan olarak **yazmadan**.

    **Neden gölge varsayılan:** simülasyonda `traffic_class` zaten doğru
    geliyor. Orada sınıflandırıcıyı yazar duruma almak, bilinen doğruyu
    %97.7'lik bir tahminle ezmek olurdu — düpedüz gerileme. Gölge modda
    karar veriliyor, simülatörün etiketiyle karşılaştırılıyor ve uyum oranı
    ölçülüyor; akışa dokunulmuyor.

    `mode="canli"` sınıfı gerçekten yazıyor. Canlı yakalamada etiket
    olmadığı için orada tek kaynak bu, ve karşılaştırılacak bir doğru da
    yok — o durumda `agreement` anlamsız kalıyor ve `None` dönüyor.
    """

    def __init__(self, cfg: Any = None) -> None:
        self.enabled = bool(getattr(cfg, "enabled", True))
        self.mode = str(getattr(cfg, "mode", MODE_SHADOW))
        self.use_process = bool(getattr(cfg, "use_process", True))
        self.sample_size = int(getattr(cfg, "sample_size", 500))
        self.total = 0
        self.agreed = 0
        self.by_basis: dict[str, list[int]] = {}      # basis -> [uyan, toplam]
        self.by_class: dict[str, list[int]] = {}      # gerçek sınıf -> [uyan, toplam]
        self.disagreements: list[dict[str, Any]] = []

    def process(self, flows: list[Any]) -> None:
        if not self.enabled:
            return
        for f in flows:
            surec = getattr(f, "process", "") if self.use_process else ""
            c = classify(signals_from_flow(f, process=surec))
            gercek = getattr(f, "traffic_class", None)

            self.total += 1
            b = self.by_basis.setdefault(c.basis, [0, 0])
            b[1] += 1
            if gercek is not None:
                k = self.by_class.setdefault(gercek.value, [0, 0])
                k[1] += 1
                uydu = c.traffic_class == gercek
                self.agreed += uydu
                b[0] += uydu
                k[0] += uydu
                if not uydu and len(self.disagreements) < self.sample_size:
                    self.disagreements.append({
                        "device": getattr(f, "device_id", ""),
                        "app": getattr(f, "app", ""),
                        "port": f"{getattr(f, 'proto', '')}/"
                                f"{getattr(f, 'dst_port', 0)}",
                        "gercek": gercek.value,
                        "tahmin": c.traffic_class.value,
                        "katman": c.basis,
                        "notlar": c.notes,
                    })
            if self.mode == MODE_LIVE:
                # Canlı modda sınıfı gerçekten yazıyoruz — tek kaynak bu.
                try:
                    f.traffic_class = c.traffic_class
                except Exception:
                    pass

    def report(self) -> dict[str, Any]:
        # Gerçek etiket yoksa (canlı yakalama) uyum oranı ölçülemez.
        # Sıfır göstermek "kötü çalışıyor" diye okunurdu; `None` doğru cevap.
        etiketli = sum(v[1] for v in self.by_class.values())
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "use_process": self.use_process,
            "total": self.total,
            "labelled": etiketli,
            "agreement": round(self.agreed / etiketli, 4) if etiketli else None,
            "by_basis": {k: {"used": v[1], "agreed": v[0],
                             "share": round(v[1] / self.total, 4)
                             if self.total else 0.0,
                             "hit": round(v[0] / v[1], 4) if v[1] else None}
                         for k, v in sorted(self.by_basis.items())},
            "by_class": {k: {"total": v[1], "agreed": v[0],
                             "rate": round(v[0] / v[1], 4) if v[1] else None}
                         for k, v in sorted(self.by_class.items())},
            "ambiguous_ports": {f"{p}/{n}": adlar
                                for (p, n), adlar in PORT_AMBIGUOUS.items()},
            "disagreements": self.disagreements[-40:],
        }
