"""Script 2: junta os dois parquets ANEEL (tecnico + cadastro) e agrega por marca,
tempo (mes/trimestre) e geografia (UF + municipio).

Depende do mapeamento gerado pelo Script 1 (montar_mapa_marcas.py) -- roda esse
primeiro. Gera um par de arquivos por campo (modulo/inversor): o grao completo
(mes x UF x municipio x marca) em parquet, e um rollup so por UF em CSV (o grao
completo pode passar de 1M linhas, perto do limite de linhas do Excel).
"""
from __future__ import annotations

import glob
import os

import pandas as pd

import marca_utils as mu

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_RAW = os.path.join(PASTA_PROJETO, "dados", "raw")
PASTA_PROCESSADOS = os.path.join(PASTA_PROJETO, "dados", "processados")

CAMPOS = {
    "modulo": {"coluna_raw": "NomFabricanteModulo", "mapa": os.path.join(PASTA_PROCESSADOS, "mapa_marca_modulo.csv")},
    "inversor": {"coluna_raw": "NomFabricanteInversor", "mapa": os.path.join(PASTA_PROCESSADOS, "mapa_marca_inversor.csv")},
}

CAMINHO_FAIXAS = os.path.join(PASTA_SCRIPTS, "faixas_potencia.csv")

CHAVES_AGRUPAMENTO = mu.COLUNAS_AGRUPAMENTO_AGREGADO


def resolver_arquivo(padrao: str) -> str:
    candidatos = glob.glob(os.path.join(PASTA_RAW, padrao))
    if not candidatos:
        raise FileNotFoundError(f"Nenhum arquivo '{padrao}' encontrado em {PASTA_RAW}")
    return max(candidatos, key=os.path.getmtime)


def carregar_base_tecnica() -> pd.DataFrame:
    arq = resolver_arquivo("empreendimento-gd-informacoes-tecnicas-fotovoltaica*.parquet")
    print(f"Lendo base tecnica: {arq}")
    df = pd.read_parquet(arq, columns=mu.COLUNAS_TECNICAS_ANEEL)
    mu.validar_colunas(df, mu.COLUNAS_TECNICAS_ANEEL, arq)
    print(f"  {len(df):,} linhas")
    return df


COLUNAS_GEOGRAFICAS_ANEEL = [
    "CodEmpreendimento", "SigTipoGeracao", "SigUF", "CodUFibge",
    "CodMunicipioIbge", "NomMunicipio", "NomRegiao",
    "DscClasseConsumo", "DscSubGrupoTarifario", "SigAgente",
]


def carregar_base_geografica() -> pd.DataFrame:
    arq = resolver_arquivo("empreendimento-geracao-distribuida*.parquet")
    print(f"Lendo base geografica: {arq}")
    df = pd.read_parquet(arq, columns=COLUNAS_GEOGRAFICAS_ANEEL)
    mu.validar_colunas(df, COLUNAS_GEOGRAFICAS_ANEEL, arq)
    df = df[df["SigTipoGeracao"] == "UFV"].drop(columns=["SigTipoGeracao"])
    # Renomeia pra nomes de negocio (o resto do pipeline e o modelo Power BI usam esses).
    df = df.rename(columns={
        "NomRegiao": "regiao", "DscClasseConsumo": "classe_consumo",
        "DscSubGrupoTarifario": "grupo_tarifario", "SigAgente": "distribuidora",
    })
    # Limpeza leve: os campos vem com sujeira de digitacao que gera categorias
    # duplicadas ("REBR" vs "REBR ", "A3a" vs "A3A"). strip resolve o espaco; o
    # grupo tarifario e um codigo (A1..A4, AS, B1..B4) entao uppercase unifica o case.
    for col in ["regiao", "classe_consumo", "grupo_tarifario", "distribuidora"]:
        df[col] = df[col].fillna("Não informado").astype(str).str.strip()
    df["grupo_tarifario"] = df["grupo_tarifario"].str.upper()
    print(f"  {len(df):,} linhas UFV")
    return df


def unir_bases(tecnica: pd.DataFrame, geografica: pd.DataFrame) -> pd.DataFrame:
    unido = tecnica.merge(geografica, how="inner",
                           left_on="CodGeracaoDistribuida", right_on="CodEmpreendimento")
    assert len(unido) == len(tecnica), (
        f"join deveria ser 1:1 mas {len(tecnica)} linhas tecnicas -> {len(unido)} linhas unidas"
    )
    return unido


def derivar_colunas_tempo(df: pd.DataFrame, coluna_data: str) -> pd.DataFrame:
    df = df.copy()
    dt = pd.to_datetime(df[coluna_data])
    df["ano"] = dt.dt.year.astype("int16")
    df["mes"] = dt.dt.month.astype("int8")
    df["trimestre"] = dt.dt.quarter.astype("int8")
    df["ano_mes"] = dt.dt.strftime("%Y-%m")
    df["ano_trimestre"] = df["ano"].astype(str) + "-Q" + df["trimestre"].astype(str)
    return df


def aplicar_marca(df: pd.DataFrame, campo: str) -> pd.Series:
    cfg = CAMPOS[campo]
    if not os.path.exists(cfg["mapa"]):
        raise FileNotFoundError(
            f"{cfg['mapa']} nao existe -- rode montar_mapa_marcas.py antes deste script"
        )
    mapa = pd.read_csv(cfg["mapa"], dtype=str, keep_default_na=False)
    mu.validar_colunas(mapa, ["raw_normalized", "canonical_brand"], cfg["mapa"])
    mapa_dict = dict(zip(mapa["raw_normalized"], mapa["canonical_brand"]))
    raw_norm = df[cfg["coluna_raw"]].map(mu.normalizar_texto)
    marca = raw_norm.map(mapa_dict)
    nao_mapeado = marca.isna().sum()
    if nao_mapeado:
        print(f"  aviso: {nao_mapeado} linhas com chave normalizada fora do mapa -> tratando como Outros "
              f"(rode montar_mapa_marcas.py de novo para cobrir)")
        marca = marca.fillna(mu.OUTROS)
    return marca


def agregar_por_marca(df: pd.DataFrame, coluna_marca: str) -> pd.DataFrame:
    # observed=True e obrigatorio: faixa_potencia e categorica, e sem isso o pandas
    # geraria o produto cartesiano de TODAS as faixas com todas as outras chaves
    # (mes x UF x municipio x marca), inflando o agregado com milhoes de linhas zeradas.
    agrupado = (
        df.groupby(CHAVES_AGRUPAMENTO + [coluna_marca], dropna=False, observed=True)["MdaPotenciaInstalada"]
        .agg(soma_kw="sum", qtd_instalacoes="count")
        .reset_index()
        .rename(columns={coluna_marca: "marca"})
    )
    agrupado["kw_medio_instalacao"] = agrupado["soma_kw"] / agrupado["qtd_instalacoes"]
    return agrupado


def checar_conservacao(agregado: pd.DataFrame, base: pd.DataFrame, campo: str):
    soma_base = base["MdaPotenciaInstalada"].sum()
    soma_agregado = agregado["soma_kw"].sum()
    tolerancia = max(1.0, abs(soma_base) * 1e-9)
    assert abs(soma_base - soma_agregado) < tolerancia, (
        f"[{campo}] soma de kW nao conservada: base={soma_base} agregado={soma_agregado}"
    )
    n_base = len(base)
    n_agregado = int(agregado["qtd_instalacoes"].sum())
    assert n_base == n_agregado, (
        f"[{campo}] contagem de linhas nao conservada: base={n_base} agregado={n_agregado}"
    )
    print(f"  [{campo}] checagem de conservacao OK -- soma_kw={soma_agregado:,.1f} qtd={n_agregado:,}")


def main():
    tecnica = carregar_base_tecnica()
    geografica = carregar_base_geografica()
    unido = unir_bases(tecnica, geografica)
    unido = derivar_colunas_tempo(unido, "DatConexao")

    unido["CodUFibge"] = unido["CodUFibge"].astype("Int64")
    unido["CodMunicipioIbge"] = unido["CodMunicipioIbge"].astype("Int64")
    unido["SigUF"] = unido["SigUF"].fillna("ND")

    faixas = mu.carregar_faixas(CAMINHO_FAIXAS)
    unido["faixa_potencia"] = faixas.classificar(unido["MdaPotenciaInstalada"])
    print(f"Faixas de potencia ({len(faixas.rotulos)}): {', '.join(faixas.rotulos)}")
    fora = unido["faixa_potencia"].isna().sum()
    if fora:
        # Nao derruba o script (a checagem de conservacao continua valendo -- as linhas
        # viram um grupo NaN), mas avisa alto: significa instalacao acima do teto
        # configurado, e o rotulo dela vai aparecer vazio no dashboard.
        acima = unido.loc[unido["faixa_potencia"].isna(), "MdaPotenciaInstalada"]
        print(f"  AVISO: {fora:,} instalacao(oes) fora das faixas (min={acima.min():,.1f} "
              f"max={acima.max():,.1f} kW) -- teto configurado e {faixas.teto_kw:,.0f} kW. "
              f"Ajuste faixas_potencia.csv.")

    for campo in CAMPOS:
        print("=" * 80)
        print(f"Agregando: {campo}")
        marca = aplicar_marca(unido, campo)
        unido_campo = unido.assign(_marca=marca)
        agregado = agregar_por_marca(unido_campo, "_marca")
        checar_conservacao(agregado, unido, campo)

        caminho_parquet = os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}.parquet")
        agregado.to_parquet(caminho_parquet, index=False)
        print(f"  grao completo salvo: {caminho_parquet} ({len(agregado):,} linhas)")

        rollup_uf = (
            agregado.groupby(["ano_mes", "ano", "mes", "trimestre", "ano_trimestre", "SigUF", "marca"], dropna=False)
            .agg(soma_kw=("soma_kw", "sum"), qtd_instalacoes=("qtd_instalacoes", "sum"))
            .reset_index()
        )
        rollup_uf["kw_medio_instalacao"] = rollup_uf["soma_kw"] / rollup_uf["qtd_instalacoes"]
        caminho_csv = os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}_uf.csv")
        rollup_uf.to_csv(caminho_csv, index=False)
        print(f"  rollup UF salvo: {caminho_csv} ({len(rollup_uf):,} linhas)")

        rollup_faixa = (
            agregado.groupby(["ano_mes", "ano", "faixa_potencia", "marca"], dropna=False, observed=True)
            .agg(soma_kw=("soma_kw", "sum"), qtd_instalacoes=("qtd_instalacoes", "sum"))
            .reset_index()
        )
        rollup_faixa["kw_medio_instalacao"] = rollup_faixa["soma_kw"] / rollup_faixa["qtd_instalacoes"]
        caminho_csv_faixa = os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}_faixa.csv")
        rollup_faixa.to_csv(caminho_csv_faixa, index=False, encoding="utf-8-sig")
        print(f"  rollup faixa salvo: {caminho_csv_faixa} ({len(rollup_faixa):,} linhas)")


if __name__ == "__main__":
    main()
