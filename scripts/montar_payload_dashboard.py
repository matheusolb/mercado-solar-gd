"""Script 3: gera o payload JSON leve para o dashboard a partir dos agregados
gerados pelo Script 2 (montar_agregado_marcas.py). Nunca rele os parquets brutos.

Piso de tempo padrao: 2020-01 (o toggle 2020+/2022+ do dashboard e so um recorte
do array mensal ja embutido, calculado no cliente -- nao precisa de dois payloads).

As comparacoes (liderança de crescimento e corte por UF) sao expostas por
"periodo" -- cada ano calendario disponivel (truncado no ultimo mes completo,
se o ano corrente ainda estiver em andamento) mais uma janela rolante dos
ultimos 12 meses completos. O cliente escolhe quaisquer dois periodos para
comparar, em vez de um par fixo mes-a-mes-anterior.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd

import marca_utils as mu

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_PROCESSADOS = os.path.join(PASTA_PROJETO, "dados", "processados")
PASTA_DASHBOARD = os.path.join(PASTA_PROJETO, "dashboard")
PISO_PADRAO = "2020-01"
PRIMEIRO_ANO = 2020
TOP_N_GRAFICO = 15
TOP_N_GRAFICO_COR = 10
TOP_N_NAO_IDENTIFICADO = 20

CAMPOS = ["modulo", "inversor"]


def amostra_nao_identificado(campo: str, n: int) -> list[dict]:
    """Le a fila de revisao (fora do escopo do piso de tempo -- e acumulado desde
    o inicio dos dados, porque o texto bruto original nao sobrevive a agregacao)
    e devolve os maiores textos NAO_ENCONTRADO por kW, para o dashboard explicar
    o que de fato esta sem marca identificada (em oposicao a marca identificada
    mas sem cor propria no grafico, que e outra fatia do mesmo bucket "Outros")."""
    caminho_mapa = os.path.join(PASTA_PROCESSADOS, f"mapa_marca_{campo}.csv")
    caminho_revisao = os.path.join(PASTA_PROCESSADOS, f"revisao_outros_{campo}.csv")
    if not os.path.exists(caminho_mapa) or not os.path.exists(caminho_revisao):
        return []
    mapa = pd.read_csv(caminho_mapa, dtype=str, keep_default_na=False)
    revisao = pd.read_csv(caminho_revisao)
    nao_encontrado_chaves = set(mapa.loc[mapa["match_method"] == "NAO_ENCONTRADO", "raw_normalized"])
    subset = revisao[revisao["raw_normalized"].isin(nao_encontrado_chaves)].sort_values("soma_kw", ascending=False)
    return [
        {"exemplo_bruto": r["exemplo_bruto"], "mw": round(r["soma_kw"] / 1000, 3), "pct_do_total_kw": round(r["pct_do_total_kw"], 3)}
        for _, r in subset.head(n).iterrows()
    ]


def carregar_agregado(campo: str) -> pd.DataFrame:
    caminho = os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}.parquet")
    if not os.path.exists(caminho):
        raise FileNotFoundError(f"{caminho} nao existe -- rode montar_agregado_marcas.py antes deste script")
    df = pd.read_parquet(caminho)
    return df[df["ano_mes"] >= PISO_PADRAO].copy()


def escolher_marcas_grafico(df: pd.DataFrame, periodos_meses: dict[str, list[str]], n: int) -> list[str]:
    """Uma marca ganha cor propria no grafico se foi relevante em QUALQUER periodo
    da serie (o pico, nao so o mais recente). Ranquear so pelo periodo mais recente
    escondia justamente as marcas que mais caíram (ex: Jinko/Trina/JA Solar/Sunova
    somem do top-8 recente porque hoje sao menores, mesmo sendo as maiores quedas
    do leaderboard) -- e o ponto de ver "a mudanca" e mostrar tanto quem subiu
    quanto quem caiu no proprio grafico, nao so na tabela de crescimento."""
    picos: dict[str, float] = {}
    for meses in periodos_meses.values():
        subset = df[df["ano_mes"].isin(meses)]
        reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
        totais = reais.groupby("marca")["soma_kw"].sum()
        for marca, kw in totais.items():
            if kw > picos.get(marca, 0.0):
                picos[marca] = kw
    top = pd.Series(picos).sort_values(ascending=False).head(n).index.tolist()
    return top + [mu.OUTROS, mu.NAO_INFORMADO]


def escolher_marcas_por_uf(df: pd.DataFrame, periodos_meses: dict[str, list[str]]) -> dict[str, list[str]]:
    """Top-8 local por UF (mesma logica de pico, mas dentro de cada estado) --
    pro corte por municipio usar marcas relevantes NAQUELE estado, nao o top-8
    do Brasil inteiro, que pode nao aparecer quase nada numa UF especifica e
    inflar "Outros" artificialmente sem motivo real."""
    resultado = {}
    for uf in df["SigUF"].dropna().unique():
        resultado[str(uf)] = escolher_marcas_grafico(df[df["SigUF"] == uf], periodos_meses, TOP_N_GRAFICO_COR)
    return resultado


def lideres_uf(df: pd.DataFrame, meses: list[str]) -> dict[str, dict]:
    """Marca real (entre TODAS, nao so o top-8 fixo) que mais instalou em cada UF
    no periodo -- existe porque restringir ao top-8 nacional pode reportar como
    "lider" uma marca que na verdade e menor que o proprio bucket Outros dali."""
    subset = df[df["ano_mes"].isin(meses)]
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    agrupado = reais.groupby(["SigUF", "marca"])["soma_kw"].sum()
    resultado: dict[str, tuple] = {}
    for (uf, marca), kw in agrupado.items():
        atual = resultado.get(uf)
        if atual is None or kw > atual[1]:
            resultado[uf] = (marca, kw)
    return {uf: {"nome": marca, "mw": round(kw / 1000, 3)} for uf, (marca, kw) in resultado.items()}


def lideres_municipio(df: pd.DataFrame, meses: list[str]) -> dict[str, dict]:
    """Mesma ideia de lideres_uf, por municipio."""
    subset = df[df["ano_mes"].isin(meses)]
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    agrupado = reais.groupby(["CodMunicipioIbge", "marca"])["soma_kw"].sum()
    resultado: dict[str, tuple] = {}
    for (cod, marca), kw in agrupado.items():
        chave = str(int(cod)) if pd.notna(cod) else "ND"
        atual = resultado.get(chave)
        if atual is None or kw > atual[1]:
            resultado[chave] = (marca, kw)
    return {chave: {"nome": marca, "mw": round(kw / 1000, 3)} for chave, (marca, kw) in resultado.items()}


def marca_para_grafico(serie_marca: pd.Series, marcas_grafico: list[str]) -> pd.Series:
    conjunto_top = set(marcas_grafico[:TOP_N_GRAFICO_COR])
    return serie_marca.where(serie_marca.isin(conjunto_top) | (serie_marca == mu.NAO_INFORMADO), mu.OUTROS)


def serie_temporal(df: pd.DataFrame, coluna_periodo: str, marcas_grafico: list[str]) -> list[dict]:
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    tmp = df.copy()
    tmp["_marca_grafico"] = marca_para_grafico(tmp["marca"], marcas_grafico)
    agrupado = tmp.groupby([coluna_periodo, "_marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado = agrupado.reindex(columns=marcas_fold, fill_value=0.0)
    resultado = []
    for periodo, linha in agrupado.sort_index().iterrows():
        valores_mw = [round(v / 1000, 3) for v in linha.values]
        resultado.append({
            "periodo": periodo,
            "total_mw": round(float(linha.sum()) / 1000, 3),
            "valores_mw": valores_mw,
        })
    return resultado


def serie_mensal_todas_marcas(df: pd.DataFrame) -> dict:
    """Serie mensal em MW para TODAS as marcas reais (nao so o top-15 do grafico
    principal) -- alimenta o filtro "ver uma marca especifica" do dashboard, pra
    poder plotar a evolucao de qualquer marca, mesmo uma pequena que nunca entra
    no top-8/15 (ex: Livoltek)."""
    reais = df[~df["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    pivot = reais.groupby(["ano_mes", "marca"])["soma_kw"].sum().unstack(fill_value=0.0).sort_index()
    return {
        "periodos_mensais": pivot.index.tolist(),
        "valores": {marca: [round(v / 1000, 3) for v in pivot[marca].values] for marca in pivot.columns},
    }


def janela_12m(ancora: pd.Period) -> list[str]:
    return [str(ancora - i) for i in range(11, -1, -1)]


def detectar_ultimo_mes_completo(totais_mensais: pd.Series, limiar: float = 0.7, janela_referencia: int = 6) -> pd.Period:
    """A ANEEL tem atraso de cadastro: os ultimos meses antes da data do snapshot
    aparecem artificialmente baixos (registros ainda sendo processados), o que nao
    e queda real de mercado. Caminha do mes mais recente para tras, descartando
    qualquer mes cujo total fique muito abaixo da mediana dos meses anteriores,
    ate achar um mes "normal" -- data-driven, nao um numero fixo de meses."""
    meses = totais_mensais.index.tolist()
    i = len(meses) - 1
    while i > janela_referencia:
        referencia = totais_mensais.iloc[max(0, i - janela_referencia):i]
        mediana_ref = referencia.median()
        if mediana_ref > 0 and totais_mensais.iloc[i] < limiar * mediana_ref:
            i -= 1
            continue
        break
    return pd.Period(meses[i], freq="M")


def construir_periodos(ancora: pd.Period) -> dict[str, list[str]]:
    periodos = {}
    for ano in range(PRIMEIRO_ANO, ancora.year + 1):
        meses = [f"{ano:04d}-{m:02d}" for m in range(1, 13)]
        meses = [m for m in meses if pd.Period(m, freq="M") <= ancora]
        if meses:
            periodos[str(ano)] = meses
    periodos["ultimos_12m"] = janela_12m(ancora)
    return periodos


def rotular_periodo(chave: str, meses: list[str]) -> str:
    if chave == "ultimos_12m":
        return "Últimos 12 meses"
    if len(meses) < 12:
        return f"{chave} (parcial)"
    return chave


def totais_por_marca_mw(df: pd.DataFrame, meses: list[str]) -> dict[str, float]:
    subset = df[df["ano_mes"].isin(meses)]
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    por_marca = reais.groupby("marca")["soma_kw"].sum()
    return {m: round(v / 1000, 3) for m, v in por_marca.items()}


def municipios_info(df: pd.DataFrame) -> dict[str, list]:
    """Nome + UF de cada municipio, uma vez so (nao muda por periodo) -- em vez
    de repetir esses dois campos em toda entrada de todo periodo (eram 2 dos 5
    campos de cada municipio, x8 periodos, so nesse texto repetido)."""
    pares = df[["CodMunicipioIbge", "NomMunicipio", "SigUF"]].drop_duplicates("CodMunicipioIbge")
    resultado = {}
    for cod, nome, uf in pares.itertuples(index=False):
        chave = str(int(cod)) if pd.notna(cod) else "ND"
        resultado[chave] = [nome, uf]
    return resultado


def totais_municipio_mw_com_marcas(df: pd.DataFrame, meses: list[str], marcas_por_uf: dict[str, list[str]],
                                    lideres: dict[str, dict]) -> dict[str, dict]:
    """Com valores_mw por marca (usando o top-8 LOCAL da UF -- ver
    escolher_marcas_por_uf) para alimentar o grafico empilhado por municipio --
    roda pra todo periodo, pra que o filtro global de periodo abra o mesmo
    grafico empilhado em qualquer recorte (nao so no periodo padrao)."""
    subset = df[df["ano_mes"].isin(meses)].copy()

    marca_grafico_col = pd.Series(index=subset.index, dtype=object)
    for uf, marcas_fold_uf in marcas_por_uf.items():
        mascara = subset["SigUF"] == uf
        if mascara.any():
            marca_grafico_col.loc[mascara] = marca_para_grafico(subset.loc[mascara, "marca"], marcas_fold_uf).values
    subset["_marca_grafico"] = marca_grafico_col

    agrupado = subset.groupby(["CodMunicipioIbge", "SigUF", "_marca_grafico"])["soma_kw"].sum()
    resultado: dict[str, dict] = {}
    for (cod, uf, marca), kw in agrupado.items():
        if kw <= 0:
            continue
        chave = str(int(cod)) if pd.notna(cod) else "ND"
        marcas_fold_uf = marcas_por_uf.get(uf, [mu.OUTROS, mu.NAO_INFORMADO])
        if chave not in resultado:
            lider = lideres.get(chave, {})
            resultado[chave] = {
                "mw": 0.0,
                "valores_mw": [0.0] * len(marcas_fold_uf),
                "marca_lider_nome": lider.get("nome", "—"),
                "marca_lider_mw": lider.get("mw", 0.0),
            }
        idx = marcas_fold_uf.index(marca)
        resultado[chave]["valores_mw"][idx] = round(kw / 1000, 3)
        resultado[chave]["mw"] = round(resultado[chave]["mw"] + kw / 1000, 3)
    return resultado


def totais_categoria_mw(df: pd.DataFrame, meses: list[str], marcas_grafico: list[str],
                         coluna: str, categorias: list[str]) -> dict[str, dict[str, float]]:
    """Composicao por marca de cada categoria (faixa de potencia, classe de consumo,
    grupo tarifario ou distribuidora -- qualquer coluna de baixa cardinalidade),
    usando o MESMO top-8 global das outras secoes de proposito: a leitura interessante
    e como a mistura de marcas MUDA de uma categoria pra outra, e isso exige as mesmas
    marcas nas mesmas cores em todas as linhas -- com um top-8 por categoria, as
    linhas nao seriam comparaveis entre si."""
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    subset = df[df["ano_mes"].isin(meses)].copy()
    subset["_marca_grafico"] = marca_para_grafico(subset["marca"], marcas_grafico)
    agrupado = subset.groupby([coluna, "_marca_grafico"], observed=True)["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado = agrupado.reindex(index=categorias, columns=marcas_fold, fill_value=0.0)
    return {str(cat): {m: round(v / 1000, 3) for m, v in linha.items()}
            for cat, linha in agrupado.iterrows()}


def perfil_categoria(df: pd.DataFrame, meses: list[str], coluna: str, categorias: list[str]) -> dict[str, list]:
    """Peso de cada categoria: [mw, qtd_instalacoes]. Os dois importam e contam
    historias diferentes -- uma categoria pode ser a maioria das instalacoes e so uma
    fracao da potencia, ou o contrario."""
    subset = df[df["ano_mes"].isin(meses)]
    agrupado = subset.groupby(coluna, observed=True).agg(
        kw=("soma_kw", "sum"), inst=("qtd_instalacoes", "sum"))
    agrupado = agrupado.reindex(categorias, fill_value=0.0)
    return {str(cat): [round(r["kw"] / 1000, 3), int(r["inst"])] for cat, r in agrupado.iterrows()}


def especialistas_por_categoria(df: pd.DataFrame, meses: list[str], coluna: str, categorias: list[str],
                                 n: int = 6) -> dict[str, list]:
    """Top marcas de cada categoria com indice de especializacao (share na categoria /
    share geral). Indice > 1 significa que a marca e mais forte naquela categoria do
    que no mercado como um todo -- e o que responde "quais marcas se sobressaem em
    cada [faixa/classe/tarifa/distribuidora]". NAO restringe ao top-8 global de
    proposito: especialistas de nicho so aparecem sem essa restricao, exatamente como
    a marca lider por municipio.
    Cada item: [marca, share_na_categoria_pct, share_geral_pct, indice]."""
    subset = df[df["ano_mes"].isin(meses)]
    total_geral = subset["soma_kw"].sum()
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    if total_geral <= 0 or reais.empty:
        return {str(c): [] for c in categorias}
    share_geral = reais.groupby("marca")["soma_kw"].sum() / total_geral * 100

    resultado: dict[str, list] = {}
    for cat in categorias:
        total_cat = subset[subset[coluna] == cat]["soma_kw"].sum()
        na_cat = reais[reais[coluna] == cat]
        if total_cat <= 0 or na_cat.empty:
            resultado[str(cat)] = []
            continue
        share_cat = (na_cat.groupby("marca")["soma_kw"].sum() / total_cat * 100).sort_values(ascending=False)
        itens = []
        for marca, sc in share_cat.head(n).items():
            sg = float(share_geral.get(marca, 0.0))
            # 4 casas, nao 1 -- o cliente ainda arredonda pra 1 casa na exibicao
            # (.toFixed(1)); guardar so 1-2 casas aqui faria um duplo-arredondamento
            # que pode virar a casa decimal perto de limites (ex: indice=1.15 ->
            # toFixed(1) da "1.1" em vez de "1.2" por representacao binaria do float).
            indice = round(sc / sg, 4) if sg > 0 else None
            itens.append([marca, round(float(sc), 2), round(sg, 2), indice])
        resultado[str(cat)] = itens
    return resultado


def escolher_top_categorias(df: pd.DataFrame, periodos_meses: dict[str, list[str]], coluna: str, n: int) -> list[str]:
    """Mesma logica de pico de escolher_marcas_grafico, aplicada a uma coluna
    generica -- usado pra distribuidora (103 valores, precisa de corte)."""
    picos: dict[str, float] = {}
    for meses in periodos_meses.values():
        subset = df[df["ano_mes"].isin(meses)]
        for cat, kw in subset.groupby(coluna)["soma_kw"].sum().items():
            picos[cat] = max(picos.get(cat, 0.0), kw)
    return pd.Series(picos).sort_values(ascending=False).head(n).index.tolist()


def fold_categoria(serie: pd.Series, top: list[str], rotulo_outros: str) -> pd.Series:
    return serie.where(serie.isin(set(top)), rotulo_outros)


def mapa_uf_regiao(df: pd.DataFrame) -> dict[str, str]:
    """UF -> regiao, pra montar o drill-down Brasil > Regiao > UF > Municipio sem
    precisar de payload novo por regiao (o cliente soma as UFs de uma regiao a partir
    de uf_mw, que ja existe). Regiao e 1:1 com UF nos dados (confirmado -- nenhuma UF
    aparece em mais de uma regiao)."""
    return {str(uf): str(reg) for uf, reg in df.groupby("SigUF")["regiao"].first().items()}


def totais_uf_mw(df: pd.DataFrame, meses: list[str], marcas_grafico: list[str]) -> dict[str, dict[str, float]]:
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    subset = df[df["ano_mes"].isin(meses)].copy()
    subset["_marca_grafico"] = marca_para_grafico(subset["marca"], marcas_grafico)
    agrupado = subset.groupby(["SigUF", "_marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado = agrupado.reindex(columns=marcas_fold, fill_value=0.0)
    return {uf: {m: round(v / 1000, 3) for m, v in linha.items()} for uf, linha in agrupado.iterrows()}


def montar_payload_campo(campo: str) -> dict:
    df = carregar_agregado(campo)

    totais_mensais = df.groupby("ano_mes")["soma_kw"].sum().sort_index()
    ultimo_mes_dados = pd.Period(totais_mensais.index[-1], freq="M")
    ancora = detectar_ultimo_mes_completo(totais_mensais)
    meses_descartados = (ultimo_mes_dados - ancora).n

    periodos_meses = construir_periodos(ancora)

    # As marcas em destaque no grafico (cor propria) sao escolhidas pelo pico --
    # o maior valor que a marca ja teve em QUALQUER ano ou nos ultimos 12 meses
    # -- nao so pelo acumulado desde 2020 nem so pelo periodo mais recente. So
    # acumulado esconde quem decolou recentemente (Astronergy); so o periodo
    # recente esconde quem mais caiu (Jinko/Trina/JA Solar/Sunova, que hoje sao
    # menores mas sao justamente as maiores quedas do leaderboard). Pico cobre
    # os dois lados da mudanca.
    marcas_grafico = escolher_marcas_grafico(df, periodos_meses, TOP_N_GRAFICO)
    marcas_por_uf = escolher_marcas_por_uf(df, periodos_meses)

    print(f"  [{campo}] ultimo mes nos dados: {ultimo_mes_dados}  ancora usada (ultimo mes completo): {ancora}  "
          f"({meses_descartados} mes(es) recentes descartados por atraso de cadastro da ANEEL)")

    ordem = sorted((k for k in periodos_meses if k != "ultimos_12m"), key=int) + ["ultimos_12m"]
    rotulos = {k: rotular_periodo(k, v) for k, v in periodos_meses.items()}
    extremos = {k: [v[0], v[-1]] for k, v in periodos_meses.items()}
    faixas_ordem = [str(c) for c in df["faixa_potencia"].cat.categories] \
        if hasattr(df["faixa_potencia"], "cat") else sorted(df["faixa_potencia"].dropna().unique())

    # Classe e tarifa: cardinalidade baixa (9 e 10 valores), cabe mostrar todas -- ordem
    # por MW total (todo o periodo, nao "pico") porque sao categorias regulatorias
    # estruturais, nao competitivas -- ordenar por pico faria sentido pra marca
    # ganhando/perdendo espaco, nao pra uma classe que so muda de tamanho lentamente.
    classes_ordem = df.groupby("classe_consumo")["soma_kw"].sum().sort_values(ascending=False).index.tolist()
    tarifas_ordem = df.groupby("grupo_tarifario")["soma_kw"].sum().sort_values(ascending=False).index.tolist()
    # Distribuidora: 103 valores, precisa de corte -- mesma logica de pico das marcas
    # (TOP_N_GRAFICO_COR pra caber num grafico empilhado sem virar ilegivel).
    ROTULO_OUTRAS_DIST = "Outras distribuidoras"
    top_distribuidoras = escolher_top_categorias(df, periodos_meses, "distribuidora", TOP_N_GRAFICO_COR)
    df = df.copy()
    df["_distribuidora_fold"] = fold_categoria(df["distribuidora"], top_distribuidoras, ROTULO_OUTRAS_DIST)
    distribuidoras_ordem = top_distribuidoras + [ROTULO_OUTRAS_DIST]

    totais_mw, marcas_mw, uf_mw, uf_lideres_por_periodo, municipios_por_periodo = {}, {}, {}, {}, {}
    faixa_mw, faixa_perfil, faixa_especialistas = {}, {}, {}
    classe_mw, classe_perfil, classe_especialistas = {}, {}, {}
    tarifa_mw, tarifa_perfil, tarifa_especialistas = {}, {}, {}
    dist_mw, dist_perfil, dist_especialistas = {}, {}, {}
    for chave, meses in periodos_meses.items():
        totais_mw[chave] = round(df[df["ano_mes"].isin(meses)]["soma_kw"].sum() / 1000, 3)
        marcas_mw[chave] = totais_por_marca_mw(df, meses)
        uf_mw[chave] = totais_uf_mw(df, meses, marcas_grafico)
        uf_lideres_por_periodo[chave] = lideres_uf(df, meses)
        lideres_mun = lideres_municipio(df, meses)
        municipios_por_periodo[chave] = totais_municipio_mw_com_marcas(df, meses, marcas_por_uf, lideres_mun)

        faixa_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "faixa_potencia", faixas_ordem)
        faixa_perfil[chave] = perfil_categoria(df, meses, "faixa_potencia", faixas_ordem)
        faixa_especialistas[chave] = especialistas_por_categoria(df, meses, "faixa_potencia", faixas_ordem)

        classe_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "classe_consumo", classes_ordem)
        classe_perfil[chave] = perfil_categoria(df, meses, "classe_consumo", classes_ordem)
        classe_especialistas[chave] = especialistas_por_categoria(df, meses, "classe_consumo", classes_ordem)

        tarifa_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "grupo_tarifario", tarifas_ordem)
        tarifa_perfil[chave] = perfil_categoria(df, meses, "grupo_tarifario", tarifas_ordem)
        tarifa_especialistas[chave] = especialistas_por_categoria(df, meses, "grupo_tarifario", tarifas_ordem)

        dist_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "_distribuidora_fold", distribuidoras_ordem)
        dist_perfil[chave] = perfil_categoria(df, meses, "_distribuidora_fold", distribuidoras_ordem)
        dist_especialistas[chave] = especialistas_por_categoria(df, meses, "_distribuidora_fold", distribuidoras_ordem)

    return {
        "marcas": marcas_grafico,
        "marcas_cor": marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO],
        "marcas_por_uf": marcas_por_uf,
        "serie_mensal": serie_temporal(df, "ano_mes", marcas_grafico),
        "serie_trimestral": serie_temporal(df, "ano_trimestre", marcas_grafico),
        "ultimo_mes_completo": str(ancora),
        "meses_recentes_descartados_por_atraso": meses_descartados,
        "amostra_nao_identificado": amostra_nao_identificado(campo, TOP_N_NAO_IDENTIFICADO),
        "serie_por_marca": serie_mensal_todas_marcas(df),
        "municipios_info": municipios_info(df),
        "faixas_ordem": faixas_ordem,
        "classes_ordem": classes_ordem,
        "tarifas_ordem": tarifas_ordem,
        "distribuidoras_ordem": distribuidoras_ordem,
        "uf_regiao": mapa_uf_regiao(df),
        "periodos": {
            "ordem": ordem,
            "rotulos": rotulos,
            "extremos": extremos,
            "totais_mw": totais_mw,
            "marcas_mw": marcas_mw,
            "uf_mw": uf_mw,
            "uf_lideres": uf_lideres_por_periodo,
            "municipios_mw": municipios_por_periodo,
            "faixa_mw": faixa_mw,
            "faixa_perfil": faixa_perfil,
            "faixa_especialistas": faixa_especialistas,
            "classe_mw": classe_mw,
            "classe_perfil": classe_perfil,
            "classe_especialistas": classe_especialistas,
            "tarifa_mw": tarifa_mw,
            "tarifa_perfil": tarifa_perfil,
            "tarifa_especialistas": tarifa_especialistas,
            "distribuidora_mw": dist_mw,
            "distribuidora_perfil": dist_perfil,
            "distribuidora_especialistas": dist_especialistas,
        },
    }


def main():
    payload = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "unidade": "MW",
        "piso_tempo": PISO_PADRAO,
        "campos": {campo: montar_payload_campo(campo) for campo in CAMPOS},
    }
    caminho = os.path.join(PASTA_DASHBOARD, "dashboard_dados.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tamanho_kb = os.path.getsize(caminho) / 1024
    print(f"payload salvo: {caminho} ({tamanho_kb:.1f} KB)")
    for campo, dados in payload["campos"].items():
        print(f"  [{campo}] marcas no grafico: {len(dados['marcas'])}  meses: {len(dados['serie_mensal'])}  "
              f"periodos: {dados['periodos']['ordem']}")


if __name__ == "__main__":
    main()
