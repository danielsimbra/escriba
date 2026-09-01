"""Estruturas de dados que atravessam o pipeline.

Tudo aqui e imutavel e sem dependencia de rede: sao os fatos do evento, ja
normalizados, para que o resto do codigo nao precise saber o formato cru do
webhook da Evolution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SUFIXO_GRUPO = "@g.us"


@dataclass(frozen=True)
class Audio:
    """Um audio recebido no WhatsApp, ja identificado no payload do webhook."""

    message_id: str
    remote_jid: str            # conversa de origem (contato ou grupo)
    from_me: bool
    push_name: str = ""
    participante: str = ""     # em grupo, quem falou; em conversa individual, vazio
    mimetype: str = "audio/ogg; codecs=opus"
    segundos: int = 0
    tamanho_bytes: int = 0
    ptt: bool = True           # true = gravado no microfone; false = arquivo anexado
    base64: str = ""           # vem preenchido quando WEBHOOK_BASE64 esta ligado
    chave: dict[str, Any] = field(default_factory=dict)     # key crua, para citar
    mensagem: dict[str, Any] = field(default_factory=dict)  # message cru, para citar

    @property
    def e_grupo(self) -> bool:
        return self.remote_jid.endswith(SUFIXO_GRUPO)

    @property
    def autor(self) -> str:
        """Quem gravou o audio. Em grupo e o participante, nao a conversa."""
        return self.participante or self.remote_jid

    @property
    def extensao(self) -> str:
        """Extensao coerente com o mimetype.

        A API de transcricao decide o decodificador pelo NOME do arquivo
        enviado no multipart; mandar audio/ogg com nome .mp3 faz a chamada
        falhar com "invalid file format".
        """
        return EXTENSOES.get(mime_base(self.mimetype), ".ogg")


@dataclass(frozen=True)
class Comando:
    """Um comando de controle digitado numa conversa ("escriba on")."""

    acao: str                  # "ligar" | "desligar" | "status" | "ajuda"
    remote_jid: str
    from_me: bool
    message_id: str = ""
    texto: str = ""


@dataclass(frozen=True)
class Transcricao:
    """Resultado das duas etapas: o que a API ouviu e o que o modelo limpou."""

    bruta: str
    limpa: str
    segundos: int = 0

    @property
    def vazia(self) -> bool:
        return not self.limpa.strip()


# -- mimetypes --------------------------------------------------------------
EXTENSOES = {
    "audio/ogg": ".ogg",
    "audio/opus": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
}


def mime_base(mimetype: str) -> str:
    """'audio/ogg; codecs=opus' -> 'audio/ogg'."""
    return (mimetype or "").split(";")[0].strip().lower()


# -- normalizacao de destino ------------------------------------------------
def normalizar_destino(bruto: str) -> str:
    """Normaliza numero individual ou JID de grupo para o formato da Evolution.

    Numero — '(00) 90000-0001' ou '+55 00 90000-0001' vira '5500900000001'.
    Grupo  — '120363123456789012@g.us' volta intacto: a Evolution exige o JID
    completo, sufixo incluido.
    JID individual ('5500900000001@s.whatsapp.net') tem o sufixo removido,
    porque as rotas de envio esperam so os digitos.
    """
    valor = (bruto or "").strip()
    if not valor:
        return ""
    if valor.endswith(SUFIXO_GRUPO):
        return valor
    if "@" in valor:
        valor = valor.split("@", 1)[0]
    # JID de aparelho vem como '5500900000001:12' quando ha multi-device.
    valor = valor.split(":", 1)[0]
    digitos = re.sub(r"\D", "", valor)
    if not digitos:
        return ""
    if not digitos.startswith("55") and len(digitos) in (10, 11):
        # So completa o codigo do pais em numero brasileiro sem DDI; numero
        # estrangeiro na allowlist e deixado como esta.
        digitos = "55" + digitos
    return digitos
