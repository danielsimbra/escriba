"""Carrega configuracao de ambiente (.env) e as regras (config/escriba.yaml).

Divisao de responsabilidade: o .env guarda segredo e endereco (chaves de API,
URL da Evolution); o YAML guarda politica (quem pode, o que limpar, como
responder). Mudar politica nao exige tocar em variavel de ambiente.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .models import normalizar_destino

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(
            f"Variável de ambiente obrigatória ausente: {name}. "
            f"Copie .env.example para .env e preencha."
        )
    return value or ""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw and raw.strip() else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "on"}


@dataclass(frozen=True)
class TranscricaoSettings:
    api_key: str
    model: str
    language: str


@dataclass(frozen=True)
class ClaudeSettings:
    api_key: str
    model: str
    max_tokens: int


@dataclass(frozen=True)
class EvolutionSettings:
    base_url: str
    instance: str
    api_key: str


@dataclass(frozen=True)
class ServidorSettings:
    host: str
    port: int
    webhook_url: str
    webhook_token: str
    max_simultaneas: int


@dataclass(frozen=True)
class Settings:
    transcricao: TranscricaoSettings
    claude: ClaudeSettings
    evolution: EvolutionSettings
    servidor: ServidorSettings
    regras_path: Path
    db_path: Path
    audios_dir: Path
    timezone: str
    log_level: str
    dry_run: bool
    salvar_audios: bool
    regras: dict[str, Any] = field(default_factory=dict)

    # -- allowlist ---------------------------------------------------------
    @property
    def _allowlist(self) -> dict[str, Any]:
        return self.regras.get("allowlist", {}) or {}

    @property
    def permitir_todos(self) -> bool:
        return bool(self._allowlist.get("permitir_todos", False))

    @property
    def conversas_autorizadas(self) -> frozenset[str]:
        brutas = self._allowlist.get("conversas") or []
        return frozenset(
            d for d in (normalizar_destino(str(c)) for c in brutas) if d
        )

    @property
    def comeca_ativo(self) -> bool:
        return bool(self._allowlist.get("comeca_ativo", True))

    def autorizada(self, remote_jid: str) -> bool:
        """True se a conversa pode ser atendida pelo Escriba.

        Autorizacao e diferente de estar ligada: uma conversa autorizada ainda
        pode estar desligada por comando (ver `estado.Estado.ativo`).
        """
        if self.permitir_todos:
            return True
        return normalizar_destino(remote_jid) in self.conversas_autorizadas

    # -- audio -------------------------------------------------------------
    @property
    def _audio(self) -> dict[str, Any]:
        return self.regras.get("audio", {}) or {}

    @property
    def processar_proprios(self) -> bool:
        return bool(self._audio.get("processar_proprios", False))

    @property
    def duracao_maxima(self) -> int:
        return int(self._audio.get("duracao_maxima_segundos", 900))

    @property
    def tamanho_maximo_bytes(self) -> int:
        return int(float(self._audio.get("tamanho_maximo_mb", 24)) * 1024 * 1024)

    # -- comandos ----------------------------------------------------------
    @property
    def _comandos(self) -> dict[str, Any]:
        return self.regras.get("comandos", {}) or {}

    @property
    def prefixo_comando(self) -> str:
        return str(self._comandos.get("prefixo", "escriba"))

    @property
    def comandos_apenas_proprio(self) -> bool:
        return bool(self._comandos.get("apenas_proprio", True))

    # -- resposta ----------------------------------------------------------
    @property
    def _resposta(self) -> dict[str, Any]:
        return self.regras.get("resposta", {}) or {}

    @property
    def prefixo_resposta(self) -> str:
        return str(self._resposta.get("prefixo", ""))

    @property
    def responder_citando(self) -> bool:
        return bool(self._resposta.get("responder_citando", True))

    @property
    def mostrar_digitando(self) -> bool:
        return bool(self._resposta.get("mostrar_digitando", True))

    @property
    def max_caracteres_mensagem(self) -> int:
        return int(self._resposta.get("max_caracteres_mensagem", 3500))

    @property
    def avisar_erro(self) -> bool:
        return bool(self._resposta.get("avisar_erro", True))

    @property
    def texto_sem_fala(self) -> str:
        return str(self._resposta.get("sem_fala", ""))

    # -- limpeza -----------------------------------------------------------
    @property
    def _limpeza(self) -> dict[str, Any]:
        return self.regras.get("limpeza", {}) or {}

    @property
    def muletas(self) -> list[str]:
        return [str(m) for m in (self._limpeza.get("muletas") or [])]

    @property
    def numeros_em_algarismos(self) -> bool:
        return bool(self._limpeza.get("numeros_em_algarismos", False))

    @property
    def instrucoes_extras(self) -> str:
        return str(self._limpeza.get("instrucoes_extras", "") or "").strip()

    @property
    def max_caracteres_por_lote(self) -> int:
        return int(self._limpeza.get("max_caracteres_por_lote", 9000))


def carregar_regras(caminho: Path) -> dict[str, Any]:
    with open(caminho, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings() -> Settings:
    regras_path = Path(_env("REGRAS_PATH", str(BASE_DIR / "config" / "escriba.yaml")))
    regras = carregar_regras(regras_path)

    return Settings(
        transcricao=TranscricaoSettings(
            api_key=_env("OPENAI_API_KEY", required=True),
            model=_env("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe"),
            language=_env("TRANSCRIBE_LANGUAGE", "pt").strip(),
        ),
        claude=ClaudeSettings(
            api_key=_env("ANTHROPIC_API_KEY", required=True),
            model=_env("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=_env_int("ANTHROPIC_MAX_TOKENS", 8000),
        ),
        evolution=EvolutionSettings(
            base_url=_env("EVOLUTION_BASE_URL", "http://evolution-api:8080").rstrip("/"),
            instance=_env("EVOLUTION_INSTANCE", "Escriba"),
            api_key=_env("EVOLUTION_API_KEY", required=True),
        ),
        servidor=ServidorSettings(
            host=_env("ESCRIBA_HOST", "0.0.0.0"),
            port=_env_int("ESCRIBA_PORT", 8000),
            webhook_url=_env("WEBHOOK_URL", "http://escriba:8000/webhook").rstrip("/"),
            webhook_token=_env("WEBHOOK_TOKEN", "").strip(),
            max_simultaneas=max(1, _env_int("MAX_TRANSCRICOES_SIMULTANEAS", 3)),
        ),
        regras_path=regras_path,
        db_path=Path(_env("DB_PATH", str(BASE_DIR / "data" / "escriba.db"))),
        audios_dir=Path(_env("AUDIOS_DIR", str(BASE_DIR / "data" / "audios"))),
        timezone=_env("TZ", "America/Sao_Paulo"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        dry_run=_env_bool("DRY_RUN", False),
        salvar_audios=_env_bool("SALVAR_AUDIOS", False),
        regras=regras,
    )
