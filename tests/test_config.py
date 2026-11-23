"""Qual arquivo de regras vale. O erro aqui e silencioso: carregar o template
no lugar da config real faz o Escriba ignorar todos os audios, sem excecao."""
from __future__ import annotations

from escriba.config import resolver_regras_path


def _montar(tmp_path, *, local: bool):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "escriba.yaml").write_text("allowlist: {}\n", encoding="utf-8")
    if local:
        (tmp_path / "config" / "escriba.local.yaml").write_text("allowlist: {}\n", encoding="utf-8")


def test_sem_local_usa_o_template(tmp_path, monkeypatch):
    monkeypatch.delenv("REGRAS_PATH", raising=False)
    _montar(tmp_path, local=False)
    assert resolver_regras_path(tmp_path).name == "escriba.yaml"


def test_local_tem_preferencia_sobre_o_template(tmp_path, monkeypatch):
    monkeypatch.delenv("REGRAS_PATH", raising=False)
    _montar(tmp_path, local=True)
    assert resolver_regras_path(tmp_path).name == "escriba.local.yaml"


def test_regras_path_explicito_vence_tudo(tmp_path, monkeypatch):
    monkeypatch.setenv("REGRAS_PATH", "/outro/lugar.yaml")
    _montar(tmp_path, local=True)
    assert str(resolver_regras_path(tmp_path)).replace("\\", "/") == "/outro/lugar.yaml"


def test_regras_path_vazio_e_ignorado(tmp_path, monkeypatch):
    """`REGRAS_PATH=` em branco no .env nao pode apontar para '.'."""
    monkeypatch.setenv("REGRAS_PATH", "  ")
    _montar(tmp_path, local=True)
    assert resolver_regras_path(tmp_path).name == "escriba.local.yaml"
