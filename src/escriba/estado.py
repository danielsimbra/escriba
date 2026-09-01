"""Estado persistente em SQLite. Duas responsabilidades, so duas.

1. **Quem esta ligado.** O "escriba on"/"escriba off" precisa sobreviver a
   restart do container — o estado nao pode morar so na memoria do processo.
2. **O que ja foi processado.** A Evolution reentrega o webhook quando nao
   recebe 200 rapido o bastante. Sem a reserva por id de mensagem, o mesmo
   audio seria transcrito duas vezes (duas chamadas de API pagas, duas
   respostas na conversa).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import normalizar_destino

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversas (
    remote_jid    TEXT PRIMARY KEY,
    ativo         INTEGER NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mensagens (
    message_id  TEXT PRIMARY KEY,
    remote_jid  TEXT NOT NULL,
    recebida_em TEXT NOT NULL,
    segundos    INTEGER NOT NULL DEFAULT 0,
    caracteres  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'recebida',
    erro        TEXT
);
CREATE INDEX IF NOT EXISTS ix_mensagens_recebida_em ON mensagens (recebida_em);
CREATE INDEX IF NOT EXISTS ix_mensagens_status ON mensagens (status);
"""


class Estado:
    def __init__(self, caminho: Path):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho = caminho
        with self._conectar() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        # timeout generoso: varios audios podem chegar juntos e o SQLite
        # serializa a escrita.
        conn = sqlite3.connect(self.caminho, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- liga/desliga por conversa ----------------------------------------
    def ativo(self, remote_jid: str, padrao: bool) -> bool:
        """Se a conversa nunca recebeu comando, vale o padrao do YAML."""
        chave = normalizar_destino(remote_jid)
        with self._conectar() as conn:
            linha = conn.execute(
                "SELECT ativo FROM conversas WHERE remote_jid = ?", (chave,)
            ).fetchone()
        return bool(linha["ativo"]) if linha else padrao

    def definir_ativo(self, remote_jid: str, ativo: bool) -> None:
        chave = normalizar_destino(remote_jid)
        with self._conectar() as conn:
            conn.execute(
                "INSERT INTO conversas (remote_jid, ativo, atualizado_em) "
                "VALUES (?, ?, ?) ON CONFLICT(remote_jid) DO UPDATE SET "
                "ativo = excluded.ativo, atualizado_em = excluded.atualizado_em",
                (chave, int(ativo), _agora()),
            )
        log.info("Conversa %s marcada como %s", chave, "ativa" if ativo else "inativa")

    def conversas(self) -> list[sqlite3.Row]:
        with self._conectar() as conn:
            return list(conn.execute(
                "SELECT remote_jid, ativo, atualizado_em FROM conversas "
                "ORDER BY atualizado_em DESC"
            ))

    # -- idempotencia do webhook ------------------------------------------
    def reservar(self, message_id: str, remote_jid: str) -> bool:
        """Marca a mensagem como em processamento. False = ja foi vista antes.

        E a defesa contra a reentrega do webhook. A reserva acontece ANTES de
        gastar chamada de API, entao a segunda entrega do mesmo audio para
        aqui.
        """
        with self._conectar() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO mensagens "
                "(message_id, remote_jid, recebida_em, status) VALUES (?, ?, ?, ?)",
                (message_id, normalizar_destino(remote_jid), _agora(), "processando"),
            )
            return bool(cursor.rowcount)

    def concluir(self, message_id: str, *, segundos: int = 0, caracteres: int = 0,
                 status: str = "ok", erro: str | None = None) -> None:
        with self._conectar() as conn:
            conn.execute(
                "UPDATE mensagens SET segundos = ?, caracteres = ?, status = ?, "
                "erro = ? WHERE message_id = ?",
                (segundos, caracteres, status, erro, message_id),
            )

    def liberar(self, message_id: str) -> None:
        """Solta a reserva para que uma reentrega possa tentar de novo.

        Usado quando a falha e de infraestrutura (rede, API fora do ar): a
        mensagem nao chegou a ser transcrita, entao nao ha por que bloquear
        uma nova tentativa.
        """
        with self._conectar() as conn:
            conn.execute("DELETE FROM mensagens WHERE message_id = ?", (message_id,))

    # -- manutencao --------------------------------------------------------
    def limpar_antigas(self, dias: int = 30) -> int:
        """Poda o historico de mensagens. As conversas ligadas ficam."""
        with self._conectar() as conn:
            cursor = conn.execute(
                "DELETE FROM mensagens WHERE recebida_em < datetime('now', ?)",
                (f"-{int(dias)} days",),
            )
            return cursor.rowcount or 0

    def estatisticas(self) -> dict[str, int]:
        with self._conectar() as conn:
            linha = conn.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(status = 'ok'), 0) AS ok, "
                "COALESCE(SUM(status = 'erro'), 0) AS erro, "
                "COALESCE(SUM(segundos), 0) AS segundos "
                "FROM mensagens"
            ).fetchone()
        return {
            "total": int(linha["total"]),
            "ok": int(linha["ok"]),
            "erro": int(linha["erro"]),
            "segundos": int(linha["segundos"]),
        }


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()
