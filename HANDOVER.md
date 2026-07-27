# ServiceUptimeOracle — Handover

## Status
Direct tests: **38/38 passing**. Ready for integration tests → StudioNet deploy → README → GitHub push.

## What This Is
GenLayer Intelligent Contract submission for the weekly Portal IC competition.
Author: PAPITO (`45469370+ometere123@users.noreply.github.com`, GitHub: `ometere123`).

**CRITICAL attribution rule**: Never add `Co-Authored-By`, `Generated with`, or any AI attribution to commits. All commits must be sole-authored by PAPITO. Verify before any push:
```
git log -1 --format='%B' | grep -i "co-authored\|claude\|generated"
```

## Completed Contracts (prior sessions)
| Contract | Address |
|---|---|
| SemanticWatcher | `0x4307441035EDdd5Fe64aAec8321729321c8c498a` |
| SourceQuorum | `0xc9fCE280384A1B3D2CE03d2CB6f6344d36e205A2` |

## This Submission: ServiceUptimeOracle
**Root**: `C:/Users/USER/Desktop/intelligent contracts/service-uptime-oracle/`

### Files
| File | Purpose |
|---|---|
| `contracts/service_uptime_oracle.py` | Main IC (~750 lines, lint clean) |
| `examples/sla_vault.py` | Consumer example — SLA enforcement vault |
| `tests/direct/test_service_uptime_oracle.py` | 38 direct tests, all passing |
| `tests/conftest.py` | `warp_to()`, Windows shim, `as_address()` |
| `gltest.config.yaml` | studionet config |
| `DECISION.md` | 12-candidate decision record |

### Contract Design
- Registers service URLs, probes them under consensus, stores results in ring buffer
- Uses `gl.nondet.web.render(url, mode="text")` → `gl.nondet.exec_prompt(prompt)` inside named `leader()` function
- EP: `gl.eq_principle.prompt_comparative(leader, EQ_PROBE_ASSESSMENT)`
- Ring buffer: `TreeMap[str, ProbeRecord]` keyed by `"{service_id}:{slot}"`, `slot = total_probes % 100`
- Status constants: UNKNOWN=0, UP=1, DEGRADED=2, DOWN=3
- Storage: `ServiceConfig`, `ServiceStats`, `ProbeRecord` (all `@allow_storage @dataclass`)
- Consumer interface: `@gl.contract_interface class IServiceUptimeOracle` with View + Write

### Key Contract Methods
**Write**: `register_service(url, name, response_contains, expected_status) → u256`, `probe(service_id)`, `deregister(service_id)`, `reactivate(service_id)`, `transfer_ownership(service_id, new_owner)`

**View**: `is_up(service_id)`, `get_stats(service_id)`, `get_uptime_bps(service_id)`, `get_uptime_bps_windowed(service_id, window_hours)`, `get_recent_probes(service_id, limit)`, `get_consecutive_down(service_id)`, `get_service(service_id)`, `service_count()`

### Mock Strategy (direct tests)
`mock_web` and `mock_llm` are **first-wins** per URL/pattern — the first registration for a given key persists; subsequent calls for the same key are silently ignored.

```python
# DOWN probe: status 503 → render raises → exception caught → STATUS_DOWN (no LLM call)
direct_vm.mock_web(r".*api\.example\.com.*", {"status": 503, "body": ""})

# UP probe: status 200 + body → render returns content → LLM called
direct_vm.mock_web(r".*api\.example\.com.*", {"status": 200, "body": BODY_UP})
direct_vm.mock_llm(r"You are assessing", json.dumps({"status": "UP", "note": "ok"}))
```

Mixed-status tests (DOWN then UP for same URL) use **two separate services with different URLs** to work around first-wins.

## What Remains

### 1. Integration Tests
Create `tests/integration/test_consensus.py`. Drive every write method and every view on StudioNet (real validators, real consensus). Pattern from SemanticWatcher's integration tests at `C:/Users/USER/Desktop/intelligent contracts/semantic-watcher/tests/integration/`.

Minimum coverage:
- Deploy → register service → probe (wait for consensus) → assert `is_up` or `get_stats`
- `get_uptime_bps`, `get_recent_probes`, `get_uptime_bps_windowed`
- `deregister` → `probe` reverts → `reactivate` → probe succeeds
- `transfer_ownership`
- `SLAVault` basic flow (fund → period not ended → claim reverts → warp → claim succeeds)

Run with:
```
cd "C:/Users/USER/Desktop/intelligent contracts/service-uptime-oracle"
python -m pytest tests/integration/ -v --timeout=120
```

### 2. StudioNet Deploy
```
cd "C:/Users/USER/Desktop/intelligent contracts/service-uptime-oracle"
genlayer deploy contracts/service_uptime_oracle.py --network studionet
```
Record the deployed address. Then call each method manually to verify on-chain behavior. Check the explorer.

### 3. README
The README must cover all 14 required IC submission points:
1. What problem does this solve
2. Why GenLayer / why trustless
3. How it uses `web.render` (non-det) + EP
4. Architecture / data flow
5. Storage layout (ring buffer)
6. Consumer interface (`IServiceUptimeOracle`)
7. `SLAVault` example walkthrough
8. Deploy steps
9. Test results (38 direct + N integration)
10. Deployed address + explorer link
11. Decisions / trade-offs (see `DECISION.md`)
12. Limitations / future work
13. How to run tests locally
14. Comparison to ecosystem projects (why this competes)

### 4. GitHub Push
Create a new repo, push with clean history, sole-authored commits. No AI attribution anywhere.

```
git init
git add contracts/ examples/ tests/ gltest.config.yaml DECISION.md README.md
git commit -m "feat: add ServiceUptimeOracle IC submission"
git remote add origin <repo-url>
git push -u origin main
```

## Environment
- OS: Windows 11 (`PowerShell` primary shell, `Bash` tool also available)
- Working dir: `C:/Users/USER/Desktop/intelligent contracts/service-uptime-oracle/`
- Python env has `gltest`, `genlayer` SDK installed
- StudioNet: `https://studio.genlayer.com/api`
- `.env` file exists in root (not committed)

## Run Tests
```
cd "C:/Users/USER/Desktop/intelligent contracts/service-uptime-oracle"
python -m pytest tests/direct/ -q
```
Expected: `38 passed`
