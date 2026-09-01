"""Ponte com a Evolution API v2: leitura do webhook e envio das respostas.

Duas metades:
  - `interpretar` traduz o payload cru do evento `messages.upsert` em um
    `Audio`, um `Comando` ou None. E funcao pura: nao toca a rede, e por isso
    da para testar com um JSON gravado.
  - `EvolutionClient` faz as chamadas HTTP (baixar midia, responder, presenca).

Importante para quem le os logs: um retorno sem erro no envio significa que a
Evolution API *aceitou* a requisicao. Isso nao e confirmacao de entrega ao
destinatario — a API v2 nao devolve esse recibo na chamada de envio.
"""
from __future__ import annotations

import base64 as b64
import logging
import unicodedata
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import EvolutionSettings
from .models import Audio, Comando, normalizar_destino

log = logging.getLogger(__name__)

TIMEOUT = (10, 120)  # (conexao, leitura) em segundos

# Envelopes que o WhatsApp coloca em volta da mensagem real. Mensagem
# temporaria e "ver uma vez" chegam com o conteudo aninhado; sem desembrulhar,
# o audio passa despercebido.
ENVELOPES = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
)


class EvolutionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Leitura do webhook
# ---------------------------------------------------------------------------
def interpretar(payload: dict[str, Any], prefixo_comando: str) -> Audio | Comando | None:
    """Traduz um evento `messages.upsert` em Audio, Comando ou None.

    Devolve None para tudo que nao interessa: figurinha, imagem, status,
    recibo de leitura, mensagem de sistema, texto que nao e comando.
    """
    dados = payload.get("data")
    if isinstance(dados, list):
        # Algumas versoes entregam um lote; nesse caso so o primeiro item
        # chega aqui — o chamador itera com `interpretar_lote`.
        dados = dados[0] if dados else None
    if not isinstance(dados, dict):
        return None

    chave = dados.get("key") or {}
    remote_jid = str(chave.get("remoteJid") or "")
    message_id = str(chave.get("id") or "")
    if not remote_jid or not message_id:
        return None
    if remote_jid == "status@broadcast":
        return None

    mensagem = _desembrulhar(dados.get("message") or {})
    from_me = bool(chave.get("fromMe"))

    audio = mensagem.get("audioMessage")
    if isinstance(audio, dict):
        return Audio(
            message_id=message_id,
            remote_jid=remote_jid,
            from_me=from_me,
            push_name=str(dados.get("pushName") or ""),
            participante=str(chave.get("participant") or dados.get("participant") or ""),
            mimetype=str(audio.get("mimetype") or "audio/ogg; codecs=opus"),
            segundos=int(audio.get("seconds") or 0),
            tamanho_bytes=int(audio.get("fileLength") or 0),
            ptt=bool(audio.get("ptt", True)),
            base64=str(dados.get("base64") or mensagem.get("base64") or ""),
            chave=chave,
            mensagem=mensagem,
        )

    texto = extrair_texto(mensagem)
    if texto:
        acao = _acao_de_comando(texto, prefixo_comando)
        if acao:
            return Comando(
                acao=acao, remote_jid=remote_jid, from_me=from_me,
                message_id=message_id, texto=texto.strip(),
            )
    return None


def interpretar_lote(payload: dict[str, Any], prefixo_comando: str) -> list[Audio | Comando]:
    """Igual a `interpretar`, mas aceita o formato em lote (`data` como lista)."""
    dados = payload.get("data")
    if not isinstance(dados, list):
        evento = interpretar(payload, prefixo_comando)
        return [evento] if evento else []
    eventos: list[Audio | Comando] = []
    for item in dados:
        evento = interpretar({**payload, "data": item}, prefixo_comando)
        if evento:
            eventos.append(evento)
    return eventos


def extrair_texto(mensagem: dict[str, Any]) -> str:
    """Texto digitado, venha ele como mensagem simples ou como resposta citada."""
    if not isinstance(mensagem, dict):
        return ""
    direto = mensagem.get("conversation")
    if isinstance(direto, str) and direto.strip():
        return direto
    estendida = mensagem.get("extendedTextMessage") or {}
    if isinstance(estendida, dict):
        texto = estendida.get("text")
        if isinstance(texto, str) and texto.strip():
            return texto
    return ""


def _desembrulhar(mensagem: dict[str, Any]) -> dict[str, Any]:
    """Remove os envelopes ate chegar no conteudo real (maximo de 3 camadas)."""
    atual = mensagem if isinstance(mensagem, dict) else {}
    for _ in range(3):
        for envelope in ENVELOPES:
            interna = atual.get(envelope)
            if isinstance(interna, dict) and isinstance(interna.get("message"), dict):
                atual = interna["message"]
                break
        else:
            break
    return atual


# Sinonimos aceitos depois do prefixo. Quem digita no celular abrevia e erra;
# reconhecer poucas formas so gera a sensacao de que "o comando nao funciona".
SINONIMOS = {
    "ligar": {"on", "ligar", "liga", "ligado", "ativar", "ativa", "start", "1"},
    "desligar": {"off", "desligar", "desliga", "desligado", "desativar",
                 "desativa", "parar", "para", "stop", "0"},
    "status": {"status", "estado"},
    "ajuda": {"ajuda", "help", "?", "comandos"},
}


def _acao_de_comando(texto: str, prefixo: str) -> str | None:
    """'/Escriba ON' -> 'ligar'. Texto que nao comeca com o prefixo -> None."""
    limpo = _sem_acento(texto.strip().lstrip("/!").lower())
    alvo = _sem_acento(prefixo.strip().lower())
    if not alvo or not limpo.startswith(alvo):
        return None
    resto = limpo[len(alvo):].strip(" :,.-")
    if not resto:
        # So o prefixo, sozinho: mostra o que da para fazer.
        return "ajuda"
    primeira = resto.split()[0]
    for acao, formas in SINONIMOS.items():
        if primeira in formas:
            return acao
    return "ajuda"


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto)
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------------
# Cliente HTTP
# ---------------------------------------------------------------------------
class EvolutionClient:
    def __init__(self, cfg: EvolutionSettings):
        self.cfg = cfg
        self.sessao = requests.Session()
        self.sessao.headers.update({
            "apikey": cfg.api_key,
            "Content-Type": "application/json",
        })

    # -- diagnostico ------------------------------------------------------
    def conectado(self) -> bool:
        """True se a instancia esta pareada com o celular e pronta para uso."""
        url = f"{self.cfg.base_url}/instance/connectionState/{self.cfg.instance}"
        try:
            resposta = self.sessao.get(url, timeout=TIMEOUT)
            resposta.raise_for_status()
            dados = resposta.json()
        except (requests.RequestException, ValueError) as exc:
            log.error("Nao foi possivel consultar a instancia Evolution: %s", exc)
            return False
        estado = dados.get("instance", {}).get("state") or dados.get("state") or ""
        log.info("Estado da instancia '%s': %s", self.cfg.instance, estado or "desconhecido")
        return estado == "open"

    # -- midia ------------------------------------------------------------
    def baixar_audio(self, audio: Audio) -> bytes:
        """Bytes do audio.

        Caminho rapido: o proprio webhook ja traz o base64 (quando a Evolution
        roda com WEBHOOK_BASE64=true). Caminho normal: pedir a midia de volta
        pelo id da mensagem — o arquivo no servidor do WhatsApp e criptografado
        e so a Evolution tem a chave para decifrar.
        """
        if audio.base64:
            log.debug("Audio %s veio em base64 no proprio webhook", audio.message_id)
            return b64.b64decode(audio.base64)

        dados = self._post(
            f"/chat/getBase64FromMediaMessage/{self.cfg.instance}",
            {"message": {"key": audio.chave}, "convertToMp4": False},
        )
        conteudo = dados.get("base64") or dados.get("media") or ""
        if not conteudo:
            raise EvolutionError(
                f"Evolution nao devolveu o audio da mensagem {audio.message_id}. "
                f"Resposta: {str(dados)[:300]}"
            )
        return b64.b64decode(conteudo)

    # -- envio ------------------------------------------------------------
    def responder(self, alvo: Audio | str, texto: str, *, citar: bool = True) -> dict[str, Any]:
        """Manda o texto na conversa, opcionalmente citando o audio original."""
        if isinstance(alvo, str):
            destino, citacao = normalizar_destino(alvo), None
        else:
            destino = normalizar_destino(alvo.remote_jid)
            citacao = (
                {"key": alvo.chave, "message": alvo.mensagem}
                if citar and alvo.chave
                else None
            )
        if not destino:
            raise EvolutionError("Destino vazio: nao da para responder.")

        payload: dict[str, Any] = {"number": destino, "text": texto}
        if citacao:
            payload["quoted"] = citacao

        log.info("Respondendo em %s (%d caracteres)", destino, len(texto))
        return self._post(f"/message/sendText/{self.cfg.instance}", payload)

    def digitando(self, remote_jid: str, segundos: int = 3) -> None:
        """Mostra "digitando..." na conversa. Best-effort: falha nao interrompe.

        Serve para a pessoa nao achar que o audio caiu no vazio enquanto a
        transcricao roda. Nao vale derrubar uma transcricao boa por causa disso.
        """
        try:
            self._post(
                f"/chat/sendPresence/{self.cfg.instance}",
                {
                    "number": normalizar_destino(remote_jid),
                    "delay": max(1, segundos) * 1000,
                    "presence": "composing",
                },
            )
        except Exception as exc:  # noqa: BLE001 - cosmetico, nunca fatal
            log.debug("Nao foi possivel enviar presenca de digitacao: %s", exc)

    # -- configuracao -----------------------------------------------------
    def configurar_webhook(self, url: str, eventos: list[str] | None = None,
                           base64_ativo: bool = True) -> dict[str, Any]:
        """Registra o Escriba como webhook da instancia.

        A v2.2+ espera o corpo aninhado em "webhook"; versoes anteriores
        aceitavam os campos soltos. Tenta o formato novo e cai no antigo se a
        API recusar o payload.
        """
        eventos = eventos or ["MESSAGES_UPSERT"]
        rota = f"/webhook/set/{self.cfg.instance}"
        aninhado = {
            "webhook": {
                "enabled": True,
                "url": url,
                "byEvents": False,
                "base64": base64_ativo,
                "events": eventos,
            }
        }
        try:
            return self._post(rota, aninhado)
        except EvolutionError as exc:
            log.warning("Formato aninhado recusado (%s); tentando o formato antigo", exc)
            return self._post(rota, {
                "enabled": True, "url": url, "webhook_by_events": False,
                "webhook_base64": base64_ativo, "events": eventos,
            })

    def webhook_atual(self) -> dict[str, Any]:
        url = f"{self.cfg.base_url}/webhook/find/{self.cfg.instance}"
        resposta = self.sessao.get(url, timeout=TIMEOUT)
        if resposta.status_code >= 400:
            raise EvolutionError(
                f"Evolution respondeu {resposta.status_code} ao consultar o webhook: "
                f"{resposta.text[:300]}"
            )
        try:
            return resposta.json()
        except ValueError:
            return {"raw": resposta.text}

    # -- internos ---------------------------------------------------------
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def _post(self, rota: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.cfg.base_url}{rota}"
        resposta = self.sessao.post(url, json=payload, timeout=TIMEOUT)
        if resposta.status_code >= 400:
            # Erro de payload nao se resolve com retry; falha direto e explica.
            raise EvolutionError(
                f"Evolution API respondeu {resposta.status_code} em {rota}: "
                f"{resposta.text[:400]}"
            )
        try:
            return resposta.json()
        except ValueError:
            return {"raw": resposta.text}
