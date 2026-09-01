"""Primeira etapa: audio -> texto bruto, pela API de transcricao da OpenAI.

Por que aqui nao e a Claude: a API da Anthropic nao recebe audio. O Escriba
usa dois modelos de proposito diferente — um que ouve (`gpt-4o-transcribe`) e
um que edita (Claude, em `limpeza.py`). Este modulo entrega a fala como ela
saiu da boca, hesitacao inclusa; limpar e trabalho da etapa seguinte.
"""
from __future__ import annotations

import logging

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import TranscricaoSettings
from .models import Audio

log = logging.getLogger(__name__)

URL = "https://api.openai.com/v1/audio/transcriptions"
TIMEOUT = (10, 300)  # audio longo demora; o teto de leitura e generoso

# Teto da propria API. O YAML corta antes disso, mas a checagem fica aqui
# tambem porque este modulo pode ser chamado direto pela CLI.
LIMITE_BYTES = 25 * 1024 * 1024


class TranscricaoError(RuntimeError):
    pass


class ErroTemporario(TranscricaoError):
    """Falha que vale repetir: limite de taxa ou indisponibilidade momentanea."""


class Transcritor:
    def __init__(self, cfg: TranscricaoSettings):
        self.cfg = cfg
        self.sessao = requests.Session()
        self.sessao.headers.update({"Authorization": f"Bearer {cfg.api_key}"})

    def transcrever(self, conteudo: bytes, *, nome: str = "audio.ogg",
                    mimetype: str = "audio/ogg") -> str:
        if not conteudo:
            raise TranscricaoError("Audio vazio: nada para transcrever.")
        if len(conteudo) > LIMITE_BYTES:
            raise TranscricaoError(
                f"Audio de {len(conteudo) / 1024 / 1024:.1f} MB passa do limite "
                f"de 25 MB da API de transcricao."
            )

        log.info(
            "Transcrevendo %s (%.1f KB) com %s",
            nome, len(conteudo) / 1024, self.cfg.model,
        )
        dados = {"model": self.cfg.model, "response_format": "json"}
        if self.cfg.language:
            # Informar o idioma evita o modelo "escorregar" para espanhol em
            # audio curto ou com muito ruido, e acelera a transcricao.
            dados["language"] = self.cfg.language

        resposta = self._enviar(conteudo, nome, mimetype, dados)
        texto = (resposta.get("text") or "").strip()
        log.info("Transcricao bruta: %d caracteres", len(texto))
        return texto

    def transcrever_audio(self, audio: Audio, conteudo: bytes) -> str:
        """Atalho que deriva nome e mimetype do proprio evento do WhatsApp."""
        return self.transcrever(
            conteudo,
            nome=f"{audio.message_id}{audio.extensao}",
            mimetype=audio.mimetype.split(";")[0].strip() or "audio/ogg",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        retry=retry_if_exception_type((requests.RequestException, ErroTemporario)),
        reraise=True,
    )
    def _enviar(self, conteudo: bytes, nome: str, mimetype: str,
                dados: dict[str, str]) -> dict:
        # O nome do arquivo importa: a API escolhe o decodificador pela
        # extensao. Audio do WhatsApp e OGG/Opus e precisa chegar como .ogg.
        arquivos = {"file": (nome, conteudo, mimetype)}
        resposta = self.sessao.post(URL, data=dados, files=arquivos, timeout=TIMEOUT)

        if resposta.status_code == 429 or resposta.status_code >= 500:
            raise ErroTemporario(
                f"OpenAI respondeu {resposta.status_code}: {resposta.text[:200]}"
            )
        if resposta.status_code >= 400:
            # 400/401/413 nao melhoram com retry: chave errada, formato
            # invalido, arquivo grande demais.
            raise TranscricaoError(
                f"OpenAI respondeu {resposta.status_code}: {resposta.text[:400]}"
            )
        try:
            return resposta.json()
        except ValueError as exc:
            raise TranscricaoError(
                f"Resposta da OpenAI nao era JSON: {resposta.text[:200]}"
            ) from exc
