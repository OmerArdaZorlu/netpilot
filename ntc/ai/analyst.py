"""AI trafik analisti: metrikleri modele anlaşılır bir özet olarak sunar,
dönen yapılandırılmış analizi sistemin ortak tiplerine çevirir."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.config import AIConfig, LinkConfig
from ..core.models import (
    ActionKind,
    Alert,
    Device,
    OptimizationAction,
    Severity,
    new_id,
    now,
)
from ..traffic.metrics import MetricsEngine
from ..traffic.optimizer import TrafficOptimizer
from .prompts import ANALYST_SYSTEM, ANALYST_USER, QA_SYSTEM, QA_USER
from .provider import LLMProvider, LLMUnavailable

log = logging.getLogger(__name__)

# Bir önerinin işaret edebileceği trafik sınıfları. Cihaz hostname'leri
# çalışma anında eklenir; ikisinin birleşimi geçerli hedef kümesidir.
_TARGET_CLASSES = {"realtime", "interactive", "streaming", "bulk",
                   "background", "link"}

_ACTION_ALIASES = {
    "rate_limit": ActionKind.RATE_LIMIT,
    "ratelimit": ActionKind.RATE_LIMIT,
    "limit": ActionKind.RATE_LIMIT,
    "prioritize": ActionKind.PRIORITIZE,
    "priority": ActionKind.PRIORITIZE,
    "deprioritize": ActionKind.DEPRIORITIZE,
    "defer": ActionKind.DEFER,
    "delay": ActionKind.DEFER,
    "rebalance": ActionKind.REBALANCE,
    "advise": ActionKind.ADVISE,
}


@dataclass
class AIReport:
    id: str
    ts: float
    summary: str
    health_score: int
    findings: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    provider: str
    model: str
    latency_ms: float
    error: str | None = None
    actions: list[OptimizationAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["actions"] = [a.to_dict() for a in self.actions]
        return d


class AIAnalyst:
    def __init__(self, cfg: AIConfig, provider: LLMProvider) -> None:
        self.cfg = cfg
        self.provider = provider
        self.last_report: AIReport | None = None

    # ------------------------------------------------------------- anlık görüntü

    def build_snapshot(self, metrics: MetricsEngine, devices: dict[str, Device],
                       optimizer: TrafficOptimizer | None = None) -> dict[str, Any]:
        """Modele gidecek kompakt özet.

        Ham akış listesi gönderilmez — küçük modellerin bağlam penceresini
        doldurup analiz kalitesini düşürür. Bunun yerine toplulaştırılmış,
        insan okunur birimlere çevrilmiş bir görünüm gönderilir.
        """
        stats = metrics.link_stats()
        signals = metrics.device_signals()

        device_rows = []
        ranked = sorted(signals.values(), key=lambda s: s.total_bps, reverse=True)
        for sig in ranked[: self.cfg.max_snapshot_flows]:
            device = devices.get(sig.device_id)
            device_rows.append({
                "hostname": device.hostname if device else sig.device_id,
                "kind": device.kind.value if device else "unknown",
                "trust": round(device.trust, 2) if device else None,
                "wan_down_mbps": round(sig.down_bps / 1e6, 2),
                "wan_up_mbps": round(sig.up_bps / 1e6, 2),
                "lan_mbps": round(sig.lan_bps / 1e6, 2),
                "flows": sig.flow_count,
                "unique_dst_ips": sig.unique_dst_ips,
                "unique_dst_ports": sig.unique_dst_ports,
                "lateral_flows": sig.lateral_flows,
                "avg_rtt_ms": sig.avg_rtt_ms,
                "retransmit_rate": sig.retransmit_rate,
                "top_app": sig.top_app,
                "class_mix": sig.class_mix,
            })

        return {
            "window_seconds": round(stats.window_seconds, 1),
            "link": {
                "down_mbps": round(stats.down_bps / 1e6, 2),
                "up_mbps": round(stats.up_bps / 1e6, 2),
                "lan_internal_mbps": round(stats.lan_bps / 1e6, 2),
                "down_capacity_mbps": metrics.link.downlink_mbps,
                "up_capacity_mbps": metrics.link.uplink_mbps,
                "down_utilization": round(stats.down_utilization, 3),
                "up_utilization": round(stats.up_utilization, 3),
                "avg_rtt_ms": stats.avg_rtt_ms,
                "retransmit_rate": stats.retransmit_rate,
                "active_flows": stats.flow_count,
                "active_devices": stats.device_count,
            },
            "traffic_class_mbps": {
                k: round(v / 1e6, 2) for k, v in stats.per_class_bps.items()
            },
            "devices": device_rows,
        }

    @staticmethod
    def _policy_text(optimizer: TrafficOptimizer | None) -> str:
        if optimizer is None or not optimizer.active:
            return "(aktif politika yok)"
        lines = []
        for action in optimizer.active.values():
            lines.append(f"- {action.kind.value} -> {action.target}: {action.reason}")
        return "\n".join(lines)

    # -------------------------------------------------------------------- analiz

    async def analyze(self, metrics: MetricsEngine, devices: dict[str, Device],
                      optimizer: TrafficOptimizer | None = None) -> AIReport:
        snapshot = self.build_snapshot(metrics, devices, optimizer)
        valid_targets = self._valid_targets(devices)
        prompt = ANALYST_USER.format(
            snapshot=json.dumps(snapshot, ensure_ascii=False, indent=2),
            active_policies=self._policy_text(optimizer),
            # Hedefleri isteme yazmak, doğrulayıcının düşürdüğü öneri sayısını
            # baştan azaltıyor: model listeden seçiyor, uydurmuyor.
            targets=", ".join(sorted(valid_targets)),
            thresholds=self._threshold_text(metrics, optimizer),
        )

        started = time.perf_counter()
        try:
            data = await self.provider.complete_json(ANALYST_SYSTEM, prompt)
            error = None
        except (LLMUnavailable, ValueError) as exc:
            log.warning("AI analizi başarısız: %s", exc)
            data = {}
            error = str(exc)
        latency = (time.perf_counter() - started) * 1000

        report = AIReport(
            id=new_id("rep"),
            ts=now(),
            summary=str(data.get("summary") or
                        ("Analiz üretilemedi." if error else "")),
            health_score=self._clamp_score(data.get("health_score"), snapshot),
            findings=self._calibrate_severity(
                self._clean_findings(data.get("findings")), metrics.link),
            recommendations=self._clean_recommendations(
                data.get("recommendations"), valid_targets),
            provider=self.provider.name,
            model=self.provider.model,
            latency_ms=round(latency, 1),
            error=error,
        )
        report.actions = self._to_actions(report.recommendations, devices)
        self.last_report = report
        return report

    async def ask(self, question: str, metrics: MetricsEngine,
                  devices: dict[str, Device],
                  optimizer: TrafficOptimizer | None = None) -> dict[str, Any]:
        """Yönetici için serbest metin soru-cevap."""
        snapshot = self.build_snapshot(metrics, devices, optimizer)
        prompt = QA_USER.format(
            snapshot=json.dumps(snapshot, ensure_ascii=False, indent=2),
            question=question,
        )
        started = time.perf_counter()
        try:
            answer = await self.provider.complete(QA_SYSTEM, prompt)
            error = None
        except LLMUnavailable as exc:
            answer = ""
            error = str(exc)
        return {
            "question": question,
            "answer": answer.strip(),
            "provider": self.provider.name,
            "model": self.provider.model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": error,
        }

    # ---------------------------------------------------------------- normalize

    @staticmethod
    def _clamp_score(value: Any, snapshot: dict[str, Any]) -> int:
        try:
            return max(0, min(100, int(float(value))))
        except (TypeError, ValueError):
            # Model skor vermediyse doluluktan kaba bir tahmin üret.
            util = snapshot.get("link", {}).get("down_utilization", 0.0)
            return max(0, min(100, int(100 - float(util) * 60)))

    @staticmethod
    def _clean_findings(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        valid = {s.value for s in Severity}
        out = []
        for item in raw[:10]:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity", "info")).lower()
            out.append({
                "title": str(item.get("title", "")).strip()[:200],
                "severity": severity if severity in valid else "info",
                "evidence": str(item.get("evidence", "")).strip()[:300],
            })
        return [f for f in out if f["title"]]

    # Eşiği olan metrikler. Model bu metrikleri kanıt gösterip önem derecesini
    # abartırsa (ölçüldü: down_utilization=0.175 için "critical"), kod düzeltir.
    _THRESHOLDED = ("down_utilization", "up_utilization")

    _EVIDENCE_PAIR = re.compile(
        r"([a-z_]+)\s*=\s*(\d+(?:\.\d+)?)")

    @classmethod
    def _calibrate_severity(cls, findings: list[dict[str, Any]],
                            link: LinkConfig) -> list[dict[str, Any]]:
        """Model önem derecesini eşiğe göre yeniden hesaplar.

        phi-4-mini sayısal karşılaştırmayı güvenilir yapamıyor: eşik istemde
        açıkça 0.80 yazılıyken 0.175 doluluğu "critical" olarak etiketledi
        (3/3 koşu). Önem derecesi ölçülebilir bir şey, o yüzden modele
        bırakılmıyor — 1. mimari ilke.

        Yalnızca eşiği olan metrikleri düzeltiyoruz; kanıtı tanımadığımız bir
        bulguyu olduğu gibi bırakıyoruz (yanlış olabilir ama uydurma bir eşikle
        düzeltmek daha kötü).
        """
        for f in findings:
            cited = {m: float(v) for m, v in
                     cls._EVIDENCE_PAIR.findall(str(f.get("evidence", "")))
                     if m in cls._THRESHOLDED}
            if not cited:
                continue
            worst = max(cited.values())
            if worst >= link.critical_threshold:
                correct = Severity.CRITICAL.value
            elif worst >= link.congestion_threshold:
                correct = Severity.HIGH.value
            else:
                correct = Severity.INFO.value
            if f["severity"] != correct:
                log.info("AI önem derecesi düzeltildi: %r %s -> %s (%s)",
                         f.get("title"), f["severity"], correct,
                         ", ".join(f"{k}={v}" for k, v in cited.items()))
                f["severity"] = correct
        return findings

    @staticmethod
    def _threshold_text(metrics: MetricsEngine,
                        optimizer: TrafficOptimizer | None) -> str:
        """Kural motorunun eşiklerini modele okunur biçimde verir.

        Eşikler `config.yaml`'da tek yerde duruyor; buradan türetiyoruz ki
        istem ile kural motoru ayrışmasın. Model eşiği bilmediğinde %17
        doluluğa "yüksek" diyordu (ölçüldü) — sayıyı vermek bunu kesiyor.
        """
        link = metrics.link
        lines = [
            f"- hat tıkanması: down_utilization veya up_utilization > "
            f"{link.congestion_threshold:.2f}",
            f"- kritik doygunluk: > {link.critical_threshold:.2f}",
            f"- hat kapasitesi: {link.downlink_mbps:.0f} Mbps indirme / "
            f"{link.uplink_mbps:.0f} Mbps yükleme",
        ]
        if optimizer is not None:
            lines.append(
                f"- tek cihaz bant tekeli: toplam bandın "
                f"{optimizer.cfg.hog_share_threshold:.0%}'inden fazlası")
        lines.append("- bu eşiklerin altındaki değerler normaldir")
        return "\n".join(lines)

    @staticmethod
    def _valid_targets(devices: dict[str, Device]) -> set[str]:
        """Bir önerinin işaret edebileceği tüm meşru hedefler."""
        return {d.hostname for d in devices.values()} | _TARGET_CLASSES

    @classmethod
    def _resolve_targets(cls, raw: str, valid: set[str]) -> list[str]:
        """Model çıktısındaki hedefi meşru hedeflere çözer.

        Model düzenli olarak üç şeyi karıştırıyor (12 koşuluk ölçümde her
        koşuda): iki hostname'i tek alana virgülle yazmak, metrik adını hedef
        sanmak (`lan_mbps`), kanıt dizesini hedefe kopyalamak
        (`trust=0.95 (srv-backup-01)`). Virgüllü hali kurtarılabilir —
        parçaların hepsi meşruysa ayrı önerilere açılır. Gerisi düşer.
        """
        name = raw.strip()
        if name in valid:
            return [name]
        parts = [p.strip() for p in name.split(",") if p.strip()]
        if len(parts) > 1 and all(p in valid for p in parts):
            return parts
        return []

    @classmethod
    def _clean_recommendations(cls, raw: Any,
                               valid: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        dropped: list[str] = []
        for item in raw[:10]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "advise")).lower().strip()
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5

            raw_target = str(item.get("target", "")).strip()[:120]
            targets = cls._resolve_targets(raw_target, valid)
            if not targets:
                # Hedefi olmayan öneri uygulanabilir değil; operatöre
                # gösterilmesi de yanıltıcı olur. Görünür şekilde düşür.
                dropped.append(raw_target)
                continue

            for target in targets:
                out.append({
                    "action": action if action in _ACTION_ALIASES else "advise",
                    "target": target,
                    "reason": str(item.get("reason", "")).strip()[:400],
                    "confidence": round(confidence, 2),
                })
        if dropped:
            log.warning("AI önerisi geçersiz hedef yüzünden düşürüldü: %s",
                        ", ".join(repr(d) for d in dropped))
        return out

    @staticmethod
    def _to_actions(recommendations: list[dict[str, Any]],
                    devices: dict[str, Device]) -> list[OptimizationAction]:
        """AI önerilerini sistemin aksiyon tipine çevirir.

        AI aksiyonları asla otomatik uygulanmaz — `applied=False` ile üretilir ve
        operatörün onayını bekler. Model halüsinasyon yapsa bile ağa dokunamaz.
        """
        by_hostname = {d.hostname: d.id for d in devices.values()}
        out = []
        for rec in recommendations:
            kind = _ACTION_ALIASES.get(rec["action"], ActionKind.ADVISE)
            target = by_hostname.get(rec["target"], rec["target"])
            out.append(OptimizationAction(
                id=new_id("act"), ts=now(), kind=kind, target=target,
                params={"suggested_by": "ai", "raw_target": rec["target"]},
                reason=rec["reason"], confidence=rec["confidence"],
                source="ai", applied=False,
            ))
        return out

    @staticmethod
    def report_alerts(report: AIReport) -> list[Alert]:
        """Yüksek önemli AI bulgularını uyarıya çevirir."""
        out = []
        for finding in report.findings:
            severity = Severity(finding["severity"])
            if severity.rank < Severity.MEDIUM.rank:
                continue
            out.append(Alert(
                id=new_id("alr"), ts=now(), severity=severity, source="ai-analyst",
                title=finding["title"], detail=finding["evidence"],
                meta={"report_id": report.id, "model": report.model},
            ))
        return out
