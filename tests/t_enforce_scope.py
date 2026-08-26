"""Kapsam basina surucu yonlendirmesi."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.enforce import (Enforcer, LinuxTcDriver, Mark, Match, PolicySet,
                         RateLimit, WindowsQosDriver)

ok = True
def check(n, got, want):
    global ok
    g = got == want
    if not g: ok = False
    print(f"  {'OK  ' if g else 'FAIL'} {n}: {got!r} (beklenen {want!r})")

print("="*70)
print("Kapsam basina surucu: cekirdek=linux, uc=windows")
print("="*70)

enf = Enforcer({"core": LinuxTcDriver(wan_if="eth1", lan_if="eth0"),
                "edge": WindowsQosDriver()})
check("etiket", enf.driver_label, "core=linux, edge=windows")

ps = PolicySet([
    # cekirdek: indirme kisiti -> tc komutu bekliyoruz
    RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="down"),
              scope="core", cap_mbps=45),
    # uc: yukleme kisiti -> New-NetQosPolicy bekliyoruz
    RateLimit(match=Match(host="pc-b", ip="10.0.0.6", direction="up"),
              scope="edge", cap_mbps=10),
    # uc: damga -> New-NetQosPolicy -DSCPAction
    Mark(match=Match(traffic_class="realtime"), scope="edge", dscp=46,
         selectors=[{"proto": "udp", "port": 3478}]),
])
r = enf.reconcile(ps)
metin = [c.text for c in r.commands]
print("  uretilen komutlar:")
for m in metin:
    print("     ", m)

check("3 kural kuruldu", len(r.added), 3)
check("cekirdek kurali tc uretti",
      any(m.startswith("tc class replace dev eth0") for m in metin), True)
check("uc kisiti Windows uretti",
      any("New-NetQosPolicy" in m and "ThrottleRateAction" in m for m in metin), True)
check("uc damgasi Windows uretti",
      any("DSCPAction 46" in m for m in metin), True)
check("uc kurali tc uretmedi",
      any(m.startswith("tc") and "10.0.0.6" in m for m in metin), False)

print("\n  --- eksik kapsam sessizce gecilmiyor ---")
enf2 = Enforcer({"core": LinuxTcDriver()})
r2 = enf2.reconcile(PolicySet([
    Mark(match=Match(traffic_class="realtime"), scope="edge", dscp=46,
         selectors=[{"proto": "udp", "port": 3478}])]))
check("uc surucusu yokken atlandi", len(r2.skipped), 1)
check("kurulmus sayilmadi", len(enf2.active), 0)
print("   gerekce:", r2.skipped[0].reason)

print("\n  --- tek surucu hala calisiyor (geriye uyum) ---")
enf3 = Enforcer(LinuxTcDriver())
r3 = enf3.reconcile(PolicySet([
    RateLimit(match=Match(host="pc-a", ip="10.0.0.5", direction="up"),
              scope="edge", cap_mbps=5)]))
check("tek surucu her kapsama bakti", len(r3.added), 1)
check("etiket", enf3.driver_label, "linux")

print("\n" + "="*70)
print("SONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
