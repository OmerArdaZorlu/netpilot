"""Sysmon telemetrisi — bağlantıyı kimin açtığını **olay tabanlı** okur.

Faz 2'nin kimlik beslemesi (`live.ConnectionOwners`) bağlantı tablosunu
yokluyor: saniyede bir bakıp o an **açık** olan soketleri görüyor. Ölçülen
süreç çözülme oranı %70'te takıldı ve kalan %30'un sebebi tek bir şey:

    iki yoklama arasında doğup ölen bağlantı hiçbir yoklamada görünmüyor.

DNS sorgusu, tek bir HTTPS isteği, bir kimlik doğrulama turu — gerçek ağda
bağlantıların çoğu böyle. Yoklama sıklığını artırmak çözüm değil: `psutil`
her yoklamada tüm tabloyu tarıyor ve 10 Hz'de CPU'yu yiyor, üstelik 100 ms
içinde açılıp kapanan bağlantı yine kaçıyor. **Kaçırılan şey bir zamanlama
sorunu; çözümü daha sık örneklemek değil, olay akışına geçmek.**

Sysmon Event 3 bağlantı **kurulduğu anda** yazılıyor; süreç kapansa bile
olay günlükte duruyor. Bu dosya o günlüğü okuyup aynı `lookup()`
sözleşmesini veriyor — `live.py` hangi beslemenin takılı olduğunu bilmek
zorunda değil.

**Okunan olaylar**

    Event 3  (ağ bağlantısı) → 5'li + PID + Image    → kimlik beslemesi
    Event 22 (DNS sorgusu)   → sorulan ad + dönen IP → uzak uç adı

Event 1 (süreç oluşumu) **okunmuyor**: Event 3 zaten `Image` alanını
taşıyor, yani trafik tarafında getirisi yok. Süreç ağacı ve komut satırı
Faz 7'nin (endpoint triyajı) işi; oraya gelince eklenir.

**Sysmon hacim vermiyor.** Event 3'te bayt alanı yok — bu katman
`capture.py`'ın yerini almıyor, `live.ConnectionOwners`'ın yerini alıyor.
Birleştirmenin şekli değişmiyor: kimlik ⋈ hacim, 5'li üzerinden.

**Neden `wevtutil`, neden PowerShell değil:** `Get-WinEvent` için her
yoklamada bir PowerShell süreci açmak ~0.6 sn ve onlarca MB; `wevtutil`
tek bir yerel ikili ve XML'i doğrudan veriyor. XPath süzgeci **günlük
tarafında** işlendiği için de kayıtların tamamı taşınmıyor.

**Yönetici hakkı gerekiyor mu:** Sysmon *kurulumu* için evet. Günlüğü
*okumak* için, kullanıcı `Event Log Readers` grubundaysa ya da kanal ACL'i
izin veriyorsa hayır. Erişim yoksa bu katman gerekçeli şekilde kapanıyor ve
`live.py` bağlantı tablosuna düşüyor — sessizce boş dönmüyor.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Iterable

log = logging.getLogger(__name__)

KANAL = "Microsoft-Windows-Sysmon/Operational"

#: Kimlik kaydının hafızası. `live.OWNER_TTL` ile aynı sebep: hacim, olayın
#: yazıldığı saniyede değil sonraki saniyelerde akıyor.
OWNER_TTL = 120.0

#: DNS eşlemesi daha uzun yaşıyor: bir ad çözüldükten sonra bağlantı
#: dakikalarca sürebilir ve aynı IP tekrar tekrar kullanılır.
DNS_TTL = 900.0

#: Tek yoklamada alınacak azami olay. Tavan olmadan ilk yoklama günlüğün
#: tamamını (yüz binlerce olay) çekmeye kalkardı.
BATCH = 500

#: İlk yoklamada geriye dönük alınacak olay sayısı. Sıfır olsaydı kaynak
#: ilk saniyelerde kör başlardı: hacim akıyor ama sahibi yok.
ILK_GERIYE = 200

BILINMEYEN_SUREC = ""


class SysmonUnavailable(RuntimeError):
    """Sysmon günlüğü okunamıyor: kurulu değil ya da erişim yok."""


# --------------------------------------------------------------------- yardım

def _yerel_ad(etiket: str) -> str:
    """`{ns}Event` → `Event`. wevtutil ad alanı yazıyor, testlerdeki elle
    yazılmış XML yazmayabiliyor; ikisini de kabul ediyoruz."""
    return etiket.rsplit("}", 1)[-1]


def normalize_ip(ham: str) -> str:
    """Adresi `capture.py`'ın ürettiği biçime getirir.

    Sysmon IPv4 bağlantısını IPv6 soketinden görürse `::ffff:10.0.0.5`
    yazıyor, yakalama ise aynı paketi `10.0.0.5` olarak sayıyor. Bu ikisi
    normalize edilmezse **5'li anahtarı hiçbir zaman tutmaz** — kimlik
    beslemesi dolu olur, birleştirme boş çıkar.
    """
    ham = (ham or "").strip().split("%")[0]
    if not ham:
        return ""
    try:
        adres = ipaddress.ip_address(ham)
    except ValueError:
        return ham
    if isinstance(adres, ipaddress.IPv6Address) and adres.ipv4_mapped:
        return str(adres.ipv4_mapped)
    return str(adres)


def _surec_adi(image: str) -> str:
    """`C:\\Windows\\System32\\svchost.exe` → `svchost.exe`.

    Bağlantı tablosu beslemesi de yalın adı veriyor (`psutil.Process.name()`);
    iki kaynağın aynı biçimi üretmesi gerekiyor, yoksa sınıflandırıcının
    süreç katmanı hangi beslemenin açık olduğuna göre farklı davranırdı.
    """
    if not image:
        return BILINMEYEN_SUREC
    return image.replace("/", "\\").rsplit("\\", 1)[-1]


def _zaman(sistem_zamani: str) -> float:
    """`2026-09-06T10:11:12.1234567Z` → epoch saniye.

    Olayın kendi zamanı kullanılıyor, okuma anı değil: yoklama gecikirse
    (yük altında birkaç saniye olabiliyor) TTL yanlış yerden sayılır ve eski
    kayıtlar taze görünürdü.
    """
    if not sistem_zamani:
        return 0.0
    metin = sistem_zamani.strip().replace("Z", "+00:00")
    # Windows 7 haneli kesir yazıyor, `fromisoformat` 6 hane kabul ediyor.
    if "." in metin:
        bas, _, son = metin.partition(".")
        kesir, uzanti = son, ""
        for i, ch in enumerate(son):
            if not ch.isdigit():
                kesir, uzanti = son[:i], son[i:]
                break
        metin = bas + "." + kesir[:6] + uzanti
    try:
        from datetime import datetime
        return datetime.fromisoformat(metin).timestamp()
    except ValueError:
        return 0.0


def olaylari_ayristir(xml_metin: str) -> list[dict[str, Any]]:
    """wevtutil XML çıktısını olay sözlüklerine çevirir.

    Kırık XML'de hiç olay döndürmek yerine **ayrıştırabildiklerimizi**
    döndürüyoruz: wevtutil çıktısı tavana takılıp ortadan kesilebiliyor ve
    tek bir yarım olay yüzünden o turdaki 499 sağlam olayı atmak, tam da
    kapatmaya çalıştığımız boşluğu geri açardı.
    """
    metin = (xml_metin or "").strip()
    if not metin:
        return []
    if not metin.startswith("<Events"):
        metin = "<Events>" + metin + "</Events>"

    kok = None
    try:
        kok = ET.fromstring(metin)
    except ET.ParseError:
        # Kesilmiş çıktı: son tam `</Event>`'e kadar kırp, kalanı at.
        son = metin.rfind("</Event>")
        if son == -1:
            log.debug("Sysmon XML ayrıştırılamadı (tam olay yok)")
            return []
        try:
            kok = ET.fromstring(metin[:son + len("</Event>")] + "</Events>")
        except ET.ParseError:
            log.debug("Sysmon XML ayrıştırılamadı", exc_info=True)
            return []

    olaylar: list[dict[str, Any]] = []
    for e in kok.iter():
        if _yerel_ad(e.tag) != "Event":
            continue
        olay: dict[str, Any] = {"event_id": 0, "record_id": 0, "ts": 0.0,
                                "data": {}}
        for cocuk in e:
            ad = _yerel_ad(cocuk.tag)
            if ad == "System":
                for alan in cocuk:
                    alan_adi = _yerel_ad(alan.tag)
                    if alan_adi == "EventID":
                        olay["event_id"] = int((alan.text or "0").strip() or 0)
                    elif alan_adi == "EventRecordID":
                        olay["record_id"] = int((alan.text or "0").strip() or 0)
                    elif alan_adi == "TimeCreated":
                        olay["ts"] = _zaman(alan.get("SystemTime") or "")
            elif ad == "EventData":
                for alan in cocuk:
                    isim = alan.get("Name")
                    if isim:
                        olay["data"][isim] = (alan.text or "").strip()
        if olay["event_id"]:
            olaylar.append(olay)
    return olaylar


# --------------------------------------------------------------------- okuyucu

class SysmonReader:
    """Sysmon günlüğünü artımlı okur (`EventRecordID` imleciyle).

    İmleç kayıt numarası üzerinden ilerliyor, zaman üzerinden değil: saat
    geri alınabiliyor ve aynı saniyede yüzlerce olay olabiliyor. Tekdüze
    artan tek alan kayıt numarası, güvenilir imleç de o.
    """

    def __init__(self, kanal: str = KANAL, event_ids: Iterable[int] = (3, 22),
                 batch: int = BATCH, ilk_geriye: int = ILK_GERIYE,
                 wevtutil: str = "wevtutil") -> None:
        self.kanal = kanal
        self.event_ids = tuple(event_ids)
        self.batch = int(batch)
        self.ilk_geriye = int(ilk_geriye)
        self.wevtutil = wevtutil
        self.son_kayit = 0
        self.baslatildi = False
        #: Tavana takıldığımız tur sayısı. Sıfırdan farklıysa yoklama sıklığı
        #: ya da tavan yetersiz — sessiz kalmıyoruz.
        self.atlanan = 0
        self.okunan = 0
        self.son_hata = ""

    # ------------------------------------------------------------- çalıştırma

    def _calistir(self, args: list[str], timeout: float = 20.0) -> str:
        try:
            sonuc = subprocess.run(
                [self.wevtutil, *args], capture_output=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except FileNotFoundError as exc:
            raise SysmonUnavailable(
                "wevtutil bulunamadı — bu katman yalnız Windows'ta çalışıyor."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SysmonUnavailable("wevtutil zaman aşımına uğradı: " + str(exc)) from exc

        if sonuc.returncode != 0:
            hata = (sonuc.stderr or b"").decode("utf-8", "replace").strip()
            raise SysmonUnavailable(
                "wevtutil " + " ".join(args[:2]) +
                " başarısız (kod " + str(sonuc.returncode) + "): " +
                (hata or "gerekçe yok"))
        # Günlük UTF-8 dönüyor; bozuk baytta düşmek yerine değiştiriyoruz —
        # tek bir kötü karakter yüzünden turu kaybetmenin anlamı yok.
        return (sonuc.stdout or b"").decode("utf-8", "replace")

    def hazir(self) -> bool:
        """Kanal var mı ve okunabiliyor mu?"""
        try:
            self._calistir(["gl", self.kanal], timeout=10.0)
            self.son_hata = ""
            return True
        except SysmonUnavailable as exc:
            self.son_hata = str(exc)
            return False

    # ------------------------------------------------------------------ sorgu

    def _xpath(self, alt_sinir: int | None) -> str:
        kosullar = " or ".join("EventID=" + str(i) for i in self.event_ids)
        parcalar = []
        if kosullar:
            parcalar.append("(" + kosullar + ")")
        if alt_sinir:
            # **`>` burada kaçırılmıyor ve bu ölçümle öğrenildi.** XPath'i
            # `&gt;` ile yazmak yalnız sorgu bir XML dosyasının içine
            # gömüldüğünde doğru; komut satırı argümanı olarak verildiğinde
            # wevtutil `&gt;`'i olduğu gibi okuyup düşüyor:
            #     kod 15001 — "A syntax error occurred at position 24"
            # Argümanları kabuk üzerinden değil liste olarak geçirdiğimiz
            # için yönlendirme riski de yok.
            parcalar.append("(EventRecordID>" + str(alt_sinir) + ")")
        if not parcalar:
            return "*"
        return "*[System[" + " and ".join(parcalar) + "]]"

    def _sorgula(self, alt_sinir: int | None, adet: int, ters: bool) -> list[dict]:
        args = ["qe", self.kanal, "/q:" + self._xpath(alt_sinir),
                "/c:" + str(adet), "/e:Events", "/f:XML"]
        if ters:
            args.append("/rd:true")
        olaylar = olaylari_ayristir(self._calistir(args))
        # `/rd:true` en yeniden başlıyor; imleç mantığı artan sıra istiyor.
        olaylar.sort(key=lambda o: o["record_id"])
        return olaylar

    def baslat(self) -> list[dict]:
        """İmleci kurar ve son `ilk_geriye` olayı döndürür.

        **Geriye dönük okuma bilerek var.** İmleci "şu andan itibaren" diye
        kursaydık ilk yoklamada elimizde hacim olur, sahibi olmazdı: açılışın
        ilk saniyeleri sistematik olarak çözülemeyen akış üretirdi.
        """
        adet = self.ilk_geriye if self.ilk_geriye > 0 else 1
        olaylar = self._sorgula(None, adet, ters=True)
        self.baslatildi = True
        if olaylar:
            self.son_kayit = olaylar[-1]["record_id"]
        if self.ilk_geriye <= 0:
            return []
        self.okunan += len(olaylar)
        return olaylar

    def yeni(self) -> list[dict]:
        """İmleçten sonraki olaylar. İmleci ilerletir."""
        if not self.baslatildi:
            return self.baslat()
        olaylar = self._sorgula(self.son_kayit, self.batch, ters=False)
        if len(olaylar) >= self.batch:
            # Tavana dayandık: bu turda kaç olay kaçtığını bilmiyoruz ama
            # kaçtığını biliyoruz. İmleç yine de ilerliyor — yerinde saymak
            # gecikmeyi sonsuza kadar büyütürdü.
            self.atlanan += 1
        if olaylar:
            self.son_kayit = olaylar[-1]["record_id"]
        self.okunan += len(olaylar)
        return olaylar

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.kanal, "cursor": self.son_kayit,
                "events_read": self.okunan, "batches_capped": self.atlanan,
                "last_error": self.son_hata}


# ------------------------------------------------------------ kimlik beslemesi

class SysmonOwners:
    """Kimlik beslemesi: Sysmon Event 3 → 5'li → (pid, süreç adı).

    `live.ConnectionOwners` ile **aynı sözleşme** (`refresh` / `lookup` /
    `hit_rate` / `to_dict`). Böylece `LiveSource` iki beslemeyi yer
    değiştirebiliyor ve birleştirme kodu tek kalıyor.

    **Yoklama kendi iş parçacığında** (`start()`), tıpkı paket yakalaması
    gibi. Sebebi ölçüldü: `wevtutil` turu bu makinede **~130 ms** sürüyor ve
    toplayıcı `tick()`'i olay döngüsünün üzerinde koşuyor. Doğrudan
    çağırsaydık her saniye 130 ms boyunca API, WebSocket ve diğer dört döngü
    dururdu — akış çözücüsünün `asyncio.to_thread`'e alınmasıyla aynı gerekçe.

    `start()` çağrılmadan da çalışıyor (`refresh()` elle çağrılabiliyor);
    testler ve `doctor` bu yolu kullanıyor.
    """

    def __init__(self, ttl: float = OWNER_TTL,
                 yerel_adresler: set[str] | None = None,
                 reader: SysmonReader | None = None,
                 dns_ttl: float = DNS_TTL,
                 poll_seconds: float = 1.0) -> None:
        self.ttl = ttl
        self.dns_ttl = dns_ttl
        self.poll_seconds = poll_seconds
        self.reader = reader if reader is not None else SysmonReader()
        self._yerel = set(yerel_adresler or ())
        self._tablo: dict[tuple, tuple[int, str, float]] = {}
        self._dns: dict[str, tuple[str, float]] = {}
        self.son_yoklama = 0.0
        self.cozulen = 0
        self.cozulemeyen = 0
        self.olay3 = 0
        self.olay22 = 0
        self.kapali_sebep = ""
        # Tablo iki iş parçacığından görülüyor: yoklama yazıyor, toplayıcı
        # okuyor. Kilitsiz bıraksaydık budama sırasındaki sözlük değişimi
        # okuyucuyu düşürürdü.
        self._kilit = threading.Lock()
        self._dur = threading.Event()
        self._is_parcacigi: threading.Thread | None = None
        #: Yoklama iş parçacığında yakalanan ardışık hata sayısı. Sıfırdan
        #: farklıysa besleme sessizce körelmiş olabilir.
        self.yoklama_hatasi = 0

    # ------------------------------------------------------------------ yaşam

    def start(self) -> None:
        """Yoklamayı arka planda başlatır."""
        if self._is_parcacigi is not None:
            return
        self._dur.clear()
        self._is_parcacigi = threading.Thread(
            target=self._dongu, name="sysmon-yoklama", daemon=True)
        self._is_parcacigi.start()

    def _dongu(self) -> None:
        while not self._dur.is_set():
            try:
                self.refresh()
                self.yoklama_hatasi = 0
            except Exception:
                # Yoklama iş parçacığı hiçbir hatada ölmemeli: öldüğü an
                # besleme sessizce boşalır ve dışarıdan "Sysmon açık"
                # görünmeye devam eder.
                self.yoklama_hatasi += 1
                log.debug("Sysmon yoklaması hata verdi", exc_info=True)
            self._dur.wait(self.poll_seconds)

    def aclose(self) -> None:
        self._dur.set()
        if self._is_parcacigi is not None:
            self._is_parcacigi.join(timeout=2.0)
            self._is_parcacigi = None

    @property
    def calisiyor(self) -> bool:
        return bool(self._is_parcacigi is not None and self._is_parcacigi.is_alive())

    def hazir(self) -> bool:
        if not self.reader.hazir():
            self.kapali_sebep = self.reader.son_hata or "Sysmon günlüğü okunamıyor"
            return False
        self.kapali_sebep = ""
        return True

    def yerel_guncelle(self, adresler: set[str]) -> None:
        """Adres listesi değişebiliyor (VPN açılması, DHCP yenilemesi). Yön
        kararı buna dayandığı için beslemenin kopyası tazelenebilir olmalı."""
        self._yerel = set(adresler)

    # ---------------------------------------------------------------- yoklama

    def refresh(self, ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        try:
            olaylar = self.reader.yeni()
        except SysmonUnavailable as exc:
            self.kapali_sebep = str(exc)
            log.debug("Sysmon okunamadı: %s", exc)
            return 0
        eklenen = self.olaylari_isle(olaylar, ts)
        self.son_yoklama = ts
        self._buda(ts)
        return eklenen

    def olaylari_isle(self, olaylar: list[dict], ts: float | None = None) -> int:
        """Ayrıştırılmış olayları tabloya işler. Test bunu doğrudan çağırıyor."""
        ts = ts if ts is not None else time.time()
        eklenen = 0
        for olay in olaylar:
            if olay.get("event_id") == 3:
                eklenen += self._baglanti(olay, ts)
            elif olay.get("event_id") == 22:
                self._dns_olayi(olay, ts)
        return eklenen

    def _baglanti(self, olay: dict, ts: float) -> int:
        d = olay.get("data") or {}
        proto = (d.get("Protocol") or "").lower()
        if proto not in ("tcp", "udp"):
            return 0
        src = normalize_ip(d.get("SourceIp", ""))
        dst = normalize_ip(d.get("DestinationIp", ""))
        if not src or not dst:
            return 0
        try:
            sport = int(d.get("SourcePort") or 0)
            dport = int(d.get("DestinationPort") or 0)
        except ValueError:
            return 0

        src_yerel, dst_yerel = src in self._yerel, dst in self._yerel
        if src_yerel and dst_yerel:
            return 0            # makine içi; yakalama da saymıyor
        if src_yerel:
            yerel_ip, yerel_port, uzak_ip, uzak_port = src, sport, dst, dport
        elif dst_yerel:
            yerel_ip, yerel_port, uzak_ip, uzak_port = dst, dport, src, sport
        elif (d.get("Initiated") or "").lower() == "false":
            # Adres listesi eksik olabilir (yeni arayüz, VPN). Sysmon'un kendi
            # yön bilgisine düşüyoruz: `Initiated=false` ise bağlantıyı karşı
            # taraf açmış, yani yerel uç hedeftir.
            yerel_ip, yerel_port, uzak_ip, uzak_port = dst, dport, src, sport
        else:
            yerel_ip, yerel_port, uzak_ip, uzak_port = src, sport, dst, dport

        try:
            pid = int(d.get("ProcessId") or 0)
        except ValueError:
            pid = 0
        ad = _surec_adi(d.get("Image", ""))
        # Olayın kendi zamanı varsa onu kullanıyoruz; TTL doğru yerden saysın.
        gorulme = olay.get("ts") or ts

        self.olay3 += 1
        # `live.ConnectionOwners` ile aynı üç kademeli anahtar: tam 5'li,
        # yerel uç, yalnız port. Kademeler aynı olmasaydı iki besleme aynı
        # akışta farklı isabet verir ve ölçüm karşılaştırılamaz olurdu.
        with self._kilit:
            for anahtar in ((proto, yerel_ip, yerel_port, uzak_ip, uzak_port),
                            (proto, yerel_ip, yerel_port, "", 0),
                            (proto, "", yerel_port, "", 0)):
                self._tablo[anahtar] = (pid, ad, gorulme)
        return 1

    def _dns_olayi(self, olay: dict, ts: float) -> None:
        d = olay.get("data") or {}
        ad = (d.get("QueryName") or "").strip().rstrip(".")
        if not ad:
            return
        self.olay22 += 1
        gorulme = olay.get("ts") or ts
        # QueryResults biçimi: "type:  5 cname.example.com;type:  1 93.184.216.34;"
        for parca in (d.get("QueryResults") or "").split(";"):
            aday = parca.strip().split(" ")[-1].strip()
            if not aday:
                continue
            ip = normalize_ip(aday)
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                continue        # CNAME satırı: adres değil, atlıyoruz
            with self._kilit:
                self._dns[ip] = (ad, gorulme)

    def _buda(self, ts: float) -> None:
        with self._kilit:
            for k in [k for k, (_, _, g) in self._tablo.items()
                      if ts - g > self.ttl]:
                del self._tablo[k]
            for ip in [ip for ip, (_, g) in self._dns.items()
                       if ts - g > self.dns_ttl]:
                del self._dns[ip]

    # ------------------------------------------------------------------ sorgu

    def lookup(self, proto: str, yerel_ip: str, yerel_port: int,
               uzak_ip: str, uzak_port: int) -> tuple[int | None, str]:
        with self._kilit:
            for anahtar in ((proto, yerel_ip, yerel_port, uzak_ip, uzak_port),
                            (proto, yerel_ip, yerel_port, "", 0),
                            (proto, "", yerel_port, "", 0)):
                kayit = self._tablo.get(anahtar)
                if kayit is not None:
                    self.cozulen += 1
                    return kayit[0], kayit[1]
        self.cozulemeyen += 1
        return None, BILINMEYEN_SUREC

    def dns_ad(self, ip: str) -> str:
        """Bu IP hangi ad için çözülmüştü? Bilinmiyorsa boş.

        Şu an yalnız **raporlanıyor**; sınıflandırıcıya bağlanması ayrı bir iş
        (katalogda alan adı tablosu yok) ve ölçümü de ayrıca yapılmalı.
        """
        with self._kilit:
            kayit = self._dns.get(normalize_ip(ip))
        return kayit[0] if kayit else ""

    @property
    def hit_rate(self) -> float | None:
        toplam = self.cozulen + self.cozulemeyen
        return (self.cozulen / toplam) if toplam else None

    def to_dict(self) -> dict[str, Any]:
        with self._kilit:
            izlenen, dns_sayisi = len(self._tablo), len(self._dns)
        return {
            "tracked": izlenen,
            "resolved": self.cozulen,
            "unresolved": self.cozulemeyen,
            "hit_rate": round(self.hit_rate, 3) if self.hit_rate is not None else None,
            "last_poll_age_s": round(time.time() - self.son_yoklama, 1)
            if self.son_yoklama else None,
            "conn_events": self.olay3,
            "dns_events": self.olay22,
            "dns_names": dns_sayisi,
            "polling": self.calisiyor,
            "poll_errors": self.yoklama_hatasi,
            "reader": self.reader.to_dict(),
            "disabled_reason": self.kapali_sebep,
        }


def sysmon_durumu(kanal: str = KANAL) -> dict[str, Any]:
    """`doctor` için: Sysmon kurulu mu, günlük okunabiliyor mu, akıyor mu?"""
    durum: dict[str, Any] = {"platform_ok": os.name == "nt", "channel": kanal,
                             "readable": False, "recent_events": 0, "reason": ""}
    if os.name != "nt":
        durum["reason"] = "Sysmon yalnız Windows'ta"
        return durum
    okuyucu = SysmonReader(kanal=kanal, ilk_geriye=5)
    if not okuyucu.hazir():
        durum["reason"] = okuyucu.son_hata or "kanal bulunamadı"
        return durum
    durum["readable"] = True
    try:
        olaylar = okuyucu.baslat()
    except SysmonUnavailable as exc:
        durum["reason"] = str(exc)
        return durum
    durum["recent_events"] = len(olaylar)
    if olaylar:
        durum["last_event_age_s"] = round(
            time.time() - (olaylar[-1]["ts"] or time.time()), 1)
    else:
        durum["reason"] = ("kanal var ama Event 3/22 yok — Sysmon "
                           "yapılandırması ağ olaylarını süzüyor olabilir")
    return durum


__all__ = ["BILINMEYEN_SUREC", "DNS_TTL", "KANAL", "OWNER_TTL", "SysmonOwners",
           "SysmonReader", "SysmonUnavailable", "normalize_ip",
           "olaylari_ayristir", "sysmon_durumu"]
