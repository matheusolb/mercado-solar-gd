"""Ranking de modelos por família, para módulo (tecnologia de célula) e inversor (tipo).

Uso:
    python montar_ranking_modelos.py              # módulo e inversor
    python montar_ranking_modelos.py inversor     # só um campo

Lê o parquet técnico da ANEEL e o mapa de marcas já montado (montar_mapa_marcas.py).
O modelo é texto livre: normaliza para só letras e números, e agrupa em "família"
tirando a potência (módulo: CS6W-545MS e CS6W-550MS viram CS6WMS; inversor: tira
todos os dígitos, MIN 5000TL-X e MIN 6000TL-X viram MINTLX).

A categoria (tecnologia do módulo, tipo do inversor) vem de regras por marca em
scripts/<arquivo de regras> (regex sobre o modelo normalizado, primeira regra que
bate vence; marca "*" vale para todas). Modelo sem regra fica em "Não classificado"
-- não adivinha -- e o script mostra por que ficou de fora.

Rating (AAA, Tier 1, ranking) é por fabricante, não por modelo: vem de
dados/referencia/classificacao_marca_<campo>.csv.
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd

import marca_utils as mu
from montar_mapa_marcas import CAMPOS, PASTA_PROCESSADOS, PASTA_PROJETO, PASTA_SCRIPTS, resolver_arquivo_fotovoltaica

TOP_FAMILIAS = 300
SEM_MODELO = {"", "NAOHA", "NA", "NAOINFORMADO", "NAOSEAPLICA", "0", "NAOTEM", "OUTRO", "OUTROS"}
NAO_CLASSIFICADO = "Não classificado"

CONFIG = {
    "modulo": {
        "coluna_modelo": "NomModeloModulo",
        "regras": "tecnologia_modelo_modulo.csv",
        "rotulo": "tecnologia",
        "ordem": ["Policristalino", "Mono PERC", "N-type TOPCon", "HJT", "BC", NAO_CLASSIFICADO],
        "familia": lambda m: re.sub(r"(?<!\d)[3-7]\d{2}(?:WP|W)?(?!\d)", "", m),  # potência 300-799 W
        "ratings": {"PV-Tech ModuleTech": "pvtech_bankability", "BloombergNEF Tier 1": "bnef_tier1"},
    },
    "inversor": {
        "coluna_modelo": "NomModeloInversor",
        "regras": "tipo_modelo_inversor.csv",
        "rotulo": "tipo",
        "ordem": ["String", "Microinversor", "Híbrido", NAO_CLASSIFICADO],
        "familia": lambda m: re.sub(r"\d", "", m),
        "ratings": {"PV-Tech InverterTech": "pvtech_bankability", "Wood Mackenzie Inverter Ranking": "woodmac_ranking"},
    },
}


def chave_marca(nome: str) -> str:
    """'LONGi Solar' e 'LONGi' casam; Tongwei é vendida como TW Solar."""
    k = nome.split()[0].lower() if isinstance(nome, str) and nome else ""
    return {"tongwei": "tw"}.get(k, k)


def classificar(df: pd.DataFrame, regras: pd.DataFrame, rotulo: str) -> pd.Series:
    """Aplica as regras nos pares (marca, modelo) distintos e devolve alinhado a df."""
    pares = df[["marca", "modelo"]].drop_duplicates().reset_index(drop=True)
    cat = pd.Series(NAO_CLASSIFICADO, index=pares.index)
    livre = pd.Series(True, index=pares.index)
    for r in regras.itertuples():
        da_marca = True if r.marca == "*" else pares["marca"] == r.marca
        m = livre & da_marca & pares["modelo"].str.contains(r.padrao, regex=True)
        cat[m] = getattr(r, rotulo)
        livre &= ~m
    pares["cat"] = cat
    return df[["marca", "modelo"]].merge(pares, on=["marca", "modelo"], how="left")["cat"].set_axis(df.index)


def categoria_por_linha(campo: str, marca: pd.Series, modelo_bruto: pd.Series) -> pd.Series:
    """Tecnologia (módulo) ou tipo (inversor) de cada instalação -- usado pela agregação do dashboard."""
    cfg = CONFIG[campo]
    texto = modelo_bruto.astype(str)
    unicos = texto.unique()
    modelo = texto.map(dict(zip(unicos, map(mu.normalizar_modelo, unicos))))
    regras = pd.read_csv(os.path.join(PASTA_SCRIPTS, cfg["regras"]), dtype=str)
    cat = classificar(pd.DataFrame({"marca": marca, "modelo": modelo}), regras, cfg["rotulo"])
    cat[modelo.isin(SEM_MODELO) | (modelo.str.len() < 4)] = NAO_CLASSIFICADO
    return cat


def motivo_sem_classificacao(nc: pd.DataFrame, marcas_com_regra: set) -> pd.Series:
    """Separa o que ainda dá para mapear do que não tem como classificar."""
    marca_compacta = nc["marca"].fillna("").map(mu.normalizar_texto).str.replace(" ", "")
    repete_marca = [len(m) >= 5 and m[:5] in mc for m, mc in zip(nc["modelo"], marca_compacta)]
    motivo = pd.Series("pendente: marca com regra, modelo fora do padrão", index=nc.index)
    motivo[~nc["marca"].isin(marcas_com_regra)] = "pendente: marca ainda não pesquisada"
    motivo[repete_marca] = "sem como: campo modelo repete a marca"
    motivo[nc["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO])] = "sem como: marca não identificada"
    motivo[nc["sem_modelo"]] = "sem como: modelo vazio ou 'não há'"
    return motivo


def ratings_por_marca(campo: str, colunas: dict) -> pd.DataFrame:
    """A pasta dados/referencia não é versionada: sem ela, o ranking sai sem as colunas de rating."""
    arq = os.path.join(PASTA_PROJETO, "dados", "referencia", f"classificacao_marca_{campo}.csv")
    if not os.path.exists(arq):
        return pd.DataFrame(columns=list(colunas.values()))
    ref = pd.read_csv(arq, dtype=str, keep_default_na=False)
    ref = ref[(ref["canonical_brand"] != "") & ref["fonte"].isin(colunas)]
    ref["chave"] = ref["canonical_brand"].map(chave_marca)
    piv = ref.pivot_table(index="chave", columns="fonte", values="classificacao", aggfunc="first")
    return piv.rename(columns=colunas).reindex(columns=list(colunas.values()))


def rodar(campo: str, df: pd.DataFrame) -> None:
    cfg = CONFIG[campo]
    rotulo = cfg["rotulo"]
    print("=" * 80, f"\n{campo}")
    mapa = pd.read_csv(CAMPOS[campo]["mapa"], dtype=str, keep_default_na=False)
    v = df[["MdaPotenciaInstalada", "ano"]].copy()
    v["marca"] = df[CAMPOS[campo]["coluna_raw"]].map(mu.normalizar_texto).map(dict(zip(mapa["raw_normalized"], mapa["canonical_brand"])))
    bruto = df[cfg["coluna_modelo"]].astype(str)
    unicos = bruto.unique()
    v["modelo_bruto"] = bruto
    v["modelo"] = bruto.map(dict(zip(unicos, map(mu.normalizar_modelo, unicos))))
    v["sem_modelo"] = v["modelo"].isin(SEM_MODELO) | (v["modelo"].str.len() < 4)
    v["familia"] = v["modelo"].map(cfg["familia"])
    if campo == "modulo":
        # mesmo critério da agregação: marca vazia/ilegível é recuperada pelo código do modelo.
        # fabricante_pelo_modelo também mostra a fábrica quando a marca registrada é revenda.
        v["fabricante_pelo_modelo"] = mu.fabricante_pelo_modelo(bruto)
        sem_marca = v["marca"].isin([mu.OUTROS, mu.NAO_INFORMADO]) & v["fabricante_pelo_modelo"].notna()
        v.loc[sem_marca, "marca"] = v.loc[sem_marca, "fabricante_pelo_modelo"]
    regras = pd.read_csv(os.path.join(PASTA_SCRIPTS, cfg["regras"]), dtype=str)
    v[rotulo] = classificar(v, regras, rotulo)
    v.loc[v["sem_modelo"], rotulo] = NAO_CLASSIFICADO
    tot = v["MdaPotenciaInstalada"].sum()

    print(f"\nkW por {rotulo}:")
    print((v.groupby(rotulo)["MdaPotenciaInstalada"].sum() / tot * 100).sort_values(ascending=False).round(1).to_string())
    nc = v[v[rotulo] == NAO_CLASSIFICADO]
    motivo = motivo_sem_classificacao(nc, set(regras["marca"]))
    print("\npor que ficou sem classificação (% do kW total):")
    print((nc.groupby(motivo)["MdaPotenciaInstalada"].sum() / tot * 100).sort_values(ascending=False).round(1).to_string())
    mix = v[v["ano"] >= 2020].pivot_table(index="ano", columns=rotulo, values="MdaPotenciaInstalada", aggfunc="sum")
    print(f"\nmix de {rotulo} por ano (% do kW do ano):")
    print((mix.div(mix.sum(axis=1), axis=0) * 100).round(1).to_string())

    ok = v[~v["sem_modelo"]]
    g = ok.groupby(["marca", "familia"]).agg(
        soma_kw=("MdaPotenciaInstalada", "sum"), instalacoes=("MdaPotenciaInstalada", "size"),
        variacoes=("modelo", "nunique"), ano_mediano=("ano", "median"),
    ).sort_values("soma_kw", ascending=False).head(TOP_FAMILIAS)
    top = ok.set_index(["marca", "familia"]).loc[g.index]
    g[rotulo] = top.groupby(level=[0, 1]).apply(lambda x: x.groupby(rotulo)["MdaPotenciaInstalada"].sum().idxmax())
    g["exemplo_bruto"] = top.groupby(level=[0, 1])["modelo_bruto"].agg(lambda s: s.value_counts().index[0])
    if "fabricante_pelo_modelo" in top:
        g["fabricante_pelo_modelo"] = top.groupby(level=[0, 1])["fabricante_pelo_modelo"].agg(
            lambda s: s.value_counts().index[0] if s.notna().any() else "")
    g["pct_do_total_kw"] = (g["soma_kw"] / tot * 100).round(3)
    out = g.reset_index()
    out["chave"] = out["marca"].map(chave_marca)
    out = out.join(ratings_por_marca(campo, cfg["ratings"]), on="chave").drop(columns="chave")
    out["ano_mediano"] = out["ano_mediano"].astype(int)
    saida = os.path.join(PASTA_PROCESSADOS, f"ranking_modelos_{campo}.csv")
    out.to_csv(saida, index=False, sep=";", decimal=",", encoding="utf-8-sig")
    print(f"\nranking salvo: {saida} ({len(out)} famílias, {out['pct_do_total_kw'].sum():.1f}% do kW, "
          f"{(out[rotulo] == NAO_CLASSIFICADO).sum()} sem {rotulo})")


def main(argv: list[str] | None = None):
    campos = (argv if argv is not None else sys.argv[1:]) or list(CONFIG)
    colunas = ["MdaPotenciaInstalada", "DatConexao"] + [c for k in campos
                                                        for c in (CAMPOS[k]["coluna_raw"], CONFIG[k]["coluna_modelo"])]
    df = pd.read_parquet(resolver_arquivo_fotovoltaica(), columns=colunas)
    df["ano"] = pd.to_datetime(df["DatConexao"]).dt.year
    for campo in campos:
        rodar(campo, df)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
