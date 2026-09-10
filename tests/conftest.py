"""Fabricas usadas pelos testes.

Nenhum teste toca a rede: as etapas que chamariam OpenAI, Anthropic ou a
Evolution sao substituidas por dublês que so registram o que receberam.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from escriba.config import (
    ClaudeSettings,
    EvolutionSettings,
    ServidorSettings,
    Settings,
    TranscricaoSettings,
)
from escriba.estado import Estado
from escriba.models import Audio
from escriba.pipeline import Servicos

REGRAS_PADRAO: dict[str, Any] = {
    "allowlist": {
        "permitir_todos": False,
        "conversas": ["5500900000001"],
        "comeca_ativo": True,
    },
    "audio": {
        "processar_proprios": False,
        "duracao_maxima_segundos": 900,
        "duracao_minima_segundos": 0,
        "tamanho_maximo_mb": 24,
    },
    "comandos": {"prefixo": "escriba", "apenas_proprio": True},
    "resposta": {
        "prefixo": "",
        "responder_citando": True,
        "mostrar_digitando": False,
        "max_caracteres_mensagem": 3500,
        "avisar_erro": True,
        "sem_fala": "Nao identifiquei fala.",
    },
    "limpeza": {
        "muletas": ["ne", "tipo"],
        "numeros_em_algarismos": False,
        "instrucoes_extras": "",
        "max_caracteres_por_lote": 9000,
    },
}


def fazer_settings(tmp_path, **ajustes: Any) -> Settings:
    """Settings completo, sem depender de .env nem de arquivo YAML."""
    regras = _mesclar(REGRAS_PADRAO, ajustes.pop("regras", {}))
    return Settings(
        transcricao=TranscricaoSettings(api_key="teste", model="gpt-4o-transcribe", language="pt"),
        claude=ClaudeSettings(api_key="teste", model="claude-haiku-4-5-20251001", max_tokens=8000),
        evolution=EvolutionSettings(
            base_url="http://evolution:8080", instance="Escriba", api_key="teste",
        ),
        servidor=ServidorSettings(
            host="127.0.0.1", port=8000, webhook_url="http://escriba:8000/webhook",
            webhook_token="segredo", max_simultaneas=2,
        ),
        regras_path=tmp_path / "escriba.yaml",
        db_path=tmp_path / "escriba.db",
        audios_dir=tmp_path / "audios",
        timezone="America/Sao_Paulo",
        log_level="INFO",
        dry_run=ajustes.pop("dry_run", False),
        salvar_audios=False,
        regras=regras,
    )


def _mesclar(base: dict[str, Any], mudancas: dict[str, Any]) -> dict[str, Any]:
    saida = {chave: dict(valor) for chave, valor in base.items()}
    for chave, valor in mudancas.items():
        saida.setdefault(chave, {}).update(valor)
    return saida


# -- dubles -----------------------------------------------------------------
class EvolutionFalsa:
    def __init__(self, conteudo: bytes = b"audio-falso"):
        self.conteudo = conteudo
        self.enviadas: list[tuple[str, str, bool]] = []
        self.presencas: list[str] = []
        self.erro_ao_baixar: Exception | None = None

    def baixar_audio(self, audio: Audio) -> bytes:
        if self.erro_ao_baixar:
            raise self.erro_ao_baixar
        return self.conteudo

    def responder(self, alvo: Any, texto: str, *, citar: bool = True) -> dict[str, Any]:
        jid = alvo if isinstance(alvo, str) else alvo.remote_jid
        self.enviadas.append((jid, texto, citar))
        return {"ok": True}

    def digitando(self, remote_jid: str, segundos: int = 3) -> None:
        self.presencas.append(remote_jid)


class TranscritorFalso:
    def __init__(self, texto: str = "eeee, bom dia, tipo, tudo bem ne?"):
        self.texto = texto
        self.erro: Exception | None = None

    def transcrever_audio(self, audio: Audio, conteudo: bytes) -> str:
        if self.erro:
            raise self.erro
        return self.texto


class LimpadorFalso:
    def __init__(self, texto: str = "Bom dia, tudo bem?"):
        self.texto = texto
        self.recebido: str | None = None

    def limpar(self, bruta: str) -> str:
        self.recebido = bruta
        return self.texto


def fazer_servicos(tmp_path, *, limpador: Any = None, **ajustes: Any) -> Servicos:
    """Servicos com dubles. `regras=` e `dry_run=` vao para `fazer_settings`."""
    cfg = fazer_settings(tmp_path, **ajustes)
    return Servicos(
        cfg=cfg,
        evolution=EvolutionFalsa(),
        transcritor=TranscritorFalso(),
        limpador=limpador or LimpadorFalso(),
        estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(2),
    )


@pytest.fixture
def servicos(tmp_path):
    """Servicos completo com dubles, pronto para `pipeline.processar`."""
    return fazer_servicos(tmp_path)


def fazer_audio(**ajustes: Any) -> Audio:
    campos: dict[str, Any] = {
        "message_id": "MSG1",
        "remote_jid": "5500900000001@s.whatsapp.net",
        "from_me": False,
        "push_name": "Fulano",
        "segundos": 30,
        "tamanho_bytes": 120_000,
        "chave": {"id": "MSG1", "remoteJid": "5500900000001@s.whatsapp.net", "fromMe": False},
        "mensagem": {"audioMessage": {"seconds": 30}},
    }
    campos.update(ajustes)
    return Audio(**campos)
