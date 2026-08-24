"""AI trafik analisti: metrikleri modele anlaşılır bir özet olarak sunar,
dönen yapılandırılmış analizi sistemin ortak tiplerine çevirir."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.config import AIConfig
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
from ..traffic.flowpolicy import DEFAULT_POLICY, FlowPolicy
from .prompts import (
    ANALYST_SYSTEM,
    ANALYST_USER,
    POLICY_SYSTEM,
    POLICY_USER,
    QA_SYSTEM,
    QA_USER,
)
from .provider import LLMProvider, LLMUnavailable, extract_json

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
    "reroute": ActionKind.REROUTE,
    "failover": ActionKind.REROUTE,
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


def _snapshot_json(snapshot: dict[str, Any]) -> str:
    """Snapshot'ı modele gidecek biçimde metne çevirir.

    Girinti yok: `indent=2` sırf boşluk için ~%20 token harcıyordu ve model
    için hiçbir şey değiştirmiyor. Budama da isteme giden metnin **aynısını**
    ölçmeli, yoksa bütçe tutmuyor.
    """
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


class AIAnalyst:
    def __init__(self, cfg: AIConfig, provider: LLMProvider) -> None:
        self.cfg = cfg
        self.provider = provider
        self.last_report: AIReport | None = None

    # ------------------------------------------------------------- anlık görüntü

    @staticmethod
    def _trim_snapshot(snapshot: dict[str, Any], max_chars: int) -> dict[str, Any]:
        """Snapshot'ı karakter bütçesine sığdırır — en az trafikli cihazı atarak.

        Neden sert tavan: model bağlamı 4096 token ve `lm_head` çıktısı istem
        uzunluğuyla büyüyor. Yük altında snapshot şişince ONNX Runtime 1.2 GB
        ayırmaya çalışıp düştü (ölçüldü). Cihaz sayısına güvenmek yetmiyor —
        bir cihazın satırı da şişebilir. Bütçe karakterden gidiyor.

        Cihazlar zaten trafiğe göre sıralı; kuyruktan atmak en az bilgi
        kaybettiren kesim. Kaç tanesinin atıldığı snapshot'a yazılıyor ki
        model eksik veriye baktığını bilsin.
        """
        if len(_snapshot_json(snapshot)) <= max_chars:
            return snapshot
        rows = snapshot.get("devices") or []
        dropped = 0
        while len(rows) > 1 and                 len(json.dumps(snapshot, ensure_ascii=False)) > max_chars:
            rows.pop()
            dropped += 1
        if dropped:
            snapshot["devices_omitted"] = dropped
        return snapshot

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
            # Sıfır olan alanlar yazılmıyor ve her sayı yuvarlanıyor.
            # Yuvarlamamak ölçülebilir zarar veriyordu: `avg_rtt_ms` ham float
            # olarak gidiyordu (46.432198712...), on cihazla bu tek başına
            # yüzlerce token. Uzun istem `lm_head` çıktısını büyütüyor ve
            # ONNX Runtime 1.2 GB ayırmaya çalışıp düşüyordu.
            row: dict[str, Any] = {
                "hostname": device.hostname if device else sig.device_id,
                "kind": device.kind.value if device else "unknown",
                "wan_down_mbps": round(sig.down_bps / 1e6, 2),
                "wan_up_mbps": round(sig.up_bps / 1e6, 2),
                "flows": sig.flow_count,
                "avg_rtt_ms": round(sig.avg_rtt_ms, 1),
                "top_app": sig.top_app,
                "class_mix": {k: round(v, 2) for k, v in
                              (sig.class_mix or {}).items() if v >= 0.01},
            }
            if device is not None:
                row["trust"] = round(device.trust, 2)
            for key, value, digits in (
                ("lan_mbps", sig.lan_bps / 1e6, 2),
                ("retransmit_rate", sig.retransmit_rate, 4),
            ):
                if value:
                    row[key] = round(value, digits)
            for key, value in (("unique_dst_ips", sig.unique_dst_ips),
                               ("unique_dst_ports", sig.unique_dst_ports),
                               ("lateral_flows", sig.lateral_flows)):
                if value:
                    row[key] = value
            device_rows.append(row)

        snapshot: dict[str, Any] = {
            "window_seconds": round(stats.window_seconds, 1),
            "link": {
                "down_mbps": round(stats.down_bps / 1e6, 2),
                "up_mbps": round(stats.up_bps / 1e6, 2),
                "lan_internal_mbps": round(stats.lan_bps / 1e6, 2),
                "down_capacity_mbps": metrics.link.downlink_mbps,
                "up_capacity_mbps": metrics.link.uplink_mbps,
                "down_utilization": round(stats.down_utilization, 3),
                "up_utilization": round(stats.up_utilization, 3),
                "avg_rtt_ms": round(stats.avg_rtt_ms, 1),
                "retransmit_rate": round(stats.retransmit_rate, 4),
                "active_flows": stats.flow_count,
                "active_devices": stats.device_count,
            },
            "traffic_class_mbps": {
                k: round(v / 1e6, 2) for k, v in stats.per_class_bps.items()
            },
            "devices": device_rows,
        }
        return self._trim_snapshot(snapshot, self.cfg.max_snapshot_chars)

    def _policy_text(optimizer: TrafficOptimizer | None) -> str:
        if optimizer is None or not optimizer.active:
            return "(aktif politika yok)"
        lines = []
        for action in optimizer.active.values():
            lines.append(f"- {action.kind.value} -> {action.target}: {action.reason}")
        return "\n".join(lines)

    # -------------------------------------------------------------------- analiz

    async def analyze(self, metrics: MetricsEngine, devices: dict[str, Device],
                      optimizer: TrafficOptimizer | None = None,
                      alerts: list[Alert] | None = None,
                      flow_plan: Any = None) -> AIReport:
        """Verilen olguları açıklar — yeni sorun tespit etmez.

        Model artık bağımsız bir tespit motoru değil: bulguyu kural motoru ve
        akış çözücüsü üretiyor, buradaki iş onları operatörün okuyacağı hale
        getirmek. Sebebi ölçüldü — phi-4-mini sayısal karşılaştırmayı
        güvenilir yapamıyor (`down_utilization=0.175` iken "critical" dedi,
        eşik istemde açıkça yazılıyken). Derecelendirme koda alındı.
        """
        snapshot = self.build_snapshot(metrics, devices, optimizer)
        facts = self._facts(alerts, flow_plan)
        valid_targets = self._valid_targets(devices)
        prompt = ANALYST_USER.format(
            snapshot=_snapshot_json(snapshot),
            alerts=facts["alerts_text"],
            flow=facts["flow_text"],
            # Hedefleri isteme yazmak, doğrulayıcının düşürdüğü öneri sayısını
            # baştan azaltıyor: model listeden seçiyor, uydurmuyor.
            targets=", ".join(sorted(valid_targets)),
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
            findings=self._attach_severity(
                self._clean_findings(data.get("findings")), facts["rows"]),
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

    # --------------------------------------------------------------- politika

    async def decide_policy(
        self, metrics: MetricsEngine, topology: Any,
        alerts: list[Alert] | None = None,
        current: FlowPolicy | None = None,
        clock_hour: int | None = None,
    ) -> tuple[FlowPolicy, list[str], str]:
        """Duruma uygun **hedefi** modele kurdurur.

        Döner: (politika, sorunlar, sonuç). Sonuç: kabul | reddedildi | korundu.

        **Modelin sisteme dokunduğu tek yer burası** ve kapı bilerek dar:
        model sayı üretmiyor, sıralama ve ağırlık üretiyor — sayıyı LP
        hesaplıyor. Ölçüldü ki bu model %17.5 doluluğu "critical" diyor ve bir
        sınıf payını %122 olarak raporluyor; aritmetiği yok. Ama "gece
        yedekleme penceresi, bulk'u yukarı al" diyebiliyor. İstediğimiz o.

        Model çökerse, geçersiz üretirse veya sağlayıcı yoksa **mevcut politika
        korunuyor.** Varsayılana dönmek daha "güvenli" görünür ama değil:
        hedefi tam da modelin güvenilmediği anda sıfırlamak ağı sallar.
        """
        mevcut = current or DEFAULT_POLICY
        if self.provider is None:
            return mevcut, ["sağlayıcı yok"], "korundu"

        try:
            durum = self._situation(metrics, topology, alerts, clock_hour)
        except Exception as exc:
            log.exception("Durum özeti kurulamadı")
            return mevcut, [f"durum özeti kurulamadı: {exc}"], "korundu"

        user = POLICY_USER.format(
            saat=durum["saat"], cikislar=durum["cikislar"],
            durum=durum["durum"], sinif_payi=durum["sinif_payi"],
            uyarilar=durum["uyarilar"],
            mevcut=json.dumps(mevcut.to_dict(), ensure_ascii=False,
                              separators=(",", ":")),
        )

        try:
            # `complete_json` json_mode'u açıyor ve model konuşkanlık yaparsa
            # gövdedeki nesneyi kurtarıyor — küçük modellerde sık gerekiyor.
            data = await self.provider.complete_json(POLICY_SYSTEM, user)
        except Exception as exc:
            log.warning("Politika isteği başarısız: %s", exc)
            return mevcut, [f"model yanıt vermedi: {exc}"], "korundu"

        if not data:
            return mevcut, ["model geçerli JSON döndürmedi"], "korundu"

        politika, sorunlar = FlowPolicy.validate(data)
        if politika is None:
            log.warning("AI politikası reddedildi: %s", sorunlar)
            return mevcut, sorunlar, "reddedildi"

        politika.source = "ai"
        return politika, sorunlar, "kabul"

    def _situation(self, metrics: MetricsEngine, topology: Any,
                   alerts: list[Alert] | None,
                   clock_hour: int | None) -> dict[str, str]:
        """Modele gidecek durum özeti — kısa ve **aritmetiksiz**.

        Yüzdeler, Mbps ve "YÜKSEK / normal" yargıları burada hazır metne
        çevriliyor; modelden oran hesaplaması istenmiyor. Tek yaptığı okumak
        ve durum yargısı vermek — becerebildiği iş bu.
        """
        st = metrics.link_stats()
        saat = clock_hour if clock_hour is not None else time.localtime().tm_hour
        if 8 <= saat < 19:
            gun = "mesai saati"
        elif 19 <= saat < 23:
            gun = "akşam"
        else:
            gun = "gece, ofis büyük ihtimalle boş"

        satirlar = []
        for e in getattr(topology, "edges", []):
            if getattr(e, "kind", "") != "wan" or e.src != "internet":
                continue
            etiket = [f"{e.dst}: {e.capacity_mbps:.0f} Mbps indirme",
                      f"{e.latency_ms:.0f} ms gecikme"]
            etiket.append(f"SAYAÇLI ({e.cost_per_gb:.1f} birim/GB)"
                          if e.cost_per_gb > 0 else "ücretsiz")
            if e.health < 0.99:
                etiket.append(f"BOZUK (sağlık %{e.health * 100:.0f})")
            satirlar.append("- " + ", ".join(etiket))

        durum = [
            f"- indirme hattı doluluğu: %{st.down_utilization * 100:.0f}",
            f"- yükleme hattı doluluğu: %{st.up_utilization * 100:.0f}",
            f"- ortalama gecikme: {st.avg_rtt_ms:.0f} ms "
            f"({'YÜKSEK' if st.avg_rtt_ms > 120 else 'normal'})",
            f"- yeniden gönderim: %{st.retransmit_rate * 100:.1f} "
            f"({'YÜKSEK' if st.retransmit_rate > 0.02 else 'normal'})",
        ]

        toplam = sum(st.per_class_bps.values()) or 1.0
        paylar = [f"- {k}: %{v / toplam * 100:.0f}"
                  for k, v in sorted(st.per_class_bps.items(),
                                     key=lambda kv: -kv[1]) if v > 0]
        uyari = [f"- {a.title}" for a in (alerts or [])[-5:]] or ["- yok"]

        return {
            "saat": f"{saat:02d}:00 ({gun})",
            "cikislar": "\n".join(satirlar) or "- bilinmiyor",
            "durum": "\n".join(durum),
            "sinif_payi": "\n".join(paylar) or "- trafik yok",
            "uyarilar": "\n".join(uyari),
        }

    async def ask(self, question: str, metrics: MetricsEngine,
                  devices: dict[str, Device],
                  optimizer: TrafficOptimizer | None = None) -> dict[str, Any]:
        """Yönetici için serbest metin soru-cevap."""
        snapshot = self.build_snapshot(metrics, devices, optimizer)
        prompt = QA_USER.format(
            snapshot=_snapshot_json(snapshot),
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
            out.append({
                "title": str(item.get("title", "")).strip()[:200],
                # Derece modelden alınmıyor. Eşleşen bir olgu varsa onun
                # derecesi, yoksa "info". Model uydurduğu bir sorunu yüksek
                # dereceli gösteremiyor — uyarı akışı temiz kalıyor.
                "severity": "info",
                "evidence": str(item.get("explanation")
                                or item.get("evidence", "")).strip()[:400],
            })
        return [f for f in out if f["title"]]

    @staticmethod
    def _attach_severity(findings: list[dict[str, Any]],
                         facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Modelin açıkladığı bulguya, kaynağındaki dereceyi geri takar.

        Eşleştirme başlık üzerinden ve gevşek: model başlığı birebir
        kopyalamıyor. Eşleşmeyen bulgu `info` kalıyor, yani uyarıya dönüşmüyor.
        """
        def tokens(t: str) -> set[str]:
            """Başlığı kök benzeri parçalara ayırır.

            Alt dize karşılaştırması Türkçe eklerde tutmuyordu: kural motoru
            "Yükleme hattında tıkanma" derken model "Yükleme hattındaki
            tıkanma" yazıyor ve hiçbir eşleşme olmuyordu. Kelimelerin ilk 5
            harfini almak eki düşürüyor ("hattında"/"hattındaki" → "hattı").
            """
            words = "".join(ch if ch.isalnum() else " " for ch in t.lower())
            return {w[:5] for w in words.split() if len(w) >= 4}

        indexed = [(tokens(f["title"]), f) for f in facts]
        for finding in findings:
            key = tokens(finding["title"])
            match = None
            best = 0.0
            for fact_tokens, fact in indexed:
                if not fact_tokens or not key:
                    continue
                ortak = len(key & fact_tokens)
                oran = ortak / min(len(key), len(fact_tokens))
                # İki anlamlı kelime ortaksa ya da küçük kümenin çoğu
                # örtüşüyorsa aynı olgudan bahsediyorlar.
                if (ortak >= 2 or oran >= 0.6) and oran > best:
                    best, match = oran, fact
            if match is not None:
                finding["severity"] = match["severity"]
                if not finding["evidence"]:
                    finding["evidence"] = match["evidence"]
        return findings


    @staticmethod
    def _facts(alerts: list[Alert] | None, flow_plan: Any) -> dict[str, Any]:
        """Modele verilecek olguları derler ve dereceyi kaydeder.

        Önem derecesi burada sabitleniyor: model açıklama yazacak, derece
        kural motorundan gelecek. Ölçüldü ki model derecelendirmeyi
        yapamıyor; kural motoru zaten yapıyor.
        """
        rows: list[dict[str, Any]] = []
        lines: list[str] = []
        for alert in (alerts or [])[:10]:
            rows.append({"title": alert.title,
                         "severity": alert.severity.value,
                         "evidence": alert.detail})
            lines.append(f"- [{alert.severity.value}] {alert.title}: "
                         f"{alert.detail}")

        flow_lines: list[str] = []
        if flow_plan is not None:
            talep = sum(a.demand.mbps for a in flow_plan.allocations)
            verilen = sum(a.granted_mbps for a in flow_plan.allocations)
            flow_lines.append(f"- toplam talep {talep:.1f} Mbps, "
                              f"karşılanan {verilen:.1f} Mbps")
            for b in flow_plan.bottlenecks()[:4]:
                flow_lines.append(f"- doymuş bağlantı {b['edge']}: "
                                  f"{b['load_mbps']:.1f}/{b['capacity_mbps']:.0f} Mbps")
                rows.append({"title": f"Doymuş bağlantı: {b['edge']}",
                             "severity": Severity.HIGH.value,
                             "evidence": f"{b['load_mbps']:.1f}/"
                                         f"{b['capacity_mbps']:.0f} Mbps"})
            for r in flow_plan.pullbacks()[:5]:
                flow_lines.append(
                    f"- {r['device']} ({r['direction']}): istediği "
                    f"{r['demand_mbps']:.1f}, verilebilen "
                    f"{r['granted_mbps']:.1f} Mbps")

        return {
            "rows": rows,
            "alerts_text": "\n".join(lines) or "(uyarı yok)",
            "flow_text": "\n".join(flow_lines) or "(akış çözümü yok)",
        }

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
                    # AI önerisi her zaman `advise`. Uygulanabilir aksiyonun
                    # sayısını akış çözücüsü veriyor; modelin oraya ikinci bir
                    # sayı yazması tam da kaldırdığımız çelişkiyi geri getirir.
                    "action": "advise",
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
