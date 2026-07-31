# Response to Review — ServiceUptimeOracle

**Review date:** Jul 31, 2026
**Requested by:** Joaquin
**Resubmitted:** Jul 31, 2026

## Feedback

> The live probe and consensus flow are substantive, but the current source
> does not actually verify the configured HTTP status: the probe supplies
> content only and every stored HTTP code is zero. Unlimited permissionless
> probing also lets callers bias the uptime percentage by choosing when and
> how often to sample, which is unsafe for SLA payouts. Please enforce the
> observed response status and add an unbiased sampling or cadence rule,
> then provide matching source and deployment.

Two issues, both fixed below.

---

## 1. HTTP status was never verified

**Root cause:** `probe()` fetched the endpoint with
`gl.nondet.web.render(url, mode="text")`, which returns rendered text only —
it does not expose an HTTP status code. `ProbeRecord.http_code` was declared
in storage but nothing ever wrote a real value into it, so it was always `0`,
and `expected_status` (configured at registration) was never actually checked
against anything real. A response body that merely *read* healthy could
reach `UP` regardless of the status code the endpoint returned.

**Fix:** [`contracts/service_uptime_oracle.py`](contracts/service_uptime_oracle.py), `_do_probe`

- Switched the fetch to `gl.nondet.web.get(url)`, which returns the real
  status code and body.
- The observed code is checked against `expected_status`
  **deterministically, before the model ever runs**:
  - if `expected_status` is set, the code must match it exactly;
  - if `expected_status` is `0`, any `2xx` code is accepted.
- A mismatch is recorded as `DOWN` unconditionally and the LLM step is
  skipped entirely — the semantic assessment can only ever pull a
  status-matching response down to `DEGRADED`, never promote a
  status-mismatched one to `UP`.
- The real status code is now stored in `ProbeRecord.http_code` and returned
  by `get_recent_probes()`.

**Verified live** against the redeployed contract: registered
`https://httpbin.org/status/200` (expected status `200`) and probed it.
`get_recent_probes()` returned `http_code: 200` — previously this field
could not be anything but `0`.

**Verified in tests:** `tests/direct/test_service_uptime_oracle.py`
- `test_recorded_probe_has_observed_http_code`
- `test_status_mismatch_is_down_without_llm_call`
- `test_status_matches_2xx_when_expected_status_is_zero`
- `test_status_outside_2xx_is_down_when_expected_status_is_zero`

---

## 2. Unlimited permissionless probing let callers bias uptime

**Root cause:** `probe()` had no rate limit. Because it's permissionless (by
design — anyone must be able to trigger a probe so a provider can't suppress
bad readings), a caller with a stake in the outcome could sample only during
healthy windows, or spam probes during an outage, and skew
`probes_up / total_probes` toward whichever answer benefited them. For an SLA
vault reading `get_uptime_bps()` to decide a payout, that's directly
exploitable.

**Fix:** [`contracts/service_uptime_oracle.py`](contracts/service_uptime_oracle.py), `probe()`

- Added `MIN_PROBE_INTERVAL_SECONDS = 300` (5 minutes).
- Enforced on-chain, deterministically, per service, regardless of caller:
  a probe attempted before the interval has elapsed since the service's
  last probe reverts with `EXPECTED: probe cadence limit`.
- This doesn't force uniform sampling — a caller can still choose *when*
  within the floor to probe — but it removes the ability to bias the record
  by simply calling more often, and caps the worst case for how much a
  single caller (or coordinated set) can distort the sample rate.

**Verified live** against the redeployed contract: a second `probe()` call
issued immediately after the first was rejected — all 5 validators agreed on
the `UserError` — and `get_probe_count()` stayed at `1`.

**Verified in tests:** `tests/direct/test_service_uptime_oracle.py`
- `test_probe_before_min_interval_reverts`
- `test_probe_after_min_interval_succeeds`
- `test_cadence_limit_is_per_service`

---

## What didn't change

Registration, ownership, lifecycle (deregister/reactivate), ring-buffer
mechanics, uptime arithmetic, and the equivalence principle text were already
correct and are untouched. The non-deterministic budget is still at most two
operations per probe (`web.get` always runs; `exec_prompt` only runs once the
status check has already passed).

---

## Evidence

| | |
|---|---|
| Source | [github.com/ometere123/service-uptime-oracle](https://github.com/ometere123/service-uptime-oracle) |
| Commit | `fix: enforce observed HTTP status and rate-limit probe cadence` |
| Redeployed contract (StudioNet) | `0xc44cEAbE1F5699210308A2664E5fD58E15F6032c` |
| Explorer | [explorer-studio.genlayer.com/address/0xc44cEAbE1F5699210308A2664E5fD58E15F6032c](https://explorer-studio.genlayer.com/address/0xc44cEAbE1F5699210308A2664E5fD58E15F6032c) |
| Prior address (predates this fix) | `0x6b7d9775B69b5e004da97480D0683EcfC1249722` |
| Direct tests | 45/45 passing (38 original + 7 new for these fixes) |
| Integration tests | 3/3 passing against live StudioNet consensus |
