"""Microsoft Foundry Local sağlayıcısı.

Foundry Local, modeli ONNX Runtime üzerinde yerelde koşturur ve OpenAI uyumlu
bir REST ucu açar. Ollama'dan iki farkı var:

  * **Uç nokta dinamiktir.** Servis her başlatıldığında farklı bir port
    seçebilir, o yüzden sabit URL varsaymak yerine `foundry service status`
    çıktısından keşfediyoruz (veya config'te açıkça verilirse onu kullanıyoruz).
  * **Model adlandırması farklıdır.** Foundry takma adları tire ile ayrılır
    (`phi-4-mini`), Ollama'da aynı model `phi4-mini` olarak geçer.

Donanım seçimi (CPU / GPU / NPU) Foundry'nin kendi işi; biz model takma adını
veriyoruz, o makineye uygun varyantı yüklüyor.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

import httpx

from ..core.config import AIConfig
from .provider import LLMProvider, LLMUnavailable

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://[\w\.\-]+:\d+")
CLI_TIMEOUT = 45.0


async def _run_cli(*args: str, timeout: float = CLI_TIMEOUT) -> tuple[int, str]:
    """`foundry` CLI'ını çağırır. CLI yoksa (127, "") döner."""
    exe = shutil.which("foundry")
    if exe is None:
        return 127, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        log.warning("foundry %s başarısız: %s", " ".join(args), exc)
        return 1, ""
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace")


class FoundryLocalProvider(LLMProvider):
    name = "foundry"

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self._base_url: str | None = cfg.base_url.strip() or None
        self._client: httpx.AsyncClient | None = None

    # --------------------------------------------------------------- keşif

    async def base_url(self) -> str:
        """Servis ucunu bulur; gerekiyorsa servisi başlatır."""
        if self._base_url:
            return self._base_url

        url = await self._status_url()
        if url is None:
            # Servis kapalıysa bir kez başlatmayı dene.
            code, _ = await _run_cli("service", "start")
            if code == 127:
                raise LLMUnavailable(
                    "foundry CLI bulunamadı — Foundry Local kurulu değil")
            url = await self._status_url()

        if url is None:
            raise LLMUnavailable(
                "Foundry Local servisi çalışmıyor — `foundry service start` dene")

        self._base_url = url
        log.info("Foundry Local ucu: %s", url)
        return url

    async def _status_url(self) -> str | None:
        code, out = await _run_cli("service", "status")
        if code == 127:
            raise LLMUnavailable(
                "foundry CLI bulunamadı — Foundry Local kurulu değil")
        match = URL_PATTERN.search(out)
        return match.group(0).rstrip("/") if match else None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            base = await self.base_url()
            self._client = httpx.AsyncClient(
                base_url=base,
                timeout=httpx.Timeout(self.cfg.timeout_seconds, connect=10.0),
            )
        return self._client

    # -------------------------------------------------------------- çağrı

    async def complete(self, system: str, prompt: str,
                       json_mode: bool = False) -> str:
        client = await self.client()
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.cfg.temperature,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            # Destekleyen modellerde çıktıyı JSON'a zorlar. Desteklenmezse
            # sunucu alanı yok sayar; extract_json zaten dağınık çıktıyı kurtarır.
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = await client.post("/v1/chat/completions", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Foundry Local çağrısı başarısız: {exc}") from exc

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("Foundry Local boş yanıt döndürdü")
        return (choices[0].get("message") or {}).get("content", "")

    # -------------------------------------------------------------- sağlık

    async def health(self) -> dict[str, Any]:
        try:
            client = await self.client()
            resp = await client.get("/v1/models", timeout=15.0)
            resp.raise_for_status()
        except LLMUnavailable as exc:
            return {"provider": self.name, "ok": False, "model": self.model,
                    "error": str(exc)}
        except httpx.HTTPError as exc:
            return {"provider": self.name, "ok": False, "model": self.model,
                    "error": f"servise ulaşılamadı: {exc}"}

        payload = resp.json()
        rows = payload.get("data", payload if isinstance(payload, list) else [])
        models = [
            (row.get("id") or row.get("name") or "") if isinstance(row, dict) else str(row)
            for row in rows
        ]
        # Foundry yüklü varyantı "phi-4-mini-cuda-gpu" gibi son ekle listeleyebilir.
        present = any(m == self.model or m.startswith(self.model) for m in models)
        return {
            "provider": self.name,
            "ok": present,
            "model": self.model,
            "base_url": self._base_url,
            "available_models": models,
            "error": None if present else
                     f"model '{self.model}' yüklü değil — "
                     f"`foundry model download {self.model}`",
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
