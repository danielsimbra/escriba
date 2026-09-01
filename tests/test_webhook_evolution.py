"""Leitura do payload da Evolution: o que vira audio, o que vira comando, o
que e descartado. Sao os testes que protegem contra mudanca de formato do
webhook — se a Evolution mexer no envelope, um destes quebra.
"""
from __future__ import annotations

from escriba.evolution import _acao_de_comando, interpretar, interpretar_lote
from escriba.models import Audio, Comando

JID = "5500900000001@s.whatsapp.net"


def evento_audio(**ajustes) -> dict:
    dados = {
        "key": {"remoteJid": JID, "fromMe": False, "id": "3EB0"},
        "pushName": "Fulano",
        "message": {
            "audioMessage": {
                "mimetype": "audio/ogg; codecs=opus",
                "seconds": 42,
                "fileLength": "123456",
                "ptt": True,
            }
        },
        "messageType": "audioMessage",
    }
    dados.update(ajustes)
    return {"event": "messages.upsert", "instance": "Escriba", "data": dados}


def evento_texto(texto: str, from_me: bool = True) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "Escriba",
        "data": {
            "key": {"remoteJid": JID, "fromMe": from_me, "id": "3EB1"},
            "message": {"conversation": texto},
            "messageType": "conversation",
        },
    }


def test_audio_vira_objeto_audio():
    evento = interpretar(evento_audio(), "escriba")
    assert isinstance(evento, Audio)
    assert evento.remote_jid == JID
    assert evento.segundos == 42
    assert evento.tamanho_bytes == 123456
    assert evento.extensao == ".ogg"
    assert not evento.e_grupo


def test_extensao_segue_o_mimetype():
    evento = interpretar(
        evento_audio(message={"audioMessage": {"mimetype": "audio/mpeg", "seconds": 5}}),
        "escriba",
    )
    assert evento.extensao == ".mp3"


def test_audio_dentro_de_mensagem_temporaria():
    """Mensagem efemera embrulha o conteudo; sem desembrulhar, o audio some."""
    embrulhado = evento_audio(message={
        "ephemeralMessage": {
            "message": {"audioMessage": {"mimetype": "audio/ogg", "seconds": 7}}
        }
    })
    evento = interpretar(embrulhado, "escriba")
    assert isinstance(evento, Audio)
    assert evento.segundos == 7


def test_base64_do_proprio_webhook_e_aproveitado():
    evento = interpretar(evento_audio(base64="QUJD"), "escriba")
    assert evento.base64 == "QUJD"


def test_audio_em_grupo_guarda_o_participante():
    grupo = evento_audio(key={
        "remoteJid": "120363123456789012@g.us",
        "fromMe": False,
        "id": "3EB2",
        "participant": "5500900000003@s.whatsapp.net",
    })
    evento = interpretar(grupo, "escriba")
    assert evento.e_grupo
    assert evento.autor == "5500900000003@s.whatsapp.net"


def test_imagem_e_descartada():
    imagem = evento_audio(message={"imageMessage": {"mimetype": "image/jpeg"}})
    assert interpretar(imagem, "escriba") is None


def test_status_do_whatsapp_e_descartado():
    status = evento_audio(key={"remoteJid": "status@broadcast", "fromMe": False, "id": "X"})
    assert interpretar(status, "escriba") is None


def test_texto_comum_nao_vira_evento():
    assert interpretar(evento_texto("bom dia"), "escriba") is None


def test_comando_reconhecido():
    evento = interpretar(evento_texto("escriba on"), "escriba")
    assert isinstance(evento, Comando)
    assert evento.acao == "ligar"
    assert evento.from_me


def test_lote_com_data_em_lista():
    payload = evento_audio()
    payload["data"] = [payload["data"], evento_texto("escriba off")["data"]]
    eventos = interpretar_lote(payload, "escriba")
    assert [type(e).__name__ for e in eventos] == ["Audio", "Comando"]


# -- gramatica dos comandos -------------------------------------------------
def test_variacoes_de_comando():
    assert _acao_de_comando("escriba on", "escriba") == "ligar"
    assert _acao_de_comando("Escriba LIGAR", "escriba") == "ligar"
    assert _acao_de_comando("/escriba ativa", "escriba") == "ligar"
    assert _acao_de_comando("escriba off", "escriba") == "desligar"
    assert _acao_de_comando("escriba parar", "escriba") == "desligar"
    assert _acao_de_comando("escriba status", "escriba") == "status"
    assert _acao_de_comando("escriba", "escriba") == "ajuda"
    assert _acao_de_comando("escriba xyz", "escriba") == "ajuda"


def test_texto_que_apenas_menciona_o_nome_nao_e_comando():
    """O prefixo tem que abrir a mensagem; citar o nome no meio nao conta."""
    assert _acao_de_comando("o escriba transcreveu certinho", "escriba") is None
    assert _acao_de_comando("bom dia", "escriba") is None
