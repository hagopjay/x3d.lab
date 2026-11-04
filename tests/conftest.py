"""Shared fixtures. Tests that need a live x3d_mcp server use the `lab` fixture, which starts the real stack
(server + gateway) once per test session. Point X3DLAB_X3D_MCP_DIR at a checkout of the pinned fork to reuse
it; otherwise the fixture clones it (network needed)."""

import os
import tempfile

import pytest

os.environ.setdefault("X3DLAB_HOME", tempfile.mkdtemp(prefix="x3dlab-test-"))


@pytest.fixture(scope="session")
def lab():
    import x3dlab
    from x3dlab.events import bus
    lab = x3dlab.up(tunnel="none", panel=False, log_to_screen=False, mcp_port=18000, gateway_port=18080, xite="cdn")
    bus.configure(to_screen=False)
    yield lab
    lab.down()


@pytest.fixture()
def base(lab):
    return lab.urls["local"]


@pytest.fixture()
def k(lab):
    return lab.dashboard_url("local")
