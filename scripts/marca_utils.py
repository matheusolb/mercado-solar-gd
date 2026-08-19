"""Normalização e classificação de nomes de fabricante (módulo/inversor) em marcas canônicas.

Pipeline determinístico e auditável (sem clustering automático / fuzzy matching):
1. nulo/vazio -> sentinela NULO
2. remove prefixo de código de registro ANEEL ("313 - SUNOVA" -> "SUNOVA")
3. remove acentos (unicodedata, sem dependência externa)
4. maiúsculas, remove pontuação, colapsa espaços
5. placeholders de texto (NAO HA, SEM INFORMACAO, ...) -> "Não informado"
6. alias exato contra a semente
7. fallback por token de palavra inteira contra a semente (ambíguo -> Outros)
8. resto -> Outros
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

NULO = "<NULO>"
NAO_INFORMADO = "Não informado"
OUTROS = "Outros"

PLACEHOLDER_VALUES = frozenset({
    "NAO HA", "NAOHA", "NAO INFORMADO", "NAOINFORMADO", "NAO TEM", "NAOTEM",
    "SEM INFORMACAO", "SEM INFORMACOES", "SEM FABRICANTE", "SEM MARCA",
    "DESCONHECIDO", "INDEFINIDO", "NENHUM", "NENHUMA", "N A", "NA", "N D",
    "ND", "X", "0", "-", "SN", "S N",
})

# Colunas do parquet tecnico da ANEEL usadas pelo pipeline -- unica fonte de verdade
# (antes duplicada, digitada a mao, em montar_mapa_marcas.py e montar_agregado_marcas.py;
# se a ANEEL renomear uma coluna, so precisa mudar aqui).
COLUNAS_TECNICAS_ANEEL = [
    "CodGeracaoDistribuida", "DatConexao", "MdaPotenciaInstalada",
    "NomFabricanteModulo", "NomFabricanteInversor",
]


# Colunas do agregado por marca (script 2, montar_agregado_marcas.py) -- fonte de
# verdade unica tambem consumida pelos scripts 3 (payload) e 5 (exportar_sqlite),
# que leem esse parquet/CSV sem filtro de coluna e so descobririam um schema
# quebrado num KeyError tardio, no meio de alguma funcao de negocio.
COLUNAS_AGRUPAMENTO_AGREGADO = [
    "ano_mes", "ano", "mes", "trimestre", "ano_trimestre",
    "regiao", "SigUF", "CodUFibge", "CodMunicipioIbge", "NomMunicipio",
    "faixa_potencia", "classe_consumo", "grupo_tarifario", "distribuidora",
]
COLUNAS_METRICAS_AGREGADO = ["marca", "soma_kw", "qtd_instalacoes", "kw_medio_instalacao"]
COLUNAS_AGREGADO = COLUNAS_AGRUPAMENTO_AGREGADO + COLUNAS_METRICAS_AGREGADO


def validar_colunas(df: pd.DataFrame, esperadas: list[str], origem: str) -> None:
    """Falha alto e com mensagem clara se `df` nao tiver todas as colunas esperadas,
    em vez de deixar um KeyError generico estourar bem mais tarde, no meio de alguma
    funcao de negocio, sem contexto nenhum do que mudou."""
    faltando = [c for c in esperadas if c not in df.columns]
    if faltando:
        raise ValueError(
            f"{origem}: coluna(s) faltando: {', '.join(faltando)}. "
            f"O schema da fonte mudou, ou uma etapa anterior do pipeline precisa rodar de novo."
        )

_PREFIX_RE = re.compile(r"^\d+\s*[-–—]\s*")
_PUNCT_RE = re.compile(r"[^A-Z0-9 ]")
_WS_RE = re.compile(r"\s+")

# 75 kW e o limite legal entre micro e minigeracao (Lei 14.300) -- muda regime
# regulatorio, tipo de cliente e canal de venda. Nao e configuravel porque nao e
# escolha nossa; e a lei. As faixas configuraveis abaixo devem respeitar essa borda.
LIMITE_MICROGERACAO_KW = 75.0

# Faixa default, usada se faixas_potencia.csv nao existir. Cortes escolhidos pela
# distribuicao real: 5 e 10 kW partem os 83% das instalacoes que sao residenciais em
# dois perfis distintos; 300 kW e 1 MW quebram a minigeracao, que e 0,5% das
# instalacoes mas 20% da potencia. Nao ha faixa acima de 5 MW -- e o teto legal e o
# dado confirma (max = 5.000,0 kW).
FAIXAS_POTENCIA_DEFAULT = [
    (5.0, "≤5 kW"),
    (10.0, "5–10 kW"),
    (20.0, "10–20 kW"),
    (75.0, "20–75 kW"),
    (300.0, "75–300 kW"),
    (1000.0, "300 kW–1 MW"),
    (5000.0, "1–5 MW"),
]


@dataclass(frozen=True)
class FaixasPotencia:
    """Faixas contiguas de potencia instalada, comecando em 0. `limites` sao as
    bordas (inclui o 0 inicial), `rotulos` tem um item menos que `limites`."""
    limites: tuple[float, ...]
    rotulos: tuple[str, ...]

    def classificar(self, kw: pd.Series) -> pd.Series:
        """Classifica a potencia de cada instalacao. Precisa rodar ANTES de qualquer
        groupby -- depois de agregar, a potencia individual esta perdida (sobra so a
        media do grupo, que nao permite reconstruir a distribuicao)."""
        return pd.cut(kw, bins=list(self.limites), labels=list(self.rotulos),
                      right=True, include_lowest=True)

    @property
    def microgeracao(self) -> frozenset[str]:
        """Rotulos que ficam inteiramente dentro da microgeracao (<=75 kW)."""
        return frozenset(r for r, teto in zip(self.rotulos, self.limites[1:])
                         if teto <= LIMITE_MICROGERACAO_KW)

    @property
    def teto_kw(self) -> float:
        return self.limites[-1]


def carregar_faixas(caminho: str | None = None) -> FaixasPotencia:
    """Le faixas_potencia.csv (colunas: limite_superior_kw, rotulo -- uma linha por
    faixa, em ordem crescente, comecando implicitamente em 0). Cai no default se o
    arquivo nao existir, pra nunca quebrar por falta de configuracao."""
    if caminho and os.path.exists(caminho):
        cfg = pd.read_csv(caminho, encoding="utf-8-sig")
        faltando = {"limite_superior_kw", "rotulo"} - set(cfg.columns)
        if faltando:
            raise ValueError(f"{caminho}: colunas faltando: {', '.join(sorted(faltando))}")
        cfg = cfg[cfg["rotulo"].notna() & (cfg["rotulo"].astype(str).str.strip() != "")]
        pares = [(float(t), str(r).strip()) for t, r in
                 zip(cfg["limite_superior_kw"], cfg["rotulo"])]
        origem = caminho
    else:
        pares = list(FAIXAS_POTENCIA_DEFAULT)
        origem = "default embutido"

    if len(pares) < 2:
        raise ValueError(f"{origem}: precisa de ao menos 2 faixas, veio {len(pares)}")
    tetos = [t for t, _ in pares]
    if any(b <= a for a, b in zip(tetos, tetos[1:])):
        raise ValueError(f"{origem}: limite_superior_kw precisa ser estritamente crescente, veio {tetos}")
    if tetos[0] <= 0:
        raise ValueError(f"{origem}: o primeiro limite_superior_kw precisa ser > 0, veio {tetos[0]}")
    rotulos = [r for _, r in pares]
    if len(set(rotulos)) != len(rotulos):
        raise ValueError(f"{origem}: rotulos duplicados em {rotulos}")
    # A borda de 75 kW nao e opcional: sem ela, uma faixa misturaria micro e
    # minigeracao e qualquer leitura por regime regulatorio ficaria errada.
    if LIMITE_MICROGERACAO_KW < tetos[-1] and LIMITE_MICROGERACAO_KW not in tetos:
        raise ValueError(
            f"{origem}: nenhuma faixa termina em {LIMITE_MICROGERACAO_KW:.0f} kW, o limite legal entre "
            f"micro e minigeracao -- alguma faixa ficaria com os dois regimes misturados. Limites: {tetos}"
        )
    return FaixasPotencia(limites=tuple([0.0] + tetos), rotulos=tuple(rotulos))


@lru_cache(maxsize=200_000)
def normalizar_texto(raw: object) -> str:
    """Normaliza um valor bruto para uma chave comparável. Retorna NULO se vazio."""
    if raw is None:
        return NULO
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return NULO
    s = _PREFIX_RE.sub("", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s if s else NULO


@dataclass(frozen=True)
class RegraSemente:
    canonical_brand: str
    aliases_exatos: frozenset
    tokens: frozenset


def carregar_semente(path: str) -> list[RegraSemente]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    regras = []
    for _, row in df.iterrows():
        aliases = frozenset(a.strip() for a in row["aliases_exatos"].split("|") if a.strip())
        tokens = frozenset(t.strip() for t in row["tokens_correspondencia"].split("|") if t.strip())
        regras.append(RegraSemente(row["canonical_brand"], aliases, tokens))
    return regras


def classificar(normalized: str, regras: list[RegraSemente]) -> tuple[str, str]:
    """Retorna (canonical_brand, match_method) para uma chave já normalizada."""
    if normalized == NULO:
        return NAO_INFORMADO, "NULO"
    if normalized in PLACEHOLDER_VALUES:
        return NAO_INFORMADO, "TEXTO_PLACEHOLDER"

    for regra in regras:
        if normalized in regra.aliases_exatos:
            return regra.canonical_brand, "ALIAS_EXATO"

    matches = set()
    for regra in regras:
        for token in regra.tokens:
            if re.search(rf"\b{re.escape(token)}\b", normalized):
                matches.add(regra.canonical_brand)
                break

    if len(matches) == 1:
        return next(iter(matches)), "TOKEN"
    if len(matches) > 1:
        return OUTROS, "TOKEN_AMBIGUO"
    return OUTROS, "NAO_ENCONTRADO"


def classificar_serie(raw_values: pd.Series, regras: list[RegraSemente]) -> pd.DataFrame:
    """Classifica uma série de valores brutos. Retorna DataFrame com raw, raw_normalized,
    canonical_brand, match_method — uma linha por valor de entrada (não deduplicado)."""
    normalizado = raw_values.map(normalizar_texto)
    distintos = normalizado.unique()
    resultado = {n: classificar(n, regras) for n in distintos}
    canonical = normalizado.map(lambda n: resultado[n][0])
    metodo = normalizado.map(lambda n: resultado[n][1])
    return pd.DataFrame({
        "raw": raw_values.values,
        "raw_normalized": normalizado.values,
        "canonical_brand": canonical.values,
        "match_method": metodo.values,
    })
