"""Kural tabanlı trafik optimizasyon motoru.

Deterministik kısım burada: ölçülebilir eşiklere bakar, gerekçeli aksiyon üretir.
AI analisti (ntc/ai/analyst.py) bunun *üstüne* bağlam ve öneri ekler — ama
uygulanabilir kararların iskeleti her zaman buradaki kurallardan çıkar, böylece
model yokken de sistem çalışır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.config import LinkConfig, OptimizerConfig
from ..core.models import (
    ActionKind,
    Alert,
    Device,
    LinkStats,
    OptimizationAction,
    Severity,
    TrafficClass,
    new_id,
    now,
)
from .metrics import DeviceSignals, MetricsEngine

log = logging.getLogger(__name__)

# Tıkanma anında sınıflara ayrılacak minimum bant payları.
CONGESTION_SHARES = {
    TrafficClass.REALTIME: 0.30,
    TrafficClass.INTERACTIVE: 0.30,
    TrafficClass.STREAMING: 0.25,
    TrafficClass.BULK: 0.10,
    TrafficClass.BACKGROUND: 0.05,
}

RTT_DEGRADED_MS = 120.0
RETRANSMIT_DEGRADED = 0.02
SCAN_PORT_THRESHOLD = 20
POLICY_TTL_SECONDS = 120.0
# Aynı koşul her değerlendirme turunda yeniden uyarı üretmesin.
ALERT_COOLDOWN_SECONDS = 90.0


@dataclass
class OptimizerResult:
    actions: list[OptimizationAction]
    alerts: list[Alert]
    stats: LinkStats
    released: list[str]


class TrafficOptimizer:
    def __init__(self, cfg: OptimizerConfig, link: LinkConfig) -> None:
        self.cfg = cfg
        self.link = link
        self.active: dict[str, OptimizationAction] = {}   # target+kind -> action
        self.history: list[OptimizationAction] = []
        self._alert_seen: dict[str, float] = {}           # uyarı anahtarı -> son yayın

    # --------------------------------------------------------------- ana akış

    def evaluate(self, metrics: MetricsEngine,
                 devices: dict[str, Device]) -> OptimizerResult:
        stats = metrics.link_stats()
        signals = metrics.device_signals()

        alerts = self._alerts_for(stats, signals, devices)
        released = self._release_stale(stats)

        # ⚠️ Aksiyon üretmiyoruz — bilinçli.
        #
        # Eskiden bu kurallar da `rate_limit` üretiyordu ve sayıları akış
        # çözücüsüyle çelişiyordu: burası "ws-dev-02'yi 70 Mbps'e sınırla"
        # derken çözücü "8.8 Mbps verilebilir" diyordu. İkisi de aynı listeye
        # düşüyor, operatör hangisinin doğru olduğunu bilmiyordu.
        #
        # İş bölümü artık net: **eşik motoru durumu tespit eder ve uyarır,
        # sayıyı çözücü verir.** Buradaki eşikler hâlâ değerli — "hat doldu",
        # "şu cihaz tekelci", "kalite bozuldu" gözlemleri deterministik ve
        # modelden bağımsız. Ama "ne kadar" sorusunun cevabı hesaptan çıkmalı,
        # uydurma bir orandan değil.
        #
        # Politika defteri (TTL, tekrar bastırma, uygula/kaldır) burada
        # kalıyor; çözücünün aksiyonları `adopt()` ile aynı defterden geçiyor.
        return OptimizerResult(actions=[], alerts=alerts,
                               stats=stats, released=released)

    def adopt(self, actions: list[OptimizationAction]) -> list[OptimizationAction]:
        """Dışarıdan gelen aksiyonları politika defterine alır.

        Akış çözücüsünün ürettiği aksiyonlar buradan geçiyor ki TTL, tekrar
        bastırma ve uygula/kaldır davranışı tek yerde kalsın.
        """
        return [a for a in actions if self._record(a)]

    # ----------------------------------------------------------------- kurallar

    def _check_downlink(self, stats: LinkStats) -> list[OptimizationAction]:
        util = stats.down_utilization
        # Eskiden burada `util < hog_share_threshold` da vardı; o eşik **tek
        # cihazın toplam banttaki payı** için, hat doluluğu için değil. İki
        # farklı büyüklüğü kıyaslıyordu. Bugünkü değerlerle (0.35 < 0.80)
        # sonucu değiştirmiyordu ama hog eşiği tıkanma eşiğinin üstüne
        # çekilseydi kural sessizce yanlış çalışacaktı.
        if util < self.link.congestion_threshold:
            return []

        if util >= self.link.critical_threshold:
            return [
                self._action(
                    ActionKind.DEFER, TrafficClass.BULK.value,
                    {"until_utilization_below": self.link.congestion_threshold,
                     "affected_class": "bulk"},
                    f"İndirme hattı kritik seviyede (%{util * 100:.0f}). Toplu "
                    f"transferler (yedek/güncelleme) tıkanma geçene kadar erteleniyor.",
                    confidence=0.95,
                ),
                self._action(
                    ActionKind.PRIORITIZE, TrafficClass.REALTIME.value,
                    {"guaranteed_share": CONGESTION_SHARES[TrafficClass.REALTIME]},
                    "Kritik dolulukta görüşme/VoIP akışlarına garantili bant ayrıldı.",
                    confidence=0.92,
                ),
            ]

        if util >= self.link.congestion_threshold:
            return [self._action(
                ActionKind.REBALANCE, "link",
                {"shares": {k.value: v for k, v in CONGESTION_SHARES.items()},
                 "utilization": round(util, 3)},
                f"İndirme doluluğu %{util * 100:.0f} ile tıkanma eşiğini aştı. "
                f"QoS sınıf payları yeniden dağıtıldı.",
                confidence=0.85,
            )]
        return []

    def _check_uplink(self, stats: LinkStats, signals: dict[str, DeviceSignals],
                      devices: dict[str, Device]) -> list[OptimizationAction]:
        if stats.up_utilization < self.link.congestion_threshold:
            return []

        top = max(signals.values(), key=lambda s: s.up_bps, default=None)
        if top is None or top.up_bps <= 0:
            return []

        device = devices.get(top.device_id)
        name = device.hostname if device else top.device_id
        cap_bps = self.link.uplink_bps * 0.25
        return [self._action(
            ActionKind.RATE_LIMIT, top.device_id,
            {"direction": "up", "cap_bps": round(cap_bps),
             "cap_mbps": round(cap_bps / 1e6, 2), "hostname": name},
            f"Yükleme hattı %{stats.up_utilization * 100:.0f} dolu; en çok yükleyen "
            f"{name} ({top.up_bps / 1e6:.1f} Mbps) {cap_bps / 1e6:.1f} Mbps'e sınırlandı.",
            confidence=0.8,
        )]

    def _check_hogs(self, stats: LinkStats, signals: dict[str, DeviceSignals],
                    devices: dict[str, Device]) -> list[OptimizationAction]:
        total = sum(stats.per_device_bps.values())
        if total <= 0:
            return []

        out: list[OptimizationAction] = []
        for device_id, bps in stats.per_device_bps.items():
            share = bps / total
            if share < self.cfg.hog_share_threshold:
                continue
            sig = signals.get(device_id)
            if sig is None:
                continue

            # Gerçek zamanlı/etkileşimli trafik "hog" sayılmaz — o zaten öncelikli.
            elastic = (sig.class_mix.get("bulk", 0) + sig.class_mix.get("streaming", 0))
            if elastic < 0.5:
                continue

            device = devices.get(device_id)
            name = device.hostname if device else device_id
            cap_bps = self.link.downlink_bps * self.cfg.hog_share_threshold
            out.append(self._action(
                ActionKind.RATE_LIMIT, device_id,
                {"direction": "down", "cap_bps": round(cap_bps),
                 "cap_mbps": round(cap_bps / 1e6, 2), "share": round(share, 3),
                 "hostname": name, "top_app": sig.top_app},
                f"{name} toplam bandın %{share * 100:.0f}'sini tek başına kullanıyor "
                f"(baskın uygulama: {sig.top_app or 'bilinmiyor'}). Esnek trafiği "
                f"{cap_bps / 1e6:.0f} Mbps'e sınırlandı.",
                confidence=0.78 if stats.down_utilization > 0.6 else 0.55,
            ))
        return out

    def _check_quality(self, stats: LinkStats, signals: dict[str, DeviceSignals],
                       devices: dict[str, Device]) -> list[OptimizationAction]:
        realtime_bps = stats.per_class_bps.get(TrafficClass.REALTIME.value, 0.0)
        degraded = (stats.avg_rtt_ms > RTT_DEGRADED_MS
                    or stats.retransmit_rate > RETRANSMIT_DEGRADED)
        if not degraded:
            return []

        out: list[OptimizationAction] = []
        if realtime_bps > 0:
            out.append(self._action(
                ActionKind.PRIORITIZE, TrafficClass.REALTIME.value,
                {"guaranteed_share": CONGESTION_SHARES[TrafficClass.REALTIME],
                 "avg_rtt_ms": stats.avg_rtt_ms,
                 "retransmit_rate": stats.retransmit_rate},
                f"Hat kalitesi bozuldu (RTT {stats.avg_rtt_ms:.0f} ms, yeniden gönderim "
                f"%{stats.retransmit_rate * 100:.1f}) ve aktif görüşme trafiği var.",
                confidence=0.83,
            ))
        else:
            out.append(self._action(
                ActionKind.DEPRIORITIZE, TrafficClass.BULK.value,
                {"avg_rtt_ms": stats.avg_rtt_ms},
                f"Gecikme {stats.avg_rtt_ms:.0f} ms'e çıktı; kuyruk şişmesini azaltmak "
                f"için toplu trafiğin önceliği düşürüldü.",
                confidence=0.7,
            ))
        return out

    # ------------------------------------------------------------------ uyarılar

    def _alerts_for(self, stats: LinkStats, signals: dict[str, DeviceSignals],
                    devices: dict[str, Device]) -> list[Alert]:
        alerts: list[Alert] = []

        if stats.down_utilization >= self.link.critical_threshold:
            alerts.append(self._alert(
                Severity.HIGH, "İndirme hattı doygun",
                f"Doluluk %{stats.down_utilization * 100:.0f}, ortalama RTT "
                f"{stats.avg_rtt_ms:.0f} ms. Etkileşimli trafik gözle görülür yavaşlar.",
                meta={"down_utilization": round(stats.down_utilization, 3)},
            ))
        elif stats.down_utilization >= self.link.congestion_threshold:
            alerts.append(self._alert(
                Severity.MEDIUM, "İndirme hattında tıkanma",
                f"Doluluk %{stats.down_utilization * 100:.0f} ile eşiği aştı.",
                meta={"down_utilization": round(stats.down_utilization, 3)},
            ))

        if stats.up_utilization >= self.link.congestion_threshold:
            alerts.append(self._alert(
                Severity.MEDIUM, "Yükleme hattında tıkanma",
                f"Yükleme doluluğu %{stats.up_utilization * 100:.0f}. Yükleme kapasitesi "
                f"indirmeye göre çok dar, video görüşmeleri ilk etkilenen olur.",
                meta={"up_utilization": round(stats.up_utilization, 3)},
            ))

        # Tarama benzeri davranış — güvenlik modülü gelene kadar erken sinyal.
        for device_id, sig in signals.items():
            if sig.unique_dst_ports >= SCAN_PORT_THRESHOLD and sig.lateral_flows > 10:
                device = devices.get(device_id)
                alerts.append(self._alert(
                    Severity.HIGH, "Olası ağ içi keşif taraması",
                    f"{device.hostname if device else device_id} son pencerede "
                    f"{sig.unique_dst_ports} farklı porta / {sig.unique_dst_ips} farklı "
                    f"IP'ye bağlanmayı denedi.",
                    device_id=device_id,
                    meta={"unique_dst_ports": sig.unique_dst_ports,
                          "unique_dst_ips": sig.unique_dst_ips,
                          "lateral_flows": sig.lateral_flows},
                ))
        return self.debounce(alerts)

    def debounce(self, alerts: list[Alert]) -> list[Alert]:
        """Aynı uyarıyı soğuma süresi dolmadan tekrar yayınlama.

        Kural motoru saniyeler arayla çalıştığı için tıkanma gibi süregelen bir
        durum aksi halde akışı doldurur ve gerçek yeni olayları görünmez kılar.
        """
        current = now()
        out = []
        for alert in alerts:
            key = f"{alert.source}:{alert.title}:{alert.device_id or '-'}"
            last = self._alert_seen.get(key)
            if last is not None and current - last < ALERT_COOLDOWN_SECONDS:
                continue
            self._alert_seen[key] = current
            out.append(alert)
        return out

    # ------------------------------------------------------------- politika defteri

    def _key(self, action: OptimizationAction) -> str:
        return f"{action.kind.value}:{action.target}"

    def _record(self, action: OptimizationAction) -> bool:
        """Aynı politika zaten aktifse tekrar yayınlama; sadece süresini uzat."""
        key = self._key(action)
        existing = self.active.get(key)
        if existing is not None:
            existing.expires_at = now() + POLICY_TTL_SECONDS
            existing.reason = action.reason
            existing.params = action.params
            return False

        if self.cfg.auto_apply and action.confidence >= self.cfg.min_confidence_to_apply:
            action.applied = True
        action.expires_at = now() + POLICY_TTL_SECONDS
        self.active[key] = action
        self.history.append(action)
        if len(self.history) > 500:
            del self.history[:-500]
        return True

    def _release_stale(self, stats: LinkStats) -> list[str]:
        """Tıkanma geçtiyse veya TTL dolduysa politikaları kaldır."""
        calm = (stats.down_utilization < self.link.congestion_threshold * 0.75
                and stats.up_utilization < self.link.congestion_threshold * 0.75)
        released: list[str] = []
        for key, action in list(self.active.items()):
            expired = action.expires_at is not None and now() >= action.expires_at
            if expired or (calm and action.kind in
                           (ActionKind.DEFER, ActionKind.REBALANCE, ActionKind.RATE_LIMIT)):
                del self.active[key]
                released.append(key)
        return released

    def apply(self, action_id: str) -> OptimizationAction | None:
        for action in self.active.values():
            if action.id == action_id:
                action.applied = True
                return action
        return None

    def revert(self, action_id: str) -> OptimizationAction | None:
        for key, action in list(self.active.items()):
            if action.id == action_id:
                action.applied = False
                del self.active[key]
                return action
        return None

    # ------------------------------------------------------------------ yardımcı

    def _action(self, kind: ActionKind, target: str, params: dict,
                reason: str, confidence: float,
                source: str = "rules") -> OptimizationAction:
        return OptimizationAction(
            id=new_id("act"), ts=now(), kind=kind, target=target,
            params=params, reason=reason, confidence=confidence, source=source,
        )

    def _alert(self, severity: Severity, title: str, detail: str,
               device_id: str | None = None, meta: dict | None = None) -> Alert:
        return Alert(
            id=new_id("alr"), ts=now(), severity=severity, source="optimizer",
            title=title, detail=detail, device_id=device_id, meta=meta or {},
        )
