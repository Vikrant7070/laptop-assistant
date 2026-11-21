"""WP 0.1: does PyTorch load, and does it see the RTX 4050?

Run with the child brain's own Python:
    .venv-childbrain\\Scripts\\python -m pytest childbrain/tests/test_gpu.py -s

Kept tiny on purpose: Nova may be using most of the 6 GB GPU at the same time.
"""

import pytest

# Nova's own Python has no torch; skip there instead of failing Nova's test run.
torch = pytest.importorskip("torch")


def test_torch_loads():
    print(f"torch {torch.__version__}, built for CUDA {torch.version.cuda}")
    assert torch.version.cuda, "this is the CPU-only PyTorch build, not the CUDA one"


def test_gpu_ok():
    assert torch.cuda.is_available(), "PyTorch loaded but cannot see the GPU"
    name = torch.cuda.get_device_name(0)
    total_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
    assert "RTX 4050" in name, f"unexpected GPU: {name}"

    # A small sum on the GPU, checked against the CPU.
    a = torch.arange(1024, dtype=torch.float32)
    b = (a.cuda() * 2).sum().item()
    assert b == float((a * 2).sum())

    torch.cuda.empty_cache()
    print(f"GPU OK: {name}, {total_gb:.1f} GB")
