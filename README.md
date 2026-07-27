# ServiceUptimeOracle

A reusable GenLayer primitive that turns live endpoint availability into an
on-chain uptime record.

`ServiceUptimeOracle` is infrastructure, not a monitoring app. It is what an
SLA vault, insurance trigger, marketplace gate or reputation system calls
instead of trusting a provider-run status page or a centralised uptime monitor.

## The problem

Every contract that settles on service availability has to answer the same
question: who observed the endpoint, and why should either party believe them?

The usual answers are weak in ways that matter:

| Approach | What breaks |
|---|---|
| Provider status page | The party with the liability controls the evidence. |
| Centralised monitor | Everyone now trusts one operator, one region, one account and one data-retention policy. |
| Raw HTTP status check | `200 OK` can still mean "database unavailable" in the body. |
| User-submitted outage proof | The caller chooses the evidence after knowing which answer benefits them. |
| One model call | Someone still has to be trusted to have run it honestly, and one reading has no consensus property. |

ServiceUptimeOracle makes the endpoint itself the evidence. Validators fetch the
URL independently, classify the rendered response, agree on the health verdict,
and write the result to a bounded on-chain history.

## What this does instead

```python
service_id = oracle.register_service(
    "https://api.example.com/health",
    "Example API",
    "ok",
    200,
)

oracle.probe(service_id)

oracle.is_up(service_id)          # latest probe was UP
oracle.get_uptime_bps(service_id) # uptime in basis points
oracle.get_recent_probes(service_id, 10)
```

Probe outcomes are explicit:

| Status | Meaning |
|---|---|
| `UNKNOWN` | Probe output was unparseable. Fails closed and counts as not-up. |
| `UP` | Endpoint responded and looked healthy. |
| `DEGRADED` | Endpoint responded but showed partial failure, maintenance or missing expected content. |
| `DOWN` | Endpoint was unreachable, empty, timed out, or returned an error response. |

`UP` is the only status counted as uptime. `DEGRADED` is not treated as up, but
it also resets `consecutive_down` so consumers can distinguish a hard outage
from partial service.

## Why this needs GenLayer

Availability is public but external. A deterministic VM cannot know whether an
API was reachable at 03:00 UTC unless someone tells it. If that someone is a
provider, customer, monitor operator or relayer, the trust problem has only been
moved.

GenLayer supplies the missing property: independent validators each observe the
same endpoint and the transaction only lands if their readings agree in
substance. No party authors the result, no single endpoint reader is privileged,
and disagreement becomes an `UNDETERMINED` transaction rather than a silent
guess.

The key point is not just that a fetch happened. It is that multiple validators
independently reached the same health classification. That is what lets another
contract safely branch on `is_up()` or `get_uptime_bps()`.

## The test that matters

The honest check on whether consensus is doing real work: does the output gate
value?

[`examples/sla_vault.py`](examples/sla_vault.py) is a complete worked consumer.
A provider bonds GEN into a vault. After the SLA period, the customer can claim a
penalty only if the oracle reports uptime below the target and enough probes
exist. The vault performs no fetching, writes no prompt, defines no equivalence
principle, and does not decide what "healthy" means.

If a single party could author the oracle result, the vault would be unsafe. The
consensus-backed uptime record is the reason it is meaningful to enter the SLA.

## Why it is not the rejected patterns

| Anti-pattern | Why this is not that |
|---|---|
| "An AI app with GenLayer attached" | The output is a typed on-chain status record consumed by contracts, not advice or a summary. |
| "A validator that only checks output format" | The equivalence principle requires validators to agree on availability status, reachability, and content-check result. Valid JSON with a different health verdict fails consensus. |
| "Judging from user-submitted text" | Callers supply a URL and expectations. Validators fetch the live endpoint inside the consensus block. |
| "A thin LLM wrapper" | Registration, access control, bounded storage, uptime arithmetic, windowing, lifecycle rules and fail-closed defaults are deterministic contract logic. |
| "A dashboard product" | The primitive has no UI assumptions. It exposes a small interface any contract can import. |

## Why each non-deterministic call is non-deterministic

Only `probe(service_id)` enters consensus. Registration, lifecycle methods,
ownership transfer and all views are deterministic.

| Call | Where | Why it cannot be deterministic |
|---|---|---|
| `gl.nondet.web.render(url, mode="text")` | `probe` | Network I/O against a live service. A contract cannot deterministically learn whether an endpoint responded. |
| `gl.nondet.exec_prompt(prompt)` | `probe` | Health is partly semantic. A parser can match bytes; it cannot reliably decide that a page saying "database unavailable" is unhealthy despite HTTP 200. |

The fetched body is evidence, never instruction. The prompt explicitly tells the
model to ignore response text that tries to command the assessor.

## What is deliberately deterministic

Every operation that decides how state changes after consensus is ordinary
contract code:

- input validation for URL, name, expected status and content checks;
- service ownership and lifecycle permissions;
- status clamping and fail-closed parsing;
- `UP` / `DEGRADED` / `DOWN` counter updates;
- `consecutive_down` reset and increment rules;
- ring-buffer slot selection;
- overall uptime arithmetic;
- windowed uptime scanning;
- recent-probe ordering;
- service count and metadata views.

The model returns a proposed health classification. The contract decides how
that classification affects storage.

## Equivalence principle

Strict byte equality would be the wrong rule. Validators can fetch the same page
moments apart and receive different timestamps, headers, cache content or minor
body variations.

The equivalence principle instead asks validators to agree on the properties
that matter:

- same availability status: `UP`, `DEGRADED` or `DOWN`;
- same reachability outcome;
- same content-check result when `response_contains` is configured.

Differences in prose, exact response body text, response headers and note
wording are ignored. A validator seeing `UP` while another sees `DOWN` is not
equivalent, because that is the service state the contract exists to record.

## Ordering discipline

The expensive work is bracketed by deterministic guards:

```text
register_service
  validate inputs, assign owner, initialise stats      <- deterministic

probe
  require service exists and is active                 <- deterministic
  render endpoint and classify health                  <- consensus
  clamp status and truncate note                       <- deterministic
  update ring buffer and aggregate counters            <- deterministic

views
  compute uptime and recent history from stored state  <- deterministic
```

A bad registration never creates a service. A deactivated service never reaches
the consensus round. A malformed model result becomes `UNKNOWN`, which fails
closed and is counted as not-up.

## Safety properties

- A provider cannot suppress bad readings. `probe()` is permissionless.
- A customer cannot fake an outage. The contract fetches the endpoint itself.
- A deactivated service cannot be probed until the owner reactivates it.
- Ownership transfer does not reset stats or history.
- A missing service returns `False` for `is_up()` and errors for detailed views.
- Empty or failed renders become `DOWN`.
- Unparseable model output becomes `UNKNOWN`.
- Statuses outside the known enum are clamped to `UNKNOWN`.
- Probe history is bounded to 100 records per service.
- Recent probes are returned newest first.
- Windowed uptime never scans unbounded history.

## How it works

```text
register_service(url, name, response_contains, expected_status)
   |
   | deterministic:
   |   validate parameters
   |   store ServiceConfig
   |   initialise ServiceStats
   v

probe(service_id)
   |
   | deterministic:
   |   require service exists and active
   v
   consensus:
      render URL as text
      classify response as UP / DEGRADED / DOWN
      EP: validators must agree on health verdict
   |
   | deterministic:
   |   clamp status
   |   write ProbeRecord into ring buffer
   |   update aggregate counters
   |   emit ServiceProbed / ServiceStatusChanged
   v

consumer contract reads:
   is_up(service_id)
   get_uptime_bps(service_id)
   get_recent_probes(service_id, limit)
```

## The ring buffer

Each service keeps at most 100 probe records. The key is:

```python
f"{service_id}:{slot}"
```

where:

```python
slot = total_probes % 100
```

This makes storage bounded and predictable. Aggregate counters keep all-time
uptime, while the ring buffer keeps enough recent history for consumers that
care about short windows and latest status changes.

## Why this is reusable

The falsifiable version: a consumer reads one or two values.

```python
uptime = IServiceUptimeOracle(self.oracle).view().get_uptime_bps(self.service_id)
probe_count = IServiceUptimeOracle(self.oracle).view().get_probe_count(self.service_id)

if probe_count >= u32(5) and uptime < u256(9900):
    ...  # settle SLA breach
```

What the consumer does not need to learn:

- how to fetch a live endpoint inside consensus;
- how to write an equivalence principle for response drift;
- how to classify `200 OK` with error content;
- how to store bounded probe history;
- how to compute uptime basis points safely;
- how to fail closed when the model or web read misbehaves.

| Use case | How it uses the primitive |
|---|---|
| SLA vault | Pays a penalty when uptime falls below target. |
| Parametric insurance | Triggers a claim when a covered service is down. |
| Marketplace gating | Suspends a listing or provider after repeated failures. |
| Access control | Allows actions only while a required endpoint is healthy. |
| Reputation | Tracks service reliability over time. |
| DAO operations | Gates milestone or vendor payments on availability. |

They differ only in service URL, target uptime, minimum probe count and the
consumer's response to the reading.

## The integration surface, in full

```python
@gl.contract_interface
class IServiceUptimeOracle:
    class View:
        def is_up(self, service_id: u256) -> bool: ...
        def get_service(self, service_id: u256) -> dict: ...
        def get_stats(self, service_id: u256) -> dict: ...
        def get_uptime_bps(self, service_id: u256) -> int: ...
        def get_uptime_bps_windowed(self, service_id: u256, window_hours: u32) -> int: ...
        def get_recent_probes(self, service_id: u256, limit: u32) -> list: ...
        def get_consecutive_down(self, service_id: u256) -> int: ...
        def service_count(self) -> int: ...

    class Write:
        def register_service(
            self,
            url: str,
            name: str,
            response_contains: str,
            expected_status: int,
        ) -> u256: ...
        def probe(self, service_id: u256) -> None: ...
```

Most consumers only need:

```python
is_healthy = oracle.view().is_up(service_id)
uptime_bps = oracle.view().get_uptime_bps(service_id)
```

## API

### Writes

| Method | Purpose |
|---|---|
| `register_service(url, name, response_contains, expected_status) -> u256` | Register an endpoint and return its service id. |
| `probe(service_id)` | Permissionlessly run one consensus-backed health probe. |
| `deregister(service_id)` | Owner-only pause. Keeps history and id. |
| `reactivate(service_id)` | Owner-only resume. |
| `transfer_ownership(service_id, new_owner)` | Move service ownership without resetting history. |

### Views

| Method | Purpose |
|---|---|
| `is_up(service_id)` | `True` only when the latest status is `UP`. |
| `get_service(service_id)` | Registration metadata and active flag. |
| `get_stats(service_id)` | Aggregate counters, latest status, timestamps and uptime. |
| `get_uptime_bps(service_id)` | All-time uptime in basis points. |
| `get_uptime_bps_windowed(service_id, window_hours)` | Recent uptime over retained probes in a time window. |
| `get_recent_probes(service_id, limit)` | Newest probe records first, capped by ring-buffer size. |
| `get_consecutive_down(service_id)` | Current hard-outage streak. |
| `get_probe_count(service_id)` | Total probes taken for the service. |
| `service_count()` | Total registered services, including inactive services. |

### Events

`ServiceRegistered` · `ServiceProbed` · `ServiceStatusChanged` ·
`ServiceDeregistered` · `OwnershipTransferred`

## Development

```powershell
python -m pytest tests/direct/ -q
python -m pytest tests/integration/test_consensus.py -v -s --network studionet
genlayer deploy --contract contracts/service_uptime_oracle.py --rpc https://studio.genlayer.com/api
```

## Test coverage

38 direct tests cover the state machine and adversarial cases.

| Area | Cases |
|---|---|
| Registration | incremental ids, stored fields, URL scheme, empty values, status bounds, service cap behavior. |
| Probe outcomes | `UP`, `DEGRADED`, `DOWN`, `UNKNOWN`, fetch failure, empty body, LLM fallback. |
| Model misbehavior | fenced JSON, malformed output, unknown status, prompt-injection content. |
| Uptime arithmetic | all-up, all-down, mixed histories, no-probe default. |
| Windowed uptime | old probes excluded, boundary behavior, no probes in window. |
| Ring buffer | newest-first ordering, limit handling, capped history. |
| Lifecycle | deregister, reactivate, probe refusal while inactive. |
| Access control | owner-only deregister/reactivate/transfer, zero-address transfer rejection. |
| Multiple services | independent stats and histories. |

Integration tests against StudioNet cover the live path:

- deploy `ServiceUptimeOracle`;
- register service;
- probe `https://example.com/` under real consensus;
- read every oracle view;
- deregister, prove probing is refused, reactivate and probe again;
- transfer ownership and prove the previous owner loses control;
- deploy `SLAVault` and verify it reads oracle state.

## Notes on the environment

Two host-level workarounds live in [`tests/conftest.py`](tests/conftest.py);
neither affects contract behavior.

On Windows, `gltest` direct mode can try to unlink a temp file still bound to
file descriptor 0. The test shim tolerates that and sweeps leaked temp files at
process exit.

The SDK also permits one `gl.Contract` subclass per process, so the registry is
reset between tests. Without that, multi-contract suites can pass or fail based
on file ordering.

For StudioNet integration, this installed `gltest` version cannot fetch contract
schemas from StudioNet. The integration test supplies a local method schema
after asserting deployment succeeded; all writes and reads still execute against
StudioNet.

## Layout

```text
contracts/service_uptime_oracle.py        the primitive
examples/sla_vault.py                     worked consumer
tests/direct/                             in-memory tests with mocked web/model
tests/integration/                        live consensus tests
tests/conftest.py                         host workarounds only
gltest.config.yaml                        StudioNet/localnet config
```

## Status

Lint clean. 38 direct tests pass. 3 integration tests pass against real
StudioNet consensus, including a full oracle surface run and a consumer-vault
smoke test.

## Deployed

| Field | Value |
|---|---|
| Network | StudioNet |
| Address | `0x6b7d9775B69b5e004da97480D0683EcfC1249722` |
| Studio | `https://studio.genlayer.com/?import-contract=0x6b7d9775B69b5e004da97480D0683EcfC1249722` |
| Explorer | `https://explorer-studio.genlayer.com/address/0x6b7d9775B69b5e004da97480D0683EcfC1249722` |

The integration suite also deploys `SLAVault` as a consumer smoke test and
verifies that it can read oracle state. The vault is an example contract, not
the submitted primitive, so the public deployment listed above is the oracle
only.

## Measured on live consensus

Stable endpoint:

```text
URL: https://example.com/
Expected status: 200
Required content: Example Domain
```

The StudioNet integration run deployed the oracle, registered the endpoint,
probed it through live consensus, then read:

- `get_service`;
- `get_stats`;
- `is_up`;
- `get_uptime_bps`;
- `get_uptime_bps_windowed`;
- `get_recent_probes`;
- `get_consecutive_down`;
- `get_probe_count`;
- `service_count`.

The same run exercised the lifecycle:

```text
deregister -> probe refused -> reactivate -> probe succeeds
transfer_ownership -> previous owner refused -> new owner deregisters
```

And the consumer path:

```text
deploy SLAVault -> read oracle address, service id, customer and bond state
```

## The honest limits

- Not for high-frequency monitoring. Each probe is a consensus transaction.
- Not a scheduler. External callers decide when to probe.
- Not a regional availability oracle yet. Each probe asks validators to observe
  the URL; it does not expose region-specific status.
- Ring-buffer history is capped at 100 records per service.
- Windowed uptime can only scan retained history.
- A genuinely intermittent service can produce `UNDETERMINED`; callers should
  retry.
- A configured `response_contains` check is case-sensitive and intentionally
  simple.

## Roadmap

- Minimum probe cadence and freshness checks for consumers.
- Multi-endpoint service groups for regional redundancy.
- Optional alert subscriber contracts.
- Richer maintenance/degradation categories.
- Per-service probe fee policy to compensate callers.
