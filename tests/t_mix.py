"""Yon basina sinif karisimi gercekten ayriliyor mu."""
import sys
import pathlib as _pl
sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from ntc.core.config import LinkConfig
from ntc.core.models import Direction, Flow, TrafficClass, new_id, now
from ntc.traffic.metrics import MetricsEngine

def flow(cls, down, up, direction=Direction.OUTBOUND):
    return Flow(id=new_id("f"), ts=now(), device_id="d1", src_ip="10.0.0.1",
                dst_ip="1.1.1.1", src_port=1, dst_port=443, proto="tcp",
                app="x", traffic_class=cls, direction=direction,
                bytes_down=down, bytes_up=up, packets=10, duration=1.0)

m = MetricsEngine(LinkConfig(), 60)
# Yedekleme: agirlikli YUKLEME.  Video: agirlikli INDIRME.
m.add([
    flow(TrafficClass.BULK,      1_000_000,  9_000_000),
    flow(TrafficClass.STREAMING, 9_000_000,  1_000_000),
])
sig = m.device_signals()["d1"]

ok = True
def chk(ad, got, want, tol=0.02):
    global ok
    good = abs(got - want) <= tol
    if not good: ok = False
    print(f"  {'OK  ' if good else 'FAIL'} {ad}: {got:.2f} (beklenen {want:.2f})")

print("genel karisim :", sig.class_mix)
print("indirme       :", sig.class_mix_down)
print("yukleme       :", sig.class_mix_up)
print()
chk("indirmede yayin baskin",  sig.class_mix_down.get("streaming", 0), 0.90)
chk("indirmede toplu az",      sig.class_mix_down.get("bulk", 0),      0.10)
chk("yuklemede toplu baskin",  sig.class_mix_up.get("bulk", 0),        0.90)
chk("yuklemede yayin az",      sig.class_mix_up.get("streaming", 0),   0.10)
# Genel karisim ikisini de %50 gosterir — eski davranisin neden yanlis oldugu
chk("genel karisim yaniltiyor", sig.class_mix.get("bulk", 0),          0.50)
print("\nSONUC:", "gecti" if ok else "KALDI")
sys.exit(0 if ok else 1)
