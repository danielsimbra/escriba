"""A camada HTTP: token, filtro de evento e despacho para o pipeline.

O endpoint precisa responder 200 rapido — a Evolution reentrega o evento se a
chamada demorar. O trabalho pesado vai para tarefa de fundo (que o TestClient
executa antes de devolver a resposta, e por isso da para checar o envio aqui).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from escriba.webhook import criar_app
from test_webhook_evolution import evento_audio, evento_texto


@pytest.fixture
def cliente(servicos):
    return TestClient(criar_app(servicos))


def test_saude_responde(cliente, servicos):
    resposta = cliente.get("/saude")
    assert resposta.status_code == 200
    assert resposta.json()["instancia"] == servicos.cfg.evolution.instance


def test_sem_token_recusa(cliente):
    assert cliente.post("/webhook", json=evento_audio()).status_code == 401


def test_token_errado_recusa(cliente):
    resposta = cliente.post("/webhook?token=errado", json=evento_audio())
    assert resposta.status_code == 401


def test_token_no_cabecalho_tambem_vale(cliente):
    resposta = cliente.post(
        "/webhook", json=evento_audio(), headers={"x-escriba-token": "segredo"},
    )
    assert resposta.status_code == 200


def test_audio_chega_ao_pipeline(cliente, servicos):
    resposta = cliente.post("/webhook?token=segredo", json=evento_audio())
    assert resposta.status_code == 200
    assert resposta.json() == {"ok": True, "eventos": 1}
    assert servicos.evolution.enviadas[0][1] == "Bom dia, tudo bem?"


def test_rota_por_evento_tambem_funciona(cliente, servicos):
    """Com `byEvents: true`, a Evolution entrega em /webhook/messages-upsert."""
    resposta = cliente.post("/webhook/messages-upsert?token=segredo", json=evento_audio())
    assert resposta.status_code == 200
    assert len(servicos.evolution.enviadas) == 1


def test_evento_de_outro_tipo_e_descartado(cliente, servicos):
    payload = evento_audio()
    payload["event"] = "contacts.update"
    resposta = cliente.post("/webhook?token=segredo", json=payload)
    assert resposta.json()["ignorado"] == "contacts.update"
    assert servicos.evolution.enviadas == []


def test_evento_de_outra_instancia_e_descartado(cliente, servicos):
    payload = evento_audio()
    payload["instance"] = "OutraCoisa"
    resposta = cliente.post("/webhook?token=segredo", json=payload)
    assert resposta.json()["ignorado"] == "outra instancia"
    assert servicos.evolution.enviadas == []


def test_comando_pelo_webhook(cliente, servicos):
    cliente.post("/webhook?token=segredo", json=evento_texto("escriba off"))
    assert servicos.estado.ativo("5500900000001", padrao=True) is False


def test_corpo_invalido_responde_400(cliente):
    resposta = cliente.post(
        "/webhook?token=segredo", content=b"nao sou json",
        headers={"Content-Type": "application/json"},
    )
    assert resposta.status_code == 400
