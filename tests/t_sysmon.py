"""SYSMON: olay gunlugunden kimlik beslemesi dogru mu?

Sysmon bu makinede kurulu degil ve kurmak yonetici hakki istiyor. Test bu
yuzden **wevtutil'i taklit ediyor**: kaydedilmis bicimde XML veriyor ve
olculen sey gunluk surucusu degil bizim mantigimiz -- ayristirma, imlec,
yon karari, anahtar bicimi, TTL, DNS eslemesi.

En kritik vaka en sonda: Sysmon'un urettigi anahtar ile `capture.py`'in
urettigi anahtar **ayni mi**. Ikisi tutmazsa kimlik beslemesi dolu gorunur,
birlesim bos cikar ve hicbir sey hata vermez -- sessizce %0 cozulme.
"""
import sys, time
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import logging; logging.basicConfig(level=logging.CRITICAL)

from ntc.traffic.sysmon import (
    SysmonOwners, SysmonReader, SysmonUnavailable, normalize_ip,
    olaylari_ayristir, _surec_adi, _zaman,
)

ok = True
YEREL = "10.0.0.5"
UZAK = "93.184.216.34"


def kontrol(baslik, kosul, not_=""):
    global ok
    if not kosul:
        ok = False
    print(f"  {'OK  ' if kosul else 'FAIL'} {baslik}" + (f"  ({not_})" if not_ else ""))


def baslik(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


NS = "http://schemas.microsoft.com/win/2004/08/events/event"


def olay3_xml(record_id, src=YEREL, sport=51514, dst=UZAK, dport=443,
              proto="tcp", pid=4812, image=r"C:\Program Files\Firefox\firefox.exe",
              initiated="true", ts="2026-09-06T10:11:12.1234567Z", ns=True):
    """Gercek Sysmon Event 3 bicimi (alan sirasi ve adlari dahil)."""
    ad = f' xmlns="{NS}"' if ns else ""
    return f"""<Event{ad}>
  <System>
    <Provider Name="Microsoft-Windows-Sysmon" />
    <EventID>3</EventID>
    <TimeCreated SystemTime="{ts}" />
    <EventRecordID>{record_id}</EventRecordID>
  </System>
  <EventData>
    <Data Name="UtcTime">2026-09-06 10:11:12.123</Data>
    <Data Name="ProcessId">{pid}</Data>
    <Data Name="Image">{image}</Data>
    <Data Name="Protocol">{proto}</Data>
    <Data Name="Initiated">{initiated}</Data>
    <Data Name="SourceIsIpv6">false</Data>
    <Data Name="SourceIp">{src}</Data>
    <Data Name="SourcePort">{sport}</Data>
    <Data Name="DestinationIsIpv6">false</Data>
    <Data Name="DestinationIp">{dst}</Data>
    <Data Name="DestinationPort">{dport}</Data>
  </EventData>
</Event>"""


def olay22_xml(record_id, ad="cdn.example.com", sonuc=None,
               ts="2026-09-06T10:11:12.1234567Z"):
    sonuc = sonuc if sonuc is not None else (
        "type:  5 edge.example.net;type:  1 93.184.216.34;")
    return f"""<Event xmlns="{NS}">
  <System>
    <EventID>22</EventID>
    <TimeCreated SystemTime="{ts}" />
    <EventRecordID>{record_id}</EventRecordID>
  </System>
  <EventData>
    <Data Name="ProcessId">4812</Data>
    <Data Name="QueryName">{ad}</Data>
    <Data Name="QueryStatus">0</Data>
    <Data Name="QueryResults">{sonuc}</Data>
    <Data Name="Image">C:\\Program Files\\Firefox\\firefox.exe</Data>
  </EventData>
</Event>"""


def sar(*olaylar):
    return "<Events>" + "".join(olaylar) + "</Events>"


# ------------------------------------------------------------- 1. ayristirma

baslik("1. XML ayristirma")

olaylar = olaylari_ayristir(sar(olay3_xml(101), olay22_xml(102)))
kontrol("iki olay okundu", len(olaylar) == 2, str(len(olaylar)))
kontrol("olay kimlikleri", [o["event_id"] for o in olaylar] == [3, 22])
kontrol("kayit numaralari", [o["record_id"] for o in olaylar] == [101, 102])
kontrol("EventData alanlari", olaylar[0]["data"].get("DestinationPort") == "443",
        olaylar[0]["data"].get("DestinationPort"))
kontrol("zaman cozuldu", olaylar[0]["ts"] > 1_700_000_000, str(olaylar[0]["ts"]))

# wevtutil ad alani yaziyor, elle yazilmis XML yazmayabiliyor.
kontrol("ad alani olmadan da okunuyor",
        len(olaylari_ayristir(sar(olay3_xml(1, ns=False)))) == 1)

# wevtutil ciktisi tavana takilip ortadan kesilebiliyor. O turdaki saglam
# olaylari atmak, kapatmaya calistigimiz boslugu geri acardi.
kesik = sar(olay3_xml(201), olay3_xml(202))[:-40]
kontrol("kesik XML'de saglam olaylar kurtariliyor",
        len(olaylari_ayristir(kesik)) >= 1, f"{len(olaylari_ayristir(kesik))} olay")
kontrol("bos girdi bos liste", olaylari_ayristir("") == [])
kontrol("cop girdi bos liste", olaylari_ayristir("bu XML degil") == [])

# 7 haneli kesir `fromisoformat`'in kabul ettigi 6 haneden fazla.
kontrol("7 haneli kesirli zaman", _zaman("2026-09-06T10:11:12.1234567Z") > 0)
kontrol("bos zaman 0", _zaman("") == 0.0)
kontrol("bozuk zaman 0", _zaman("dun") == 0.0)


# ------------------------------------------------------------ 2. normalizasyon

baslik("2. Adres ve surec adi normalizasyonu")

kontrol("v4-mapped v6 sadelesiyor", normalize_ip("::ffff:10.0.0.5") == "10.0.0.5",
        normalize_ip("::ffff:10.0.0.5"))
kontrol("bolge eki atiliyor", normalize_ip("fe80::1%12") == "fe80::1",
        normalize_ip("fe80::1%12"))
kontrol("duz v4 degismiyor", normalize_ip("10.0.0.5") == "10.0.0.5")
kontrol("adres olmayan aynen doner", normalize_ip("host.local") == "host.local")
kontrol("bos bos", normalize_ip("") == "")

kontrol("Image -> yalin ad",
        _surec_adi(r"C:\Windows\System32\svchost.exe") == "svchost.exe")
kontrol("ileri bolu de calisiyor", _surec_adi("/usr/bin/curl") == "curl")
kontrol("bos Image bos ad", _surec_adi("") == "")


# ------------------------------------------------------------- 3. kimlik tablosu

baslik("3. Event 3 -> kimlik tablosu")

s = SysmonOwners(yerel_adresler={YEREL})
s.olaylari_isle(olaylari_ayristir(sar(olay3_xml(1))), ts=time.time())

pid, ad = s.lookup("tcp", YEREL, 51514, UZAK, 443)
kontrol("tam 5'li eslesmesi", (pid, ad) == (4812, "firefox.exe"), f"{pid}/{ad}")

# UDP'de yerel uc anahtari, joker adrese bagli sokette port anahtari.
pid2, ad2 = s.lookup("tcp", YEREL, 51514, "1.2.3.4", 8443)
kontrol("yerel uc yedegi tutuyor", ad2 == "firefox.exe", ad2)
pid3, ad3 = s.lookup("tcp", "10.0.0.77", 51514, "1.2.3.4", 8443)
kontrol("port yedegi tutuyor", ad3 == "firefox.exe", ad3)
pid4, ad4 = s.lookup("tcp", YEREL, 9999, UZAK, 443)
kontrol("bilinmeyen 5'li cozulmuyor", pid4 is None and ad4 == "", f"{pid4}/{ad4}")
kontrol("isabet orani sayiliyor", abs((s.hit_rate or 0) - 0.75) < 1e-9,
        str(s.hit_rate))

# Yon: yerel uc hedefteyse anahtar ters cevrilmeli.
s2 = SysmonOwners(yerel_adresler={YEREL})
s2.olaylari_isle(olaylari_ayristir(sar(
    olay3_xml(2, src=UZAK, sport=443, dst=YEREL, dport=51515,
              initiated="false", image=r"C:\srv\nginx.exe"))))
pid5, ad5 = s2.lookup("tcp", YEREL, 51515, UZAK, 443)
kontrol("gelen baglantida yerel uc dogru", ad5 == "nginx.exe", ad5)

# Adres listesi eksikse (yeni arayuz/VPN) Sysmon'un kendi yon bilgisi.
s3 = SysmonOwners(yerel_adresler=set())
s3.olaylari_isle(olaylari_ayristir(sar(
    olay3_xml(3, src="172.20.0.9", sport=40000, dst=UZAK, dport=443,
              initiated="true"))))
kontrol("adres listesi bosken Initiated=true yon veriyor",
        s3.lookup("tcp", "172.20.0.9", 40000, UZAK, 443)[1] == "firefox.exe")
s4 = SysmonOwners(yerel_adresler=set())
s4.olaylari_isle(olaylari_ayristir(sar(
    olay3_xml(4, src=UZAK, sport=443, dst="172.20.0.9", dport=40001,
              initiated="false"))))
kontrol("adres listesi bosken Initiated=false ters ceviriyor",
        s4.lookup("tcp", "172.20.0.9", 40001, UZAK, 443)[1] == "firefox.exe")

# Makine ici trafik yakalamada da sayilmiyor; iki tarafta da atlanmali.
s5 = SysmonOwners(yerel_adresler={YEREL, "127.0.0.1"})
eklenen = s5.olaylari_isle(olaylari_ayristir(sar(
    olay3_xml(5, src=YEREL, dst="127.0.0.1", dport=8080))))
kontrol("makine ici baglanti atlaniyor", eklenen == 0, str(eklenen))

# ICMP gibi protokoller 5'li anahtarina oturmuyor.
s6 = SysmonOwners(yerel_adresler={YEREL})
kontrol("tcp/udp disi protokol atlaniyor",
        s6.olaylari_isle(olaylari_ayristir(sar(
            olay3_xml(6, proto="icmp")))) == 0)

# v4-mapped adresli olay, duz v4 sorgusuyla bulunmali (birlesimin sartı).
s7 = SysmonOwners(yerel_adresler={YEREL})
s7.olaylari_isle(olaylari_ayristir(sar(
    olay3_xml(7, src="::ffff:10.0.0.5", dst="::ffff:93.184.216.34"))))
kontrol("v4-mapped olay duz v4 ile bulunuyor",
        s7.lookup("tcp", YEREL, 51514, UZAK, 443)[1] == "firefox.exe")

# TTL: hacim olayin yazildigi saniyede degil sonrasinda akiyor, ama sonsuza
# kadar degil -- port yeniden kullanildiginda eski sahip yanlis cevap olur.
s8 = SysmonOwners(ttl=60.0, yerel_adresler={YEREL})
simdi = time.time()
s8.olaylari_isle([{"event_id": 3, "record_id": 9, "ts": simdi - 300,
                   "data": {"Protocol": "tcp", "SourceIp": YEREL,
                            "SourcePort": "51514", "DestinationIp": UZAK,
                            "DestinationPort": "443", "ProcessId": "1",
                            "Image": "eski.exe", "Initiated": "true"}}], ts=simdi)
s8._buda(simdi)
kontrol("TTL asmis kayit budaniyor",
        s8.lookup("tcp", YEREL, 51514, UZAK, 443)[0] is None)


# ---------------------------------------------------------------- 4. DNS

baslik("4. Event 22 -> DNS eslemesi")

d = SysmonOwners(yerel_adresler={YEREL})
d.olaylari_isle(olaylari_ayristir(sar(olay22_xml(10))))
kontrol("A kaydi eslesti", d.dns_ad("93.184.216.34") == "cdn.example.com",
        d.dns_ad("93.184.216.34"))
kontrol("CNAME satiri adres sayilmadi", d.dns_ad("edge.example.net") == "")
kontrol("bilinmeyen IP bos", d.dns_ad("8.8.8.8") == "")
kontrol("DNS olayi sayildi", d.olay22 == 1, str(d.olay22))

d2 = SysmonOwners(yerel_adresler={YEREL})
d2.olaylari_isle(olaylari_ayristir(sar(
    olay22_xml(11, ad="www.example.com.", sonuc="type:  1 1.2.3.4;"))))
kontrol("sondaki nokta atiliyor", d2.dns_ad("1.2.3.4") == "www.example.com",
        d2.dns_ad("1.2.3.4"))

d3 = SysmonOwners(yerel_adresler={YEREL})
d3.olaylari_isle(olaylari_ayristir(sar(
    olay22_xml(12, sonuc="type:  1 ::ffff:5.6.7.8;"))))
kontrol("DNS sonucunda da v4-mapped sadelesiyor",
        d3.dns_ad("5.6.7.8") == "cdn.example.com", d3.dns_ad("5.6.7.8"))

d4 = SysmonOwners(yerel_adresler={YEREL}, dns_ttl=60.0)
simdi = time.time()
d4.olaylari_isle([{"event_id": 22, "record_id": 13, "ts": simdi - 3600,
                   "data": {"QueryName": "eski.example.com",
                            "QueryResults": "type:  1 4.4.4.4;"}}], ts=simdi)
d4._buda(simdi)
kontrol("eski DNS kaydi budaniyor", d4.dns_ad("4.4.4.4") == "")


# ------------------------------------------------------------- 5. okuyucu/imlec

baslik("5. Artimli okuma (wevtutil taklit ediliyor)")


class SahteOkuyucu(SysmonReader):
    """wevtutil'i taklit eder: gunlugu bir liste olarak tutar."""

    def __init__(self, gunluk, **kw):
        super().__init__(**kw)
        self.gunluk = list(gunluk)          # (record_id, xml)
        self.sorgular = []

    def _calistir(self, args, timeout=20.0):
        self.sorgular.append(args)
        if args[0] == "gl":
            return "name: " + self.kanal
        alt = 0
        for a in args:
            if "EventRecordID>" in a:
                alt = int(a.split("EventRecordID>")[1].split(")")[0])
        adet = int(next(a for a in args if a.startswith("/c:"))[3:])
        secilen = [x for x in self.gunluk if x[0] > alt]
        if "/rd:true" in args:
            secilen = list(reversed(secilen))
        return sar(*[x[1] for x in secilen[:adet]])


gunluk = [(n, olay3_xml(n, sport=50000 + n)) for n in range(1, 11)]
r = SahteOkuyucu(gunluk, ilk_geriye=3)
ilk = r.baslat()
kontrol("ilk okuma geriye donuk", len(ilk) == 3, str(len(ilk)))
kontrol("geriye donuk olaylar en yenilerden",
        [o["record_id"] for o in ilk] == [8, 9, 10])
kontrol("imlec en yeni kayitta", r.son_kayit == 10, str(r.son_kayit))
kontrol("yeni olay yokken bos", r.yeni() == [])

r.gunluk.append((11, olay3_xml(11)))
yeni = r.yeni()
kontrol("yalniz yeni kayit geliyor", [o["record_id"] for o in yeni] == [11],
        str([o["record_id"] for o in yeni]))
# `>` kacirilmiyor: wevtutil komut satirinda `&gt;` gorunce kod 15001 ile
# dusuyor (gercek gunlukte olculdu). Bu satir o hatanin nobetcisi.
kontrol("sorgu imleci XPath'e yaziyor (kacissiz >)",
        any("EventRecordID>10" in a for a in r.sorgular[-1]))
kontrol("XPath'te XML kacisi yok",
        not any("&gt;" in a for a in r.sorgular[-1]))
kontrol("olay kimlikleri suzuluyor",
        any("EventID=3" in a and "EventID=22" in a for a in r.sorgular[-1]))

# Tavan: bir turda batch kadar olay gelirse kacirmis olabiliriz -- imlec
# yine de ilerlemeli, yoksa gecikme sonsuza kadar buyur.
rc = SahteOkuyucu([(n, olay3_xml(n)) for n in range(1, 21)],
                  ilk_geriye=0, batch=5)
rc.baslat()
rc.son_kayit = 0
rc.baslatildi = True
alinan = rc.yeni()
kontrol("tavan kadar olay aliniyor", len(alinan) == 5, str(len(alinan)))
kontrol("tavana takilma sayiliyor", rc.atlanan == 1, str(rc.atlanan))
kontrol("imlec ilerledi", rc.son_kayit == 5, str(rc.son_kayit))

# ilk_geriye=0: imleci kur ama gecmisi getirme.
r0 = SahteOkuyucu(gunluk, ilk_geriye=0)
kontrol("ilk_geriye=0 gecmis getirmiyor", r0.baslat() == [])
kontrol("ilk_geriye=0 imleci yine de kuruyor", r0.son_kayit == 10,
        str(r0.son_kayit))


class DusenOkuyucu(SysmonReader):
    def _calistir(self, args, timeout=20.0):
        raise SysmonUnavailable("kanal bulunamadi")


sd = SysmonOwners(reader=DusenOkuyucu(), yerel_adresler={YEREL})
kontrol("erisim yoksa hazir() False", sd.hazir() is False)
kontrol("gerekce kaydediliyor", "kanal bulunamadi" in sd.kapali_sebep,
        sd.kapali_sebep)
kontrol("refresh dusmuyor, 0 donuyor", sd.refresh() == 0)


# --------------------------------------------------- 6. birlesim (asil risk)

baslik("6. Anahtar bicimi capture.py ile ayni mi")

from ntc.traffic.capture import PacketVolumeFeed
from scapy.layers.inet import IP, TCP
from scapy.layers.l2 import Ether

feed = PacketVolumeFeed(iface="yok", yerel_adresler={YEREL})
feed.feed(Ether() / IP(src=YEREL, dst=UZAK) / TCP(sport=51514, dport=443) / (b"x" * 100))
kayitlar = feed.drain()
kontrol("yakalama tek kayit uretti", len(kayitlar) == 1, str(len(kayitlar)))

k = kayitlar[0]
birlesim = SysmonOwners(yerel_adresler={YEREL})
birlesim.olaylari_isle(olaylari_ayristir(sar(olay3_xml(50))))
pid_b, ad_b = birlesim.lookup(k.proto, k.local_ip, k.local_port,
                              k.remote_ip, k.remote_port)
kontrol("yakalama kaydi Sysmon kimligiyle eslesti",
        (pid_b, ad_b) == (4812, "firefox.exe"), f"{pid_b}/{ad_b}")


baslik("7. BirlesikSahipler: Sysmon once, tablo yedek")

from ntc.traffic.live import BirlesikSahipler, ConnectionOwners


class SahteTablo(ConnectionOwners):
    """Yalniz uzun omurlu baglantiyi goren yoklama."""

    def __init__(self, gorulen):
        super().__init__()
        self.gorulen = gorulen

    def refresh(self, ts=None):
        return 0

    def lookup(self, proto, yerel_ip, yerel_port, uzak_ip, uzak_port):
        kayit = self.gorulen.get((proto, yerel_ip, yerel_port, uzak_ip, uzak_port))
        if kayit is None:
            self.cozulemeyen += 1
            return None, ""
        self.cozulen += 1
        return kayit


uzun = {("tcp", YEREL, 40000, UZAK, 443): (999, "teams.exe")}
sy = SysmonOwners(yerel_adresler={YEREL})
sy.olaylari_isle(olaylari_ayristir(sar(olay3_xml(60))))     # kisa omurlu
b = BirlesikSahipler(SahteTablo(uzun), sy)

kontrol("kisa omurlu akisi Sysmon cozuyor",
        b.lookup("tcp", YEREL, 51514, UZAK, 443) == (4812, "firefox.exe"))
kontrol("Sysmon'un gormedigini tablo cozuyor",
        b.lookup("tcp", YEREL, 40000, UZAK, 443) == (999, "teams.exe"))
kontrol("ikisi de bilmiyorsa cozulemedi",
        b.lookup("tcp", YEREL, 12345, UZAK, 80) == (None, ""))
kontrol("katki ayri sayiliyor", (b.sysmon_hit, b.tablo_hit, b.cozulemeyen) == (1, 1, 1),
        f"{b.sysmon_hit}/{b.tablo_hit}/{b.cozulemeyen}")
kontrol("birlesik isabet orani", abs((b.hit_rate or 0) - 2 / 3) < 1e-9,
        str(b.hit_rate))

d = b.to_dict()
kontrol("durum sozlugu iki beslemeyi de tasiyor",
        "sysmon" in d and "table" in d and d["by_sysmon"] == 1)

# Sysmon yokken tek besleme ile ayni sozlesme.
bs = BirlesikSahipler(SahteTablo(uzun), None)
kontrol("Sysmon'suz tablo yine calisiyor",
        bs.lookup("tcp", YEREL, 40000, UZAK, 443) == (999, "teams.exe"))
kontrol("Sysmon'suz dns_ad bos", bs.dns_ad(UZAK) == "")
kontrol("Sysmon'suz durum sozlugunde sysmon yok",
        "sysmon" not in bs.to_dict())


baslik("8. Yoklama is parcacigi")

# `wevtutil` turu bu makinede ~130 ms surdu ve toplayici `tick()` olay
# dongusunun uzerinde kosuyor. Yoklama ayri is parcaciginda olmazsa her
# saniye 130 ms boyunca API/WebSocket duruyor.
yavas_gunluk = [(1, olay3_xml(1))]


class YavasOkuyucu(SysmonReader):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.tur = 0

    def _calistir(self, args, timeout=20.0):
        if args[0] == "gl":
            return "ok"
        self.tur += 1
        time.sleep(0.05)                     # wevtutil gecikmesi
        return sar(olay3_xml(self.tur, sport=50000 + self.tur))


sp = SysmonOwners(yerel_adresler={YEREL}, poll_seconds=0.02,
                  reader=YavasOkuyucu(ilk_geriye=1))
kontrol("baslamadan calisiyor False", sp.calisiyor is False)
sp.start()
t0 = time.time()
_ = sp.lookup("tcp", YEREL, 51514, UZAK, 443)   # okuma bloklanmamali
gecen = time.time() - t0
kontrol("lookup yoklamayi beklemiyor", gecen < 0.02, f"{gecen*1000:.1f} ms")
kontrol("is parcacigi calisiyor", sp.calisiyor is True)
time.sleep(0.35)
kontrol("arka planda olay islendi", sp.olay3 >= 2, f"{sp.olay3} olay")
sp.aclose()
kontrol("aclose is parcacigini durduruyor", sp.calisiyor is False)
kontrol("durum sozlugu yoklamayi raporluyor",
        sp.to_dict()["polling"] is False and sp.to_dict()["poll_errors"] == 0)


class PatlayanOkuyucu(SysmonReader):
    def _calistir(self, args, timeout=20.0):
        if args[0] == "gl":
            return "ok"
        raise RuntimeError("beklenmedik")


# Yoklama is parcacigi hicbir hatada olmemeli: oldugu an besleme sessizce
# bosalir ve disaridan "Sysmon acik" gorunmeye devam eder.
sp2 = SysmonOwners(yerel_adresler={YEREL}, poll_seconds=0.02,
                   reader=PatlayanOkuyucu())
sp2.start()
time.sleep(0.15)
kontrol("hata is parcacigini oldurmuyor", sp2.calisiyor is True)
kontrol("hata sayiliyor", sp2.yoklama_hatasi >= 1, str(sp2.yoklama_hatasi))
sp2.aclose()


baslik("9. LiveSource baglantisi")

from ntc.core.config import LiveConfig

ayar = LiveConfig()
kontrol("varsayilan mod auto", ayar.sysmon == "auto", ayar.sysmon)

from ntc.traffic.live import LiveSource

ayar_kapali = LiveConfig(); ayar_kapali.sysmon = "off"
ls = LiveSource(ayar_kapali)
kontrol("off modda sysmon beslemesi kurulmuyor", ls.sysmon is None)
kontrol("off modda owners yine birlesik",
        isinstance(ls.owners, BirlesikSahipler))

ayar_acik = LiveConfig(); ayar_acik.sysmon = "auto"
ls2 = LiveSource(ayar_acik)
kontrol("auto modda sysmon beslemesi kuruluyor", ls2.sysmon is not None)
kontrol("kanal yapilandirmadan geliyor",
        ls2.sysmon.reader.kanal == ayar_acik.sysmon_channel,
        ls2.sysmon.reader.kanal)
kontrol("durum sozlugunde mod raporlaniyor",
        ls2.to_dict().get("sysmon_mode") == "auto")

print("\n" + "=" * 68)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
