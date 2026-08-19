import pandas as pd
import pytest

import montar_payload_dashboard as mpd


def _periodos_fabricados():
    return ["2024-01", "2024-02"] + [f"2025-{m:02d}" for m in range(1, 13)] + [f"2026-{m:02d}" for m in range(1, 8)]


def test_montar_payload_campo_smoke(tmp_path, monkeypatch):
    monkeypatch.setattr(mpd, "PASTA_PROCESSADOS", str(tmp_path))
    periodos = _periodos_fabricados()
    linhas = []
    for i, pm in enumerate(periodos):
        ano, mes = pm.split("-")
        linhas.append({
            "ano_mes": pm, "ano": int(ano), "mes": int(mes), "trimestre": (int(mes) - 1) // 3 + 1,
            "ano_trimestre": f"{ano}-Q{(int(mes) - 1) // 3 + 1}",
            "regiao": "Sudeste", "SigUF": "SP", "CodUFibge": 35,
            "CodMunicipioIbge": 3550308, "NomMunicipio": "São Paulo",
            "faixa_potencia": "≤5 kW", "classe_consumo": "Residencial",
            "grupo_tarifario": "B1", "distribuidora": "ENEL SP",
            "marca": "Canadian Solar", "soma_kw": 10.0 + i, "qtd_instalacoes": 2, "kw_medio_instalacao": 5.0,
        })
    df = pd.DataFrame(linhas)
    df.to_parquet(tmp_path / "agregado_marca_modulo.parquet", index=False)

    payload = mpd.montar_payload_campo("modulo")

    assert payload["ultimo_mes_completo"]
    assert "Canadian Solar" in payload["marcas"]
    assert "serie_mensal" in payload and "serie_trimestral" in payload

    # 2025 e um ano calendario completo nos dados fabricados -- soma manual e
    # determinística independente de onde a ancora (ultimo mes completo) cair.
    soma_2025 = sum(10.0 + i for i, pm in enumerate(periodos) if pm.startswith("2025"))
    assert payload["periodos"]["totais_mw"]["2025"] == pytest.approx(soma_2025 / 1000, rel=1e-6)
