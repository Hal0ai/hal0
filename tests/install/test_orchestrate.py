from hal0.install.orchestrate import (
    Selections,
    SetupResult,
    SlotOutcome,
    SlotSelection,
)


def test_selections_roundtrip():
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081, model_id="qwen3-4b")],
        extensions={"openwebui": True, "hermes": True, "pi": False},
        npu_opt_in=False,
    )
    assert sel.slots[0].model_id == "qwen3-4b"
    assert sel.slots[0].device is None  # derived later
    assert sel.extensions["pi"] is False


def test_setup_result_shape():
    res = SetupResult(
        slots=[SlotOutcome(slot="chat", model_id="qwen3-4b")], extensions=[], model_ids=[], pulls=[]
    )
    assert res.slots[0].created is False
    assert res.slots[0].skipped is None
