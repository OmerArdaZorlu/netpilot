"""SQLite kalıcı katmanı.

Bilinçli tasarım kararı: *her* akış diske yazılmaz. Simülasyonda bile saniyede
onlarca akış üretiliyor; hepsini saklamak günde milyonlarca satır demek ve
hiçbir sorguya değmez. Bunun yerine:
  - metrik örnekleri (zaman serisi)  -> her tikte
  - uyarı / aksiyon / AI raporu      -> olay bazlı
  - akışlar                          -> sadece "dikkat çekici" olanlar
    (senaryo etiketli, LAN içi tarama, bilinmeyen uygulama)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..core.models import Alert, Flow, LinkStats, OptimizationAction, now

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS metric_samples (
    ts REAL PRIMARY KEY,
    down_bps REAL, up_bps REAL,
    down_util REAL, up_util REAL,
    avg_rtt_ms REAL, retransmit_rate REAL,
    flow_count INTEGER, device_count INTEGER,
    per_class TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY, ts REAL, severity TEXT, source TEXT,
    title TEXT, detail TEXT, device_id TEXT, meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, ts REAL, kind TEXT, target TEXT,
    params TEXT, reason TEXT, confidence REAL, source TEXT, applied INTEGER
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC);

CREATE TABLE IF NOT EXISTS ai_reports (
    id TEXT PRIMARY KEY, ts REAL, summary TEXT, health_score INTEGER,
    findings TEXT, recommendations TEXT, provider TEXT, model TEXT,
    latency_ms REAL, error TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_ts ON ai_reports(ts DESC);

-- Akış çözücüsünün kararları. Tam plan (kenar kenar döküm) saklanmıyor;
-- saklanan, sonradan sorulacak şey: ne kadar talep vardı, ne kadarı
-- karşılandı, kimden ne kadar kısıldı, hangi kenar doydu.
CREATE TABLE IF NOT EXISTS flow_plans (
    id TEXT PRIMARY KEY, ts REAL,
    demand_mbps REAL, granted_mbps REAL,
    feasible INTEGER, note TEXT,
    pullbacks TEXT, bottlenecks TEXT
);
CREATE INDEX IF NOT EXISTS idx_flowplans_ts ON flow_plans(ts DESC);

CREATE TABLE IF NOT EXISTS notable_flows (
    id TEXT PRIMARY KEY, ts REAL, device_id TEXT, src_ip TEXT, dst_ip TEXT,
    src_port INTEGER, dst_port INTEGER, proto TEXT, app TEXT,
    traffic_class TEXT, direction TEXT, bytes_down INTEGER, bytes_up INTEGER,
    packets INTEGER, rtt_ms REAL, flags TEXT, egress TEXT
);
CREATE INDEX IF NOT EXISTS idx_flows_ts ON notable_flows(ts DESC);
CREATE INDEX IF NOT EXISTS idx_flows_device ON notable_flows(device_id, ts DESC);
"""


def is_notable(flow: Flow) -> bool:
    return bool(flow.flags) or flow.direction.value == "lateral" or flow.app == "unknown"


class Storage:
    def __init__(self, path: Path, retain_hours: int = 24) -> None:
        self.path = path
        self.retain_hours = retain_hours
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ yaşam

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(
            sqlite3.connect, str(self.path), 5.0, 0, "DEFERRED", False
        )
        self._conn.row_factory = sqlite3.Row
        await asyncio.to_thread(self._conn.executescript, SCHEMA)
        await asyncio.to_thread(self._conn.commit)
        log.info("Veritabanı hazır: %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Storage.open() çağrılmadı")
        return self._conn

    async def _write(self, sql: str, rows: list[tuple]) -> None:
        if not rows:
            return
        conn = self._require()
        async with self._lock:
            await asyncio.to_thread(self._executemany, conn, sql, rows)

    @staticmethod
    def _executemany(conn: sqlite3.Connection, sql: str, rows: list[tuple]) -> None:
        conn.executemany(sql, rows)
        conn.commit()

    async def _read(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._require()
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ yazma

    async def save_sample(self, stats: LinkStats) -> None:
        await self._write(
            "INSERT OR REPLACE INTO metric_samples VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(
                now(), stats.down_bps, stats.up_bps,
                stats.down_utilization, stats.up_utilization,
                stats.avg_rtt_ms, stats.retransmit_rate,
                stats.flow_count, stats.device_count,
                json.dumps(stats.per_class_bps),
            )],
        )

    async def save_alerts(self, alerts: Iterable[Alert]) -> None:
        await self._write(
            "INSERT OR REPLACE INTO alerts VALUES (?,?,?,?,?,?,?,?)",
            [(a.id, a.ts, a.severity.value, a.source, a.title, a.detail,
              a.device_id, json.dumps(a.meta, ensure_ascii=False))
             for a in alerts],
        )

    async def save_actions(self, actions: Iterable[OptimizationAction]) -> None:
        await self._write(
            "INSERT OR REPLACE INTO actions VALUES (?,?,?,?,?,?,?,?,?)",
            [(a.id, a.ts, a.kind.value, a.target,
              json.dumps(a.params, ensure_ascii=False), a.reason,
              a.confidence, a.source, int(a.applied))
             for a in actions],
        )

    async def save_report(self, report) -> None:
        await self._write(
            "INSERT OR REPLACE INTO ai_reports VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(report.id, report.ts, report.summary, report.health_score,
              json.dumps(report.findings, ensure_ascii=False),
              json.dumps(report.recommendations, ensure_ascii=False),
              report.provider, report.model, report.latency_ms, report.error)],
        )

    async def save_flow_plan(self, plan_id: str, ts: float, plan) -> None:
        """Akış planının özetini saklar.

        Tam plan yüzlerce satır kenar dökümü; saklanması pahalı ve sonradan
        sorulan şey o değil. Saklanan: toplamlar, geri çekmeler, darboğazlar —
        "geçen hafta hangi cihaz en çok kısıldı" sorusunu cevaplayan alanlar.
        """
        await self._write(
            "INSERT OR REPLACE INTO flow_plans VALUES (?,?,?,?,?,?,?,?)",
            [(plan_id, ts,
              sum(a.demand.mbps for a in plan.allocations),
              sum(a.granted_mbps for a in plan.allocations),
              1 if plan.feasible else 0, plan.note,
              json.dumps(plan.pullbacks(), ensure_ascii=False),
              json.dumps(plan.bottlenecks(), ensure_ascii=False))],
        )

    async def recent_flow_plans(self, limit: int = 50) -> list[dict]:
        rows = await self._read(
            "SELECT id, ts, demand_mbps, granted_mbps, feasible, note, "
            "pullbacks, bottlenecks FROM flow_plans ORDER BY ts DESC LIMIT ?",
            (limit,))
        return [{
            "id": r["id"], "ts": r["ts"],
            "demand_mbps": round(r["demand_mbps"] or 0.0, 2),
            "granted_mbps": round(r["granted_mbps"] or 0.0, 2),
            "feasible": bool(r["feasible"]), "note": r["note"],
            "pullbacks": json.loads(r["pullbacks"] or "[]"),
            "bottlenecks": json.loads(r["bottlenecks"] or "[]"),
        } for r in rows]

    async def save_notable_flows(self, flows: Iterable[Flow]) -> None:
        rows = [
            (f.id, f.ts, f.device_id, f.src_ip, f.dst_ip, f.src_port, f.dst_port,
             f.proto, f.app, f.traffic_class.value, f.direction.value,
             f.bytes_down, f.bytes_up, f.packets, f.rtt_ms, json.dumps(f.flags),
             f.egress)
            for f in flows if is_notable(f)
        ]
        await self._write(
            "INSERT OR REPLACE INTO notable_flows VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    # ------------------------------------------------------------------ okuma

    async def recent_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._read(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["meta"] = json.loads(r["meta"] or "{}")
        return rows

    async def recent_actions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._read(
            "SELECT * FROM actions ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["params"] = json.loads(r["params"] or "{}")
            r["applied"] = bool(r["applied"])
        return rows

    async def recent_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._read(
            "SELECT * FROM ai_reports ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["findings"] = json.loads(r["findings"] or "[]")
            r["recommendations"] = json.loads(r["recommendations"] or "[]")
        return rows

    async def recent_notable_flows(self, limit: int = 100,
                                   device_id: str | None = None) -> list[dict[str, Any]]:
        if device_id:
            rows = await self._read(
                "SELECT * FROM notable_flows WHERE device_id=? ORDER BY ts DESC LIMIT ?",
                (device_id, limit))
        else:
            rows = await self._read(
                "SELECT * FROM notable_flows ORDER BY ts DESC LIMIT ?", (limit,))
        for r in rows:
            r["flags"] = json.loads(r["flags"] or "[]")
        return rows

    async def samples_since(self, seconds: float) -> list[dict[str, Any]]:
        rows = await self._read(
            "SELECT * FROM metric_samples WHERE ts >= ? ORDER BY ts ASC",
            (now() - seconds,))
        for r in rows:
            r["per_class"] = json.loads(r["per_class"] or "{}")
        return rows

    # ------------------------------------------------------------------ bakım

    async def prune(self) -> int:
        cutoff = now() - self.retain_hours * 3600
        conn = self._require()
        async with self._lock:
            def _prune() -> int:
                total = 0
                for table in ("metric_samples", "alerts", "actions",
                              "ai_reports", "notable_flows", "flow_plans"):
                    cur = conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
                    total += cur.rowcount
                conn.commit()
                return total
            return await asyncio.to_thread(_prune)
