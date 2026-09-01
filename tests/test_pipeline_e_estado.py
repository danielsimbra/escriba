"""As regras que decidem se um audio vira texto — e o que impede transcricao
repetida quando a Evolution reentrega o mesmo evento.
"""
from __future__ import annotations

import pytest
import requests

from conftest import EvolutionFalsa, fazer_audio, fazer_settings
from escriba import pipeline
from escriba.estado import Estado
from escriba.models import Comando
from escriba.transcricao import TranscricaoError


# -- allowlist e liga/desliga ----------------------------------------------
def test_audio_de_conversa_autorizada_vira_texto(servicos):
    pipeline.processar(servicos, fazer_audio())
    assert len(servicos.evolution.enviadas) == 1
    jid, texto, citou = servicos.evolution.enviadas[0]
    assert texto == "Bom dia, tudo bem?"
    assert citou


def test_conversa_fora_da_allowlist_e_ignorada(servicos):
    pipeline.processar(servicos, fazer_audio(remote_jid="5500900000004@s.whatsapp.net"))
    assert servicos.evolution.enviadas == []


def test_audio_proprio_e_ignorado_por_padrao(servicos):
    pipeline.processar(servicos, fazer_audio(from_me=True))
    assert servicos.evolution.enviadas == []


def test_conversa_desligada_por_comando_nao_transcreve(servicos):
    servicos.estado.definir_ativo("5500900000001", False)
    pipeline.processar(servicos, fazer_audio())
    assert servicos.evolution.enviadas == []


def test_comando_liga_de_volta(servicos):
    servicos.estado.definir_ativo("5500900000001", False)
    pipeline.processar(servicos, Comando(
        acao="ligar", remote_jid="5500900000001@s.whatsapp.net", from_me=True,
    ))
    assert servicos.estado.ativo("5500900000001", padrao=False)

    pipeline.processar(servicos, fazer_audio())
    # A confirmacao do comando e a transcricao.
    assert len(servicos.evolution.enviadas) == 2


def test_comando_de_terceiro_nao_desliga_o_agente(servicos):
    """Com `apenas_proprio`, o contato do outro lado nao controla o Escriba."""
    pipeline.processar(servicos, Comando(
        acao="desligar", remote_jid="5500900000001@s.whatsapp.net", from_me=False,
    ))
    assert servicos.estado.ativo("5500900000001", padrao=True)
    assert servicos.evolution.enviadas == []


# -- limites ---------------------------------------------------------------
def test_audio_longo_demais_e_recusado_com_aviso(servicos):
    pipeline.processar(servicos, fazer_audio(segundos=1200))
    assert len(servicos.evolution.enviadas) == 1
    assert "limite" in servicos.evolution.enviadas[0][1]


def test_audio_recusado_nao_avisa_duas_vezes(servicos):
    """Reentrega do webhook nao pode repetir o aviso de limite na conversa."""
    audio = fazer_audio(segundos=1200)
    pipeline.processar(servicos, audio)
    pipeline.processar(servicos, audio)
    assert len(servicos.evolution.enviadas) == 1


def test_audio_grande_demais_e_recusado(servicos):
    pipeline.processar(servicos, fazer_audio(tamanho_bytes=40 * 1024 * 1024))
    assert len(servicos.evolution.enviadas) == 1
    assert "grande demais" in servicos.evolution.enviadas[0][1]


# -- idempotencia ----------------------------------------------------------
def test_reentrega_do_mesmo_audio_nao_transcreve_duas_vezes(servicos):
    audio = fazer_audio()
    pipeline.processar(servicos, audio)
    pipeline.processar(servicos, audio)
    assert len(servicos.evolution.enviadas) == 1


def test_falha_de_rede_libera_a_reserva_para_nova_tentativa(servicos):
    servicos.evolution.erro_ao_baixar = requests.ConnectionError("sem rede")
    audio = fazer_audio()
    pipeline.processar(servicos, audio)
    assert servicos.evolution.enviadas[0][1].startswith("Nao consegui baixar")

    # Rede voltou: a reentrega do webhook agora consegue transcrever.
    servicos.evolution.erro_ao_baixar = None
    pipeline.processar(servicos, audio)
    assert servicos.evolution.enviadas[-1][1] == "Bom dia, tudo bem?"


def test_falha_de_transcricao_nao_libera_a_reserva(servicos):
    """Erro de conteudo repetiria igual; nao vale gastar API de novo."""
    servicos.transcritor.erro = TranscricaoError("formato invalido")
    audio = fazer_audio()
    pipeline.processar(servicos, audio)
    servicos.transcritor.erro = None
    pipeline.processar(servicos, audio)
    assert len(servicos.evolution.enviadas) == 1
    assert "Nao consegui transcrever" in servicos.evolution.enviadas[0][1]


def test_audio_abaixo_do_piso_e_ignorado_em_silencio(tmp_path):
    from escriba.pipeline import Servicos
    import threading
    from conftest import LimpadorFalso, TranscritorFalso

    cfg = fazer_settings(tmp_path, regras={"audio": {"duracao_minima_segundos": 40}})
    srv = Servicos(
        cfg=cfg, evolution=EvolutionFalsa(), transcritor=TranscritorFalso(),
        limpador=LimpadorFalso(), estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(2),
    )
    pipeline.processar(srv, fazer_audio(segundos=15))
    assert srv.evolution.enviadas == []
    assert srv.estado.estatisticas()["total"] == 1
    assert srv.estado.estatisticas()["ok"] == 0


def test_audio_acima_do_piso_transcreve_normalmente(tmp_path):
    from escriba.pipeline import Servicos
    import threading
    from conftest import LimpadorFalso, TranscritorFalso

    cfg = fazer_settings(tmp_path, regras={"audio": {"duracao_minima_segundos": 40}})
    srv = Servicos(
        cfg=cfg, evolution=EvolutionFalsa(), transcritor=TranscritorFalso(),
        limpador=LimpadorFalso(), estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(2),
    )
    pipeline.processar(srv, fazer_audio(segundos=45))
    assert len(srv.evolution.enviadas) == 1


def test_piso_zero_desliga_o_filtro(servicos):
    """Config padrao dos testes ja tem duracao_minima_segundos=0."""
    pipeline.processar(servicos, fazer_audio(segundos=1))
    assert len(servicos.evolution.enviadas) == 1


# -- casos de conteudo ------------------------------------------------------
def test_audio_sem_fala_avisa_mesmo_com_avisar_erro_desligado(tmp_path):
    from escriba.pipeline import Servicos
    import threading
    from conftest import LimpadorFalso, TranscritorFalso

    cfg = fazer_settings(tmp_path, regras={"resposta": {"avisar_erro": False}})
    srv = Servicos(
        cfg=cfg, evolution=EvolutionFalsa(), transcritor=TranscritorFalso(),
        limpador=LimpadorFalso(texto=""), estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(2),
    )
    pipeline.processar(srv, fazer_audio())
    assert srv.evolution.enviadas[0][1] == "Nao identifiquei fala."


def test_dry_run_nao_envia_nada(tmp_path):
    from escriba.pipeline import Servicos
    import threading
    from conftest import LimpadorFalso, TranscritorFalso

    cfg = fazer_settings(tmp_path, dry_run=True)
    srv = Servicos(
        cfg=cfg, evolution=EvolutionFalsa(), transcritor=TranscritorFalso(),
        limpador=LimpadorFalso(), estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(2),
    )
    pipeline.processar(srv, fazer_audio())
    assert srv.evolution.enviadas == []
    assert srv.estado.estatisticas()["ok"] == 1


# -- estado ----------------------------------------------------------------
def test_reserva_so_vale_uma_vez(tmp_path):
    estado = Estado(tmp_path / "e.db")
    assert estado.reservar("A", "5500900000001") is True
    assert estado.reservar("A", "5500900000001") is False


def test_liberar_permite_reservar_de_novo(tmp_path):
    estado = Estado(tmp_path / "e.db")
    estado.reservar("A", "5500900000001")
    estado.liberar("A")
    assert estado.reservar("A", "5500900000001") is True


def test_estado_ignora_formatacao_do_numero(tmp_path):
    """JID, numero cru e numero formatado sao a mesma conversa."""
    estado = Estado(tmp_path / "e.db")
    estado.definir_ativo("5500900000001@s.whatsapp.net", False)
    assert estado.ativo("(00) 90000-0001", padrao=True) is False


# -- allowlist na configuracao ---------------------------------------------
@pytest.mark.parametrize("entrada", [
    "5500900000001",
    "5500900000001@s.whatsapp.net",
    "+55 00 90000-0001",
    "5500900000001:12@s.whatsapp.net",
])
def test_autorizada_aceita_qualquer_formatacao(tmp_path, entrada):
    cfg = fazer_settings(tmp_path)
    assert cfg.autorizada(entrada)


def test_grupo_precisa_estar_na_lista(tmp_path):
    grupo = "120363123456789012@g.us"
    cfg = fazer_settings(tmp_path)
    assert not cfg.autorizada(grupo)

    cfg = fazer_settings(tmp_path, regras={"allowlist": {"conversas": [grupo]}})
    assert cfg.autorizada(grupo)


def test_permitir_todos_abre_para_qualquer_conversa(tmp_path):
    cfg = fazer_settings(tmp_path, regras={"allowlist": {"permitir_todos": True}})
    assert cfg.autorizada("5500900000005@s.whatsapp.net")
