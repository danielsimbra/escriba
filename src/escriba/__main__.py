"""Ponto de entrada do Escriba.

    python -m escriba                      # sobe o servidor de webhook (padrao)
    python -m escriba configurar-webhook   # registra o Escriba na instancia
    python -m escriba testar-evolution     # confere se a instancia esta pareada
    python -m escriba transcrever a.ogg    # roda o pipeline num arquivo local
    python -m escriba transcrever a.ogg --bruta   # mostra tambem o texto cru
    python -m escriba limpar texto.txt     # so a etapa de limpeza (sem audio)
    python -m escriba status               # configuracao e conversas ligadas
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, logging_setup
from .config import BASE_DIR, Settings, load_settings
from .estado import Estado
from .evolution import EvolutionClient
from .limpeza import Limpador
from .models import EXTENSOES, mime_base
from .pipeline import criar_servicos
from .transcricao import Transcritor

log = logging.getLogger("escriba")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="escriba", description="Transcricao limpa de audios do WhatsApp",
    )
    sub = parser.add_subparsers(dest="comando")

    sub.add_parser("servir", help="Sobe o servidor de webhook (padrao)")
    sub.add_parser("configurar-webhook", help="Registra o Escriba na instancia Evolution")
    sub.add_parser("testar-evolution", help="Confere a conexao com a Evolution API")
    p_tr = sub.add_parser("transcrever", help="Transcreve um arquivo de audio local")
    p_tr.add_argument("arquivo", help="Caminho do audio (.ogg, .mp3, .m4a, .wav)")
    p_tr.add_argument("--bruta", action="store_true",
                      help="Mostra tambem a transcricao antes da limpeza")
    p_li = sub.add_parser("limpar", help="Limpa um texto ja transcrito")
    p_li.add_argument("arquivo", nargs="?", default="-",
                      help="Arquivo de texto, ou '-' para ler da entrada padrao")
    sub.add_parser("status", help="Mostra a configuracao e as conversas ligadas")

    args = parser.parse_args(argv)
    comando = args.comando or "servir"

    try:
        cfg = load_settings()
    except Exception as exc:
        # Config quebrada e o erro mais comum na primeira execucao; sem stack trace.
        print(f"[escriba] Erro de configuracao: {exc}", file=sys.stderr)
        return 2

    logging_setup.configurar(cfg.log_level, BASE_DIR / "logs")

    acoes = {
        "servir": lambda: _servir(cfg),
        "configurar-webhook": lambda: _configurar_webhook(cfg),
        "testar-evolution": lambda: _testar_evolution(cfg),
        "transcrever": lambda: _transcrever(cfg, Path(args.arquivo), args.bruta),
        "limpar": lambda: _limpar(cfg, args.arquivo),
        "status": lambda: _status(cfg),
    }
    return acoes[comando]()


# -- comandos --------------------------------------------------------------
def _servir(cfg: Settings) -> int:
    import uvicorn

    from .webhook import criar_app

    servicos = criar_servicos(cfg)
    app = criar_app(servicos)

    log.info(
        "Escriba %s subindo | instancia=%s | %s:%d",
        __version__, cfg.evolution.instance, cfg.servidor.host, cfg.servidor.port,
    )
    log.info(
        "Transcricao: %s | Limpeza: %s | dry_run=%s",
        cfg.transcricao.model, cfg.claude.model, cfg.dry_run,
    )
    log.info("Regras carregadas de %s", cfg.regras_path)
    if cfg.permitir_todos:
        log.warning(
            "allowlist.permitir_todos=true: o Escriba vai atender QUALQUER conversa."
        )
    elif not cfg.conversas_autorizadas:
        log.warning(
            "Nenhuma conversa autorizada em %s. O Escriba vai ignorar tudo que "
            "chegar ate voce preencher allowlist.conversas.", cfg.regras_path,
        )
    else:
        log.info("Conversas autorizadas: %d", len(cfg.conversas_autorizadas))
    if not cfg.servidor.webhook_token:
        log.warning(
            "WEBHOOK_TOKEN vazio: qualquer um que alcance esta porta pode injetar "
            "evento falso. Preencha o token se o Escriba nao estiver isolado."
        )

    # access_log desligado: o pipeline ja registra o que importa (quem mandou,
    # quantos segundos, quantos caracteres). A linha por requisicao do uvicorn
    # so encheria o log com o mesmo POST /webhook repetido.
    uvicorn.run(
        app, host=cfg.servidor.host, port=cfg.servidor.port,
        log_level="info", access_log=False,
    )
    return 0


def _configurar_webhook(cfg: Settings) -> int:
    cliente = EvolutionClient(cfg.evolution)
    url = cfg.servidor.webhook_url
    if cfg.servidor.webhook_token:
        # O token viaja na URL registrada; assim a Evolution o devolve em toda
        # entrega sem precisar de configuracao de header no gateway.
        separador = "&" if "?" in url else "?"
        url = f"{url}{separador}token={cfg.servidor.webhook_token}"

    print(f"Instancia : {cfg.evolution.instance}")
    print(f"Evolution : {cfg.evolution.base_url}")
    print(f"Webhook   : {url}")
    resposta = cliente.configurar_webhook(url)
    print("\nWebhook registrado. Resposta da Evolution:")
    print(resposta)
    print(
        "\nConfira tambem se a instancia esta com WEBHOOK_BASE64 ligado; sem ele "
        "o Escriba busca a midia numa segunda chamada (funciona igual, so mais lento)."
    )
    return 0


def _testar_evolution(cfg: Settings) -> int:
    cliente = EvolutionClient(cfg.evolution)
    print(f"Instancia : {cfg.evolution.instance}")
    print(f"URL       : {cfg.evolution.base_url}")
    if not cliente.conectado():
        print("\nInstancia NAO conectada. Abra o manager da Evolution e leia o QR Code.")
        return 1
    print("\nInstancia conectada.")
    try:
        print("Webhook atual:", cliente.webhook_atual())
    except Exception as exc:  # noqa: BLE001 - diagnostico, nao pode derrubar
        print(f"Nao foi possivel ler o webhook configurado: {exc}")
    return 0


def _transcrever(cfg: Settings, arquivo: Path, mostrar_bruta: bool) -> int:
    if not arquivo.exists():
        print(f"[escriba] Arquivo nao encontrado: {arquivo}", file=sys.stderr)
        return 2

    conteudo = arquivo.read_bytes()
    mimetype = _mimetype_por_extensao(arquivo)
    print(f"Arquivo   : {arquivo.name} ({len(conteudo) / 1024:.1f} KB, {mimetype})")

    bruta = Transcritor(cfg.transcricao).transcrever(
        conteudo, nome=arquivo.name, mimetype=mimetype,
    )
    if mostrar_bruta:
        print("\n--- transcricao bruta ---")
        print(bruta or "(vazia)")

    limpa = Limpador(cfg).limpar(bruta)
    print("\n--- transcricao limpa ---")
    print(limpa or "(sem fala inteligivel)")
    return 0


def _limpar(cfg: Settings, origem: str) -> int:
    if origem == "-":
        texto = sys.stdin.read()
    else:
        caminho = Path(origem)
        if not caminho.exists():
            print(f"[escriba] Arquivo nao encontrado: {caminho}", file=sys.stderr)
            return 2
        texto = caminho.read_text(encoding="utf-8")

    limpa = Limpador(cfg).limpar(texto)
    print(limpa or "(sem fala inteligivel)")
    return 0


def _status(cfg: Settings) -> int:
    estado = Estado(cfg.db_path)
    numeros = estado.estatisticas()

    print(f"Escriba {__version__}")
    print(f"Regras       : {cfg.regras_path}")
    print(f"Banco        : {cfg.db_path}")
    print(f"Transcricao  : {cfg.transcricao.model} (idioma: {cfg.transcricao.language or 'auto'})")
    print(f"Limpeza      : {cfg.claude.model}")
    print(f"Instancia    : {cfg.evolution.instance} @ {cfg.evolution.base_url}")
    print(f"DRY_RUN      : {cfg.dry_run}")
    print()
    if cfg.permitir_todos:
        print("Allowlist    : TODAS as conversas (permitir_todos=true)")
    else:
        print(f"Allowlist    : {len(cfg.conversas_autorizadas)} conversa(s)")
        for jid in sorted(cfg.conversas_autorizadas):
            print(f"               - {jid}")
    print(f"Padrao       : {'ligado' if cfg.comeca_ativo else 'desligado'} ate receber comando")
    print()

    conversas = estado.conversas()
    if conversas:
        print("Conversas com comando registrado:")
        for linha in conversas:
            marca = "ligado " if linha["ativo"] else "deslig."
            print(f"  [{marca}] {linha['remote_jid']}  ({linha['atualizado_em'][:19]})")
    else:
        print("Nenhuma conversa recebeu comando ainda.")
    print()
    print(
        f"Audios: {numeros['total']} recebidos | {numeros['ok']} transcritos | "
        f"{numeros['erro']} com erro | {numeros['segundos'] // 60} min de audio"
    )
    return 0


def _mimetype_por_extensao(arquivo: Path) -> str:
    """Mimetype a partir da extensao, para o teste pela CLI."""
    sufixo = arquivo.suffix.lower()
    for mime, ext in EXTENSOES.items():
        if ext == sufixo:
            return mime_base(mime)
    return "audio/ogg"


if __name__ == "__main__":
    raise SystemExit(main())
