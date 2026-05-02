import manager


def test_configured_ports_from_agents_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BASE", tmp_path)
    (tmp_path / "agents.json").write_text(
        '{"agents":['
        '{"name":"a1","host":"127.0.0.1","port":23001,"enabled":true},'
        '{"name":"a2","host":"127.0.0.1","port":23002,"enabled":false},'
        '{"name":"a3","host":"127.0.0.1","port":23003,"enabled":true}'
        ']}',
        encoding="utf-8",
    )

    ports = manager._configured_ports()

    assert ports == [23001, 23003]


def test_configured_ports_fallback_legacy_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BASE", tmp_path)
    monkeypatch.delenv("AGENTS_CONFIG_FILE", raising=False)
    monkeypatch.delenv("AGENTS_CONFIG", raising=False)
    monkeypatch.setenv("HERMES_PROXY_PORT", "24001")
    monkeypatch.setenv("OPENCLAW_PROXY_PORT", "24002")

    ports = manager._configured_ports()

    assert ports == [24001, 24002]
