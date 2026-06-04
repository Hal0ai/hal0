from hal0.config.schema import SlotConfig


def _base(**over):
    data = {"name": "utility", "port": 8081, "model": {"default": "tiny.gguf"}}
    data.update(over)
    return data


def test_role_defaults_to_none():
    cfg = SlotConfig.model_validate(_base())
    assert cfg.role is None


def test_role_round_trips():
    cfg = SlotConfig.model_validate(_base(role="utility"))
    assert cfg.role == "utility"
    assert SlotConfig.model_validate(cfg.model_dump()).role == "utility"
