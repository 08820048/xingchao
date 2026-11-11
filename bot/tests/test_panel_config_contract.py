"""面板配置接口的字段契约：GET 返回的字段名必须与前端读取、POST 保存的字段名一致。

曾经的 bug：GET 返回 kv 键（vision_guard_enabled…），前端读 enabled / qrcode_enabled…，
读到 undefined，于是开关刷新后永远显示为「关」，看起来像"开启后自己关了"。
"""

import re
from pathlib import Path

import pytest

from src.plugins import visionguard as vg
from src.plugins import weeklyreport as wr

PANEL = "\n".join(f.read_text() for f in sorted(Path("src/plugins/panel").glob("*.py")))
APP = Path("../web/src/App.tsx").read_text() if Path("../web/src/App.tsx").exists() else ""


@pytest.fixture
def kv(store, monkeypatch):
    monkeypatch.setattr(vg, "get_store", lambda: store)
    monkeypatch.setattr(wr, "get_store", lambda: store)
    return store


async def test_vision_get_uses_frontend_field_names_and_reflects_saved_values(kv):
    cfg = await vg.vision_config()
    assert set(cfg) == set(vg.PANEL_KEYS)
    assert cfg["qrcode_enabled"] is True and cfg["notify"] is True  # 默认开
    await kv.set_kv("qrcode_guard_enabled", "false")
    await kv.set_kv("vision_notify", "false")
    cfg = await vg.vision_config()
    assert cfg["qrcode_enabled"] is False and cfg["notify"] is False
    await kv.set_kv("qrcode_guard_enabled", "true")
    assert (await vg.vision_config())["qrcode_enabled"] is True


async def test_report_panel_config_roundtrip(kv):
    assert (await wr.panel_report_config())["enabled"] is False
    await kv.set_kv("report_enabled", "true")
    cfg = await wr.panel_report_config()
    assert cfg["enabled"] is True and set(cfg) == set(wr.PANEL_KEYS)
    # 调度器用的仍是 kv 键
    assert (await wr.report_config())["report_enabled"] is True


def test_post_fields_are_all_readable_from_get():
    """POST 接受的字段名都必须能从 GET 读回来。"""
    block = PANEL[PANEL.index('async def panel_visionguard_post'):]
    block = block.split("@app.", 1)[0]
    posted = set(re.findall(r'\("(\w+)", "(?:vision|qrcode)_\w+"\)', block))
    assert posted and posted <= set(vg.PANEL_KEYS), posted - set(vg.PANEL_KEYS)


@pytest.mark.skipif(not APP, reason="前端源码不在")
def test_frontend_reads_only_fields_the_backend_returns():
    for ts_type, fields in (("VisionConfig", vg.PANEL_KEYS), ("ReportConfig", {**wr.PANEL_KEYS, "groups": 0})):
        body = APP[APP.index(f"type {ts_type} = {{"):]
        body = body[: body.index("};")]
        declared = set(re.findall(r"^\s+(\w+):", body, re.M))
        assert declared == set(fields), (ts_type, declared ^ set(fields))
