import pytest

import marca_utils as mu


def test_normalizar_texto_remove_prefixo_registro():
    assert mu.normalizar_texto("313 - SUNOVA") == "SUNOVA"


def test_normalizar_texto_nulo():
    assert mu.normalizar_texto(None) == mu.NULO
    assert mu.normalizar_texto("") == mu.NULO
    assert mu.normalizar_texto("   ") == mu.NULO


def test_normalizar_texto_acentos_e_pontuacao():
    assert mu.normalizar_texto("Canadian-Solar, Inc.") == "CANADIAN SOLAR INC"
    assert mu.normalizar_texto("Ápex Módulos") == "APEX MODULOS"


def test_classificar_nulo_vira_nao_informado():
    canonical, metodo = mu.classificar(mu.NULO, [])
    assert canonical == mu.NAO_INFORMADO
    assert metodo == "NULO"


def test_classificar_placeholder_vira_nao_informado():
    canonical, metodo = mu.classificar("NAO INFORMADO", [])
    assert canonical == mu.NAO_INFORMADO
    assert metodo == "TEXTO_PLACEHOLDER"


def test_classificar_alias_exato():
    regra = mu.RegraSemente("Canadian Solar", frozenset({"CANADIAN SOLAR"}), frozenset())
    canonical, metodo = mu.classificar("CANADIAN SOLAR", [regra])
    assert canonical == "Canadian Solar"
    assert metodo == "ALIAS_EXATO"


def test_classificar_token_unico():
    regra = mu.RegraSemente("BYD", frozenset(), frozenset({"BYD"}))
    canonical, metodo = mu.classificar("BYD SOLAR MODULOS", [regra])
    assert canonical == "BYD"
    assert metodo == "TOKEN"


def test_classificar_token_ambiguo_vira_outros():
    r1 = mu.RegraSemente("BYD", frozenset(), frozenset({"SOLAR"}))
    r2 = mu.RegraSemente("Canadian Solar", frozenset(), frozenset({"SOLAR"}))
    canonical, metodo = mu.classificar("EMPRESA SOLAR LTDA", [r1, r2])
    assert canonical == mu.OUTROS
    assert metodo == "TOKEN_AMBIGUO"


def test_classificar_desconhecido_vira_outros():
    canonical, metodo = mu.classificar("MARCA TOTALMENTE DESCONHECIDA XYZ", [])
    assert canonical == mu.OUTROS
    assert metodo == "NAO_ENCONTRADO"


def test_carregar_faixas_default_respeita_borda_legal():
    faixas = mu.carregar_faixas(None)
    assert 75.0 in faixas.limites


def test_carregar_faixas_rejeita_sem_borda_75kw(tmp_path):
    caminho = tmp_path / "faixas_invalidas.csv"
    caminho.write_text("limite_superior_kw,rotulo\n50,ate 50\n200,ate 200\n", encoding="utf-8")
    with pytest.raises(ValueError, match="75"):
        mu.carregar_faixas(str(caminho))


def test_validar_colunas_ok_nao_levanta():
    import pandas as pd
    df = pd.DataFrame({"a": [1], "b": [2]})
    mu.validar_colunas(df, ["a", "b"], "teste")


def test_validar_colunas_faltando_levanta_com_nome():
    import pandas as pd
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(ValueError, match="b"):
        mu.validar_colunas(df, ["a", "b"], "teste")
