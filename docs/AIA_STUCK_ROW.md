# AIA Bill Uploads stuck-row investigation (Task 1)

Date: 2026-08-12  
Tenant: live (session-scoped). Synthetic ZZ test file only.  
**Approve never called.**

## Orphan under study

| Field | Value |
|---|---|
| fileName | `ZZ_PIPELINE_TEST_DO_NOT_APPROVE_20260812T072211Z.pdf` |
| fileUuid | (run 1) |
| fileCategory | `voucher` |
| status | `file_uploaded` (stuck since 07:29Z) |
| Needs Review | **not present** (tally or review-bills) |

## Cause

Not only missing `triggerEvent`.

1. **Missing `triggerEvent: true` on first PATCH** can leave a fresh registration at `file_uploaded` indefinitely (run 1: ~12 min until a later PATCH with `triggerEvent` unblocked the **`bill`** row).
2. **Duplicate `bill-status` registration** for the same `fileUuid` (run 1’s `voucher` retry after the `bill` row) can leave a **second** row that never advances.
3. **Recovery is not guaranteed.** One allowed recovery attempt on this orphan (PATCH `triggerEvent: true`, poll 3 min at 5s) → still `file_uploaded`, never entered Needs Review. Likely because extraction already consumed that DMS object via the sibling `bill` row.

So: always send `triggerEvent: true` on step 3, never double-register the same file, and treat late `triggerEvent` as best-effort recovery only.

## Delete path

**None found.**

| Probe | Result |
|---|---|
| `DELETE …/bill-status` | **405** (Allow: POST, GET) |
| `DELETE …/bill-status/{id}` | **404** |
| `OPTIONS upload-file-to-dms` | Allow: **POST, PATCH** only |
| Bundle search | No Bill Uploads delete/retry/reprocess API; only Needs Review DELETE + All Bills delete |
| DMS/document admin guesses | 404 |

Cleanup after HITL is only via Needs Review DELETE. Rows that never leave `file_uploaded` have **no customer-facing or API remove path**.

## Recovery procedure (for volume)

1. `push_bill` always includes `triggerEvent: true` (already in client).
2. Poll `bill-status` every 5s until `file_hitl_*` (budget ~90–120s).
3. If still `file_uploaded`: **one** PATCH `{fileUuid, status: uploaded, thirdPartyProduct, triggerEvent: true}`, poll again.
4. If still stuck: **record as unremovable orphan** — do not create more experiments on the live tenant. Stop the volume run if orphans accumulate beyond an agreed budget (suggest: abort if ≥2 unrecovered sticks).

## Customer visibility

Stuck `file_uploaded` maps UI label **“Extracting”** on **Purchases → Bill Uploads** only. It does **not** appear in Needs Review. Customers see a permanent Extracting row until AIA ops intervene (no self-serve delete in the product UI/API we can find).

## Outcome of this investigation

- Orphan **still present** after failed recovery.
- Volume push (Task 4) must use the recovery procedure above and keep the tenant’s Bill Uploads clean by not leaving new sticks.
