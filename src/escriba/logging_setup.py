"""Configuracao de log: console + arquivo rotativo em logs/escriba.log."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
DATA = "%Y-%m-%d %H:%M:%S"


def configurar(nivel: str = "INFO", dir_logs: Path | None = None) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel.upper(), logging.INFO))
    raiz.handlers.clear()

    formatador = logging.Formatter(FORMATO, datefmt=DATA)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatador)
    raiz.addHandler(console)

    if dir_logs:
        dir_logs.mkdir(parents=True, exist_ok=True)
        arquivo = RotatingFileHandler(
            dir_logs / "escriba.log", maxBytes=5 * 1024 * 1024,
            backupCount=5, encoding="utf-8",
        )
        arquivo.setFormatter(formatador)
        raiz.addHandler(arquivo)

    # Bibliotecas verbosas que nao acrescentam nada ao diagnostico do agente.
    for ruidosa in ("httpx", "httpcore", "urllib3", "anthropic._base_client"):
        logging.getLogger(ruidosa).setLevel(logging.WARNING)
    # O log de acesso do uvicorn repetiria cada webhook; o pipeline ja registra
    # o que interessa, com o JID e a duracao do audio.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
