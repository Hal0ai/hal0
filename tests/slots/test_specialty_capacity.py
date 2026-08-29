from hal0.slots.capacity import (
    _ctx_tokens_for,
    companion_bytes_mb,
    estimate_file_size_kv_mb,
)

PF_META = {
    "metadata": {
        "specialty": "promptforge",
        "companions": {
            "promptforge_ffn": "/m/ffn.pfs",
            "promptforge_gdn": "/m/gdn.pfs",
            "promptforge_output_k8": "/m/k8.pfs",
        },
        # card sizes: 17.1 GB + 4.0 GB + 0.7 GB
        "companion_sizes": {
            "promptforge_ffn": 17_100_000_000,
            "promptforge_gdn": 4_000_000_000,
            "promptforge_output_k8": 700_000_000,
        },
    }
}


def test_companion_bytes_summed():
    mb = companion_bytes_mb(PF_META)
    assert 20_000 < mb < 21_500  # 21.8e9 bytes ≈ 20790 MiB


def test_plain_model_zero():
    assert companion_bytes_mb({"metadata": {}}) == 0.0
    assert companion_bytes_mb(None) == 0.0


def test_estimate_includes_companions():
    base = estimate_file_size_kv_mb(15_000.0, PF_META["metadata"])
    with_comp = estimate_file_size_kv_mb(
        15_000.0, PF_META["metadata"], companion_mb=companion_bytes_mb(PF_META)
    )
    assert with_comp > base + 20_000


def test_specialty_ctx_default():
    assert _ctx_tokens_for(PF_META["metadata"]) == 262_144


def test_explicit_defaults_context_size_still_wins():
    meta = {"defaults": {"context_size": 8192}, **PF_META["metadata"]}
    assert _ctx_tokens_for(meta) == 8192
