"""Script 1: constrói/atualiza o mapeamento raw->marca canônica para módulo e inversor.

Uso:
    python montar_mapa_marcas.py                       # modo incremental (padrão)
    python montar_mapa_marcas.py --modo reclassificar_outros   # reclassifica só o backlog de Outros

Roda de novo quando o parquet ANEEL for atualizado. O modo incremental só classifica
chaves normalizadas novas -- nunca reabre decisões já tomadas nem linhas com
revisao_manual=True. Gera também a fila de revisão (Outros por kW) e a tabela de
cobertura de identificação por ano, usada para decidir o piso 2020 vs 2022 do dashboard.

Revisão manual direto na fila: a coluna "correcao_marca" em revisao_outros_*.csv
começa vazia -- se você escrever um nome de marca ali (numa linha específica) e
rodar este script de novo, essa correção é aplicada direto no mapa (com
revisao_manual=True, protegida de qualquer reclassificação automática futura)
antes de qualquer outra coisa. Serve pra corrigir um caso pontual sem precisar
editar a lista semente (seed_marca_*.csv) -- essa continua sendo o lugar certo
para uma marca que aparece em várias variações de texto (a correção na fila de
revisão vale só para aquele texto bruto específico).
"""
from __future__ import annotations

import argparse
import glob
import os
from datetime import date

import pandas as pd

import marca_utils as mu

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_RAW = os.path.join(PASTA_PROJETO, "dados", "raw")
PASTA_PROCESSADOS = os.path.join(PASTA_PROJETO, "dados", "processados")

CAMPOS = {
    "modulo": {
        "coluna_raw": "NomFabricanteModulo",
        "seed": os.path.join(PASTA_SCRIPTS, "seed_marca_modulo.csv"),
        "mapa": os.path.join(PASTA_PROCESSADOS, "mapa_marca_modulo.csv"),
        "revisao": os.path.join(PASTA_PROCESSADOS, "revisao_outros_modulo.csv"),
    },
    "inversor": {
        "coluna_raw": "NomFabricanteInversor",
        "seed": os.path.join(PASTA_SCRIPTS, "seed_marca_inversor.csv"),
        "mapa": os.path.join(PASTA_PROCESSADOS, "mapa_marca_inversor.csv"),
        "revisao": os.path.join(PASTA_PROCESSADOS, "revisao_outros_inversor.csv"),
    },
}

COLUNAS_MAPA = ["raw_normalized", "canonical_brand", "match_method", "exemplo_bruto",
                "data_primeira_ocorrencia", "revisao_manual", "observacoes"]


def resolver_arquivo_fotovoltaica() -> str:
    candidatos = glob.glob(os.path.join(PASTA_RAW, "empreendimento-gd-informacoes-tecnicas-fotovoltaica*.parquet"))
    if not candidatos:
        raise FileNotFoundError(
            f"Nenhum arquivo 'empreendimento-gd-informacoes-tecnicas-fotovoltaica*.parquet' encontrado em {PASTA_RAW}"
        )
    return max(candidatos, key=os.path.getmtime)


def carregar_mapa_existente(caminho: str) -> pd.DataFrame:
    if os.path.exists(caminho):
        df = pd.read_csv(caminho, dtype=str, keep_default_na=False)
        df["revisao_manual"] = df["revisao_manual"].map({"True": True, "False": False}).fillna(False)
        return df
    df = pd.DataFrame(columns=COLUNAS_MAPA)
    df["revisao_manual"] = False
    return df


def aplicar_correcoes_revisao(caminho_revisao: str, mapa_existente: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Le a fila de revisao de uma rodada anterior e aplica qualquer 'correcao_marca'
    preenchida a mao direto no mapa (revisao_manual=True, protegida de reclassificacao
    automatica futura). Nao falha se o arquivo nao existir ou for de uma versao sem
    essa coluna -- so nao aplica nada nesse caso."""
    if not os.path.exists(caminho_revisao):
        return mapa_existente, 0
    revisao_anterior = pd.read_csv(caminho_revisao, dtype=str, keep_default_na=False)
    if "correcao_marca" not in revisao_anterior.columns:
        return mapa_existente, 0
    correcoes = revisao_anterior[revisao_anterior["correcao_marca"].str.strip() != ""]
    if correcoes.empty:
        return mapa_existente, 0

    mapa_indexado = mapa_existente.set_index("raw_normalized", drop=False)
    hoje = date.today().isoformat()
    aplicadas = 0
    for _, linha in correcoes.iterrows():
        chave = linha["raw_normalized"]
        marca_corrigida = linha["correcao_marca"].strip()
        if chave not in mapa_indexado.index:
            continue
        mapa_indexado.loc[chave, "canonical_brand"] = marca_corrigida
        mapa_indexado.loc[chave, "match_method"] = "MANUAL"
        mapa_indexado.loc[chave, "revisao_manual"] = True
        obs_atual = mapa_indexado.loc[chave, "observacoes"]
        mapa_indexado.loc[chave, "observacoes"] = (obs_atual + " | " if obs_atual else "") + f"corrigido manualmente em {hoje} via revisao_outros"
        aplicadas += 1
    return mapa_indexado.reset_index(drop=True), aplicadas


def montar_mapa(campo: str, df_base: pd.DataFrame, modo: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = CAMPOS[campo]
    regras = mu.carregar_semente(cfg["seed"])

    raw = df_base[cfg["coluna_raw"]]
    kw = df_base["MdaPotenciaInstalada"]
    normalizado = raw.map(mu.normalizar_texto)

    tmp = pd.DataFrame({"raw": raw.values, "raw_normalized": normalizado.values, "kw": kw.values})
    resumo = tmp.groupby("raw_normalized").agg(
        qtd_instalacoes=("kw", "size"),
        soma_kw=("kw", "sum"),
        exemplo_bruto=("raw", "first"),
    )

    mapa_existente = carregar_mapa_existente(cfg["mapa"])
    mapa_existente, n_corrigidas = aplicar_correcoes_revisao(cfg["revisao"], mapa_existente)
    if n_corrigidas:
        print(f"  {n_corrigidas} correção(ões) manual(is) aplicada(s) via revisao_outros")
    existentes = set(mapa_existente["raw_normalized"])
    hoje = date.today().isoformat()

    if modo == "reclassificar_outros":
        alvo = mapa_existente[(mapa_existente["canonical_brand"] == mu.OUTROS) & (~mapa_existente["revisao_manual"])]
        chaves_para_classificar = list(alvo["raw_normalized"])
    else:
        chaves_para_classificar = [k for k in resumo.index if k not in existentes]

    novas_linhas = []
    for chave in chaves_para_classificar:
        canonical, metodo = mu.classificar(chave, regras)
        exemplo = resumo["exemplo_bruto"].get(chave, chave)
        novas_linhas.append({
            "raw_normalized": chave,
            "canonical_brand": canonical,
            "match_method": metodo,
            "exemplo_bruto": exemplo,
            "data_primeira_ocorrencia": hoje,
            "revisao_manual": False,
            "observacoes": "",
        })
    novas_df = pd.DataFrame(novas_linhas, columns=COLUNAS_MAPA)

    if modo == "reclassificar_outros":
        mapa_atualizado = mapa_existente.set_index("raw_normalized", drop=False)
        novas_indexada = novas_df.set_index("raw_normalized", drop=False)
        mapa_atualizado.update(novas_indexada)
        mapa_final = mapa_atualizado.reset_index(drop=True)
    else:
        mapa_final = pd.concat([mapa_existente, novas_df], ignore_index=True)

    print(f"  chaves totais no mapa: {len(mapa_final)}  (novas/reclassificadas nesta rodada: {len(novas_df)})")
    return mapa_final, resumo


def main(argv: list[str] | None = None):
    """`argv=None` le da linha de comando (uso normal). Passar `argv=[]` (lista vazia,
    nao None) forca o modo default mesmo se o processo chamador tiver seus proprios
    argumentos -- e o que atualizar_tudo.py faz, pra nao confundir --sqlite dali com
    --modo daqui."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=["incremental", "reclassificar_outros"], default="incremental")
    args = parser.parse_args(argv)

    arq = resolver_arquivo_fotovoltaica()
    print(f"Lendo {arq}")
    df = pd.read_parquet(arq, columns=mu.COLUNAS_TECNICAS_ANEEL)
    mu.validar_colunas(df, mu.COLUNAS_TECNICAS_ANEEL, arq)
    print(f"{len(df):,} linhas")
    ano = pd.to_datetime(df["DatConexao"]).dt.year

    for campo, cfg in CAMPOS.items():
        print("=" * 80)
        print(f"Campo: {campo}")
        mapa, resumo = montar_mapa(campo, df, args.modo)
        mapa.to_csv(cfg["mapa"], index=False)
        print(f"  mapeamento salvo: {cfg['mapa']}")

        mapa_dict = dict(zip(mapa["raw_normalized"], mapa["canonical_brand"]))
        raw_norm = df[cfg["coluna_raw"]].map(mu.normalizar_texto)
        marca = raw_norm.map(mapa_dict)

        total_kw = df["MdaPotenciaInstalada"].sum()
        identificado_mask = ~marca.isin([mu.OUTROS, mu.NAO_INFORMADO])
        kw_identificado = df.loc[identificado_mask, "MdaPotenciaInstalada"].sum()
        kw_outros = df.loc[marca == mu.OUTROS, "MdaPotenciaInstalada"].sum()
        kw_nao_informado = df.loc[marca == mu.NAO_INFORMADO, "MdaPotenciaInstalada"].sum()
        print(f"  cobertura kW total: identificado={kw_identificado / total_kw:.1%}  "
              f"outros={kw_outros / total_kw:.1%}  nao_informado={kw_nao_informado / total_kw:.1%}")

        outros_chaves = set(mapa.loc[mapa["canonical_brand"] == mu.OUTROS, "raw_normalized"])
        revisao = resumo.loc[resumo.index.isin(outros_chaves)].reset_index()
        revisao["pct_do_total_kw"] = revisao["soma_kw"] / total_kw * 100
        revisao = revisao.sort_values("soma_kw", ascending=False)
        revisao["correcao_marca"] = ""
        revisao[["raw_normalized", "exemplo_bruto", "qtd_instalacoes", "soma_kw", "pct_do_total_kw", "correcao_marca"]].to_csv(
            cfg["revisao"], index=False)
        print(f"  fila de revisão salva: {cfg['revisao']} ({len(revisao)} chaves em Outros) "
              f"-- preencha 'correcao_marca' numa linha e rode de novo para corrigir direto")
        if len(revisao):
            top5 = revisao.head(5)
            print("  top 5 'Outros' por kW (candidatos a entrar na semente):")
            for _, r in top5.iterrows():
                print(f"    {r['exemplo_bruto']!r}: {r['soma_kw']:.0f} kW ({r['pct_do_total_kw']:.2f}%)")

        cov = pd.DataFrame({"ano": ano, "kw": df["MdaPotenciaInstalada"], "identificado": identificado_mask})
        cov = cov[(cov["ano"] >= 2020) & (cov["ano"] <= ano.max())]
        por_ano = cov.groupby("ano").apply(
            lambda g: pd.Series({
                "total_kw": g["kw"].sum(),
                "pct_identificado": g.loc[g["identificado"], "kw"].sum() / g["kw"].sum() * 100,
            }),
            include_groups=False,
        )
        print("  cobertura de identificação por ano (kW real vs Outros/Não informado):")
        print(por_ano.to_string(float_format=lambda x: f"{x:,.1f}"))


if __name__ == "__main__":
    main()
