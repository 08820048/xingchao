"""面板积分导出的 CSV 基础契约：BOM、公式注入转义。"""

from __future__ import annotations


def test_csv_export_has_bom_and_escapes_formulas():
    from src.plugins.panel import routes_points

    assert routes_points._safe_cell("=1+1") == "'=1+1"
    assert routes_points._safe_cell("-20") == "'-20"
    assert routes_points._safe_cell("正常") == "正常"

    response = routes_points._csv_response(
        "points.csv", ["user_id", "note"], [[1, "=cmd"], [2, "正常"]]
    )
    assert response.media_type == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    body = bytes(response.body).decode("utf-8")
    assert body.startswith("\ufeff")
    assert "'=cmd" in body and "正常" in body
