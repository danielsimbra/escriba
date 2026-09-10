"""Orquestracao: do evento do WhatsApp ate a resposta na conversa.

Ordem das decisoes, e ela importa:

    autorizada? -> ligada? -> e audio de outra pessoa? -> cabe nos limites?
    -> reserva o id -> baixa -> transcreve -> limpa -> responde

As tres primeiras perguntas sao de politica e nao custam nada. A reserva vem
logo antes da primeira chamada paga, para que uma reentrega do webhook nunca
gere duas transcricoes do mesmo audio.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from .config import Settings
from .estado import Estado
from .evolution import EvolutionClient, EvolutionError
from .limpeza import Limpador, LimpezaError
from .models import Audio, Comando
from .transcricao import TranscricaoError, Transcritor

log = logging.getLogger(__name__)

AJUDA = (
    "*Escriba*\n"
    "Transcrevo os audios desta conversa e devolvo o texto limpo.\n\n"
    "`{p} on` — comeco a transcrever aqui\n"
    "`{p} off` — paro de transcrever aqui\n"
    "`{p} status` — mostro como estou agora\n"
    "`{p} ajuda` — esta mensagem"
)


@dataclass
class Servicos:
    """Dependencias do pipeline, reunidas para poderem ser trocadas no teste."""

    cfg: Settings
    evolution: EvolutionClient
    transcritor: Transcritor
    limpador: Limpador
    estado: Estado
    semaforo: threading.Semaphore


def criar_servicos(cfg: Settings) -> Servicos:
    return Servicos(
        cfg=cfg,
        evolution=EvolutionClient(cfg.evolution),
        transcritor=Transcritor(cfg.transcricao),
        limpador=Limpador(cfg),
        estado=Estado(cfg.db_path),
        semaforo=threading.BoundedSemaphore(cfg.servidor.max_simultaneas),
    )


def processar(srv: Servicos, evento: Audio | Comando) -> None:
    """Ponto unico de entrada do pipeline. Nunca levanta excecao para cima.

    Quem chama e o handler do webhook, rodando em background: uma excecao ali
    so viraria stack trace orfao no log do uvicorn. Todo erro e tratado,
    registrado e — quando faz sentido — comunicado na conversa.
    """
    try:
        if isinstance(evento, Comando):
            _processar_comando(srv, evento)
        else:
            _processar_audio(srv, evento)
    except Exception:  # noqa: BLE001 - fronteira do pipeline
        log.exception("Falha nao tratada ao processar evento de %s", evento.remote_jid)


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def _processar_comando(srv: Servicos, comando: Comando) -> None:
    cfg = srv.cfg
    conversa = comando.conversa
    if not cfg.autorizada(conversa):
        # Conversa fora da allowlist nem sabe que o Escriba existe. Fica em
        # INFO, e nao DEBUG: e a rejeicao mais comum, e ficar muda faz parecer
        # que o webhook nunca recebeu nada.
        log.info(
            "Comando ignorado: %s nao esta na allowlist (jid cru: %s)",
            conversa, comando.remote_jid,
        )
        return
    if cfg.comandos_apenas_proprio and not comando.from_me:
        log.info(
            "Comando '%s' de %s ignorado: so o dono pode ligar/desligar",
            comando.acao, conversa,
        )
        return

    if comando.acao == "ligar":
        srv.estado.definir_ativo(conversa, True)
        resposta = "Escriba ligado nesta conversa. Os audios que chegarem aqui viram texto."
    elif comando.acao == "desligar":
        srv.estado.definir_ativo(conversa, False)
        resposta = (
            f"Escriba desligado nesta conversa. "
            f"Mande `{cfg.prefixo_comando} on` para voltar."
        )
    elif comando.acao == "status":
        resposta = _texto_status(srv, conversa)
    else:
        resposta = AJUDA.format(p=cfg.prefixo_comando)

    # Comando e respondido sempre na conversa onde foi digitado, mesmo com
    # destino fixo: quem mandou o comando espera a confirmacao ali.
    _responder(srv, comando.remote_jid, resposta)


def _texto_status(srv: Servicos, remote_jid: str) -> str:
    cfg = srv.cfg
    ligado = srv.estado.ativo(remote_jid, cfg.comeca_ativo)
    numeros = srv.estado.estatisticas()
    linhas = [
        "*Escriba*",
        f"Nesta conversa: {'ligado' if ligado else 'desligado'}",
        f"Transcricao: {cfg.transcricao.model}",
        f"Limpeza: {cfg.claude.model}",
        f"Audios ja transcritos: {numeros['ok']}",
    ]
    if cfg.dry_run:
        linhas.append("Modo de teste (DRY_RUN) ligado: nada e enviado de verdade.")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def _processar_audio(srv: Servicos, audio: Audio) -> None:
    cfg = srv.cfg

    conversa = audio.conversa

    # As duas rejeicoes abaixo ficam em INFO de proposito. Sao as mais comuns
    # e, quando eram DEBUG, um audio descartado nao imprimia UMA linha: do
    # lado de fora era identico a "o webhook nunca recebeu". O jid cru vai
    # junto porque em LID ele e diferente do telefone, e a diferenca entre os
    # dois e exatamente o que explica a rejeicao.
    if not cfg.autorizada(conversa):
        log.info(
            "Audio ignorado: %s nao esta na allowlist (jid cru: %s, de: %s)",
            conversa, audio.remote_jid, audio.push_name or "?",
        )
        return
    if audio.from_me and not cfg.processar_proprios:
        log.info(
            "Audio ignorado: gravado por mim em %s e processar_proprios=false",
            conversa,
        )
        return
    if not srv.estado.ativo(conversa, cfg.comeca_ativo):
        log.info("Audio ignorado: Escriba desligado em %s", conversa)
        return

    # A reserva vem antes ate das checagens de limite: se a Evolution
    # reentregar o evento, a pessoa nao recebe o mesmo aviso duas vezes.
    if not srv.estado.reservar(audio.message_id, conversa):
        log.info("Audio %s ja foi processado antes; ignorando reentrega", audio.message_id)
        return

    if cfg.duracao_minima > 0 and audio.segundos and audio.segundos < cfg.duracao_minima:
        # Filtro de custo, nao de erro: fica silencioso mesmo com
        # `resposta.avisar_erro` ligado. Avisar toda vez que alguem manda um
        # "ok" de 2s so poluiria a conversa.
        log.info(
            "Audio de %ds em %s abaixo do piso de %ds; ignorado sem aviso",
            audio.segundos, conversa, cfg.duracao_minima,
        )
        srv.estado.concluir(audio.message_id, segundos=audio.segundos,
                            status="ignorado", erro="duracao abaixo do piso")
        return

    if audio.segundos and audio.segundos > cfg.duracao_maxima:
        log.info(
            "Audio de %ds em %s passa do teto de %ds",
            audio.segundos, conversa, cfg.duracao_maxima,
        )
        srv.estado.concluir(audio.message_id, segundos=audio.segundos,
                            status="recusado", erro="duracao acima do teto")
        _avisar(srv, audio, (
            f"Esse audio tem {audio.segundos // 60}min e passa do limite "
            f"configurado ({cfg.duracao_maxima // 60}min). Nao transcrevi."
        ))
        return
    if audio.tamanho_bytes and audio.tamanho_bytes > cfg.tamanho_maximo_bytes:
        log.info(
            "Audio de %.1f MB em %s passa do teto configurado",
            audio.tamanho_bytes / 1024 / 1024, conversa,
        )
        srv.estado.concluir(audio.message_id, segundos=audio.segundos,
                            status="recusado", erro="tamanho acima do teto")
        _avisar(srv, audio, "Esse audio e grande demais para eu transcrever.")
        return

    with srv.semaforo:
        _transcrever_e_responder(srv, audio)


def _transcrever_e_responder(srv: Servicos, audio: Audio) -> None:
    cfg = srv.cfg
    quem = audio.push_name or audio.autor
    log.info(
        "Transcrevendo audio de %s em %s (%ds)",
        quem, audio.conversa, audio.segundos,
    )

    # Com destino fixo o "digitando" apareceria na conversa do contato, e
    # entregaria que existe um agente lendo os audios dele. So mostra quando
    # a resposta realmente vai sair naquela conversa.
    if cfg.mostrar_digitando and not cfg.dry_run and not cfg.destino_resposta:
        srv.evolution.digitando(audio.remote_jid, segundos=5)

    try:
        conteudo = srv.evolution.baixar_audio(audio)
        if cfg.salvar_audios:
            _guardar_audio(cfg.audios_dir, audio, conteudo)
        if len(conteudo) > cfg.tamanho_maximo_bytes:
            raise TranscricaoError(
                f"Audio baixado tem {len(conteudo) / 1024 / 1024:.1f} MB e passa do teto."
            )

        bruta = srv.transcritor.transcrever_audio(audio, conteudo)
        limpa = srv.limpador.limpar(bruta)

    except (requests.RequestException, EvolutionError) as exc:
        # Falha de infraestrutura: o audio nao chegou a ser transcrito. Libera
        # a reserva para que a reentrega do webhook possa tentar de novo.
        log.error("Falha de rede/Evolution no audio %s: %s", audio.message_id, exc)
        srv.estado.liberar(audio.message_id)
        _avisar(srv, audio, "Nao consegui baixar esse audio. Pode mandar de novo?")
        return
    except (TranscricaoError, LimpezaError) as exc:
        # Falha de conteudo (formato, tamanho, recusa): repetir daria no mesmo.
        log.error("Falha ao transcrever o audio %s: %s", audio.message_id, exc)
        srv.estado.concluir(audio.message_id, segundos=audio.segundos,
                            status="erro", erro=str(exc)[:500])
        _avisar(srv, audio, "Nao consegui transcrever esse audio.")
        return

    if not limpa:
        srv.estado.concluir(audio.message_id, segundos=audio.segundos, status="sem_fala")
        if cfg.texto_sem_fala:
            _avisar(srv, audio, cfg.texto_sem_fala, sempre=True)
        return

    _enviar_transcricao(srv, audio, limpa)
    srv.estado.concluir(
        audio.message_id, segundos=audio.segundos, caracteres=len(limpa), status="ok",
    )


def _enviar_transcricao(srv: Servicos, audio: Audio, texto: str) -> None:
    cfg = srv.cfg
    destino = cfg.destino_resposta
    partes = partir(
        cfg.prefixo_resposta + _cabecalho(cfg, audio) + texto,
        cfg.max_caracteres_mensagem,
    )

    if cfg.dry_run:
        log.info(
            "[DRY_RUN] Transcricao de %s NAO enviada para %s (%d parte(s)):\n%s",
            audio.conversa, destino or "conversa de origem", len(partes), texto,
        )
        return

    for indice, parte in enumerate(partes, start=1):
        corpo = parte if len(partes) == 1 else f"*(parte {indice}/{len(partes)})*\n\n{parte}"
        # Citar so faz sentido respondendo na propria conversa: a Evolution
        # nao consegue citar mensagem que esta em outro chat.
        if destino:
            srv.evolution.responder(destino, corpo, citar=False)
        else:
            srv.evolution.responder(
                audio, corpo, citar=cfg.responder_citando and indice == 1,
            )


def _cabecalho(cfg, audio: Audio) -> str:
    """Diz de quem e o audio. So aparece quando ha destino fixo.

    Sem isso, todas as transcricoes caem numa conversa so, sem citacao e sem
    nome: um monte de texto solto sem dono. Respondendo na origem o cabecalho
    seria redundante — a conversa ja diz quem falou.
    """
    if not cfg.destino_resposta:
        return ""
    quem = audio.push_name or _so_digitos(audio.autor)
    partes = [f"*{quem}*"]
    if audio.e_grupo:
        partes.append("em grupo")
    if audio.segundos:
        partes.append(_duracao(audio.segundos))
    return " · ".join(partes) + "\n\n"


def _duracao(segundos: int) -> str:
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos}m{resto:02d}s" if minutos else f"{resto}s"


def _so_digitos(jid: str) -> str:
    return jid.split("@", 1)[0].split(":", 1)[0]


def _avisar(srv: Servicos, audio: Audio, texto: str, *, sempre: bool = False) -> None:
    """Aviso curto. Silencioso quando `resposta.avisar_erro` e false.

    Segue o mesmo caminho da transcricao: com destino fixo, o contato nao
    recebe aviso de erro nenhum — se ele nao sabe que existe um agente, uma
    mensagem de "nao consegui transcrever" seria confusa do lado dele.
    """
    if not sempre and not srv.cfg.avisar_erro:
        return
    destino = srv.cfg.destino_resposta
    if srv.cfg.dry_run:
        log.info(
            "[DRY_RUN] Aviso NAO enviado para %s: %s",
            destino or audio.conversa, texto,
        )
        return
    try:
        if destino:
            srv.evolution.responder(
                destino, _cabecalho(srv.cfg, audio) + texto, citar=False,
            )
        else:
            srv.evolution.responder(audio, texto, citar=srv.cfg.responder_citando)
    except Exception as exc:  # noqa: BLE001 - aviso nao pode derrubar o fluxo
        log.warning("Nao foi possivel avisar %s: %s", destino or audio.conversa, exc)


def _responder(srv: Servicos, remote_jid: str, texto: str) -> None:
    if srv.cfg.dry_run:
        log.info("[DRY_RUN] Resposta NAO enviada para %s: %s", remote_jid, texto)
        return
    try:
        srv.evolution.responder(remote_jid, texto, citar=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("Nao foi possivel responder em %s: %s", remote_jid, exc)


def _guardar_audio(destino: Path, audio: Audio, conteudo: bytes) -> None:
    """Grava o audio em disco quando SALVAR_AUDIOS=true (depuracao)."""
    destino.mkdir(parents=True, exist_ok=True)
    nome = f"{datetime.now():%Y%m%d-%H%M%S}-{audio.message_id}{audio.extensao}"
    caminho = destino / nome
    caminho.write_bytes(conteudo)
    log.info("Audio guardado em %s", caminho)


def partir(texto: str, limite: int) -> list[str]:
    """Quebra o texto em mensagens, preferindo o fim de paragrafo, depois frase.

    O WhatsApp aceita mensagem longa, mas um paredao de texto no celular e
    ruim de ler — e o app corta a exibicao com "ler mais".
    """
    texto = texto.strip()
    # Piso de sanidade: abaixo disso a "mensagem" viraria picotada demais para
    # ser lida. Nao ha teto — o WhatsApp aguenta texto longo.
    limite = max(200, int(limite))
    if len(texto) <= limite:
        return [texto] if texto else []

    partes: list[str] = []
    atual = ""
    for bloco in texto.split("\n\n"):
        candidato = f"{atual}\n\n{bloco}" if atual else bloco
        if len(candidato) <= limite:
            atual = candidato
            continue
        if atual:
            partes.append(atual)
            atual = ""
        if len(bloco) <= limite:
            atual = bloco
            continue
        # Paragrafo maior que o limite: quebra por frase.
        for frase in re.split(r"(?<=[.!?…])\s+", bloco):
            # Frase sozinha maior que o limite (fala corrida, sem ponto):
            # quebra no espaco. Nunca descartar o resto — isso comeria
            # pedaco do que a pessoa falou.
            while len(frase) > limite:
                corte = frase.rfind(" ", 0, limite)
                corte = corte if corte > limite // 2 else limite
                if atual:
                    partes.append(atual)
                    atual = ""
                partes.append(frase[:corte].strip())
                frase = frase[corte:].lstrip()

            candidato = f"{atual} {frase}".strip()
            if len(candidato) <= limite:
                atual = candidato
            else:
                if atual:
                    partes.append(atual)
                atual = frase
    if atual.strip():
        partes.append(atual.strip())
    return partes
