"""Infaz katmaninin dogrulamasi.

Her senaryonun beklenen cevabi elle yazilabilir; olculen sey "calisti gibi
duruyor" degil, "dogru komutu uretti ve dogru sayida uretti".
"""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

from ntc.core.models import Device, DeviceKind, TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer
from ntc.enforce import (
    DescribeDriver, Enforcer, LinuxTcDriver, MODE_LIVE, MODE_SHADOW,
    Mark, Match, PathPin, PolicySet, RateLimit, UnsupportedRule,
    WindowsQosDriver, policies_from_plan,
)

ok = True


def check(name, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print(f"  {'OK  ' if good else 'FAIL'} {name}: {got!r} (beklenen {want!r})")


def check_true(name, cond, detail=""):
    global ok
    if not cond:
        ok = False
    print(f"  {'OK  ' if cond else 'FAIL'} {name} {detail}")


def line(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def dev(host, ip):
    return Device(id=f"dev-{host}", ip=ip, mac="00:00:00:00:00:00",
                  hostname=host, kind=DeviceKind.WORKSTATION)


DEVICES = {d.id: d for d in [dev("pc-a", "10.0.0.5"), dev("pc-b", "10.0.0.6")]}


# --------------------------------------------------------- 1. plan -> politika
line("1. Plandan politikaya ceviri")
topo = Topology(edges=[
    Edge("sw-access", "sw-core", 1000), Edge("sw-core", "wan", 100, kind="wan"),
    Edge("wan", INTERNET, 100, kind="wan"),
], default_access="sw-access")
plan = FlowOptimizer(topo).solve([
    Demand("pc-a", INTERNET, TC.BULK, 80.0, direction="up"),
    Demand("pc-b", INTERNET, TC.BULK, 80.0, direction="up"),
])
ps = policies_from_plan(plan, DEVICES)
rates = [r for r in ps.rules if r.kind == "rate"]
marks = [r for r in ps.rules if r.kind == "mark"]
check("hiz kurali sayisi", len(rates), 2)
check("damga kurali sayisi (tek sinif)", len(marks), 1)
check("tavan = verilen", round(rates[0].cap_mbps), 50)
check("yukleme kurali uc makinede", rates[0].scope, "edge")
check("IP envanterden geldi", rates[0].match.ip in ("10.0.0.5", "10.0.0.6"), True)
print("  ornek:", rates[0].describe())
print("  damga:", marks[0].describe())

# --------------------------------------------------- 2. indirme -> cekirdekte
line("2. Indirme kisiti cekirdege, yukleme uca")
topo2 = Topology.default(200, 20)
plan2 = FlowOptimizer(topo2).solve([
    Demand("pc-a", topo2.attach_point("pc-a"), TC.BULK, 400.0,
           src=INTERNET, direction="down"),
    Demand("pc-a", INTERNET, TC.BULK, 60.0, direction="up"),
])
ps2 = policies_from_plan(plan2, DEVICES)
scope_by_dir = {r.match.direction: r.scope for r in ps2.rules if r.kind == "rate"}
check("indirme kapsami", scope_by_dir.get("down"), "core")
check("yukleme kapsami", scope_by_dir.get("up"), "edge")

# ------------------------------------------------------- 3. anahtar kararliligi
line("3. Ayni kural her turda ayni anahtari aliyor")
ps2b = policies_from_plan(plan2, DEVICES)
k1 = sorted(r.key for r in ps2.rules)
k2 = sorted(r.key for r in ps2b.rules)
check("anahtarlar ayni", k1 == k2, True)
check("ad oneki netpilot-", all(r.name.startswith("netpilot-") for r in ps2.rules), True)

# -------------------------------------------------------------- 4. uzlastirma
line("4. Uzlastirma — ilk tur kurar, ikinci tur hicbir sey yapmaz")
enf = Enforcer(DescribeDriver(), mode=MODE_SHADOW)
r1 = enf.reconcile(ps2)
check("ilk turda eklenen", len(r1.added), len(ps2.rules))
check("ilk turda komut var", len(r1.commands) > 0, True)
check("golge modda calistirilmadi", r1.executed, False)
r2 = enf.reconcile(ps2)
check("ikinci turda eklenen", len(r2.added), 0)
check("ikinci turda degisen", len(r2.changed), 0)
check("ikinci turda komut", len(r2.commands), 0)
check("hepsi ayni", len(r2.unchanged), len(ps2.rules))
print("  ozet:", r2.summary())

# ------------------------------------------------------ 5. deger degisimi
line("5. Tavan degisince guncelleniyor, kimlik degismiyor")
m = Match(host="pc-a", ip="10.0.0.5", direction="down")
enf5 = Enforcer(DescribeDriver())
a = PolicySet([RateLimit(match=m, cap_mbps=45.0)])
b = PolicySet([RateLimit(match=m, cap_mbps=30.0)])
c = PolicySet([RateLimit(match=m, cap_mbps=30.04)])   # gurultu
enf5.reconcile(a)
r = enf5.reconcile(b)
check("degisen olarak gorundu", len(r.changed), 1)
check("silinmedi", len(r.removed), 0)
r = enf5.reconcile(c)
check("0.04 Mbps gurultusu yok sayildi", len(r.changed), 0)

# ------------------------------------------------------------- 6. kaldirma
line("6. Istenmeyen kural kaldiriliyor")
r = enf5.reconcile(PolicySet([]))
check("kaldirilan", len(r.removed), 1)
check("kaldirma komutu yikici", r.commands[0].destructive, True)
check("aktif kural kalmadi", len(enf5.active), 0)

# -------------------------------------------------------------- 7. onay kapisi
line("7. Onay kapisi — onaysiz kural kurulmuyor")
enf7 = Enforcer(DescribeDriver())
onayli = {ps2.rules[0].key}
r = enf7.reconcile(ps2, approved=onayli)
check("yalniz onayli kuruldu", len(r.added), 1)
check("aktif kural", len(enf7.active), 1)
r = enf7.reconcile(ps2, approved=set())
check("onay kalkinca kaldirildi", len(r.removed), 1)

# -------------------------------------------------------- 8. Windows sinirlari
line("8. Windows QoS yapamadigini yapamaz diyor")
win = WindowsQosDriver()
try:
    win.add(RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="down"),
                      cap_mbps=45))
    check("indirme reddedildi", False, True)
except UnsupportedRule as e:
    check("indirme reddedildi", True, True)
    print("   gerekce:", e)
try:
    win.add(PathPin(match=Match(host="pc-a", ip="10.0.0.5", direction="up"),
                    shares={"wan-a": 0.6, "wan-b": 0.4}, branch_node="sw-core"))
    check("yol atamasi reddedildi", False, True)
except UnsupportedRule as e:
    check("yol atamasi reddedildi", True, True)
    print("   gerekce:", e)
cmds = win.add(RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="up"),
                         cap_mbps=45))
check("yukleme kabul edildi", len(cmds), 1)
check("uzaktan calistirma sarili", "Invoke-Command -ComputerName pc-a" in cmds[0].text, True)
check("bit/sn dogru", "45000000" in cmds[0].text, True)
print("  ", cmds[0].text)

# ------------------------------------------------------- 9. belirsiz port
line("9. 443 belirsizligi sessizce tahmin edilmiyor")
mk = [r for r in policies_from_plan(plan2, DEVICES).rules if r.kind == "mark"][0]
print("   sinif:", mk.match.traffic_class, "| kesin:", mk.selectors, "| belirsiz:", mk.apps)
try:
    out = win.add(Mark(match=Match(traffic_class="streaming"), dscp=18,
                       selectors=[], apps=["netflix", "youtube"]))
    check("secicisiz damga reddedildi", False, True)
except UnsupportedRule as e:
    check("secicisiz damga reddedildi", True, True)
    print("   gerekce:", e)

# ------------------------------------------------------------ 10. Linux komut
line("10. Linux tc komutlari")
lin = LinuxTcDriver(wan_if="eth1", lan_if="eth0",
                    table_by_egress={"wan-a": "netpilot1", "wan-b": "netpilot2"})
c_down = lin.add(RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="down"),
                           cap_mbps=45.0))
check("indirme icin 2 komut", len(c_down), 2)
check("LAN bacaginda", " dev eth0 " in c_down[0].text, True)
check("45000kbit", "rate 45000kbit" in c_down[0].text, True)
check("hedef IP eslesmesi", "match ip dst 10.0.0.5/32" in c_down[1].text, True)
for x in c_down:
    print("   ", x.text)
c_up = lin.add(RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="up"),
                         cap_mbps=10.0))
check("yukleme WAN bacaginda", " dev eth1 " in c_up[0].text, True)
check("kaynak IP eslesmesi", "match ip src 10.0.0.5/32" in c_up[1].text, True)

pp = PathPin(match=Match(host="pc-a", ip="10.0.0.5", direction="up",
                         traffic_class="bulk"),
             shares={"wan-a": 0.6, "wan-b": 0.4}, branch_node="sw-core")
c_path = lin.add(pp)
check("yol icin 3 komut (mark + 2 tablo)", len(c_path), 3)
check("oncelik sirasi", "priority 200" in c_path[1].text, True)
for x in c_path:
    print("   ", x.text)

# ------------------------------------------------- 11. tanimsiz tablo reddi
line("11. Tanimsiz yonlendirme tablosu sessizce gecilmiyor")
lin_bos = LinuxTcDriver(table_by_egress={"wan-a": "netpilot1"})
try:
    lin_bos.add(pp)
    check("eksik tablo reddedildi", False, True)
except UnsupportedRule as e:
    check("eksik tablo reddedildi", True, True)
    print("   gerekce:", e)

# --------------------------------------------------- 12. IP yoksa reddediliyor
line("12. IP bilinmeyen cihaz icin tc filtresi uretilmiyor")
try:
    lin.add(RateLimit(match=Match(host="bilinmeyen", direction="down"), cap_mbps=10))
    check("IPsiz kural reddedildi", False, True)
except UnsupportedRule as e:
    check("IPsiz kural reddedildi", True, True)
    print("   gerekce:", e)

# ------------------------------ 13. atlanan kural "kuruldu" sayilmiyor
line("13. Atlanan kural bir sonraki turda tekrar deneniyor")
enf13 = Enforcer(WindowsQosDriver())
ps13 = PolicySet([
    RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="down"), cap_mbps=45),
    RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="up"), cap_mbps=10),
])
r13 = enf13.reconcile(ps13)
check("bir kural atlandi", len(r13.skipped), 1)
check("bir kural kuruldu", len(r13.added), 1)
check("atlanan aktife girmedi", len(enf13.active), 1)
r13b = enf13.reconcile(ps13)
check("atlanan tekrar denendi", len(r13b.skipped), 1)
check("kurulan tekrar kurulmadi", len(r13b.added), 0)

# ------------------------------------------------------------- 14. geri alma
line("14. Geri alma her seyi kaldiriyor")
enf14 = Enforcer(DescribeDriver())
enf14.reconcile(ps2)
n = len(enf14.active)
r14 = enf14.rollback()
check("kaldirilan sayisi", len(r14.removed), n)
check("aktif kalmadi", len(enf14.active), 0)
check("hepsi yikici komut", all(c.destructive for c in r14.commands), True)

# ------------------------------------------------ 15. canli mod runner istiyor
line("15. Canli mod runner olmadan acilmiyor")
try:
    Enforcer(LinuxTcDriver(), mode=MODE_LIVE)
    check("runnersiz canli reddedildi", False, True)
except ValueError as e:
    check("runnersiz canli reddedildi", True, True)
    print("   gerekce:", e)

kosulan = []
enf15 = Enforcer(DescribeDriver(), mode=MODE_LIVE, runner=kosulan.extend)
r15 = enf15.reconcile(PolicySet([RateLimit(
    match=Match(host="pc-a", ip="10.0.0.5", direction="up"), cap_mbps=5)]))
check("canli modda calistirildi", r15.executed, True)
check("runner komut aldi", len(kosulan) > 0, True)

# ------------------------------------------------------- 16. yol atamasi ureti
line("16. Coklu cikista yol atamasi uretiliyor")
topo16 = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "wan-a", 60, latency_ms=8, kind="wan"),
    Edge("wan-a", INTERNET, 60, latency_ms=8, kind="wan"),
    Edge("sw-core", "wan-b", 60, latency_ms=40, kind="wan"),
    Edge("wan-b", INTERNET, 60, latency_ms=40, kind="wan"),
], default_access="sw-access")
plan16 = FlowOptimizer(topo16).solve([
    Demand("pc-a", INTERNET, TC.BULK, 100.0, direction="up")])
ps16 = policies_from_plan(plan16, DEVICES)
paths = [r for r in ps16.rules if r.kind == "path"]
check("yol kurali uretildi", len(paths), 1)
check("dallanma dugumu", paths[0].branch_node, "sw-core")
check("paylar toplami 1", round(sum(paths[0].shares.values()), 2), 1.0)
print("  ", paths[0].describe())

print("\n" + "=" * 70)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
