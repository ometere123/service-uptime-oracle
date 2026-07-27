"""Shared test configuration for ServiceUptimeOracle.

Contains the warp_to helper, Windows temp-file shim, contract registry reset,
and address coercion helper. None of this affects contract behaviour.
"""

import atexit
import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_contract_registry():
    """One gl.Contract subclass per process — reset between tests."""
    yield
    try:
        import genlayer.gl.genvm_contracts as contracts
    except ImportError:
        return
    contracts.__known_contract__ = None


def warp_to(direct_vm, iso_timestamp: str) -> None:
    """Advance the transaction clock everywhere the contract can read it.

    direct_vm.warp() sets the VM clock and patches datetime.now(), but it
    does not update the "datetime" key in gl.message_raw — that key is
    injected once at contract load and never refreshed. Any time-dependent
    logic that reads the message datetime therefore sees a frozen clock no
    matter how many times a test warps.

    This helper warps the VM and patches both accessor shapes together.
    Without it, every deadline and windowed-uptime test passes vacuously
    with zero elapsed time.
    """
    direct_vm.warp(iso_timestamp)

    import sys

    gl = sys.modules.get("genlayer.gl")
    if gl is None:
        return

    raw = getattr(gl, "message_raw", None)
    if isinstance(raw, dict):
        raw["datetime"] = iso_timestamp

    message = getattr(gl, "message", None)
    nested  = getattr(message, "raw", None)
    if isinstance(nested, dict):
        nested["datetime"] = iso_timestamp


CONTRACTS_DIR     = os.path.join(os.path.dirname(os.path.dirname(__file__)), "contracts")
EXAMPLES_DIR      = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples")
REFERENCE_CONTRACT = os.path.join(CONTRACTS_DIR, "service_uptime_oracle.py")


def as_address(value):
    """Coerce a raw-bytes account fixture into a genlayer Address."""
    if not isinstance(value, bytes):
        return value
    try:
        from genlayer.py.types import Address
    except ImportError:
        from pathlib import Path
        from gltest.direct.sdk_loader import setup_sdk_paths
        setup_sdk_paths(Path(REFERENCE_CONTRACT), None)
        from genlayer.py.types import Address
    return Address(value)


# ── Windows shim ──────────────────────────────────────────────────────────────
#
# gltest's direct loader unlinks a temp file that is still bound to fd 0.
# On POSIX the descriptor keeps the inode alive and the unlink succeeds.
# On Windows the file is locked, so os.unlink raises PermissionError
# (WinError 32). We let the unlink fail quietly and sweep the leaked files
# at process exit.

if sys.platform == "win32":  # pragma: no cover
    try:
        from gltest.direct import loader as _gltest_loader
    except ImportError:
        _gltest_loader = None

    if _gltest_loader is not None:
        _leaked_paths: list[str] = []
        _real_unlink = os.unlink

        def _tolerant_unlink(path, *args, **kwargs):
            try:
                return _real_unlink(path, *args, **kwargs)
            except PermissionError:
                _leaked_paths.append(os.fspath(path))

        _original_inject = _gltest_loader._inject_message_to_fd0

        def _inject_message_to_fd0(vm):
            os.unlink = _tolerant_unlink
            try:
                return _original_inject(vm)
            finally:
                os.unlink = _real_unlink

        _gltest_loader._inject_message_to_fd0 = _inject_message_to_fd0

        @atexit.register
        def _sweep_leaked_temp_files():
            for path in _leaked_paths:
                try:
                    _real_unlink(path)
                except OSError:
                    pass
