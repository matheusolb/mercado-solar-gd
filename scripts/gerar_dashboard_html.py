"""Script 4: monta o HTML final do dashboard a partir do template + dashboard_dados.json.

Gera dois arquivos a partir do MESMO miolo (nunca diverge):
- dashboard.html      -- wrapper completo (<!doctype>/<html>/<head>/<body>), para abrir direto no navegador
- dashboard_fragment.html -- so o miolo, sem wrapper, para publicar como Artifact (a ferramenta envolve sozinha)
"""
from __future__ import annotations

import os

PASTA_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
PASTA_PROJETO = os.path.dirname(PASTA_SCRIPTS)
PASTA_DASHBOARD = os.path.join(PASTA_PROJETO, "dashboard")
PLACEHOLDER = "/*__DASHBOARD_DATA_JSON__*/{}"

TITULO = "Evolução de marcas na Geração Distribuída Solar (ANEEL)"


def main():
    caminho_template = os.path.join(PASTA_SCRIPTS, "dashboard_template.html")
    caminho_json = os.path.join(PASTA_DASHBOARD, "dashboard_dados.json")

    with open(caminho_template, encoding="utf-8") as f:
        template = f.read()
    with open(caminho_json, encoding="utf-8") as f:
        dados_json = f.read()

    if PLACEHOLDER not in template:
        raise ValueError(f"Placeholder {PLACEHOLDER!r} nao encontrado em dashboard_template.html")
    miolo = template.replace(PLACEHOLDER, dados_json)

    caminho_fragmento = os.path.join(PASTA_DASHBOARD, "dashboard_fragment.html")
    with open(caminho_fragmento, "w", encoding="utf-8") as f:
        f.write(miolo)
    print(f"fragmento salvo: {caminho_fragmento}")

    completo = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITULO}</title>
</head>
<body style="margin:0;">
{miolo}
</body>
</html>
"""
    caminho_html = os.path.join(PASTA_DASHBOARD, "dashboard.html")
    with open(caminho_html, "w", encoding="utf-8") as f:
        f.write(completo)
    print(f"dashboard local salvo: {caminho_html}")


if __name__ == "__main__":
    main()
