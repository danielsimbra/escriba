"""Servidor HTTP que recebe os eventos da Evolution API.

Regra de ouro do handler: **responder 200 rapido**. A Evolution reentrega o
evento se a chamada demorar ou falhar, e transcrever um audio leva dezenas de
segundos. Por isso o endpoint so valida, traduz o payload e joga o trabalho
para uma tarefa de fundo — a resposta HTTP sai em milissegundos.
"""
from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request

from . import __version__, pipeline
from .evolution import interpretar_lote
from .pipeline import Servicos

log = logging.getLogger(__name__)

# So este evento interessa. A Evolution pode estar configurada para mandar
# recibo de leitura, presenca e atualizacao de contato; tudo isso e descartado
# sem custo.
EVENTOS_ACEITOS = {"messages.upsert", "messages_upsert"}


def criar_app(srv: Servicos) -> FastAPI:
    app = FastAPI(
        title="Escriba",
        description="Transcricao limpa de audios do WhatsApp",
        version=__version__,
        docs_url=None,      # nao ha nada de util para expor publicamente
        redoc_url=None,
        openapi_url=None,
    )

    def conferir_token(token: str | None, cabecalho: str | None) -> None:
        esperado = srv.cfg.servidor.webhook_token
        if not esperado:
            return
        if token != esperado and cabecalho != esperado:
            # Sem detalhar o motivo: quem chama sem o token nao precisa saber
            # se o token existe, so que nao passou.
            raise HTTPException(status_code=401, detail="nao autorizado")

    @app.get("/saude")
    def saude() -> dict[str, object]:
        """Usado pelo healthcheck do Docker e por voce, no navegador."""
        return {
            "status": "ok",
            "versao": __version__,
            "instancia": srv.cfg.evolution.instance,
            "dry_run": srv.cfg.dry_run,
            "conversas_autorizadas": (
                "todas" if srv.cfg.permitir_todos
                else len(srv.cfg.conversas_autorizadas)
            ),
        }

    @app.post("/webhook")
    @app.post("/webhook/{evento}")
    async def receber(
        request: Request,
        tarefas: BackgroundTasks,
        evento: str = "",
        token: str | None = Query(default=None),
        x_escriba_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        conferir_token(token, x_escriba_token)

        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 - corpo invalido nao merece stack trace
            raise HTTPException(status_code=400, detail="corpo nao e JSON")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="corpo nao e um objeto JSON")

        # A rota /webhook/<evento> existe porque a Evolution, com
        # `byEvents: true`, entrega em uma URL por tipo de evento.
        nome = str(payload.get("event") or evento or "").replace("-", "_").lower()
        if nome and nome not in EVENTOS_ACEITOS:
            return {"ok": True, "ignorado": nome}

        instancia = str(payload.get("instance") or "")
        if instancia and instancia.lower() != srv.cfg.evolution.instance.lower():
            log.warning(
                "Evento da instancia '%s' descartado (esperada: '%s')",
                instancia, srv.cfg.evolution.instance,
            )
            return {"ok": True, "ignorado": "outra instancia"}

        # A leitura do payload roda aqui, sincrona, e nao na tarefa de fundo.
        # Sem esta protecao, um campo em formato inesperado (foi o caso do
        # `fileLength` como Long do protobuf) virava 500 — e a Evolution
        # reentregava o mesmo evento em loop, sem nada ser processado.
        try:
            eventos = interpretar_lote(payload, srv.cfg.prefixo_comando)
        except Exception:  # noqa: BLE001 - fronteira: payload e dado externo
            log.exception(
                "Payload de %s nao pode ser lido; evento descartado",
                instancia or "instancia desconhecida",
            )
            # 200 de proposito: reentregar o mesmo payload quebrado nao muda
            # o resultado, so multiplica o erro.
            return {"ok": False, "erro": "payload ilegivel"}

        for item in eventos:
            tarefas.add_task(pipeline.processar, srv, item)
        return {"ok": True, "eventos": len(eventos)}

    return app
