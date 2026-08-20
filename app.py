"""Radar de Marcas Solares -- versao Streamlit.

Replica as mesmas 4 secoes do dashboard HTML (dashboard/dashboard.html): tendencia,
leaderboard de crescimento, ranking completo e corte por UF. Mesma logica de
normalizacao de marca e deteccao de atraso de cadastro da ANEEL, so a camada de
apresentacao troca de HTML/SVG a mao por Streamlit + Plotly.

Rodar com: streamlit run app.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PASTA = os.path.dirname(os.path.abspath(__file__))
CAMINHO_DB = os.path.join(PASTA, "dados", "processados", "mercado_solar.db")
CAMINHO_FAIXAS = os.path.join(PASTA, "scripts", "faixas_potencia.csv")

# scripts/ nao e um pacote instalado -- roda a partir da raiz do projeto (streamlit
# run app.py), entao precisa entrar no sys.path manualmente pra importar os modulos
# de la.
sys.path.insert(0, os.path.join(PASTA, "scripts"))
import logica_mercado as lm  # noqa: E402
import marca_utils as mu  # noqa: E402
# Mesma fonte de verdade que o dashboard HTML le do payload.
from montar_payload_dashboard import (  # noqa: E402
    LEADERBOARD_TOP_N, MULTIPLOS_TOP_N, PISO_PADRAO, TOP_N_GRAFICO_COR as TOP_N_COR,
)

OUTROS = mu.OUTROS
NAO_INFORMADO = mu.NAO_INFORMADO
# Paleta categorica (identidade de marca no grafico) validada contra daltonismo/
# contraste -- fica fixa (ver skill dataviz). Cores de cromo/status abaixo usam
# a marca Amara (extraida de Em produção/Projects/Painel Base RD.html).
# TOP_N_GRAFICO_COR mudou de 8 para 10 no pipeline (montar_payload_dashboard.py) --
# as 2 ultimas entradas antes de Outros/Nao informado tem que bater com esse numero.
PALETA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948",
          "#1c8fa6", "#8a6d1e", "#898781", "#c3c2b7"]
COR_ACENTO = "#00953b"
COR_BOM = "#00953b"
COR_MAU = "#c23a26"

st.set_page_config(page_title="Radar de Marcas Solares", page_icon="☀️", layout="wide")


@st.cache_data
def carregar_agregado(campo: str, versao_db: float) -> pd.DataFrame:
    """`versao_db` e o mtime do banco -- entra na chave do cache de proposito, pra que
    rodar o pipeline de novo (que reescreve o .db) invalide o cache automaticamente.
    Sem isso o app continua servindo o DataFrame antigo em memoria e quebra quando o
    schema muda (foi o que aconteceu ao adicionar faixa_potencia)."""
    con = sqlite3.connect(CAMINHO_DB)
    df = pd.read_sql(f"SELECT * FROM agregado_marca_{campo}", con)
    con.close()
    return df[df["ano_mes"] >= PISO_PADRAO]


@st.cache_data
def carregar_faixas_ordem() -> list[str]:
    """Ordem das faixas vem de scripts/faixas_potencia.csv -- a mesma fonte de verdade
    que o pipeline usa, pra ordem e rotulos nunca divergirem entre os dois dashboards.
    Se o arquivo faltar, cai numa ordenacao pela potencia media de cada faixa, que
    chega no mesmo resultado sem depender de parsear os rotulos."""
    if os.path.exists(CAMINHO_FAIXAS):
        cfg = pd.read_csv(CAMINHO_FAIXAS, encoding="utf-8-sig")
        rotulos = [str(r).strip() for r in cfg["rotulo"] if str(r).strip()]
        if rotulos:
            return rotulos
    return []


def ordenar_faixas(df: pd.DataFrame) -> list[str]:
    presentes = set(df["faixa_potencia"].dropna().unique())
    configuradas = [f for f in carregar_faixas_ordem() if f in presentes]
    if configuradas:
        return configuradas
    medias = (df.groupby("faixa_potencia")["soma_kw"].sum()
              / df.groupby("faixa_potencia")["qtd_instalacoes"].sum())
    return medias.sort_values().index.tolist()


def filtrar_intervalo(df: pd.DataFrame, de: str, ate: str) -> pd.DataFrame:
    return df[(df["ano_mes"] >= f"{de}-01") & (df["ano_mes"] <= f"{ate}-12")]


def em_intervalo(de: str, ate: str) -> str:
    """Mesma logica do dashboard HTML (funcao emPeriodo em dashboard_template.html) --
    um unico intervalo De/Ate cobre tanto "um periodo so" (de == ate) quanto uma
    janela de varios anos, sem precisar de um mecanismo separado pra cada caso."""
    return f"em {de}" if de == ate else f"entre {de} e {ate}"


def _meses_do_ano(ano: str) -> list[str]:
    """Todos os 'YYYY-MM' de um ano, no formato que logica_mercado.top_marcas_por_pico espera."""
    return [f"{ano}-{m:02d}" for m in range(1, 13)]


def totais_por_marca(df: pd.DataFrame, de: str, ate: str) -> tuple[pd.Series, float]:
    subset = filtrar_intervalo(df, de, ate)
    total = subset["soma_kw"].sum()
    reais = subset[~subset["marca"].isin([OUTROS, NAO_INFORMADO])]
    return reais.groupby("marca")["soma_kw"].sum(), total


def ordenar_categorias(df: pd.DataFrame, coluna: str) -> list[str]:
    """Ordem por MW total do periodo inteiro (nao muda com o filtro de periodo
    global) -- pra classe de consumo e grupo tarifario, categorias regulatorias
    estruturais que so mudam de tamanho lentamente, nao competitivas como marca."""
    return df.groupby(coluna)["soma_kw"].sum().sort_values(ascending=False).index.tolist()


def top_categorias_por_pico(df: pd.DataFrame, anos_disponiveis: list[str], coluna: str, n: int) -> list[str]:
    """Mesma logica de top_marcas_por_pico, generica -- usada pra distribuidora
    (103 valores, precisa de corte)."""
    picos: dict[str, float] = {}
    for ano in anos_disponiveis:
        subset = df[df["ano_mes"].str.startswith(ano)]
        for cat, kw in subset.groupby(coluna)["soma_kw"].sum().items():
            picos[cat] = max(picos.get(cat, 0.0), kw)
    return pd.Series(picos).sort_values(ascending=False).head(n).index.tolist()


def calcular_categoria(subset: pd.DataFrame, coluna: str, ordem: list[str], rotulo_coluna: str,
                        top8: list[str], marcas_cor: list[str], intervalo_de: str, intervalo_ate: str,
                        rotulo_singular: str, artigo: str, rotulo_plural: str, duas_pontas: bool,
                        rotulo_outros: str | None = None) -> dict | None:
    """Parte de calculo de render_secao_categoria: groupby, tabela de especialistas e o
    texto da legenda -- nada de Streamlit aqui, so dados prontos pra desenhar. Retorna
    None quando nao ha instalacao identificada no recorte (o caller mostra a legenda
    vazia). Ver render_secao_categoria() pro motor generico Faixa/Classe/Tarifa/
    Distribuidora, e renderCategoria() em dashboard_template.html pro porque de
    rotulo_outros ficar fora do superlativo "maior categoria"."""
    if not ordem or subset.empty:
        return None

    subset = subset.copy()
    subset["_marca_grafico"] = lm.marca_para_grafico(subset["marca"], top8)
    tab = (subset.groupby([coluna, "_marca_grafico"], observed=True)["soma_kw"].sum().unstack(fill_value=0.0) / 1000)
    tab = tab.reindex(index=ordem, columns=marcas_cor, fill_value=0.0)

    perfil = subset.groupby(coluna, observed=True).agg(
        mw=("soma_kw", lambda s: s.sum() / 1000), inst=("qtd_instalacoes", "sum")).reindex(ordem, fill_value=0.0)
    total_geral = perfil["mw"].sum()
    if total_geral <= 0:
        return None

    reais = subset[~subset["marca"].isin([OUTROS, NAO_INFORMADO])]
    total_mercado = subset["soma_kw"].sum()
    share_geral = (reais.groupby("marca")["soma_kw"].sum() / total_mercado * 100) if total_mercado else pd.Series(dtype=float)

    linhas_esp = []
    for cat in ordem:
        total_cat = subset[subset[coluna] == cat]["soma_kw"].sum()
        na_cat = reais[reais[coluna] == cat]
        if total_cat <= 0 or na_cat.empty:
            continue
        sc = (na_cat.groupby("marca")["soma_kw"].sum() / total_cat * 100).sort_values(ascending=False)
        for marca, share_c in sc.head(3).items():
            sg = float(share_geral.get(marca, 0.0))
            linhas_esp.append({
                rotulo_coluna: cat, "Marca": marca,
                "Share na categoria %": round(float(share_c), 2), "Share geral %": round(sg, 2),
                "Índice": round(share_c / sg, 1) if sg > 0 else None,
            })

    perfil_p_maior = perfil.drop(index=[rotulo_outros], errors="ignore") if rotulo_outros else perfil
    if perfil_p_maior["mw"].sum() <= 0:
        return None
    maior = perfil_p_maior["mw"].idxmax()
    lider_maior = next((l for l in linhas_esp if l[rotulo_coluna] == maior), None)
    inst_fmt = f"{int(perfil.loc[maior, 'inst']):,}".replace(",", ".")
    texto = (f"**{maior}** é {artigo} maior {rotulo_singular} {em_intervalo(intervalo_de, intervalo_ate)}, com "
             f"{perfil.loc[maior, 'mw']:.1f} MW ({perfil.loc[maior, 'mw']/total_geral*100:.0f}% da potência "
             f"em {inst_fmt} instalações)")
    if lider_maior:
        texto += f", e **{lider_maior['Marca']}** é a marca mais presente nessa categoria ({lider_maior['Share na categoria %']:.1f}%"
        if lider_maior["Índice"]:
            texto += f", {lider_maior['Índice']:.1f}× a participação geral dela"
        texto += ")"
    texto += "."
    if duas_pontas and len(ordem) > 1:
        candidatos_topo = [c for c in ordem if c != rotulo_outros]
        topo = candidatos_topo[-1] if candidatos_topo else None
        lider_topo = next((l for l in linhas_esp if l[rotulo_coluna] == topo), None) if topo else None
        if lider_topo and lider_topo["Marca"] != lider_maior["Marca"]:
            texto += (f" Já em **{topo}** quem lidera é **{lider_topo['Marca']}** com {lider_topo['Share na categoria %']:.1f}%"
                      + (f" ({lider_topo['Índice']:.1f}× a participação que ela tem no mercado como um todo)"
                         if lider_topo["Índice"] else "")
                      + f". {rotulo_plural} diferentes, líderes diferentes.")

    return {"tab": tab, "texto": texto, "especialistas": pd.DataFrame(linhas_esp)}


def render_secao_categoria(subset: pd.DataFrame, coluna: str, ordem: list[str], rotulo_coluna: str,
                            top8: list[str], marcas_cor: list[str], mapa_cor: dict, intervalo_de: str,
                            intervalo_ate: str, campo: str, rotulo_singular: str, artigo: str, rotulo_plural: str,
                            duas_pontas: bool, chave_download: str, altura: int = 380,
                            rotulo_outros: str | None = None) -> None:
    """Motor generico por tras de Faixa/Classe/Tarifa/Distribuidora -- mesma mecanica
    (barra empilhada por marca + tabela de especialistas) sobre uma coluna categorica
    diferente. Ver calcular_categoria() pro calculo; aqui e so Streamlit."""
    calc = calcular_categoria(subset, coluna, ordem, rotulo_coluna, top8, marcas_cor, intervalo_de,
                               intervalo_ate, rotulo_singular, artigo, rotulo_plural, duas_pontas, rotulo_outros)
    if calc is None:
        st.caption(f"Nenhuma instalação identificada {em_intervalo(intervalo_de, intervalo_ate)}.")
        return
    st.caption(calc["texto"])

    fig = px.bar(
        calc["tab"], x=marcas_cor, y=calc["tab"].index, orientation="h",
        labels={"value": "MW", "y": rotulo_coluna, "variable": "Marca"}, color_discrete_map=mapa_cor,
    )
    fig.update_traces(hovertemplate="<b>%{fullData.name}</b><br>%{y}: %{x:.2f} MW<extra></extra>")
    fig.update_layout(barmode="stack", height=altura, legend_title="Marca",
                       yaxis=dict(categoryorder="array", categoryarray=list(calc["tab"].index)[::-1]))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        f"**Especialização por {rotulo_singular}.** Índice de especialização = participação da marca na "
        "categoria ÷ participação no mercado total. Valores acima de 1 indicam concentração acima da média; "
        "3×, por exemplo, indica presença três vezes maior que a média de mercado. A lista não se limita às "
        "10 marcas em destaque — inclui especialistas de nicho."
    )
    st.dataframe(calc["especialistas"], use_container_width=True, hide_index=True)
    st.download_button(
        f"⬇️ Baixar especialistas por {rotulo_singular} (CSV)",
        calc["especialistas"].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"especialistas_{chave_download}_{campo}_{intervalo_de}_{intervalo_ate}.csv",
        mime="text/csv", key=f"dl_{chave_download}",
    )


def tabela_comparacao(df: pd.DataFrame, ano_de: str, ano_ate: str) -> pd.DataFrame:
    kw_de, total_de = totais_por_marca(df, ano_de, ano_de)
    kw_ate, total_ate = totais_por_marca(df, ano_ate, ano_ate)
    todas_marcas = sorted(set(kw_de.index) | set(kw_ate.index))
    linhas = []
    for m in todas_marcas:
        ki, kf = float(kw_de.get(m, 0.0)), float(kw_ate.get(m, 0.0))
        share_i = ki / total_de * 100 if total_de else 0.0
        share_f = kf / total_ate * 100 if total_ate else 0.0
        # Crescimento relativo exige base > 0; ki<=0 e kf>0 e marca nova no periodo.
        if ki > 0:
            cresc_rel, entrante_novo = (kf - ki) / ki * 100, False
        else:
            cresc_rel, entrante_novo = None, kf > 0
        linhas.append({
            "marca": m, "mw_inicial": ki / 1000, "mw_final": kf / 1000,
            "share_inicial_pct": share_i, "share_final_pct": share_f,
            "delta_pontos_percentuais": share_f - share_i,
            "delta_mw_absoluto": (kf - ki) / 1000,
            "crescimento_relativo_pct": cresc_rel, "entrante_novo": entrante_novo,
        })
    return pd.DataFrame(linhas)


# ------------------------------------------------------------------ sidebar
st.sidebar.title("☀️ Radar de Marcas Solares")
campo = st.sidebar.radio("Campo", ["modulo", "inversor"], format_func=lambda v: "Módulo" if v == "modulo" else "Inversor")
granularidade = st.sidebar.radio("Granularidade", ["mensal", "trimestral", "anual"], format_func=str.capitalize)

df_bruto = carregar_agregado(campo, os.path.getmtime(CAMINHO_DB))
totais_mensais = df_bruto.groupby("ano_mes")["soma_kw"].sum().sort_index()
ancora = lm.detectar_ultimo_mes_completo(totais_mensais)
# Trunca no ultimo mes completo pra todas as visoes -- os meses mais recentes tem
# atraso de cadastro da ANEEL e aparecem artificialmente baixos, o que distorceria
# qualquer comparacao (ex: "2026" so tem 4 meses limpos, nao o ano inteiro).
df = df_bruto[df_bruto["ano_mes"] <= ancora].copy()
anos_disponiveis = sorted(df["ano_mes"].str[:4].unique())

# Intervalo, escolhido uma vez so aqui -- usado por TODAS as secoes abaixo (Tendencia,
# Comparar marcas, Leaderboard, Ranking, Faixa/Classe/Tarifa/Distribuidora, Estado e
# Município). De == Até funciona como "um periodo so" (equivalente ao antigo filtro de
# periodo unico); De < Até soma o intervalo inteiro nas secoes de composicao (Ranking,
# Faixa/Classe/..., Estado/Município) e compara os dois extremos no Leaderboard e na
# Tendencia. Antes existiam 3 mecanismos de tempo independentes (Período global,
# Gráfico de/até, Leaderboard De/Até) que podiam ficar em recortes diferentes ao
# mesmo tempo -- unificados aqui num so.
_col_de, _col_ate = st.sidebar.columns(2)
intervalo_de = _col_de.selectbox("Intervalo de", anos_disponiveis, index=0, key="intervalo_de")
intervalo_ate = _col_ate.selectbox("até", anos_disponiveis, index=len(anos_disponiveis) - 1, key="intervalo_ate")
if intervalo_de > intervalo_ate:
    intervalo_de, intervalo_ate = intervalo_ate, intervalo_de


def _ajustar_estado_para_regiao():
    """Callback do seletor de Regiao -- roda ANTES do rerun desenhar os widgets,
    e corrige o Estado selecionado se ele nao pertencer mais a regiao escolhida
    (sem isso o selectbox de Estado quebraria: Streamlit exige que o valor
    guardado em session_state esteja dentro das novas opcoes)."""
    reg = st.session_state.get("regiao_global", "Brasil (todas)")
    if reg == "Brasil (todas)":
        return
    ufs_regiao = sorted(df[df["regiao"] == reg]["SigUF"].dropna().unique())
    if ufs_regiao and st.session_state.get("estado_global") not in ufs_regiao:
        st.session_state["estado_global"] = ufs_regiao[0]


regioes_disponiveis = ["Brasil (todas)"] + sorted(df["regiao"].dropna().unique())
regiao_global = st.sidebar.selectbox(
    "Região (Estado / Município)", regioes_disponiveis, index=0, key="regiao_global",
    on_change=_ajustar_estado_para_regiao,
)
ufs_disponiveis = (sorted(df[df["regiao"] == regiao_global]["SigUF"].dropna().unique())
                    if regiao_global != "Brasil (todas)" else sorted(df["SigUF"].dropna().unique()))
_indice_sp = ufs_disponiveis.index("SP") if "SP" in ufs_disponiveis else 0
estado_global = st.sidebar.selectbox(
    "Estado (Estado / Município)", ufs_disponiveis, index=_indice_sp, key="estado_global",
)

top15 = lm.top_marcas_por_pico(df, (_meses_do_ano(a) for a in anos_disponiveis), 15)
top8 = top15[:TOP_N_COR]
marcas_cor = top8 + [OUTROS, NAO_INFORMADO]
mapa_cor = dict(zip(marcas_cor, PALETA))

# Ordem das categorias calculada uma vez sobre TODO o periodo (nao muda com o
# filtro global) -- mesma logica das secoes de faixa/UF, pra linha nao pular de
# posicao so porque o usuario trocou o periodo.
ROTULO_OUTRAS_DIST = "Outras distribuidoras"
classes_ordem = ordenar_categorias(df, "classe_consumo")
tarifas_ordem = ordenar_categorias(df, "grupo_tarifario")
top_distribuidoras = top_categorias_por_pico(df, anos_disponiveis, "distribuidora", TOP_N_COR)
distribuidoras_ordem = top_distribuidoras + [ROTULO_OUTRAS_DIST]

st.title("Evolução de marcas de módulos e inversores")
st.caption(
    f"Instalações fotovoltaicas de geração distribuída conectadas desde 2020, por marca de "
    f"{'módulo' if campo == 'modulo' else 'inversor'}, em MW de potência instalada. Último mês sem atraso de "
    f"cadastro da ANEEL: **{ancora}**."
)
st.caption(
    f"🔧 Intervalo, Região e Estado na barra lateral aplicam-se a todas as seções abaixo, mantendo o mesmo "
    f"recorte de tempo e geografia em todo o painel (atual: {em_intervalo(intervalo_de, intervalo_ate)}, {estado_global})."
)

with st.expander("❓ Como usar este painel"):
    st.markdown(
        "**O que este painel revela:** quais marcas de painel solar e de inversor vêm ganhando espaço no "
        "mercado brasileiro, e em quais regiões esse movimento é mais forte. Base: registros da ANEEL para "
        "instalações residenciais e comerciais desde 2020.\n\n"
        "**Intervalo (De/Até), na barra lateral:** um único controle de tempo para o painel inteiro. Com os "
        "dois extremos iguais, cada seção mostra um período específico; alargando o intervalo, as seções de "
        "composição (Ranking, Faixa/Classe/Tarifa/Distribuidora, Estado, Município) somam o período inteiro, "
        "e a Tendência e o Leaderboard comparam o início com o fim do intervalo.\n\n"
        "**Participação de mercado ao longo do tempo:** evolução mensal da potência instalada por marca, no "
        "intervalo escolhido. Os valores detalhados aparecem ao passar o cursor sobre o gráfico.\n\n"
        "**Comparar marcas específicas:** isola a trajetória de até 8 marcas selecionadas — útil para "
        "acompanhar marcas de menor participação que não aparecem em destaque.\n\n"
        "**Quem ganhou e quem perdeu espaço:** compara o início e o fim do intervalo selecionado e identifica "
        "imediatamente quem ganhou e quem perdeu participação de mercado entre eles.\n\n"
        "**Ranking completo de marcas:** todas as marcas identificadas no intervalo, ordenadas por volume "
        "instalado — não se limita às marcas em destaque no gráfico principal.\n\n"
        "**Comportamento por faixa de potência / classe de consumo / grupo tarifário / distribuidora:** o "
        "mesmo recorte analítico sob quatro dimensões: porte da instalação, perfil do consumidor, tarifa e "
        "distribuidora regional. A tabela abaixo de cada gráfico identifica quem se destaca em cada categoria, "
        "incluindo marcas de menor porte.\n\n"
        "**Comportamento por estado / por município:** onde cada marca é mais forte. Uma Região selecionada "
        "na barra lateral restringe os estados disponíveis, e um Estado abre o corte por município.\n\n"
        "**Por que \"Outros\" existe:** o cadastro na ANEEL é feito em texto livre, o que gera múltiplas "
        "grafias para a mesma marca. Essas variações são consolidadas por metodologia própria; casos sem "
        "identificação segura permanecem em \"Outros\"."
    )

# ------------------------------------------------------------------ tendencia
st.header("Participação de mercado ao longo do tempo")

_ano_base, _ano_recente = intervalo_de, intervalo_ate
_destaques = tabela_comparacao(df, _ano_base, _ano_recente)
_lider = _destaques.sort_values("share_final_pct", ascending=False).iloc[0]
_subiu = _destaques.sort_values("delta_pontos_percentuais", ascending=False).iloc[0]
_caiu = _destaques.sort_values("delta_pontos_percentuais", ascending=True).iloc[0]
st.caption(
    f"**{_lider['marca']}** lidera com {_lider['share_final_pct']:.1f}% de participação em {_ano_recente}. "
    f"**{_subiu['marca']}** foi quem mais ganhou espaço desde {_ano_base} (+{_subiu['delta_pontos_percentuais']:.1f} p.p.), "
    f"e **{_caiu['marca']}** quem mais perdeu ({_caiu['delta_pontos_percentuais']:.1f} p.p.)."
)

# As TOP_N_COR marcas em destaque aqui reagem ao Intervalo escolhido na barra lateral
# (nao ficam fixas no pico de TODOS os anos, como o top8/marcas_cor "oficiais" usados
# em Comportamento por estado) -- uma marca so relevante fora da janela visivel nao
# deveria ocupar uma das cores enquanto o filtro mostra so um recorte especifico.
# Mesma logica de topMarcasPorPeriodos/serieMensalDinamica no dashboard HTML.
anos_no_range = [a for a in anos_disponiveis if intervalo_de <= a <= intervalo_ate]
top8_dinamico = lm.top_marcas_por_pico(df, (_meses_do_ano(a) for a in anos_no_range), TOP_N_COR)
marcas_cor_dinamico = top8_dinamico + [OUTROS, NAO_INFORMADO]
mapa_cor_dinamico = dict(zip(marcas_cor_dinamico, PALETA))

df_janela = filtrar_intervalo(df, intervalo_de, intervalo_ate).copy()
df_janela["marca_grafico"] = lm.marca_para_grafico(df_janela["marca"], top8_dinamico)
coluna_periodo = {"mensal": "ano_mes", "trimestral": "ano_trimestre", "anual": "ano"}[granularidade]

serie = (df_janela.groupby([coluna_periodo, "marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0) / 1000)
serie = serie.reindex(columns=marcas_cor_dinamico, fill_value=0.0).sort_index()

# Area empilhada em vez de barra: com ~80 meses no eixo, barra virava uma sequencia
# de tracos finos colados; area le como uma curva so. line_shape="spline" suaviza
# sem inventar pico entre dois meses reais (Plotly nao estoura o valor NOS pontos,
# so a interpolacao visual entre eles).
fig_tendencia = px.area(
    serie, x=serie.index.astype(str), y=marcas_cor_dinamico,
    labels={"value": "MW", "x": granularidade.capitalize(), "variable": "Marca"},
    color_discrete_map=mapa_cor_dinamico,
    line_shape="spline",
)
fig_tendencia.update_traces(hovertemplate="<b>%{fullData.name}</b><br>%{x}: %{y:.2f} MW<extra></extra>", line_width=1)
fig_tendencia.update_layout(height=460, legend_title="Marca", hovermode="x unified")
st.plotly_chart(fig_tendencia, use_container_width=True)

with st.expander("Ver dados em tabela"):
    st.dataframe(serie.round(1), use_container_width=True)

# ------------------------------------------------------------------ cada marca individualmente
st.header("Cada marca, individualmente")

# Complementa o grafico principal, que so tem espaco de cor pras TOP_N_COR maiores
# (o resto cai dentro de "Outros", mesmo sendo marca identificada). Usa o mesmo
# intervalo Grafico-de/ate do grafico principal, pra nao virar mais um filtro
# independente. Mesma logica (e mesmo ajuste de "atual") do dashboard HTML.
_reais_janela = df_janela[~df_janela["marca"].isin([OUTROS, NAO_INFORMADO])]
_top_multiplos = (_reais_janela.groupby("marca")["soma_kw"].sum().sort_values(ascending=False).head(MULTIPLOS_TOP_N).index.tolist())
_meses_janela = sorted(df_janela["ano_mes"].unique())
_serie_multiplos = (
    (_reais_janela.groupby(["marca", "ano_mes"])["soma_kw"].sum() / 1000)
    .unstack(fill_value=0.0).reindex(columns=_meses_janela, fill_value=0.0)
)

_rotulo_intervalo = em_intervalo(intervalo_de, intervalo_ate)
st.caption(
    f"As {len(_top_multiplos)} maiores marcas {_rotulo_intervalo}, em escala individual — incluindo marcas hoje "
    f"agregadas em \"Outros\" no gráfico principal."
)

_cols_multiplos = st.columns(4)
for _i, _marca in enumerate(_top_multiplos):
    _vals = _serie_multiplos.loc[_marca]
    _pico = float(_vals.max())
    # "Atual" usa o mes de referencia (ancora), nao o ultimo mes do recorte -- que
    # pode ter atraso de cadastro da ANEEL e aparecer perto de zero.
    _atual = float(_vals[ancora]) if ancora in _vals.index else float(_vals.iloc[-1])
    with _cols_multiplos[_i % 4]:
        _fig_mini = go.Figure(go.Scatter(
            x=_vals.index, y=_vals.values, mode="lines", fill="tozeroy",
            line=dict(color=COR_ACENTO, width=1.5, shape="spline"), fillcolor="rgba(0,149,59,0.15)",
            hovertemplate="%{x}: %{y:.1f} MW<extra></extra>",
        ))
        _fig_mini.update_layout(
            height=70, margin=dict(l=0, r=0, t=18, b=0), showlegend=False,
            xaxis=dict(visible=False), yaxis=dict(visible=False, rangemode="tozero"),
            title=dict(text=_marca, font=dict(size=12)),
        )
        st.plotly_chart(_fig_mini, use_container_width=True, config={"displayModeBar": False}, key=f"multiplo_{_marca}")
        st.caption(f"pico {_pico:.1f} MW · atual {_atual:.1f} MW")

# ------------------------------------------------------------------ comparador de marcas
st.header("Comparar marcas específicas")

reais_todas = df[~df["marca"].isin([OUTROS, NAO_INFORMADO])]
marcas_ordenadas = reais_todas.groupby("marca")["soma_kw"].sum().sort_values(ascending=False).index.tolist()
marcas_escolhidas = st.multiselect("Marcas (até 8)", marcas_ordenadas, default=[marcas_ordenadas[0]], max_selections=8, key="marcas_comparadas")

if marcas_escolhidas:
    tabela_comp = {}
    for m in marcas_escolhidas:
        tabela_comp[m] = (df_janela[df_janela["marca"] == m].groupby(coluna_periodo)["soma_kw"].sum() / 1000).reindex(serie.index, fill_value=0.0)
    serie_comp = pd.DataFrame(tabela_comp)

    if len(marcas_escolhidas) == 1:
        m = marcas_escolhidas[0]
        st.caption(f"{m} instalou {serie_comp[m].sum():.1f} MW em {'módulos' if campo=='modulo' else 'inversores'} no período, "
                   f"com pico de {serie_comp[m].max():.1f} MW em {serie_comp[m].idxmax()}.")
    else:
        totais_comp = serie_comp.sum().sort_values(ascending=False)
        st.caption(f"{totais_comp.index[0]} instalou mais que as demais no período ({totais_comp.iloc[0]:.1f} MW), "
                   f"contra {totais_comp.iloc[-1]:.1f} MW de {totais_comp.index[-1]}.")

    fig_marca = px.line(serie_comp, x=serie_comp.index.astype(str), y=marcas_escolhidas,
                         labels={"x": granularidade.capitalize(), "value": "MW", "variable": "Marca"})
    fig_marca.update_traces(line_width=2, hovertemplate="<b>%{fullData.name}</b><br>%{x}: %{y:.2f} MW<extra></extra>")
    fig_marca.update_layout(height=320, legend_title="Marca", hovermode="x unified")
    st.plotly_chart(fig_marca, use_container_width=True)
else:
    st.caption("Escolha ao menos uma marca para comparar.")

# ------------------------------------------------------------------ leaderboard
st.header("Quem ganhou e quem perdeu espaço")

metrica = st.radio("Métrica", ["pontos", "mw"], format_func=lambda v: "Pontos de share" if v == "pontos" else "MW absoluto", horizontal=True)

if intervalo_de == intervalo_ate:
    st.caption("Selecione um intervalo (De ≠ Até) na barra lateral para comparar crescimento entre dois pontos no tempo.")
lb = tabela_comparacao(df, intervalo_de, intervalo_ate)
_ordenada_pp = lb.sort_values("delta_pontos_percentuais", ascending=False)
st.caption(
    f"De {intervalo_de} para {intervalo_ate}: **{_ordenada_pp.iloc[0]['marca']}** ganhou mais espaço "
    f"(+{_ordenada_pp.iloc[0]['delta_pontos_percentuais']:.1f} p.p.), enquanto **{_ordenada_pp.iloc[-1]['marca']}** "
    f"foi quem mais perdeu ({_ordenada_pp.iloc[-1]['delta_pontos_percentuais']:.1f} p.p.)."
)
metrica_col = "delta_pontos_percentuais" if metrica == "pontos" else "delta_mw_absoluto"
lb_ordenada = lb.sort_values(metrica_col, ascending=False)
selecionadas = pd.concat([lb_ordenada.head(LEADERBOARD_TOP_N), lb_ordenada.tail(LEADERBOARD_TOP_N)]).drop_duplicates("marca").sort_values(metrica_col)

fig_lb = px.bar(
    selecionadas, x=metrica_col, y="marca", orientation="h",
    color=selecionadas[metrica_col] > 0, color_discrete_map={True: COR_BOM, False: COR_MAU},
    labels={metrica_col: "Δ pontos percentuais" if metrica == "pontos" else "Δ MW", "marca": ""},
)
_unidade_lb = "p.p." if metrica == "pontos" else "MW"
fig_lb.update_traces(hovertemplate="<b>%{y}</b><br>%{x:.2f} " + _unidade_lb + "<extra></extra>")
fig_lb.update_layout(showlegend=False, height=480)
st.plotly_chart(fig_lb, use_container_width=True)

with st.expander("Ver tabela completa"):
    st.dataframe(lb.sort_values(metrica_col, ascending=False).round(2), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ ranking
st.header("Ranking completo de marcas")

top_n = st.selectbox("Mostrar", [10, 15, 20, "Todas"], index=1)

kw_r, total_r = totais_por_marca(df, intervalo_de, intervalo_ate)
ranking = (kw_r / 1000).sort_values(ascending=False).reset_index()
ranking.columns = ["Marca", "MW"]
ranking["Participação %"] = ranking["MW"] / (total_r / 1000) * 100 if total_r else 0.0
_top3 = ranking.head(3)
st.caption(
    f"{len(ranking)} marcas disputam espaço {em_intervalo(intervalo_de, intervalo_ate)}; as 3 maiores "
    f"({', '.join(_top3['Marca'])}) somam {_top3['Participação %'].sum():.0f}% do mercado."
)
if top_n != "Todas":
    ranking = ranking.head(int(top_n))
st.dataframe(ranking.round(2), use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ por faixa de potencia
st.header("Comportamento por faixa de potência")
subset_periodo = filtrar_intervalo(df, intervalo_de, intervalo_ate)
render_secao_categoria(
    subset_periodo, "faixa_potencia", ordenar_faixas(subset_periodo), "Faixa",
    top8, marcas_cor, mapa_cor, intervalo_de, intervalo_ate, campo,
    rotulo_singular="faixa", artigo="a", rotulo_plural="Faixas", duas_pontas=True, chave_download="faixa",
)

# ------------------------------------------------------------------ por classe de consumo
st.header("Comportamento por classe de consumo")
render_secao_categoria(
    subset_periodo, "classe_consumo", classes_ordem, "Classe",
    top8, marcas_cor, mapa_cor, intervalo_de, intervalo_ate, campo,
    rotulo_singular="classe de consumo", artigo="a", rotulo_plural="Classes", duas_pontas=False,
    chave_download="classe", altura=340,
)

# ------------------------------------------------------------------ por grupo tarifario
st.header("Comportamento por grupo tarifário")
render_secao_categoria(
    subset_periodo, "grupo_tarifario", tarifas_ordem, "Grupo tarifário",
    top8, marcas_cor, mapa_cor, intervalo_de, intervalo_ate, campo,
    rotulo_singular="grupo tarifário", artigo="o", rotulo_plural="Grupos tarifários", duas_pontas=False,
    chave_download="tarifa", altura=360,
)

# ------------------------------------------------------------------ por distribuidora
st.header("Comportamento por distribuidora")
subset_dist = subset_periodo.copy()
subset_dist["distribuidora"] = subset_dist["distribuidora"].where(
    subset_dist["distribuidora"].isin(top_distribuidoras), ROTULO_OUTRAS_DIST)
render_secao_categoria(
    subset_dist, "distribuidora", distribuidoras_ordem, "Distribuidora",
    top8, marcas_cor, mapa_cor, intervalo_de, intervalo_ate, campo,
    rotulo_singular="distribuidora", artigo="a", rotulo_plural="Distribuidoras", duas_pontas=False,
    chave_download="distribuidora", altura=380, rotulo_outros=ROTULO_OUTRAS_DIST,
)

# ------------------------------------------------------------------ por UF
st.header("Comportamento por estado" + (f" ({regiao_global})" if regiao_global != "Brasil (todas)" else ""))
if regiao_global == "Brasil (todas)":
    st.caption(
        "Drill-down: escolha uma Região na barra lateral pra ver só os estados dela aqui, e um Estado pra abrir "
        "o corte por município abaixo. Os dois já respeitam a escolha um do outro (o Estado só lista UFs da Região)."
    )

subset_uf = filtrar_intervalo(df, intervalo_de, intervalo_ate).copy()
if regiao_global != "Brasil (todas)":
    subset_uf = subset_uf[subset_uf["regiao"] == regiao_global]
subset_uf["marca_grafico"] = lm.marca_para_grafico(subset_uf["marca"], top8)
uf_tab = (subset_uf.groupby(["SigUF", "marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0) / 1000)
uf_tab = uf_tab.reindex(columns=marcas_cor, fill_value=0.0)
uf_tab["Total"] = uf_tab.sum(axis=1)
uf_tab = uf_tab.sort_values("Total", ascending=True)

_uf_lider = uf_tab.index[-1]
_reais_periodo = subset_uf[~subset_uf["marca"].isin([OUTROS, NAO_INFORMADO])]
_marca_dominante_uf = _reais_periodo[_reais_periodo["SigUF"] == _uf_lider].groupby("marca")["soma_kw"].sum().idxmax()
st.caption(
    f"**{_uf_lider}** lidera com {uf_tab.loc[_uf_lider, 'Total']:.1f} MW {em_intervalo(intervalo_de, intervalo_ate)}, "
    f"com **{_marca_dominante_uf}** como marca mais presente no estado."
)

# O estado escolhido na barra lateral aparece em negrito/destaque aqui -- o
# mesmo estado que abre o corte por municipio logo abaixo, pra deixar visivel
# que as duas secoes seguem o mesmo recorte.
_ticktext_uf = [
    f"<b><span style='color:{COR_ACENTO}'>{uf}</span></b>" if uf == estado_global else uf
    for uf in uf_tab.index
]
fig_uf = px.bar(
    uf_tab.drop(columns="Total"), x=marcas_cor, y=uf_tab.index,
    orientation="h", labels={"value": "MW", "y": "UF", "variable": "Marca"},
    color_discrete_map=mapa_cor,
)
fig_uf.update_traces(hovertemplate="<b>%{fullData.name}</b><br>%{y}: %{x:.2f} MW<extra></extra>")
fig_uf.update_layout(
    barmode="stack", height=750, legend_title="Marca",
    yaxis=dict(tickmode="array", tickvals=list(uf_tab.index), ticktext=_ticktext_uf),
)
st.plotly_chart(fig_uf, use_container_width=True)

# ------------------------------------------------------------------ por municipio
st.header("Comportamento por município")

subset_uf_completa = df[df["SigUF"] == estado_global]
subset_mun = filtrar_intervalo(subset_uf_completa, intervalo_de, intervalo_ate).copy()
totais_mun = subset_mun.groupby("NomMunicipio")["soma_kw"].sum().sort_values(ascending=False) / 1000

if len(totais_mun):
    # Top-8 LOCAL dessa UF (pico dentro do estado, nao o top-8 do Brasil inteiro)
    # -- assim "Outros" reflete o que de fato nao foi identificado ali, nao so o
    # que nao faz parte do top-8 nacional, que pode ser quase irrelevante localmente.
    top8_uf = lm.top_marcas_por_pico(subset_uf_completa, (_meses_do_ano(a) for a in anos_disponiveis), TOP_N_COR)
    marcas_cor_uf = top8_uf + [OUTROS, NAO_INFORMADO]
    mapa_cor_uf = dict(zip(marcas_cor_uf, PALETA))

    subset_mun["marca_grafico"] = lm.marca_para_grafico(subset_mun["marca"], top8_uf)
    mun_tab = (subset_mun.groupby(["NomMunicipio", "marca_grafico"])["soma_kw"].sum().unstack(fill_value=0.0) / 1000)
    mun_tab = mun_tab.reindex(columns=marcas_cor_uf, fill_value=0.0)
    mun_tab["Total"] = mun_tab.sum(axis=1)
    mun_tab = mun_tab.sort_values("Total", ascending=False)
    top_mun = mun_tab.head(20).sort_values("Total", ascending=True)

    total_uf_mun = totais_mun.sum()
    _reais_mun = subset_mun[~subset_mun["marca"].isin([OUTROS, NAO_INFORMADO])]
    marca_lider_top1 = _reais_mun[_reais_mun["NomMunicipio"] == mun_tab.index[0]].groupby("marca")["soma_kw"].sum().idxmax()
    st.caption(
        f"**{mun_tab.index[0]}** lidera entre os {len(totais_mun)} municípios de {estado_global} com instalação "
        f"{em_intervalo(intervalo_de, intervalo_ate)}, com {mun_tab.iloc[0]['Total']:.1f} MW ({mun_tab.iloc[0]['Total']/total_uf_mun*100:.1f}% do estado), "
        f"e **{marca_lider_top1}** é a marca líder no município."
    )

    fig_mun = px.bar(
        top_mun.drop(columns="Total"), x=marcas_cor_uf, y=top_mun.index,
        orientation="h", labels={"value": "MW", "y": "Município", "variable": "Marca"},
        color_discrete_map=mapa_cor_uf,
    )
    fig_mun.update_traces(hovertemplate="<b>%{fullData.name}</b><br>%{y}: %{x:.2f} MW<extra></extra>")
    fig_mun.update_layout(barmode="stack", height=560, legend_title="Marca")
    st.plotly_chart(fig_mun, use_container_width=True)

    with st.expander("Ver tabela completa"):
        tabela_mun = mun_tab.reset_index()
        tabela_mun["% do estado"] = tabela_mun["Total"] / total_uf_mun * 100
        _lider_por_municipio = _reais_mun.groupby(["NomMunicipio", "marca"])["soma_kw"].sum().groupby("NomMunicipio").idxmax()
        tabela_mun["Marca líder"] = tabela_mun["NomMunicipio"].map(lambda m: _lider_por_municipio.get(m, (None, "—"))[1])
        st.dataframe(
            tabela_mun[["NomMunicipio", "Total", "% do estado", "Marca líder"]].rename(columns={"NomMunicipio": "Município", "Total": "MW"}).round(2),
            use_container_width=True, hide_index=True,
        )
else:
    st.caption(f"Nenhum município de {estado_global} com instalação identificada {em_intervalo(intervalo_de, intervalo_ate)}.")
