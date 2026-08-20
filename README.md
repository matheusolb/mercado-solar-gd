# Radar de Marcas Solares — Geração Distribuída no Brasil

Pipeline de dados + dashboard interativo sobre a evolução de marcas de painel
solar e inversor na geração distribuída fotovoltaica brasileira, a partir dos
dados abertos da ANEEL.

**[Ver dashboard ao vivo](https://matheusolb.github.io/mercado-solar-gd/dashboard.html)**
— ou baixe [`dashboard/dashboard.html`](dashboard/dashboard.html) e abra direto no
navegador (é autocontido, sem servidor).

## O problema

A ANEEL publica, para cada instalação de geração distribuída, o fabricante do
módulo e do inversor em **texto livre** digitado por quem cadastrou o projeto.
Isso gera uma fragmentação enorme: `"JINKO"`, `"JINKO SOLAR"` e `"JINKOSOLAR"`
são a mesma marca, mas aparecem como valores distintos — o dataset bruto tem
mais de 56 mil variações de nome de fabricante de módulo para o que são, na
prática, algumas dezenas de marcas reais.

Este projeto normaliza esse texto para marcas canônicas de forma determinística
e auditável (sem clustering automático), agrega por marca × tempo × geografia, e
apresenta a evolução resultante — quais marcas cresceram, quais perderam espaço,
e como isso varia por estado.

## Principais achados

- Depois da normalização, o texto livre é identificado corretamente em **89,7%**
  do kW instalado (módulo) e **92,7%** (inversor) — o resto cai num bucket
  "Outros" auditável (ver `dados/processados/revisao_outros_*.csv`).
- A cobertura de identificação é estável entre 2020 e 2026 (não há uma "época
  mais limpa" nos dados — a fragmentação de nomes é constante ao longo do tempo).
- A ANEEL tem um atraso real de cadastro de ~4 meses entre a data de conexão e
  a data em que o registro aparece no snapshot público — os scripts detectam
  isso automaticamente (comparando cada mês recente com a mediana dos 6
  anteriores) em vez de assumir um número fixo de meses.
- Métrica usada: **potência instalada (`MdaPotenciaInstalada`, kW)**, não
  quantidade de equipamentos nem `MdaPotenciaModulos`/`MdaPotenciaInversores`
  — essas duas últimas colunas têm outliers de unidade (uma linha real registra
  9.996 kW de módulos numa instalação de 75 kW e 196 módulos).

## Estrutura do repositório

```
dados/
  raw/            dados brutos da ANEEL (parquet) -- nao versionado, ver abaixo
  processados/    saidas do pipeline: mapeamento de marcas, agregados, banco SQLite
scripts/          os 5 scripts do pipeline + a lista semente de marcas
dashboard/        dashboard.html (autocontido) + o JSON de dados que o alimenta
```

## Como rodar

```bash
pip install -r requirements.txt
```

Baixe os dois arquivos parquet do [portal de dados abertos da
ANEEL](https://dadosabertos.aneel.gov.br/dataset/relacao-de-empreendimentos-de-geracao-distribuida)
(recursos "empreendimento-geracao-distribuida" e
"empreendimento-gd-informacoes-tecnicas-fotovoltaica") e coloque em `dados/raw/`.

Depois, na pasta `scripts/`, o jeito mais simples é rodar tudo de uma vez:

```bash
python atualizar_tudo.py              # os 4 passos principais em sequência
python atualizar_tudo.py --sqlite     # idem, e também reconstrói mercado_solar.db (~800MB, mais lento)
```

Ou passo a passo, se preferir rodar só uma etapa (por exemplo, só o passo 1 depois
que a ANEEL atualizar os dados, sem refazer o resto):

```bash
python montar_mapa_marcas.py          # normaliza os nomes de fabricante -> marca canonica
python montar_agregado_marcas.py      # junta os dois parquets e agrega por mes/UF/municipio/marca
python montar_payload_dashboard.py    # gera o JSON leve que o dashboard consome
python gerar_dashboard_html.py        # monta dashboard/dashboard.html a partir do template + JSON
python exportar_sqlite.py             # opcional: consolida tudo num dados/processados/mercado_solar.db
```

Cada script é independente e lê a saída do anterior.

## Versão alternativa em Streamlit

`app.py` (raiz do projeto) é um port das mesmas 4 seções pro Streamlit + Plotly,
lendo direto de `dados/processados/mercado_solar.db`. Serve de comparação com a
versão HTML/JS -- mesma lógica de marca/atraso de cadastro, camada de
apresentação diferente.

```bash
pip install -r requirements-streamlit.txt
streamlit run app.py
```

## Revisando e expandindo o mapeamento de marcas

O mapeamento não é definitivo — é uma lista semente (`scripts/seed_marca_modulo.csv`
e `seed_marca_inversor.csv`) que qualquer um pode editar:

| Arquivo | O que é | O que fazer com ele |
|---|---|---|
| `dados/processados/revisao_outros_modulo.csv` / `_inversor.csv` | Fila de revisão: todo texto que caiu em "Outros", ordenado por kW (o maior impacto primeiro) | Olhar os primeiros, identificar a marca real, adicionar em `seed_marca_*.csv` |
| `scripts/seed_marca_modulo.csv` / `seed_marca_inversor.csv` | Lista semente editável (`canonical_brand`, `aliases_exatos`, `tokens_correspondencia` separados por `\|`) | Editar direto — é só CSV |
| `dados/processados/mapa_marca_modulo.csv` / `_inversor.csv` | O mapeamento completo já resolvido, uma linha por texto bruto normalizado (`match_method` mostra como cada um foi classificado) | Pode editar `canonical_brand` de uma linha específica e marcar `revisao_manual=True` para essa correção nunca ser sobrescrita numa próxima rodada |

Depois de editar a lista semente, rode:

```bash
python atualizar_tudo.py --reclassificar-outros
```

Isso reclassifica só o backlog de "Outros" contra a lista semente atualizada —
não reabre nada que já foi classificado, e não toca em linhas com
`revisao_manual=True` — e já roda os outros 3 passos em seguida pra refletir no
dashboard.

## Duas marcas no mesmo campo

Uma parte real do "Outros" vem de registros como `"1 BYD / 2 Canadian"` (duas
marcas na mesma instalação). O classificador detecta esse caso (mais de uma
marca bate no mesmo texto) e não tenta adivinhar — manda para "Outros" com o
motivo `TOKEN_AMBIGUO`, distinto de `NAO_ENCONTRADO` (texto realmente
desconhecido). É uma fração pequena do total (5-12% do que sobra em "Outros").

## Stack

Python (pandas, pyarrow) para o pipeline · SQLite (stdlib) para a exportação
opcional · HTML/CSS/JS puro para o dashboard (SVG desenhado à mão, sem biblioteca
de gráficos, sem build step).
