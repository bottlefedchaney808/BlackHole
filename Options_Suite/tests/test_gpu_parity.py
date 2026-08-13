"""
CPU/GPU parity test for the Heston LSM pricer (Options_Suite/MCHestonLSM.py).

Context: MCHestonLSM pricing runs through an `xp` backend (`xp = cupy if a
working CUDA device is detected at import else numpy`, mirroring MC.py), so
the exact same algorithm executes on the GPU when present and falls back to
CPU numpy otherwise. This test confirms the two backends agree.

Why CRN with a subprocess instead of just calling heston_lsm_price twice:
  - heston_lsm_price draws its own standard normals via `xp.random.seed(seed)`.
    cupy.random and numpy.random are DIFFERENT RNGs, so the same integer seed
    produces different paths on each backend -- the two results would differ
    by pure Monte Carlo sampling noise (~O(1/sqrt(N))), not by backend error.
  - So we drive both backends with the SAME pre-generated shock arrays via
    `_heston_lsm_price_crn` (common random numbers). With identical paths the
    simulation and LSM regression are bit-for-bit the same numerics on both
    backends (up to ~1e-12 float rounding in the elementwise ops), so a tight
    parity tolerance is meaningful.
  - The backend (`xp`) is fixed at module import of MCHestonLSM, and running
    under pytest the test conftest forces the CPU path. So each backend runs
    in its own `sys.executable -c` subprocess with OPTIONS_SUITE_FORCE_CPU
    set (CPU) or unset (GPU). The GPU child asserts it really did pick up
    cupy before its price is trusted -- otherwise the "parity" would be a
    trivial CPU-vs-CPU pass.

This test SKIPs when no CUDA-capable device is present (so it never fails on
a CPU-only box); it does NOT require OPTIONS_SUITE_FORCE_CPU to be unset in
the parent environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

S, K, T, r, q, V0, kappa, theta, xi, rho = (
    100.0,
    100.0,
    1.0,
    0.03,
    0.0,
    0.04,
    1.5,
    0.04,
    0.3,
    -0.3,
)
SIMS, STEPS = 100_000, 50
SEED = 2026


def _has_cuda_device() -> bool:
    """Independently probe for a usable CUDA device (cupy import, no op)."""
    code = (
        "try:\n"
        "    import cupy\n"
        "    print(cupy.cuda.runtime.getDeviceCount())\n"
        "except Exception:\n"
        "    print(0)\n"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return int(out.stdout.strip() or "0") > 0
    except Exception:
        return False


def _run_backend(shock_path: Path, force_cpu: bool) -> tuple[float, bool]:
    """Run _heston_lsm_price_crn in a subprocess on one backend."""
    code = (
        "import sys, numpy as np\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import Options_Suite.MCHestonLSM as M\n"
        "from Options_Suite.MCHestonLSM import _heston_lsm_price_crn\n"
        f"d = np.load({str(shock_path)!r})\n"
        "z1, zp = d['z1'], d['zp']\n"
        "if M.xp is not np:\n"
        "    z1 = M.xp.asarray(z1); zp = M.xp.asarray(zp)\n"
        f"p = _heston_lsm_price_crn({S}, {K}, {T}, {r}, {q}, {V0}, "
        f"{kappa}, {theta}, {xi}, {rho}, {SIMS}, {STEPS}, 'call', z1, zp)\n"
        "print(f'PRICE={float(p):.12f}')\n"
        "print(f'GPU={bool(M.GPU_ACTIVE)}')\n"
    )
    env = dict(os.environ)
    if force_cpu:
        env["OPTIONS_SUITE_FORCE_CPU"] = "1"
    else:
        env.pop("OPTIONS_SUITE_FORCE_CPU", None)
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"subprocess failed:\n{out.stderr}")
    price = gpu = None
    for line in out.stdout.splitlines():
        if line.startswith("PRICE="):
            price = float(line.split("=", 1)[1])
        elif line.startswith("GPU="):
            gpu = line.split("=", 1)[1] == "True"
    if price is None or gpu is None:
        raise RuntimeError(
            f"could not parse backend output from:\n{out.stdout}\n{out.stderr}"
        )
    return price, gpu


@pytest.mark.unit
@pytest.mark.skipif(not _has_cuda_device(), reason="no CUDA-capable device detected")
def test_heston_lsm_cpu_gpu_parity():
    """GPU (cupy) and CPU (numpy) must agree on the same fixed paths."""
    rng = np.random.default_rng(SEED)
    z1 = rng.standard_normal((STEPS, SIMS))
    zp = rng.standard_normal((STEPS, SIMS))

    shock_path = Path(__file__).with_name(
        f"_gpu_parity_shocks_{os.getpid()}_{uuid.uuid4().hex}.npz"
    )
    try:
        np.savez(shock_path, z1=z1, zp=zp)
        price_cpu, gpu_cpu = _run_backend(shock_path, force_cpu=True)
        price_gpu, gpu_gpu = _run_backend(shock_path, force_cpu=False)
    finally:
        shock_path.unlink(missing_ok=True)

    assert gpu_cpu is False, "CPU subprocess should have GPU_ACTIVE=False"
    assert gpu_gpu is True, "GPU subprocess did not activate the cupy backend"

    rel = abs(price_gpu - price_cpu) / abs(price_cpu)
    assert rel < 1e-3, (
        f"GPU {price_gpu:.8f} vs CPU {price_cpu:.8f} differ by {rel:.2e} "
        f"(> 1e-3) on identical shock paths -- backend mismatch."
    )
