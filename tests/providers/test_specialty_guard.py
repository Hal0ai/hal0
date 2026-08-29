import pytest

from hal0.errors import UnprocessableEntity
from hal0.providers.container import _guard_specialty_runner
from hal0.runners import RUNNER_IMAGES


def _pf_model(companions=None):
    comps = companions if companions is not None else {
        "promptforge_ffn": "/var/lib/hal0/models/m/ffn.pfs",
        "promptforge_gdn": "/var/lib/hal0/models/m/gdn.pfs",
        "promptforge_output_k8": "/var/lib/hal0/models/m/k8.pfs",
    }
    return {
        "_model_key": "qwen-pf",
        "metadata": {"specialty": "promptforge", "companions": comps},
    }


def test_capable_runner_passes():
    assert _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["promptforge"]) is None


def test_incapable_runner_degrades_with_reason():
    reason = _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"
    assert reason["specialty"] == "promptforge"
    assert reason["runner"] == "rocmfpx"


def test_missing_required_companion_degrades_even_on_capable_runner():
    model = _pf_model(companions={"promptforge_ffn": "/x/ffn.pfs"})  # gdn+k8 missing
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["promptforge"])
    assert reason["code"] == "slot.specialty_degraded"
    assert "promptforge_gdn" in reason["detail"]


def test_plain_model_is_untouched():
    model = {"_model_key": "plain", "metadata": {}}
    assert _guard_specialty_runner(model, RUNNER_IMAGES["cpu"]) is None


def test_unknown_specialty_degrades_not_crashes():
    model = {"_model_key": "future", "metadata": {"specialty": "hyperdrive-v9"}}
    reason = _guard_specialty_runner(model, RUNNER_IMAGES["rocmfpx"])
    assert reason["code"] == "slot.specialty_degraded"


def test_degraded_not_ok_raises_422(monkeypatch):
    import dataclasses
    import hal0.registry.specialty as sp
    strict = dataclasses.replace(sp.SPECIALTY_KINDS["promptforge"], degraded_ok=False)
    monkeypatch.setitem(sp.SPECIALTY_KINDS, "promptforge", strict)
    with pytest.raises(UnprocessableEntity) as exc:
        _guard_specialty_runner(_pf_model(), RUNNER_IMAGES["rocmfpx"])
    assert exc.value.code == "slot.unsupported_specialty_for_runner"
