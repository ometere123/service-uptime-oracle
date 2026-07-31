"""StudioNet integration coverage for ServiceUptimeOracle.

These tests exercise live validator consensus, real web.get calls, and the
public surface that direct mode can only simulate.

Run:
    gltest tests/integration/test_consensus.py -v -s --network studionet
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gltest import get_contract_factory
from gltest.accounts import create_accounts
from gltest.assertions import tx_execution_failed, tx_execution_succeeded
from gltest.contracts.contract import Contract
from gltest.utils import extract_contract_address


ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = ROOT / "contracts" / "service_uptime_oracle.py"
VAULT_PATH = ROOT / "examples" / "sla_vault.py"

STABLE_URL = "https://example.com/"
SERVICE_NAME = "Example Domain"
EXPECTED_TEXT = "Example Domain"

ORACLE_SCHEMA = {
    "methods": {
        "register_service": {"readonly": False},
        "probe": {"readonly": False},
        "deregister": {"readonly": False},
        "reactivate": {"readonly": False},
        "transfer_ownership": {"readonly": False},
        "is_up": {"readonly": True},
        "get_service": {"readonly": True},
        "get_stats": {"readonly": True},
        "get_uptime_bps": {"readonly": True},
        "get_uptime_bps_windowed": {"readonly": True},
        "get_recent_probes": {"readonly": True},
        "get_consecutive_down": {"readonly": True},
        "service_count": {"readonly": True},
        "get_probe_count": {"readonly": True},
    }
}

VAULT_SCHEMA = {
    "methods": {
        "fund": {"readonly": False},
        "claim": {"readonly": False},
        "reclaim": {"readonly": False},
        "get_state": {"readonly": True},
    }
}


@pytest.fixture(scope="module")
def oracle():
    factory = get_contract_factory("ServiceUptimeOracle")
    receipt = factory.deploy_contract_tx(args=[])
    assert tx_execution_succeeded(receipt)
    address = extract_contract_address(receipt)
    print(f"\nDeployed ServiceUptimeOracle at {address}")
    return Contract.new(address=address, schema=ORACLE_SCHEMA)


def _expect_refused(label, fn):
    try:
        receipt = fn()
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0]
        print(f"  [REFUSED as designed] {label}: {first_line[:160]}")
        return

    assert tx_execution_failed(receipt), f"{label} was allowed unexpectedly"


def _register_service(oracle, url=STABLE_URL, name=SERVICE_NAME):
    receipt = oracle.register_service(args=[url, name, EXPECTED_TEXT, 200]).transact()
    assert tx_execution_succeeded(receipt)
    return oracle.service_count().call()


def _probe(oracle, service_id):
    receipt = oracle.probe(args=[service_id]).transact()
    assert tx_execution_succeeded(receipt)


def test_live_probe_and_views_reach_consensus(oracle):
    service_id = _register_service(oracle)

    service = oracle.get_service(args=[service_id]).call()
    assert service["service_id"] == service_id
    assert service["url"] == STABLE_URL
    assert service["name"] == SERVICE_NAME
    assert service["active"] is True

    _probe(oracle, service_id)

    stats = oracle.get_stats(args=[service_id]).call()
    assert stats["total_probes"] == 1
    assert stats["last_status"] in (1, 2, 3)
    assert stats["last_status_name"] in ("UP", "DEGRADED", "DOWN")
    assert 0 <= stats["uptime_bps"] <= 10000

    assert oracle.is_up(args=[service_id]).call() == (stats["last_status"] == 1)
    assert oracle.get_uptime_bps(args=[service_id]).call() == stats["uptime_bps"]
    assert 0 <= oracle.get_uptime_bps_windowed(args=[service_id, 24]).call() <= 10000
    assert oracle.get_consecutive_down(args=[service_id]).call() >= 0

    probes = oracle.get_recent_probes(args=[service_id, 5]).call()
    assert len(probes) == 1
    assert probes[0]["probe_index"] == 0
    assert probes[0]["status"] == stats["last_status"]


def test_lifecycle_methods_and_transfer_ownership(oracle):
    owner_service_id = _register_service(
        oracle,
        url="https://www.iana.org/help/example-domains",
        name="IANA Example Domains",
    )
    other = create_accounts(1)[0]

    assert tx_execution_succeeded(
        oracle.deregister(args=[owner_service_id]).transact()
    )
    assert oracle.get_service(args=[owner_service_id]).call()["active"] is False

    _expect_refused(
        "probing a deregistered service",
        lambda: oracle.probe(args=[owner_service_id]).transact(),
    )

    assert tx_execution_succeeded(
        oracle.reactivate(args=[owner_service_id]).transact()
    )
    _probe(oracle, owner_service_id)

    assert tx_execution_succeeded(
        oracle.transfer_ownership(args=[owner_service_id, other.address]).transact()
    )
    assert oracle.get_service(args=[owner_service_id]).call()["owner"] == str(other.address)

    _expect_refused(
        "previous owner deregistering after transfer",
        lambda: oracle.deregister(args=[owner_service_id]).transact(),
    )
    assert tx_execution_succeeded(
        oracle.connect(other).deregister(args=[owner_service_id]).transact()
    )


def test_sla_vault_reads_oracle_state(oracle):
    customer = create_accounts(1)[0]
    service_id = _register_service(
        oracle,
        url="https://example.com/?sla-vault",
        name="SLA Vault Example",
    )
    _probe(oracle, service_id)

    period_end = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    vault_factory = get_contract_factory(contract_file_path=VAULT_PATH)
    receipt = vault_factory.deploy_contract_tx(
        args=[oracle.address, service_id, customer.address, 10000, period_end, 1]
    )
    assert tx_execution_succeeded(receipt)
    vault_address = extract_contract_address(receipt)
    vault = Contract.new(address=vault_address, schema=VAULT_SCHEMA)
    print(f"\nDeployed SLAVault at {vault.address}")

    state = vault.get_state().call()
    assert state["oracle"] == str(oracle.address)
    assert state["service_id"] == service_id
    assert state["customer"] == str(customer.address)

    uptime_bps = oracle.get_uptime_bps(args=[service_id]).call()
    if uptime_bps >= 10000:
        _expect_refused(
            "customer claiming when SLA was met",
            lambda: vault.connect(customer).claim().transact(),
        )
    else:
        receipt = vault.connect(customer).claim().transact()
        assert tx_execution_succeeded(receipt)
        assert vault.get_state().call()["claimed"] is True
