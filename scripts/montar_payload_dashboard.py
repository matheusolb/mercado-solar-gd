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

import logica_mercado as lm
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
# So afetam apresentacao (nao o calculo do payload) -- moram aqui porque este
# script e a fonte de verdade que os dois dashboards leem.
LEADERBOARD_TOP_N = 6
MULTIPLOS_TOP_N = 16

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
    mu.validar_colunas(df, mu.COLUNAS_AGREGADO, caminho)
    return df[df["ano_mes"] >= PISO_PADRAO].copy()


def escolher_marcas_grafico(df: pd.DataFrame, periodos_meses: dict[str, list[str]], n: int) -> list[str]:
    """Uma marca ganha cor propria no grafico se foi relevante em QUALQUER periodo
    da serie (o pico, nao so o mais recente). Ranquear so pelo periodo mais recente
    escondia justamente as marcas que mais caíram (ex: Jinko/Trina/JA Solar/Sunova
    somem do top-8 recente porque hoje sao menores, mesmo sendo as maiores quedas
    do leaderboard) -- e o ponto de ver "a mudanca" e mostrar tanto quem subiu
    quanto quem caiu no proprio grafico, nao so na tabela de crescimento.
    O ranking em si vem de logica_mercado.py; aqui so acrescenta Outros/Nao
    informado no fim."""
    top = lm.top_marcas_por_pico(df, periodos_meses.values(), n)
    return top + [mu.OUTROS, mu.NAO_INFORMADO]


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


def marcas_reais_indice(df: pd.DataFrame) -> list[str]:
    """Lista de todas as marcas reais (nao Outros/Nao informado) do campo inteiro,
    uma vez so -- serve de indice compartilhado pro breakdown por marca de cada
    municipio (ver totais_municipio_mw), pra nao repetir nome de marca em cada
    municipio-periodo (era a maior parte do peso de uma versao anterior)."""
    reais = df[~df["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    return sorted(reais["marca"].unique().tolist())


def serie_temporal(df: pd.DataFrame, coluna_periodo: str, marcas_grafico: list[str]) -> list[dict]:
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    tmp = df.copy()
    tmp["_marca_grafico"] = lm.marca_para_grafico(tmp["marca"], marcas_grafico[:TOP_N_GRAFICO_COR])
    agrupado_mw = tmp.groupby([coluna_periodo, "_marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado_mw = agrupado_mw.reindex(columns=marcas_fold, fill_value=0.0)
    agrupado_inst = tmp.groupby([coluna_periodo, "_marca_grafico"])["qtd_instalacoes"].sum().unstack(fill_value=0)
    agrupado_inst = agrupado_inst.reindex(index=agrupado_mw.index, columns=marcas_fold, fill_value=0)
    resultado = []
    for periodo in agrupado_mw.sort_index().index:
        linha_mw, linha_inst = agrupado_mw.loc[periodo], agrupado_inst.loc[periodo]
        resultado.append({
            "periodo": periodo,
            "total_mw": round(float(linha_mw.sum()) / 1000, 3),
            "total_inst": int(linha_inst.sum()),
            "valores_mw": [round(v / 1000, 3) for v in linha_mw.values],
            "valores_inst": [int(v) for v in linha_inst.values],
        })
    return resultado


def serie_mensal_todas_marcas(df: pd.DataFrame) -> dict:
    """Serie mensal em MW e em instalacoes para TODAS as marcas reais (nao so o
    top-15 do grafico principal) -- alimenta o filtro "ver uma marca especifica" e
    o grid "Cada marca individualmente", pra poder plotar a evolucao de qualquer
    marca (mesmo uma pequena que nunca entra no top-8/15, ex: Livoltek) nas duas
    metricas."""
    reais = df[~df["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    pivot_mw = reais.groupby(["ano_mes", "marca"])["soma_kw"].sum().unstack(fill_value=0.0).sort_index()
    pivot_inst = reais.groupby(["ano_mes", "marca"])["qtd_instalacoes"].sum().unstack(fill_value=0)
    pivot_inst = pivot_inst.reindex(index=pivot_mw.index, columns=pivot_mw.columns, fill_value=0)
    return {
        "periodos_mensais": pivot_mw.index.tolist(),
        "valores": {marca: [round(v / 1000, 3) for v in pivot_mw[marca].values] for marca in pivot_mw.columns},
        "valores_inst": {marca: [int(v) for v in pivot_inst[marca].values] for marca in pivot_mw.columns},
    }


def janela_12m(ancora: pd.Period) -> list[str]:
    return [str(ancora - i) for i in range(11, -1, -1)]



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


def totais_por_marca_instalacoes(df: pd.DataFrame, meses: list[str]) -> dict[str, int]:
    """Mesma logica de totais_por_marca_mw, mas contagem de instalacoes -- serve
    pra distinguir marca com poucas instalacoes grandes de marca com muitas
    pequenas, coisa que MW sozinho nao mostra."""
    subset = df[df["ano_mes"].isin(meses)]
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    por_marca = reais.groupby("marca")["qtd_instalacoes"].sum()
    return {m: int(v) for m, v in por_marca.items()}


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


def totais_municipio_mw(df: pd.DataFrame, meses: list[str], marcas_indice: dict[str, int]) -> dict[str, dict]:
    """Total de mw (TODO municipio, nenhum corte de top-N -- inclui Outros/Nao
    informado) + breakdown esparso por marca real (marcas_idx: [[indice, mw], ...],
    indice aponta pra "municipios_marcas_todas" no payload; so entra marca que
    de fato instalou ali, sem zero-preenchimento). Esse breakdown e o que permite ao
    cliente somar quantos periodos quiser e ainda calcular, com exatidao, quem
    lidera cada municipio no intervalo somado -- nao so um retrato de um ano."""
    subset = df[df["ano_mes"].isin(meses)]
    total_por_municipio = subset.groupby("CodMunicipioIbge").agg(
        kw=("soma_kw", "sum"), inst=("qtd_instalacoes", "sum"))
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    marca_por_municipio = reais.groupby(["CodMunicipioIbge", "marca"])["soma_kw"].sum()

    resultado: dict[str, dict] = {}
    for cod, linha in total_por_municipio.iterrows():
        if linha["kw"] <= 0:
            continue
        chave = str(int(cod)) if pd.notna(cod) else "ND"
        resultado[chave] = {"mw": round(linha["kw"] / 1000, 3), "inst": int(linha["inst"]), "marcas_idx": []}

    for (cod, marca), kw in marca_por_municipio.items():
        if kw <= 0:
            continue
        chave = str(int(cod)) if pd.notna(cod) else "ND"
        if chave in resultado:
            resultado[chave]["marcas_idx"].append([marcas_indice[marca], round(kw / 1000, 3)])
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
    subset["_marca_grafico"] = lm.marca_para_grafico(subset["marca"], marcas_grafico[:TOP_N_GRAFICO_COR])
    agrupado = subset.groupby([coluna, "_marca_grafico"], observed=True)["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado = agrupado.reindex(index=categorias, columns=marcas_fold, fill_value=0.0)
    return {str(cat): {m: round(v / 1000, 3) for m, v in linha.items()}
            for cat, linha in agrupado.iterrows()}


def totais_categoria_instalacoes(df: pd.DataFrame, meses: list[str], marcas_grafico: list[str],
                                  coluna: str, categorias: list[str]) -> dict[str, dict[str, int]]:
    """Mesma composicao de totais_categoria_mw, em quantidade de instalacoes em vez
    de MW -- alimenta o toggle MW/Instalacoes do grafico empilhado de
    Faixa/Classe/Tarifa/Distribuidora."""
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    subset = df[df["ano_mes"].isin(meses)].copy()
    subset["_marca_grafico"] = lm.marca_para_grafico(subset["marca"], marcas_grafico[:TOP_N_GRAFICO_COR])
    agrupado = subset.groupby([coluna, "_marca_grafico"], observed=True)["qtd_instalacoes"].sum().unstack(fill_value=0)
    agrupado = agrupado.reindex(index=categorias, columns=marcas_fold, fill_value=0)
    return {str(cat): {m: int(v) for m, v in linha.items()}
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
                                 n: int = 6, coluna_valor: str = "soma_kw") -> dict[str, list]:
    """Top marcas de cada categoria com indice de especializacao (share na categoria /
    share geral). Indice > 1 significa que a marca e mais forte naquela categoria do
    que no mercado como um todo -- e o que responde "quais marcas se sobressaem em
    cada [faixa/classe/tarifa/distribuidora]". NAO restringe ao top-8 global de
    proposito: especialistas de nicho so aparecem sem essa restricao, exatamente como
    a marca lider por municipio. coluna_valor troca a metrica-base (soma_kw ou
    qtd_instalacoes) -- alimenta o toggle MW/Instalacoes, chamado uma vez pra cada.
    Cada item: [marca, share_na_categoria_pct, share_geral_pct, indice]."""
    subset = df[df["ano_mes"].isin(meses)]
    total_geral = subset[coluna_valor].sum()
    reais = subset[~subset["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])]
    if total_geral <= 0 or reais.empty:
        return {str(c): [] for c in categorias}
    share_geral = reais.groupby("marca")[coluna_valor].sum() / total_geral * 100

    resultado: dict[str, list] = {}
    for cat in categorias:
        total_cat = subset[subset[coluna] == cat][coluna_valor].sum()
        na_cat = reais[reais[coluna] == cat]
        if total_cat <= 0 or na_cat.empty:
            resultado[str(cat)] = []
            continue
        share_cat = (na_cat.groupby("marca")[coluna_valor].sum() / total_cat * 100).sort_values(ascending=False)
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
    subset["_marca_grafico"] = lm.marca_para_grafico(subset["marca"], marcas_grafico[:TOP_N_GRAFICO_COR])
    agrupado = subset.groupby(["SigUF", "_marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0)
    agrupado = agrupado.reindex(columns=marcas_fold, fill_value=0.0)
    return {uf: {m: round(v / 1000, 3) for m, v in linha.items()} for uf, linha in agrupado.iterrows()}


def totais_uf_instalacoes(df: pd.DataFrame, meses: list[str], marcas_grafico: list[str]) -> dict[str, dict[str, int]]:
    """Mesma composicao de totais_uf_mw, em quantidade de instalacoes -- alimenta
    o toggle MW/Instalacoes do corte por estado/regiao."""
    marcas_fold = marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO]
    subset = df[df["ano_mes"].isin(meses)].copy()
    subset["_marca_grafico"] = lm.marca_para_grafico(subset["marca"], marcas_grafico[:TOP_N_GRAFICO_COR])
    agrupado = subset.groupby(["SigUF", "_marca_grafico"])["qtd_instalacoes"].sum().unstack(fill_value=0)
    agrupado = agrupado.reindex(columns=marcas_fold, fill_value=0)
    return {uf: {m: int(v) for m, v in linha.items()} for uf, linha in agrupado.iterrows()}


def montar_payload_campo(campo: str) -> dict:
    df = carregar_agregado(campo)

    totais_mensais = df.groupby("ano_mes")["soma_kw"].sum().sort_index()
    ultimo_mes_dados = pd.Period(totais_mensais.index[-1], freq="M")
    ancora = pd.Period(lm.detectar_ultimo_mes_completo(totais_mensais), freq="M")
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
    municipios_marcas_todas = marcas_reais_indice(df)
    municipios_marcas_indice = {m: i for i, m in enumerate(municipios_marcas_todas)}

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

    totais_mw, totais_inst, marcas_mw, marcas_inst = {}, {}, {}, {}
    uf_mw, uf_inst, uf_lideres_por_periodo, municipios_por_periodo = {}, {}, {}, {}
    faixa_mw, faixa_inst, faixa_perfil, faixa_especialistas, faixa_especialistas_inst = {}, {}, {}, {}, {}
    classe_mw, classe_inst, classe_perfil, classe_especialistas, classe_especialistas_inst = {}, {}, {}, {}, {}
    tarifa_mw, tarifa_inst, tarifa_perfil, tarifa_especialistas, tarifa_especialistas_inst = {}, {}, {}, {}, {}
    dist_mw, dist_inst, dist_perfil, dist_especialistas, dist_especialistas_inst = {}, {}, {}, {}, {}
    for chave, meses in periodos_meses.items():
        totais_mw[chave] = round(df[df["ano_mes"].isin(meses)]["soma_kw"].sum() / 1000, 3)
        totais_inst[chave] = int(df[df["ano_mes"].isin(meses)]["qtd_instalacoes"].sum())
        marcas_mw[chave] = totais_por_marca_mw(df, meses)
        marcas_inst[chave] = totais_por_marca_instalacoes(df, meses)
        uf_mw[chave] = totais_uf_mw(df, meses, marcas_grafico)
        uf_inst[chave] = totais_uf_instalacoes(df, meses, marcas_grafico)
        uf_lideres_por_periodo[chave] = lideres_uf(df, meses)
        municipios_por_periodo[chave] = totais_municipio_mw(df, meses, municipios_marcas_indice)

        faixa_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "faixa_potencia", faixas_ordem)
        faixa_inst[chave] = totais_categoria_instalacoes(df, meses, marcas_grafico, "faixa_potencia", faixas_ordem)
        faixa_perfil[chave] = perfil_categoria(df, meses, "faixa_potencia", faixas_ordem)
        faixa_especialistas[chave] = especialistas_por_categoria(df, meses, "faixa_potencia", faixas_ordem)
        faixa_especialistas_inst[chave] = especialistas_por_categoria(df, meses, "faixa_potencia", faixas_ordem, coluna_valor="qtd_instalacoes")

        classe_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "classe_consumo", classes_ordem)
        classe_inst[chave] = totais_categoria_instalacoes(df, meses, marcas_grafico, "classe_consumo", classes_ordem)
        classe_perfil[chave] = perfil_categoria(df, meses, "classe_consumo", classes_ordem)
        classe_especialistas[chave] = especialistas_por_categoria(df, meses, "classe_consumo", classes_ordem)
        classe_especialistas_inst[chave] = especialistas_por_categoria(df, meses, "classe_consumo", classes_ordem, coluna_valor="qtd_instalacoes")

        tarifa_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "grupo_tarifario", tarifas_ordem)
        tarifa_inst[chave] = totais_categoria_instalacoes(df, meses, marcas_grafico, "grupo_tarifario", tarifas_ordem)
        tarifa_perfil[chave] = perfil_categoria(df, meses, "grupo_tarifario", tarifas_ordem)
        tarifa_especialistas[chave] = especialistas_por_categoria(df, meses, "grupo_tarifario", tarifas_ordem)
        tarifa_especialistas_inst[chave] = especialistas_por_categoria(df, meses, "grupo_tarifario", tarifas_ordem, coluna_valor="qtd_instalacoes")

        dist_mw[chave] = totais_categoria_mw(df, meses, marcas_grafico, "_distribuidora_fold", distribuidoras_ordem)
        dist_inst[chave] = totais_categoria_instalacoes(df, meses, marcas_grafico, "_distribuidora_fold", distribuidoras_ordem)
        dist_perfil[chave] = perfil_categoria(df, meses, "_distribuidora_fold", distribuidoras_ordem)
        dist_especialistas[chave] = especialistas_por_categoria(df, meses, "_distribuidora_fold", distribuidoras_ordem)
        dist_especialistas_inst[chave] = especialistas_por_categoria(df, meses, "_distribuidora_fold", distribuidoras_ordem, coluna_valor="qtd_instalacoes")

    return {
        "marcas": marcas_grafico,
        "marcas_cor": marcas_grafico[:TOP_N_GRAFICO_COR] + [mu.OUTROS, mu.NAO_INFORMADO],
        "serie_mensal": serie_temporal(df, "ano_mes", marcas_grafico),
        "serie_trimestral": serie_temporal(df, "ano_trimestre", marcas_grafico),
        "ultimo_mes_completo": str(ancora),
        "meses_recentes_descartados_por_atraso": meses_descartados,
        "amostra_nao_identificado": amostra_nao_identificado(campo, TOP_N_NAO_IDENTIFICADO),
        "serie_por_marca": serie_mensal_todas_marcas(df),
        "municipios_info": municipios_info(df),
        "municipios_marcas_todas": municipios_marcas_todas,
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
            "totais_inst": totais_inst,
            "marcas_mw": marcas_mw,
            "marcas_inst": marcas_inst,
            "uf_mw": uf_mw,
            "uf_inst": uf_inst,
            "uf_lideres": uf_lideres_por_periodo,
            "municipios_mw": municipios_por_periodo,
            "faixa_mw": faixa_mw,
            "faixa_inst": faixa_inst,
            "faixa_perfil": faixa_perfil,
            "faixa_especialistas": faixa_especialistas,
            "faixa_especialistas_inst": faixa_especialistas_inst,
            "classe_mw": classe_mw,
            "classe_inst": classe_inst,
            "classe_perfil": classe_perfil,
            "classe_especialistas": classe_especialistas,
            "classe_especialistas_inst": classe_especialistas_inst,
            "tarifa_mw": tarifa_mw,
            "tarifa_inst": tarifa_inst,
            "tarifa_perfil": tarifa_perfil,
            "tarifa_especialistas": tarifa_especialistas,
            "tarifa_especialistas_inst": tarifa_especialistas_inst,
            "distribuidora_mw": dist_mw,
            "distribuidora_inst": dist_inst,
            "distribuidora_perfil": dist_perfil,
            "distribuidora_especialistas": dist_especialistas,
            "distribuidora_especialistas_inst": dist_especialistas_inst,
        },
    }


def main():
    payload = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "unidade": "MW",
        "piso_tempo": PISO_PADRAO,
        "top_n_cor": TOP_N_GRAFICO_COR,
        "leaderboard_top_n": LEADERBOARD_TOP_N,
        "multiplos_top_n": MULTIPLOS_TOP_N,
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
