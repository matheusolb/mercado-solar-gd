import pandas as pd
import pytest

import montar_agregado_marcas as mam


def _df_fabricado():
    return pd.DataFrame({
        "ano_mes": ["2024-01", "2024-01", "2024-02"],
        "ano": [2024, 2024, 2024],
        "mes": [1, 1, 2],
        "trimestre": [1, 1, 1],
        "ano_trimestre": ["2024-Q1", "2024-Q1", "2024-Q1"],
        "regiao": ["Sudeste", "Sudeste", "Sul"],
        "SigUF": ["SP", "SP", "PR"],
        "CodUFibge": [35, 35, 41],
        "CodMunicipioIbge": [3550308, 3550308, 4106902],
        "NomMunicipio": ["São Paulo", "São Paulo", "Curitiba"],
        "faixa_potencia": ["≤5 kW", "5–10 kW", "≤5 kW"],
        "classe_consumo": ["Residencial", "Residencial", "Comercial"],
        "grupo_tarifario": ["B1", "B1", "B1"],
        "distribuidora": ["ENEL SP", "ENEL SP", "COPEL"],
        "_marca": ["Canadian Solar", "Jinko Solar", "Outros"],
        "MdaPotenciaInstalada": [5.4, 8.2, 3.0],
    })


def test_agregar_por_marca_conserva_soma_e_contagem():
    df = _df_fabricado()
    agregado = mam.agregar_por_marca(df, "_marca")
    assert agregado["soma_kw"].sum() == pytest.approx(df["MdaPotenciaInstalada"].sum())
    assert int(agregado["qtd_instalacoes"].sum()) == len(df)


def test_checar_conservacao_nao_levanta_para_agregado_correto():
    df = _df_fabricado()
    agregado = mam.agregar_por_marca(df, "_marca")
    mam.checar_conservacao(agregado, df, "teste")


def test_checar_conservacao_detecta_soma_quebrada():
    df = _df_fabricado()
    agregado = mam.agregar_por_marca(df, "_marca")
    agregado.loc[0, "soma_kw"] += 1.0
    with pytest.raises(AssertionError, match="soma de kW nao conservada"):
        mam.checar_conservacao(agregado, df, "teste")


def test_checar_conservacao_detecta_contagem_quebrada():
    df = _df_fabricado()
    agregado = mam.agregar_por_marca(df, "_marca")
    agregado.loc[0, "qtd_instalacoes"] += 1
    with pytest.raises(AssertionError, match="contagem de linhas nao conservada"):
        mam.checar_conservacao(agregado, df, "teste")
