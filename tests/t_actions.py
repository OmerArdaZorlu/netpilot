"""Plandan uretilen aksiyonlar dogru mu."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.core.models import TrafficClass as TC
from ntc.traffic.topology import Edge, Topology, INTERNET
from ntc.traffic.flowopt import Demand, FlowOptimizer, actions_from_plan

ok = True
def chk(ad, kosul):
    global ok
    if not kosul: ok = False
    print(f"  {'OK  ' if kosul else 'FAIL'} {ad}")

# --- 1. TEK yollu topoloji: reroute OLMAMALI (eski hata buydu) ---
print("\n1. Tek yol -> reroute uretilmemeli")
tek = Topology.default(200, 20)
plan1 = FlowOptimizer(tek).solve([
    Demand("pc", INTERNET, TC.BULK, 60.0, direction="up"),   # 20'lik hatta 60
])
acts1 = actions_from_plan(plan1, {})
kinds1 = [a.kind.value for a in acts1]
print("   uretilen:", kinds1)
chk("reroute yok", "reroute" not in kinds1)
chk("rate_limit var", "rate_limit" in kinds1)

# --- 2. Cok yollu: reroute OLMALI ---
print("\n2. Cok yol -> reroute uretilmeli")
cok = Topology(edges=[
    Edge("sw-access", "sw-core", 1000),
    Edge("sw-core", "a", 60, latency_ms=8, kind="wan"),
    Edge("a", INTERNET, 60, latency_ms=8, kind="wan"),
    Edge("sw-core", "b", 60, latency_ms=30, kind="wan"),
    Edge("b", INTERNET, 60, latency_ms=30, kind="wan"),
], default_access="sw-access")
plan2 = FlowOptimizer(cok).solve([Demand("pc", INTERNET, TC.BULK, 100.0)])
acts2 = actions_from_plan(plan2, {})
rr = [a for a in acts2 if a.kind.value == "reroute"]
chk("reroute uretildi", len(rr) == 1)
if rr:
    p = rr[0].params
    print("   dallanma dugumu:", p["branch_node"])
    print("   bolunme        :", p["split_mbps"])
    chk("dallanma sw-core'da", p["branch_node"] == "sw-core")
    chk("iki cikisa bolundu", len(p["split_mbps"]) == 2)
    chk("toplam 100 Mbps", abs(sum(p["split_mbps"].values()) - 100.0) < 0.5)
    print("   gerekce:", rr[0].reason)

# --- 3. rate_limit tavani = verilebilen hiz ---
print("\n3. rate_limit tavani cozucuden geliyor")
rl = [a for a in acts1 if a.kind.value == "rate_limit"][0]
print("   ", rl.reason)
chk("tavan 20 Mbps (hat kapasitesi)", abs(rl.params["cap_mbps"] - 20.0) < 0.5)
chk("geri cekme 40 Mbps", abs(rl.params["pullback_mbps"] - 40.0) < 0.5)
chk("yon yukleme", rl.params["direction"] == "up")
chk("kaynak cozucu", rl.params["source"] == "flow-solver")

print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
