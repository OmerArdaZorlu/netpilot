"""Yerel LLM sağlayıcı katmanı.

Tek arayüz, iki uygulama:
  * OllamaProvider — gerçek yerel model (phi4-mini, llama3.2, qwen2.5 ...)
  * MockProvider   — model kurulu değilken sistemin çalışmaya devam etmesi için

Sistemin hiçbir yeri Ollama'yı doğrudan tanımaz; hep bu arayüzden geçer.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..core.config import AIConfig

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"
    model: str = "-"

    @abstractmethod
    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        ...

    async def complete_json(self, system: str, prompt: str) -> dict[str, Any]:
        """JSON bekleyen çağrılar için — model konuşkanlık yaparsa kurtarır."""
        raw = await self.complete(system, prompt, json_mode=True)
        return extract_json(raw)

    async def aclose(self) -> None:
        return None


def extract_json(raw: str) -> dict[str, Any]:
    """Metnin içinden ilk geçerli JSON nesnesini söker.

    Küçük modeller sık sık ```json bloğu veya "İşte analiz:" gibi bir önsöz
    ekler; bunları temizlemeden parse etmek üretimde en sık kırılan yer.
    """
    text = raw.strip()
    if not text:
        raise ValueError("model boş yanıt döndürdü")

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError(f"yanıtta JSON bulunamadı: {raw[:200]!r}")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:idx + 1])
    raise ValueError(f"JSON tamamlanmamış: {raw[:200]!r}")


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg
        self.model = cfg.ollama_model or cfg.model
        self._client = httpx.AsyncClient(
            base_url=(cfg.base_url or cfg.ollama_base_url).rstrip("/"),
            timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        )

    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": 8192,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama çağrısı başarısız: {exc}") from exc

        data = resp.json()
        return (data.get("message") or {}).get("content", "")

    async def health(self) -> dict[str, Any]:
        try:
            resp = await self._client.get("/api/tags", timeout=5.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"provider": self.name, "ok": False, "model": self.model,
                    "error": str(exc)}

        models = [m.get("name", "") for m in resp.json().get("models", [])]
        # "phi4-mini" istendiğinde "phi4-mini:latest" de kabul edilmeli
        present = any(m == self.model or m.startswith(f"{self.model}:")
                      for m in models)
        return {
            "provider": self.name,
            "ok": present,
            "model": self.model,
            "available_models": models,
            "error": None if present else
                     f"model '{self.model}' indirilmemiş — `ollama pull {self.model}`",
        }

    async def aclose(self) -> None:
        await self._client.aclose()


class MockProvider(LLMProvider):
    """Model yokken deterministik, işe yarar bir çıktı üretir.

    Amaç sahte zeka değil: boru hattının (snapshot -> analiz -> rapor -> UI)
    model olmadan da uçtan uca test edilebilmesi.
    """

    name = "mock"
    model = "rule-based-stub"

    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        return json.dumps(self._analyze(prompt), ensure_ascii=False)

    async def health(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True, "model": self.model,
                "error": None,
                "note": "Yerel model bulunamadı; kural tabanlı yedek kullanılıyor."}

    def _analyze(self, prompt: str) -> dict[str, Any]:
        snapshot: dict[str, Any] = {}
        try:
            snapshot = extract_json(prompt)
        except ValueError:
            pass

        link = snapshot.get("link", {})
        down = float(link.get("down_utilization", 0) or 0)
        up = float(link.get("up_utilization", 0) or 0)
        rtt = float(link.get("avg_rtt_ms", 0) or 0)

        findings: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        if down >= 0.8:
            findings.append({
                "title": "İndirme hattı tıkanmış",
                "severity": "high" if down >= 0.94 else "medium",
                "evidence": f"down_utilization={down:.2f}",
            })
            recommendations.append({
                "action": "defer",
                "target": "bulk",
                "reason": "Toplu transferleri yoğunluk dışına kaydır.",
                "confidence": 0.8,
            })
        if up >= 0.8:
            findings.append({
                "title": "Yükleme hattı dar boğaz",
                "severity": "medium",
                "evidence": f"up_utilization={up:.2f}",
            })
        if rtt > 120:
            findings.append({
                "title": "Gecikme yükseldi",
                "severity": "medium",
                "evidence": f"avg_rtt_ms={rtt:.0f}",
            })
            recommendations.append({
                "action": "prioritize",
                "target": "realtime",
                "reason": "Gecikmeye duyarlı trafiği koru.",
                "confidence": 0.75,
            })

        if not findings:
            summary = (f"Ağ sağlıklı. İndirme %{down * 100:.0f}, yükleme "
                       f"%{up * 100:.0f} doluluk, RTT {rtt:.0f} ms.")
        else:
            summary = (f"{len(findings)} bulgu var; indirme %{down * 100:.0f}, "
                       f"yükleme %{up * 100:.0f} doluluk, RTT {rtt:.0f} ms.")

        return {
            "summary": summary,
            "health_score": max(0, min(100, int(100 - down * 45 - up * 25
                                               - min(rtt, 300) / 10))),
            "findings": findings,
            "recommendations": recommendations,
        }


async def _build(kind: str, cfg: AIConfig) -> LLMProvider:
    if kind == "foundry":
        # Döngüsel içe aktarmayı önlemek için burada import ediliyor.
        from .foundry import FoundryLocalProvider
        return FoundryLocalProvider(cfg)
    if kind == "ollama":
        return OllamaProvider(cfg)
    return MockProvider()


async def create_provider(cfg: AIConfig) -> LLMProvider:
    """İstenen sağlayıcıyı kurar; `auto` sırayla Foundry → Ollama → mock dener.

    Sağlayıcı seçimi hiçbir zaman sistemi durdurmaz: hiçbiri hazır değilse mock
    devreye girer ve kural motoru çalışmaya devam eder.
    """
    choice = (cfg.provider or "auto").lower()

    if choice == "mock":
        return MockProvider()

    if choice in ("foundry", "ollama"):
        provider = await _build(choice, cfg)
        try:
            status = await provider.health()
        except LLMUnavailable as exc:
            status = {"ok": False, "error": str(exc)}
        if not status.get("ok"):
            log.warning("%s sağlıklı değil ama provider=%s zorlandı: %s",
                        choice, choice, status.get("error"))
        return provider

    for kind in ("foundry", "ollama"):
        provider = await _build(kind, cfg)
        try:
            status = await provider.health()
        except LLMUnavailable as exc:
            status = {"ok": False, "error": str(exc)}
        if status.get("ok"):
            log.info("Yerel model hazır: %s / %s", kind, provider.model)
            return provider
        log.info("%s kullanılamıyor: %s", kind, status.get("error"))
        await provider.aclose()

    log.warning("Hiçbir yerel model hazır değil — mock sağlayıcıya düşülüyor.")
    return MockProvider()
