"""Partes da limpeza e do envio que nao dependem de modelo nenhum: como o
texto e cortado para caber no teto de tokens e no tamanho da mensagem.
"""
from __future__ import annotations

from escriba.limpeza import _desembrulhar_saida, dividir
from escriba.pipeline import partir


# -- divisao para o modelo --------------------------------------------------
def test_texto_curto_vira_um_lote_so():
    assert dividir("Bom dia.", 9000) == ["Bom dia."]


def test_texto_vazio_nao_vira_lote():
    assert dividir("   ", 9000) == []


def test_corte_cai_no_fim_da_frase():
    texto = ("Primeira frase bem comprida. " * 40).strip()
    lotes = dividir(texto, 600)
    assert len(lotes) > 1
    assert all(len(lote) <= 600 for lote in lotes)
    assert all(lote.endswith(".") for lote in lotes)


def test_fala_corrida_sem_pontuacao_e_cortada_no_espaco():
    """Transcricao bruta de fala rapida chega quase sem ponto final."""
    texto = "palavra " * 500
    lotes = dividir(texto, 700)
    assert len(lotes) > 1
    assert all(len(lote) <= 700 for lote in lotes)
    # Nenhuma palavra foi partida ao meio.
    assert all(pedaco == "palavra" for lote in lotes for pedaco in lote.split())


def test_juncao_dos_lotes_preserva_todo_o_conteudo():
    texto = "Uma frase. Outra frase. E mais uma frase aqui. Fim."
    assert " ".join(dividir(texto, 20)).split() == texto.split()


# -- embrulho que o modelo as vezes coloca ---------------------------------
def test_cerca_de_codigo_e_removida():
    assert _desembrulhar_saida("```\nBom dia.\n```") == "Bom dia."


def test_aspas_em_volta_do_texto_inteiro_sao_removidas():
    assert _desembrulhar_saida('"Bom dia."') == "Bom dia."


def test_aspas_internas_sao_preservadas():
    texto = 'Ele disse "vou amanha" e desligou.'
    assert _desembrulhar_saida(texto) == texto


# -- quebra da mensagem do WhatsApp ----------------------------------------
def test_mensagem_curta_nao_e_quebrada():
    assert partir("Bom dia.", 3500) == ["Bom dia."]


def test_quebra_preferindo_paragrafo():
    paragrafo = "Frase de exemplo com tamanho razoavel. " * 5
    texto = "\n\n".join([paragrafo.strip()] * 6)
    partes = partir(texto, 500)
    assert len(partes) > 1
    assert all(len(parte) <= 500 for parte in partes)


def test_paragrafo_gigante_e_quebrado_por_frase():
    texto = "Uma frase de tamanho medio aqui. " * 60
    partes = partir(texto.strip(), 500)
    assert all(len(parte) <= 500 for parte in partes)
    assert "".join(partes).count("Uma frase") == 60


def test_fala_corrida_sem_ponto_nao_perde_conteudo():
    """Audio falado rapido chega sem pontuacao; o corte nao pode comer texto."""
    texto = "palavra " * 400
    partes = partir(texto.strip(), 300)
    assert all(len(parte) <= 300 for parte in partes)
    assert sum(parte.count("palavra") for parte in partes) == 400
