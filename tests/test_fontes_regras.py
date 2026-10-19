"""Toda regra de classificação (tipo de inversor, tecnologia de módulo) diz de onde veio o conhecimento.

Regra "confirmado" de marca precisa de link e data da consulta; regra "inferido" diz que é dedução.
"""
import os

import pandas as pd
import pytest

PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
TIPOS = {"ficha técnica (fabricante)", "ficha técnica ou anúncio (distribuidor)", "página do fabricante",
         "catálogo do fabricante", "base pública (CEC/ENF/EPD)", "notícia do setor", "texto do cadastro",
         "dedução pela nomenclatura", "dedução pela potência"}


@pytest.mark.parametrize("arquivo", ["tipo_modelo_inversor.csv", "tecnologia_modelo_modulo.csv"])
def test_regras_tem_fonte(arquivo):
    d = pd.read_csv(os.path.join(PASTA, arquivo), dtype=str, keep_default_na=False)
    assert {"tipo_fonte", "link_fonte", "data_consulta"} <= set(d.columns)
    assert set(d.tipo_fonte) <= TIPOS, set(d.tipo_fonte) - TIPOS
    confirmadas = d[(d.confianca == "confirmado") & (d.marca != "*")]
    sem_link = confirmadas[~confirmadas.link_fonte.str.startswith("http") | (confirmadas.data_consulta == "")]
    assert sem_link.empty, f"regra confirmada sem link ou data: {sem_link[['marca', 'padrao']].values.tolist()}"
    deducao = d[d.tipo_fonte.str.startswith("dedução")]
    assert (deducao.confianca != "confirmado").all(), "dedução não pode estar marcada como confirmada"
