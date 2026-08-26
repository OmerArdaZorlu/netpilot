"""Sekil esikleri: tahminle degil supurerek sec."""
import collections, sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))

import ntc.traffic.classify as C
from ntc.traffic.simulator import TrafficSimulator
from ntc.traffic.classify import classify, signals_from_flow

IP = {"netflix": "23.246.2.11", "youtube": "142.250.187.14",
      "windows-update": "13.107.42.14"}

sim = TrafficSimulator(seed=11)
akislar = []
for _ in range(140):
    akislar.extend(sim.tick(1.0))

def olc(gercek_ip):
    d = 0
    for f in akislar:
        s = signals_from_flow(f)
        s.dst_ip = IP.get(f.app, "") if gercek_ip else ""
        d += classify(s).traffic_class == f.traffic_class
    return d / len(akislar)

print("SHAPE_SUSTAINED_RATIO supurmesi")
print(f"{'esik':>8}{'A (IP yok)':>14}{'B (IP var)':>14}")
print("-" * 36)
en_iyi = None
for r in (0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.20):
    C.SHAPE_SUSTAINED_RATIO = r
    a, b = olc(False), olc(True)
    print(f"{r:>8.3f}{a*100:>13.1f}%{b*100:>13.1f}%")
    if en_iyi is None or a + b > en_iyi[1]:
        en_iyi = (r, a + b, a, b)
print("-" * 36)
print(f"en iyi: {en_iyi[0]:.3f}  (A %{en_iyi[2]*100:.1f}, B %{en_iyi[3]*100:.1f})")

C.SHAPE_SUSTAINED_RATIO = 0.02
print()
print("SHAPE_STREAM_DOWN_BPS supurmesi (tek yonluluk kurali kapali)")
C.SHAPE_SUSTAINED_RATIO = 0.0
print(f"{'esik Mbps':>10}{'A (IP yok)':>14}{'B (IP var)':>14}")
print("-" * 38)
for m in (3, 4, 5, 6, 7, 8, 10, 12):
    C.SHAPE_STREAM_DOWN_BPS = m * 1_000_000.0
    print(f"{m:>10}{olc(False)*100:>13.1f}%{olc(True)*100:>13.1f}%")
