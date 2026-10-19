"""Baixa da ANEEL (dados abertos) os dois parquets de geração distribuída usados pelo pipeline,
só quando a ANEEL publicou versão nova (compara a data de modificação do recurso com a última baixada).

Uso: python baixar_aneel.py   (também roda como etapa 0 do atualizar_tudo.py)
"""
from __future__ import annotations

import json
import os
import urllib.request

PASTA_RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dados", "raw")
API = "https://dadosabertos.aneel.gov.br/api/3/action/package_show?id=relacao-de-empreendimentos-de-geracao-distribuida"
ARQUIVOS = ("empreendimento-gd-informacoes-tecnicas-fotovoltaica.parquet", "empreendimento-geracao-distribuida.parquet")
ESTADO = os.path.join(PASTA_RAW, "versao_aneel.json")


def main() -> bool:
    """True se baixou alguma versão nova."""
    with urllib.request.urlopen(API, timeout=60) as r:
        recursos = {os.path.basename(x["url"]): x for x in json.load(r)["result"]["resources"]}
    novo = False
    estado = json.load(open(ESTADO, encoding="utf-8")) if os.path.exists(ESTADO) else {}
    for nome in ARQUIVOS:
        rec = recursos[nome]
        versao = rec.get("last_modified") or rec["metadata_modified"]
        destino = os.path.join(PASTA_RAW, nome)
        if estado.get(nome) == versao and os.path.exists(destino):
            print(f"  {nome}: já na versão {versao[:10]}")
            continue
        print(f"  {nome}: baixando versão {versao[:10]} ({int(rec.get('size') or 0) / 1e6:.0f} MB)...")
        temp = destino + ".baixando"
        urllib.request.urlretrieve(rec["url"], temp)
        if rec.get("size") and os.path.getsize(temp) != int(rec["size"]):  # download cortado não substitui o bom
            os.remove(temp)
            raise RuntimeError(f"{nome}: tamanho baixado difere do publicado; mantido o arquivo anterior")
        os.replace(temp, destino)
        prefixo = nome.removesuffix(".parquet")
        for antigo in os.listdir(PASTA_RAW):  # cópias antigas baixadas à mão, ex. "... (5).parquet"
            if antigo.startswith(prefixo + " (") and antigo.endswith(".parquet"):
                os.remove(os.path.join(PASTA_RAW, antigo))
        estado[nome] = versao
        novo = True
        with open(ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, indent=1)
    return novo


if __name__ == "__main__":
    main()
