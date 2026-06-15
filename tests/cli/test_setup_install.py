from hal0.cli.setup_install import choose_apply_mode


def test_mode_in_process_when_api_down(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: False)
    assert choose_apply_mode() == "in_process"


def test_mode_api_when_up(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: True)
    assert choose_apply_mode() == "api"
