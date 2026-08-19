"""Logica de negocio compartilhada entre montar_payload_dashboard.py (dashboard HTML)
e app.py (dashboard Streamlit) -- os dois calculam as mesmas coisas (ultimo mes
completo, top-N de marcas por pico, dobra pra "Outros") a partir do mesmo agregado,
so a camada de apresentacao troca de JSON/SVG por Plotly. Antes cada um reimplementava
essas funcoes por conta propria (copy-paste), e ja divergiram silenciosamente uma vez
(ver comentario em app.py sobre "cidade lider" divergente) -- esse modulo e a fonte
unica pra parte de calculo.
"""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

import marca_utils as mu


def detectar_ultimo_mes_completo(totais_mensais: pd.Series, limiar: float = 0.7, janela_referencia: int = 6) -> str:
    """A ANEEL tem atraso de cadastro: os ultimos meses antes da data do snapshot
    aparecem artificialmente baixos (registros ainda sendo processados), o que nao
    e queda real de mercado. Caminha do mes mais recente para tras, descartando
    qualquer mes cujo total fique muito abaixo da mediana dos meses anteriores,
    ate achar um mes "normal" -- data-driven, nao um numero fixo de meses.
    `totais_mensais` precisa vir ordenado por ano_mes crescente. Retorna a string
    'YYYY-MM' (quem precisar de pd.Period, converte no call site)."""
    meses = totais_mensais.index.tolist()
    i = len(meses) - 1
    while i > janela_referencia:
        referencia = totais_mensais.iloc[max(0, i - janela_referencia):i]
        mediana_ref = referencia.median()
        if mediana_ref > 0 and totais_mensais.iloc[i] < limiar * mediana_ref:
            i -= 1
            continue
        break
    return meses[i]


def picos_por_marca(df: pd.DataFrame, grupos_de_meses: Iterable[list[str]], coluna_marca: str = "marca") -> pd.Series:
    """Para cada marca, o maior soma_kw que ela atingiu em QUALQUER um dos grupos de
    meses dados (o pico, nao so o periodo mais recente) -- ranquear so pelo periodo
    mais recente esconderia justamente as marcas que mais caíram (ex: Jinko/Trina/
    JA Solar/Sunova, ainda grandes hoje mas menores que ja foram): o ponto de ver
    "a mudanca" e mostrar tanto quem subiu quanto quem caiu, nao so quem lidera agora.
    `grupos_de_meses` e qualquer iteravel de listas de strings 'YYYY-MM' -- os values
    de um dict de periodos, ou uma lista de anos ja expandida mes a mes."""
    picos: dict[str, float] = {}
    for meses in grupos_de_meses:
        subset = df[df["ano_mes"].isin(meses)]
        reais = subset[~subset[coluna_marca].isin([mu.OUTROS, mu.NAO_INFORMADO])]
        for marca, kw in reais.groupby(coluna_marca)["soma_kw"].sum().items():
            picos[marca] = max(picos.get(marca, 0.0), kw)
    return pd.Series(picos, dtype=float)


def top_marcas_por_pico(df: pd.DataFrame, grupos_de_meses: Iterable[list[str]], n: int,
                         coluna_marca: str = "marca") -> list[str]:
    """As `n` marcas de maior pico (ver picos_por_marca), sem Outros/Nao informado."""
    picos = picos_por_marca(df, grupos_de_meses, coluna_marca)
    return picos.sort_values(ascending=False).head(n).index.tolist()


def marca_para_grafico(serie_marca: pd.Series, marcas_top: list[str]) -> pd.Series:
    """Dobra qualquer marca fora de `marcas_top` em Outros -- Nao informado passa
    direto (e uma categoria diferente de "marca real que nao entrou no top-N")."""
    conjunto_top = set(marcas_top)
    return serie_marca.where(serie_marca.isin(conjunto_top) | (serie_marca == mu.NAO_INFORMADO), mu.OUTROS)
