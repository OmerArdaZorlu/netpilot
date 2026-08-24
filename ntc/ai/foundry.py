"""Microsoft Foundry Local sağlayıcısı.

Foundry Local, modeli ONNX Runtime üzerinde yerelde koşturur ve OpenAI uyumlu
bir REST ucu açar. Ollama'dan iki farkı var:

  * **Uç nokta dinamiktir.** Servis her başlatıldığında farklı bir port
    seçebilir, o yüzden sabit URL varsaymak yerine `foundry server status`
    çıktısından keşfediyoruz (veya config'te açıkça verilirse onu kullanıyoruz).
  * **Model adlandırması farklıdır.** Foundry takma adları tire ile ayrılır
    (`phi-4-mini`), Ollama'da aynı model `phi4-mini` olarak geçer.

⚠️ **Takma ad en hızlı varyantı seçmiyor.** `phi-4-mini` bu makinede OpenVINO
varyantına çözüldü (Intel iGPU, 9.5 tok/sn) — NVIDIA RTX 4060 boşta dururken.
CUDA varyantı aynı işi 60 tok/sn ile yapıyor. Hata vermeden, sadece yavaşlayarak
oluyor; o yüzden varyant `config.yaml` içinde açıkça sabitleniyor.
`foundry model info <takma ad>` seçenekleri ve hangisinin önbellekte olduğunu
listeler.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from typing import Any

import httpx

from ..core.config import AIConfig
from .provider import LLMProvider, LLMUnavailable

log = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://[\w\.\-]+:\d+")
CLI_TIMEOUT = 20.0
# Modeli belleğe alma ölçümü: CUDA varyantı 10-13 sn, OpenVINO 65 sn.
# Sınır bol tutuluyor — süre dolarsa zincir ollama/mock'a düşer, sistem durmaz.
LOAD_TIMEOUT = 300.0
BUSY_RETRIES = 3
BUSY_BACKOFF = 2.0
# Soğuk başlangıçta servis önce ONNX execution provider'larını indiriyor; bu
# ilk seferde dakikalar sürebiliyor. Yine de sınırlı tutuyoruz: açılış yolu
# modele bağlanmamalı, süre dolarsa zincir ollama/mock'a düşer.
START_TIMEOUT = 90.0


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


def _parse_status_url(out: str) -> str | None:
    """`foundry server status -o json` çıktısından servis ucunu çıkarır.

    Gerçek çıktı (0.10.3):
        kapalı:  {"running":false,"state":"initializing"}
        açık:    {"running":true,"state":"ready","pid":2828,
                  "webUrls":["http://127.0.0.1:58082"],...}

    Port her başlatmada değişiyor, o yüzden sabit varsayım yok. JSON'u
    ayrıştıramazsak (CLI sürümü `-o json` bilmiyorsa metin döner) düz metin
    üzerinde regex'e düşüyoruz — keşif tek bir çıktı biçimine bağlı kalmasın.
    """
    text = (out or "").strip()
    if not text:
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = URL_PATTERN.search(text)
        return match.group(0).rstrip("/") if match else None

    if not isinstance(data, dict):
        return None
    if data.get("running") is False:
        return None

    urls = data.get("webUrls") or []
    if isinstance(urls, str):
        urls = [urls]
    for candidate in urls:
        if isinstance(candidate, str) and URL_PATTERN.match(candidate.strip()):
            return candidate.strip().rstrip("/")

    # Alan adı sürümle değişirse JSON'un tamamında URL ara.
    match = URL_PATTERN.search(text)
    return match.group(0).rstrip("/") if match else None


def _error_message(resp: httpx.Response) -> str:
    """Foundry hata gövdesinden okunabilir mesajı çıkarır.

    Gövdeyi yutmamak önemli: "model yüklü değil" durumu yalnızca burada
    görünüyor, HTTP durum kodu sadece 400 diyor.
    """
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300].strip() or "(boş gövde)"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    return resp.text[:300].strip()


def _needs_load(message: str) -> bool:
    """Hata "model belleğe alınmamış" mı diyor?"""
    low = message.lower()
    return "not loaded" in low or "load the model" in low


def _is_busy(message: str) -> bool:
    """Daemon başka bir çıkarımla mı meşgul? ("Infer Request is busy")"""
    return "busy" in message.lower()


class FoundryLocalProvider(LLMProvider):
    name = "foundry"

    def __init__(self, cfg: AIConfig) -> None:
        self.cfg = cfg
        self.model = cfg.model
        self._base_url: str | None = cfg.base_url.strip() or None
        self._client: httpx.AsyncClient | None = None
        self._loaded = False
        self._load_lock = asyncio.Lock()
        # Daemon aynı anda tek çıkarım yapıyor; ikinci istek 500 "Infer Request
        # is busy" ile düşüyor. Sıraya sokmak, denemeleri boşa harcamaktan iyi.
        self._infer_lock = asyncio.Lock()

    # --------------------------------------------------------------- keşif

    async def base_url(self) -> str:
        """Servis ucunu bulur; gerekiyorsa servisi başlatır."""
        if self._base_url:
            return self._base_url

        url = await self._status_url()
        if url is None:
            # Servis kapalıysa bir kez başlatmayı dene.
            code, _ = await _run_cli("server", "start", timeout=START_TIMEOUT)
            if code == 127:
                raise LLMUnavailable(
                    "foundry CLI bulunamadı — Foundry Local kurulu değil")
            url = await self._status_url()

        if url is None:
            raise LLMUnavailable(
                "Foundry Local servisi çalışmıyor — `foundry server start` dene")

        self._base_url = url
        log.info("Foundry Local ucu: %s", url)
        return url

    async def _status_url(self) -> str | None:
        code, out = await _run_cli("server", "status", "-o", "json")
        if code == 127:
            raise LLMUnavailable(
                "foundry CLI bulunamadı — Foundry Local kurulu değil")
        return _parse_status_url(out)

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            base = await self.base_url()
            self._client = httpx.AsyncClient(
                base_url=base,
                timeout=httpx.Timeout(self.cfg.timeout_seconds, connect=10.0),
            )
        return self._client

    # ------------------------------------------------------- bellek yükleme

    async def _post(self, client: httpx.AsyncClient,
                    payload: dict[str, Any]) -> httpx.Response:
        try:
            return await client.post("/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(
                f"Foundry Local'a ulaşılamadı: {exc}") from exc

    async def _ensure_loaded(self) -> None:
        """Modeli daemon belleğine alır.

        Ölçülen süre varyanta göre değişiyor: CUDA 10-13 sn, OpenVINO 65 sn.
        Bu yüzden açılış yolunda değil, ilk gerçek çağrıda yapılıyor. Kilit,
        eşzamanlı analiz döngülerinin aynı modeli birden çok kez yüklemeye
        çalışmasını engelliyor.
        """
        async with self._load_lock:
            if self._loaded:
                return
            log.info("Foundry modeli belleğe alınıyor: %s (bir dakika sürebilir)",
                     self.model)
            code, out = await _run_cli("model", "load", self.model,
                                       timeout=LOAD_TIMEOUT)
            if code == 127:
                raise LLMUnavailable(
                    "foundry CLI bulunamadı — model belleğe alınamıyor")
            if code != 0:
                raise LLMUnavailable(
                    f"`foundry model load {self.model}` başarısız: "
                    f"{out.strip()[:300]}")
            self._loaded = True
            log.info("Foundry modeli hazır: %s", self.model)

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
            # Foundry alanı kabul ediyor ama uygulamıyor (ölçüldü: 200 dönüyor,
            # içerik düz metin). extract_json zaten dağınık çıktıyı kurtarıyor.
            payload["response_format"] = {"type": "json_object"}

        async with self._infer_lock:
            resp = await self._post(client, payload)

            if resp.status_code == 400 and _needs_load(_error_message(resp)):
                # Foundry, Ollama'nın aksine modeli istek üzerine belleğe
                # almıyor; açık bir yükleme adımı istiyor.
                await self._ensure_loaded()
                resp = await self._post(client, payload)

            # Kilit kendi çağrılarımızı sıraya sokuyor ama daemon'ı başka bir
            # istemci de kullanıyor olabilir (foundry chat, ikinci bir örnek).
            for attempt in range(BUSY_RETRIES):
                if not (resp.status_code == 500
                        and _is_busy(_error_message(resp))):
                    break
                delay = BUSY_BACKOFF * (attempt + 1)
                log.info("Foundry meşgul, %.0f sn sonra tekrar denenecek", delay)
                await asyncio.sleep(delay)
                resp = await self._post(client, payload)

        if resp.status_code >= 400:
            raise LLMUnavailable(
                f"Foundry Local çağrısı başarısız: HTTP {resp.status_code} — "
                f"{_error_message(resp)}")

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
        models: list[str] = []
        aliases: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                models.append(str(row))
                continue
            models.append(row.get("id") or row.get("name") or "")
            # Foundry somut varyantı listeler ("phi-4-mini-instruct-openvino-gpu")
            # ve istediğimiz takma adı `parent` alanında verir.
            if row.get("parent"):
                aliases.append(str(row["parent"]))

        present = any(m == self.model or m.startswith(self.model) for m in models) \
            or self.model in aliases
        return {
            "provider": self.name,
            "ok": present,
            "model": self.model,
            "base_url": self._base_url,
            "available_models": models,
            # /v1/models "indirilmiş" olanları listeliyor, "belleğe alınmış"
            # olanları değil — ilk çağrı bu yüzden yavaş.
            "note": None if not present or self._loaded else
                    "model ilk çağrıda belleğe alınacak (~1 dk)",
            "error": None if present else
                     f"model '{self.model}' indirilmemiş — "
                     f"`foundry model download {self.model}`",
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
