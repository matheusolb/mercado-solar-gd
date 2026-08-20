"""Roda o pipeline inteiro em sequencia: mapeamento de marcas -> agregado -> payload
-> dashboard HTML. Um comando em vez de quatro (ou cinco, com --sqlite).

Uso (rodar a partir da pasta scripts/):
    python atualizar_tudo.py              # pipeline principal
    python atualizar_tudo.py --sqlite     # tambem reconstroi mercado_solar.db (~800MB, mais lento)
    python atualizar_tudo.py --reclassificar-outros   # depois de editar seed_marca_*.csv

Depois de trocar o parquet bruto da ANEEL ou aplicar correcao_marca em
revisao_outros_*.csv, o uso normal (sem flag) ja resolve. --reclassificar-outros
so e necessario depois de editar a lista semente (aliases/tokens novos).
"""
from __future__ import annotations

import argparse
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import exportar_sqlite
import gerar_dashboard_html
import montar_agregado_marcas
import montar_mapa_marcas
import montar_payload_dashboard


def rodar_etapa(numero: str, nome: str, funcao, *args) -> None:
    print(f"\n{'=' * 80}\n{numero} {nome}\n{'=' * 80}")
    inicio = time.time()
    funcao(*args)
    print(f"-- {nome}: concluido em {time.time() - inicio:.0f}s")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", action="store_true",
                         help="tambem reconstroi dados/processados/mercado_solar.db")
    parser.add_argument("--reclassificar-outros", action="store_true",
                         help="reclassifica o backlog de Outros contra o seed_marca_*.csv atualizado")
    args = parser.parse_args()

    modo_mapa = ["--modo", "reclassificar_outros"] if args.reclassificar_outros else []

    total_inicio = time.time()
    rodar_etapa("1/4", "Mapeamento de marcas", montar_mapa_marcas.main, modo_mapa)
    rodar_etapa("2/4", "Agregacao", montar_agregado_marcas.main)
    rodar_etapa("3/4", "Payload do dashboard", montar_payload_dashboard.main)
    rodar_etapa("4/4", "Dashboard HTML", gerar_dashboard_html.main)
    if args.sqlite:
        rodar_etapa("5/5", "Banco SQLite (Streamlit)", exportar_sqlite.main)

    print(f"\nPipeline completo em {time.time() - total_inicio:.0f}s. "
          f"Revise `git status` e commite quando quiser publicar.")


if __name__ == "__main__":
    main()
