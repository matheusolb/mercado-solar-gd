import pandas as pd

import logica_mercado as lm


def test_detectar_ultimo_mes_completo_descarta_queda_recente():
    totais = pd.Series([10, 10, 10, 10, 10, 10, 10, 10, 1],
                        index=[f"2024-{m:02d}" for m in range(1, 10)])
    assert lm.detectar_ultimo_mes_completo(totais) == "2024-08"


def test_detectar_ultimo_mes_completo_mantem_se_normal():
    totais = pd.Series([10, 10, 10, 10, 10, 10, 10, 10, 9],
                        index=[f"2024-{m:02d}" for m in range(1, 10)])
    assert lm.detectar_ultimo_mes_completo(totais) == "2024-09"


def test_top_marcas_por_pico_usa_maximo_nao_ultimo():
    df = pd.DataFrame({
        "ano_mes": ["2023-01", "2024-01"],
        "marca": ["A", "A"],
        "soma_kw": [100.0, 10.0],
    })
    top = lm.top_marcas_por_pico(df, [["2023-01"], ["2024-01"]], 1)
    assert top == ["A"]


def test_top_marcas_por_pico_ignora_outros_e_nao_informado():
    df = pd.DataFrame({
        "ano_mes": ["2024-01"] * 3,
        "marca": ["A", "Outros", "Não informado"],
        "soma_kw": [1.0, 1000.0, 1000.0],
    })
    top = lm.top_marcas_por_pico(df, [["2024-01"]], 5)
    assert top == ["A"]


def test_marca_para_grafico_dobra_fora_do_top():
    serie = pd.Series(["A", "B", "Não informado"])
    resultado = lm.marca_para_grafico(serie, ["A"])
    assert list(resultado) == ["A", "Outros", "Não informado"]
