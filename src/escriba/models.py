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
    remote_jid: str            # conversa de origem, COMO VEIO (pode ser @lid)
    from_me: bool
    push_name: str = ""
    participante: str = ""     # em grupo, quem falou; em conversa individual, vazio
    # O WhatsApp migrou parte do enderecamento para LID: `remoteJid` chega como
    # "<numero opaco>@lid", que NAO e o telefone, e o telefone real vem no
    # `remoteJidAlt` (o mesmo vale para `participantAlt` em grupo). Guardamos
    # os dois: o cru serve para falar com a Evolution, o alt para identificar.
    remote_jid_alt: str = ""
    participante_alt: str = ""
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
    def conversa(self) -> str:
        """A conversa em telefone, para allowlist e estado.

        Nunca use `remote_jid` cru para decidir politica: em LID ele e um
        numero opaco que jamais casa com a allowlist, e o audio some sem
        motivo aparente. Para grupo o proprio `remote_jid` ja e a identidade.
        """
        if self.e_grupo:
            return self.remote_jid
        return self.remote_jid_alt or self.remote_jid

    @property
    def autor(self) -> str:
        """Quem gravou o audio. Em grupo e o participante, nao a conversa."""
        return self.participante_alt or self.participante or self.conversa

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
    remote_jid: str            # como veio, pode ser @lid
    from_me: bool
    message_id: str = ""
    texto: str = ""
    remote_jid_alt: str = ""

    @property
    def conversa(self) -> str:
        """Mesma regra do Audio: telefone quando o JID cru vier em LID."""
        if self.remote_jid.endswith(SUFIXO_GRUPO):
            return self.remote_jid
        return self.remote_jid_alt or self.remote_jid


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
        # So completa o codigo do pais em numero brasileiro sem DDI. Numeros
        # internacionais iniciados por 1 (NANP: EUA/Canada) sao deixados
        # como recebidos.
        #
        # ATENCAO: essa checagem colide com DDD 11-19, que sao todos de Sao
        # Paulo e tambem comecam por "1" (ex.: "11987654321", DDD 11). Um
        # contato de SP sem o "55" na allowlist cai aqui e NAO recebe o
        # prefixo — o numero final fica invalido. Prefira sempre gravar o
        # numero com "55" na frente em config/escriba.yaml para contato
        # brasileiro; essa heuristica e so uma rede de seguranca.
        if not digitos.startswith("1"):
            digitos = "55" + digitos
    return digitos
