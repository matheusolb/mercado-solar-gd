"""Script 5 (opcional): consolida os CSVs/parquets de dados/processados num unico
arquivo SQLite (dados/processados/mercado_solar.db), para quem preferir abrir com
DB Browser for SQLite, DBeaver, ou consultar via SQL em vez de CSV solto.

Usa sqlite3 (stdlib) via pandas.to_sql -- nenhuma dependencia nova.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import pandas as pd

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_PROCESSADOS = os.path.join(PASTA_PROJETO, "dados", "processados")
CAMINHO_DB = os.path.join(PASTA_PROCESSADOS, "mercado_solar.db")

CAMPOS = ["modulo", "inversor"]


def main():
    if os.path.exists(CAMINHO_DB):
        os.remove(CAMINHO_DB)
    con = sqlite3.connect(CAMINHO_DB)

    linhas_totais = {}
    for campo in CAMPOS:
        mapa = pd.read_csv(os.path.join(PASTA_PROCESSADOS, f"mapa_marca_{campo}.csv"))
        mapa.to_sql(f"mapa_marca_{campo}", con, index=False, if_exists="replace")
        linhas_totais[f"mapa_marca_{campo}"] = len(mapa)

        revisao = pd.read_csv(os.path.join(PASTA_PROCESSADOS, f"revisao_outros_{campo}.csv"))
        revisao.to_sql(f"revisao_outros_{campo}", con, index=False, if_exists="replace")
        linhas_totais[f"revisao_outros_{campo}"] = len(revisao)

        agregado = pd.read_parquet(os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}.parquet"))
        # faixa_potencia vem categorica do parquet -- o SQLite nao tem esse tipo, e
        # to_sql com categorical grava de forma imprevisivel. Converte pra texto.
        for col in agregado.select_dtypes(include=["category"]).columns:
            agregado[col] = agregado[col].astype(str)
        agregado.to_sql(f"agregado_marca_{campo}", con, index=False, if_exists="replace",
                         dtype=None, chunksize=50_000)
        linhas_totais[f"agregado_marca_{campo}"] = len(agregado)

        uf = pd.read_csv(os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}_uf.csv"))
        uf.to_sql(f"agregado_marca_{campo}_uf", con, index=False, if_exists="replace")
        linhas_totais[f"agregado_marca_{campo}_uf"] = len(uf)

        faixa = pd.read_csv(os.path.join(PASTA_PROCESSADOS, f"agregado_marca_{campo}_faixa.csv"),
                            encoding="utf-8-sig")
        faixa.to_sql(f"agregado_marca_{campo}_faixa", con, index=False, if_exists="replace")
        linhas_totais[f"agregado_marca_{campo}_faixa"] = len(faixa)

    cur = con.cursor()
    for campo in CAMPOS:
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{campo}_ano_mes ON agregado_marca_{campo}(ano_mes)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{campo}_marca ON agregado_marca_{campo}(marca)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{campo}_uf ON agregado_marca_{campo}(SigUF)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{campo}_faixa ON agregado_marca_{campo}(faixa_potencia)")

    metadados = pd.DataFrame([{
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "fonte": "ANEEL - dados abertos de geracao distribuida (empreendimento-geracao-distribuida, "
                 "empreendimento-gd-informacoes-tecnicas-fotovoltaica)",
        "metrica_potencia": "MdaPotenciaInstalada (kW) -- nao MdaPotenciaModulos/Inversores, que tem "
                             "outliers de unidade (ate 9996 kW numa instalacao de poucos kW reais)",
    }])
    metadados.to_sql("metadados", con, index=False, if_exists="replace")

    con.commit()
    con.close()

    tamanho_mb = os.path.getsize(CAMINHO_DB) / 1024**2
    print(f"banco salvo: {CAMINHO_DB} ({tamanho_mb:.1f} MB)")
    for tabela, n in linhas_totais.items():
        print(f"  {tabela}: {n:,} linhas")


if __name__ == "__main__":
    main()
