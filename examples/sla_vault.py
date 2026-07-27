# v0.2.18
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────
# SLAVault — worked consumer of ServiceUptimeOracle
#
# A service provider bonds GEN into this vault as a performance guarantee.
# Customers can claim a proportional penalty if the service drops below the
# agreed uptime target. The provider can reclaim their bond after the SLA
# period ends — but only if the target was met.
#
# This contract holds no probe logic. It reads two numbers off the oracle:
#   IServiceUptimeOracle(oracle).view().get_uptime_bps(service_id)
#   IServiceUptimeOracle(oracle).view().get_probe_count(service_id)
#
# Deploy order:
#   1. deploy ServiceUptimeOracle                   → oracle_address
#   2. oracle.register_service(url, name, ...)      → service_id
#   3. deploy SLAVault(oracle, service_id, target, period_end, min_probes)
#   4. provider calls fund() with the bond amount
#   5. anyone probes via oracle.probe(service_id) throughout the period
#   6. after period_end, customer calls claim() or provider calls reclaim()
# ─────────────────────────────────────────────────────────────────────────────

ERR_EXPECTED = "EXPECTED"
MIN_PROBES_DEFAULT = 5   # require at least this many probes before claim


@gl.contract_interface
class IServiceUptimeOracle:
    class View:
        def get_uptime_bps(self, service_id: u256) -> int: ...
        def get_probe_count(self, service_id: u256) -> int: ...
        def is_up(self, service_id: u256) -> bool: ...


@gl.evm.contract_interface
class _Recipient:
    """EVM-layer recipient for bond refunds and penalty payouts."""
    class View:
        pass
    class Write:
        pass


@allow_storage
@dataclass
class VaultState:
    claimed:   bool   # customer has claimed a penalty
    reclaimed: bool   # provider has reclaimed the bond
    paid:      bool   # a payment was emitted


class BondDeposited(gl.Event):
    def __init__(self, amount: u256, /, **blob): ...


class PenaltyClaimed(gl.Event):
    def __init__(self, customer: Address, amount: u256, uptime_bps: int, /, **blob): ...


class BondReclaimed(gl.Event):
    def __init__(self, provider: Address, amount: u256, /, **blob): ...


class SLAVault(gl.Contract):
    """
    SLA enforcement vault backed by ServiceUptimeOracle.

    The oracle's uptime_bps is the single source of truth. No one can
    override it — not the provider, not the customer, not this contract.
    """

    oracle:      Address
    service_id:  u256
    provider:    Address
    customer:    Address
    target_bps:  u256   # e.g. 9900 = 99 %
    period_end:  str    # ISO timestamp after which claims open
    min_probes:  u32    # minimum probe count for a valid claim
    state:       VaultState

    def __init__(
        self,
        oracle:     Address,
        service_id: u256,
        customer:   Address,
        target_bps: int,
        period_end: str,
        min_probes: int,
    ) -> None:
        self.oracle     = oracle if isinstance(oracle, Address) else Address(oracle)
        self.service_id = service_id
        self.provider   = gl.message.sender_address
        self.customer   = customer if isinstance(customer, Address) else Address(customer)
        self.target_bps = u256(max(0, min(10000, target_bps)))
        self.period_end = period_end
        self.min_probes = u32(max(1, min_probes))

    @gl.public.write.payable
    def fund(self) -> None:
        """Provider deposits the performance bond. Must be non-zero."""
        if gl.message.sender_address != self.provider:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: only the provider may fund")
        if gl.message.value == u256(0):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: bond must be non-zero")
        if bool(self.state.reclaimed) or bool(self.state.claimed):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: vault is already settled")
        BondDeposited(gl.message.value).emit()

    @gl.public.write
    def claim(self) -> None:
        """Customer claims a penalty if uptime fell below the target.

        Can only be called after period_end. The penalty is proportional to
        how far uptime fell below target — a total failure pays the full bond;
        a 0.5 % miss pays 0.5 % of the bond.

        State is written before value leaves (re-entrancy guard).
        """
        if gl.message.sender_address != self.customer:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: only the customer may claim")
        if bool(self.state.claimed) or bool(self.state.reclaimed):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: vault already settled")

        self._require_period_ended()

        oracle_if  = IServiceUptimeOracle(self.oracle)
        probe_count = oracle_if.view().get_probe_count(self.service_id)
        if probe_count < int(self.min_probes):
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: not enough probes ({probe_count} < {int(self.min_probes)})"
            )

        uptime_bps = oracle_if.view().get_uptime_bps(self.service_id)
        target_bps = int(self.target_bps)

        if uptime_bps >= target_bps:
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: SLA met — uptime {uptime_bps} bps >= target {target_bps} bps"
            )

        # Penalty proportional to the shortfall
        shortfall   = target_bps - uptime_bps          # e.g. 200 bps shortfall
        bond        = self.balance
        penalty     = bond * u256(shortfall) // u256(target_bps)
        if penalty > bond:
            penalty = bond

        # Write state before value moves
        self.state.claimed = True
        self.state.paid    = penalty > u256(0)

        if penalty > u256(0):
            _Recipient(self.customer).emit_transfer(value=penalty)

        # Return remainder to provider
        remainder = self.balance - penalty if self.balance >= penalty else u256(0)
        # Note: balance has already been reduced by the penalty emit; re-read
        leftover = self.balance
        if leftover > u256(0):
            _Recipient(self.provider).emit_transfer(value=leftover)

        PenaltyClaimed(self.customer, penalty, uptime_bps).emit()

    @gl.public.write
    def reclaim(self) -> None:
        """Provider reclaims the full bond after period ends if SLA was met.

        Reverts if uptime fell below target — the customer must claim instead.
        State is written before value leaves.
        """
        if gl.message.sender_address != self.provider:
            raise gl.vm.UserError(f"{ERR_EXPECTED}: only the provider may reclaim")
        if bool(self.state.claimed) or bool(self.state.reclaimed):
            raise gl.vm.UserError(f"{ERR_EXPECTED}: vault already settled")

        self._require_period_ended()

        uptime_bps = IServiceUptimeOracle(self.oracle).view().get_uptime_bps(
            self.service_id
        )
        if uptime_bps < int(self.target_bps):
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: SLA breached — customer may claim penalty"
            )

        amount = self.balance
        self.state.reclaimed = True

        if amount > u256(0):
            _Recipient(self.provider).emit_transfer(value=amount)

        BondReclaimed(self.provider, amount).emit()

    @gl.public.view
    def get_state(self) -> dict:
        return {
            "oracle":      str(self.oracle),
            "service_id":  int(self.service_id),
            "provider":    str(self.provider),
            "customer":    str(self.customer),
            "target_bps":  int(self.target_bps),
            "period_end":  str(self.period_end),
            "min_probes":  int(self.min_probes),
            "bond":        int(self.balance),
            "claimed":     bool(self.state.claimed),
            "reclaimed":   bool(self.state.reclaimed),
            "paid":        bool(self.state.paid),
        }

    def _require_period_ended(self) -> None:
        from datetime import datetime, timezone
        now_str = str(gl.message.raw.datetime if hasattr(gl.message, "raw") else "")
        if not now_str:
            raw = getattr(gl, "message_raw", {})
            now_str = raw.get("datetime", "") if isinstance(raw, dict) else ""
        if not now_str:
            now_str = datetime.now(timezone.utc).isoformat()

        def _parse(s: str):
            s = s.rstrip("Z").replace("+00:00", "")
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)

        try:
            if _parse(now_str) < _parse(str(self.period_end)):
                raise gl.vm.UserError(
                    f"{ERR_EXPECTED}: SLA period has not ended yet"
                )
        except gl.vm.UserError:
            raise
        except Exception:
            raise gl.vm.UserError(
                f"{ERR_EXPECTED}: could not parse period_end timestamp"
            )
