"""Roda o pipeline inteiro em sequencia: download da ANEEL (se houver versao nova) -> mapeamento de marcas -> agregado -> payload
-> dashboard HTML. Um comando em vez de quatro (ou cinco, com --sqlite).

Uso (rodar a partir da pasta scripts/):
    python atualizar_tudo.py              # pipeline principal
    python atualizar_tudo.py --sqlite     # tambem reconstroi mercado_solar.db (~800MB, mais lento)
    python atualizar_tudo.py --reclassificar-outros   # depois de editar seed_marca_*.csv
    python atualizar_tudo.py --so-se-novo # tarefa diaria: so roda se a ANEEL publicou versao nova

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

import baixar_aneel
import exportar_sqlite
import gerar_dashboard_html
import montar_agregado_marcas
import montar_mapa_marcas
import montar_payload_dashboard

try:  # interno, fora do repositorio publico: planilha de categorias de marca do compras
    import atualizar_categorias_marca
except ImportError:
    atualizar_categorias_marca = None


def rodar_etapa(numero: str, nome: str, funcao, *args) -> None:
    print(f"\n{'=' * 80}\n{numero} {nome}\n{'=' * 80}")
    inicio = time.time()
    funcao(*args)
    print(f"-- {nome}: concluido em {time.time() - inicio:.0f}s")


def baixar() -> bool:
    try:
        return baixar_aneel.main()
    except (OSError, RuntimeError) as e:  # sem internet ou download cortado: segue com os arquivos da pasta
        print(f"  AVISO: download da ANEEL falhou ({e}); usando os arquivos que ja estao em dados/raw")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", action="store_true",
                         help="tambem reconstroi dados/processados/mercado_solar.db")
    parser.add_argument("--reclassificar-outros", action="store_true",
                         help="reclassifica o backlog de Outros contra o seed_marca_*.csv atualizado")
    parser.add_argument("--so-se-novo", action="store_true",
                         help="para se a ANEEL nao publicou versao nova (tarefa agendada)")
    args = parser.parse_args()

    modo_mapa = ["--modo", "reclassificar_outros"] if args.reclassificar_outros else []

    total_inicio = time.time()
    print(f"\n{'=' * 80}\n0/4 Download da ANEEL (so se houver versao nova)\n{'=' * 80}")
    if not baixar() and args.so_se_novo:
        print("Nada novo na ANEEL; pipeline nao rodou.")
        return
    rodar_etapa("1/4", "Mapeamento de marcas", montar_mapa_marcas.main, modo_mapa)
    rodar_etapa("2/4", "Agregacao", montar_agregado_marcas.main)
    rodar_etapa("3/4", "Payload do dashboard", montar_payload_dashboard.main)
    rodar_etapa("4/4", "Dashboard HTML", gerar_dashboard_html.main)
    if atualizar_categorias_marca:
        rodar_etapa("+", "Planilha de categorias de marca (marcas novas)", atualizar_categorias_marca.main)
    if args.sqlite:
        rodar_etapa("5/5", "Banco SQLite (Streamlit)", exportar_sqlite.main)

    print(f"\nPipeline completo em {time.time() - total_inicio:.0f}s. "
          f"Revise `git status` e commite quando quiser publicar.")


if __name__ == "__main__":
    main()
