# AIA Accounts Payable API — Discovery Map

**Scope:** reverse-engineering of `https://app.aiaccountant.com/accounts-payable` so the PDF-split POC can hand correctly bounded invoices to AIA’s existing extraction (or, if viable, skip extraction and post vouchers). Includes a **2026-08-12** watched Mode A live upload (synthetic one-page invoice, Needs Review only — **Approve / `POST …/tally/bill` never called**).

**Method:** authenticated session cookie against live JSON APIs + Next.js bundle analysis. **2026-08-12:** Mode A upload + DELETE review-bill exercised on a synthetic invoice only. **Approve / `POST …/tally/bill` / all-bills / Tally sync were never called.**

**Credentials:** session export lives only under gitignored `.secrets/` (see `.gitignore`: `.secrets/`). This document refers to cookies by **name** only. No cookie values, CSRF secrets, tokens, company names, vendor names, amounts, GSTINs, invoice numbers, or raw UUIDs appear below.

**Prior art:** [`docs/REVIEW_VOUCHER_SCHEMA.md`](REVIEW_VOUCHER_SCHEMA.md) §3e flagged Approve as never captured — this pass closes that from the bundle (still not from a live POST).

---

## 1. Auth model

### What carries the session

| Cookie | Domain | Flags | Role |
|---|---|---|---|
| `__Secure-next-auth.session-token` | `app.aiaccountant.com` | httpOnly, Secure, SameSite=Lax, persistent expiry | **Session.** Encrypted JWE (`alg=dir`, `enc=A256GCM`). Required for `/api/*`. |
| `__Host-next-auth.csrf-token` | `app.aiaccountant.com` | httpOnly, Secure, SameSite=Lax, session | NextAuth CSRF pair. Used for NextAuth flows (`/api/auth/*`), **not** observed on AP `fetch` calls. |
| `__Secure-next-auth.callback-url` | `app.aiaccountant.com` | httpOnly, Secure, SameSite=Lax, session | Post-login redirect hint. |
| `sidebar_state` | `app.aiaccountant.com` | not httpOnly | UI chrome only. |
| `_ga*`, `_clck`, `_clsk`, `_gcl_au`, `fc_*`, `_fuid` | `.aiaccountant.com` | analytics / third-party | Not required for AP APIs. |

### Headers

- AP calls are **cookie-based**. Bundle `fetch` wrappers set `Content-Type: application/json` and rely on the browser cookie jar.
- **No `Authorization` header** appears in the AP client bundles.
- CSRF: `/api/auth/csrf` returns `{ csrfToken }`. NextAuth sign-in posts include it. Ordinary AP JSON `fetch` calls do **not** attach an `X-CSRF-Token` (or similar) in the inspected code.
- **Live writes (Mode A steps 1–4 + DELETE review-bills):** succeeded with **Cookie + `Content-Type: application/json` only** — **no CSRF header, no `Origin`, no `Referer`.** Company switch requires NextAuth session update: `POST /api/users/switch-company` then `POST /api/auth/session` with `{ csrfToken, data: { companyUuid, companyName, ucUuid, toolConnected, … } }` and persisting the new `__Secure-next-auth.session-token` (`Origin`/`Referer` used successfully for that auth update).
- **Company list:** `GET /api/companies/user?userUuid=…` → `{ data: [{ companyName, companyUuid, ucUuid, companyId, … }] }`.
- **Pagination (bill-status):** query accepts `page` + `pageSize` **or** `limit`; response is DRF-style `{ count, next, previous, results }` (**no** `page`/`pageSize` in the body). **Needs Review lists** use `{ files, page, pageSize, totalPages, total, totalNeedsReview }`.

### Company scope

- Session payload (`GET /api/auth/session`) includes `user.companyUuid`, `user.ucUuid`, `user.toolConnected` (`tally` | `zoho`), plus profile fields.
- Most AP endpoints take **`companyId={companyUuid}` as a query param** (occasionally `companyUuid` for error-detail downloads).
- Path segments use resource ids (`{billId}` / prediction uuid / `vchUuid`), not company id.
- `POST /api/users/switch-company` + NextAuth `POST /api/auth/session` `{ csrfToken, data }` updates the JWT company (live-verified). Session cookie must be re-persisted after update.

### Server-side replay outside a browser

| Factor | Assessment |
|---|---|
| Cookie replay | Technically works today: `curl` with `__Secure-next-auth.session-token` reaches AP JSON. |
| Longevity | Token is persistent but rotatable; NextAuth session can invalidate. Not a service credential. |
| CSRF | Not blocking read APIs observed; write paths may still enforce server-side origin/session checks. |
| Production fitness | **Do not** build the integration on a stolen browser cookie. Need a first-party service account, machine token, or internal network trust from the AP team. |
| SameSite=Lax | Fine for top-level and same-site XHR from `app.aiaccountant.com`; cross-site third-party embeds will not send the cookie. |

---

## 2. Endpoint table

Shapes are structural (keys / enums). Values redacted.

### Auth / session

| Method | Path | Purpose | Request | Response (shape) |
|---|---|---|---|---|
| GET | `/api/auth/session` | Current user + company | Cookie | `{ user: { uuid, companyUuid, ucUuid, toolConnected, permissions, … }, expires }` |
| GET | `/api/auth/csrf` | NextAuth CSRF | Cookie | `{ csrfToken }` |
| GET | `/api/app-config` | Feature flags | Cookie | flat flag map (`excel_bulk_bill`, `bulk-review-pdf`, …) |

### Upload / DMS / extraction trigger

**Live-verified (2026-08-12 Mode A run).** Synthetic values only in fixtures under `vendors/aia/fixtures/`.

| Method | Path | Purpose | Request (live) | Response (live) |
|---|---|---|---|---|
| POST | `/api/upload-file-to-dms` | Start upload; obtain storage target | **Required:** `ucUuid`, `companyUuid`, `fileSize`, `fileName`, `fileCategory`, `fileExtension`, `additionalMetadata.ContentType`, plus frontend placeholders `accountNumber: "87651676591"`, `bankName: "-1"`, `description: ""`. Optional password fields. | **HTTP 201** `{ message, data: { fileUuid, presignedUrl, dataKey }, error }` |
| POST | `/api/upload-file` | Push bytes to storage (app-mediated) | `presignedUrl`, `fileType`, `fileName`, `fileContent` (base64) | **HTTP 200** `{ message: "File uploaded successfully" }` |
| PATCH | `/api/upload-file-to-dms` | Mark DMS object uploaded / kick pipeline | `fileUuid`, `status: "uploaded"`, `thirdPartyProduct`, **`triggerEvent: true`** (see note) | **HTTP 200** `{ message, data: { fileUuid, ucUuid, companyUuid, fileUrl, noPasswordFileUrl, … } }` |
| POST | `/api/accounts-payable/bill-status` | **Register file for AP extraction** | `fileUuid`, `fileCategory`, `fileName`, `thirdPartyProduct`, `companyUuid`, `fileSize`, `fileExtension` | **HTTP 201** full `BillFileStatus` row (`status: "file_uploaded"`, …) |
| GET | `/api/accounts-payable/bill-status` | Bill Uploads list / poll | Query: `companyId`, `page`, **`pageSize`** (also accepts `limit`; pagination body uses neither — see below) | `{ count, next, previous, results: [ BillFileStatus ] }` |
| GET | `/api/accounts-payable/file-status-error-details` | Machine-readable failure details | Query: `companyUuid`, `fileUuid` | error detail payload |
| GET | `/api/accounts-payable/file-status-error-details/download-…` | Download error report | Query: `companyUuid`, `fileUuid` | file blob |

**Upload sequence:**  
`(1) POST upload-file-to-dms` → `(2) POST upload-file` → `(3) PATCH status=uploaded` (+ `triggerEvent`) → `(4) POST bill-status`.

**`fileCategory` (critical — two namespaces):**
- **DMS** `POST /api/upload-file-to-dms` accepts only an enum including `"bill"` (not `"voucher"`). Live **HTTP 422** if `voucher` is sent: `Input should be 'bank_statement', …, 'bill', …`.
- **AP** `POST …/bill-status` uses `"voucher"` when `toolConnected=tally`, else `"bill"`. That routes Needs Review to **`tally/review-vouchers`** vs **`review-bills`**.
- Frontend: DMS default `"bill"`; bill-status `N = tally ? "voucher" : "bill"`. Client mirrors that split.

**`triggerEvent`:** without it, a live row stayed `file_uploaded` for >10 minutes (no `lastUpdateDate` change). A subsequent PATCH with `triggerEvent: true` advanced the same file to `file_hitl_success` within ~15s. Prefer sending `triggerEvent: true` on step 3.

**Minimal body fails:** POST DMS without `ucUuid`/`companyUuid`/`fileSize`/`accountNumber`/`bankName` → **HTTP 400** field errors (observed).

### Needs Review / voucher read

| Method | Path | Purpose | Request | Response (shape) |
|---|---|---|---|---|
| GET | `/api/accounts-payable/tally/review-vouchers` | Needs Review queue (Tally) | Query: `companyId`, `page`, `pageSize` | `{ files, page, pageSize, totalPages, total, totalNeedsReview }` |
| GET | `/api/accounts-payable/tally/review-vouchers/{billId}` | Single review voucher | Query: `companyId` | `{ prediction, navigation }` — see envelope below |
| GET | `/api/accounts-payable/review-bills` | Needs Review (non-Tally / Zoho path) | Query: `companyId`, `page`, `pageSize` | analogous list |
| GET | `/api/accounts-payable/review-bills/{billId}` | Single review bill (Zoho-shaped) | Query: `companyId` | prediction envelope |
| DELETE | `/api/accounts-payable/tally/review-vouchers/{billId}` | Delete from Tally Needs Review | Query: `companyId` | `{ status, replacementId?, totalRemaining? }` |
| DELETE | `/api/accounts-payable/review-bills/{billId}` | Delete from non-Tally / `fileCategory=bill` Needs Review (**live-called**) | Query: `companyId` | **HTTP 200** `{ status: "deleted", replacementId, totalRemaining }` |
| DELETE | `/api/accounts-payable/review-bills/bulk-delete` | Bulk delete | body: `billFilePredictionUuids[]` | ok / error |

**List item shape (live):** Needs Review list entries are `{ uuid, fileName, createdBy, lastUpdatedBy, creationDate, lastUpdateDate }` — the path id is `uuid` (= `billFilePredictionUuid` on the detail envelope).

### Confirmed read envelope (Tally review voucher)

```
{
  prediction: {
    billFilePredictionUuid,   // {billId} in routes
    fileName,
    status,                   // e.g. needs_review
    metadata: { fileUuid, status /* file_hitl_* */, … },
    payload: {
      data,                   // ERP voucher fields (vendorName, supInvNo, lines, …)
      error, errorMessage,
      fileUuid,
      fileMetadata: { pages, fileExtension, fileCategory, … },
      rawExtractionData,
      thirdPartyProduct,      // "tally"
      overallConfidenceScore,
      enrichmentConfidenceScore,
      extractionConfidenceScore
    },
    vendorReviewSummary?,
    …
  },
  navigation: { currentPosition, total, prevId, nextId, … }
}
```

`prediction.payload.data` still matches the earlier schema notes: header fields + `lines: { items[], taxes[], ledgers[] }` with master UUIDs.

### Write / approve / create (from bundle — not live-POSTed)

| Method | Path | Purpose | Request | Notes |
|---|---|---|---|---|
| POST | `/api/accounts-payable/tally/bill` | **Create bill / Approve from review** | Full voucher JSON (see §5) | `mode==="review"` + `predictionUuid` ⇒ approve path (`reviewMode: true`) |
| PUT | `/api/accounts-payable/tally/bill?vchUuid=…` | Update existing Tally bill | Same body family | Used for edit / update-bill |
| POST/PUT | `/api/accounts-payable/zoho/bill` | Zoho analogue | Zoho-shaped body builder in same chunk | When `toolConnected !== tally` |
| DELETE | `/api/accounts-payable/tally/bill?companyId&vchUuid` | Delete posted bill | — | **not called** |
| GET | `/api/accounts-payable/tally/is-sup-inv-no-exists` | Duplicate supplier invoice check | query | boolean `isPresent` |
| GET | `/api/accounts-payable/tally/is-bill-ref-no-exists` | Duplicate bill ref | query | boolean `isPresent` |
| GET | `/api/accounts-payable/tally/all-bills` | All Bills table | `companyId` + required filters (e.g. `vchType`) | list |

### Realtime

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/realtime-notifications/ably/token` | Ably auth; client subscribes to company channel |

Event type observed in bundle: `ap_bill_file_status_changed` (plus `third_party_sync_completed`). UI **also** polls Bill Uploads / Needs Review every **10s** (`refetchInterval: 1e4`).

---

## 3. Lifecycle diagram

```
                     ┌─────────────────────────────────────────┐
  PDF/image/xlsx     │  POST /upload-file-to-dms                │
  (UI: Upload Bills) │  POST /upload-file  (base64 → storage)   │
                     │  PATCH /upload-file-to-dms status=uploaded│
                     └──────────────────┬──────────────────────┘
                                        │
                                        ▼
                     POST /api/accounts-payable/bill-status
                     file status ≈ file_uploaded  →  UI "Extracting"
                                        │
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
         file_data_extracted   file_hitl_success     file_hitl_rejected
         file_data_enriched    (UI: "Completed")     (UI: "Failed")
         (still Extracting)            │             statusMessage +
                                       │             error-details API
                                       ▼
                          Needs Review queue
                          prediction.status = needs_review
                          metadata.status = file_hitl_success
                                       │
                                       ▼
                     GET .../tally/review-vouchers/{billId}
                     human edits form (HITL)
                                       │
                    Approve / Approve & Next  (form submit)
                                       │
                                       ▼
                     POST /api/accounts-payable/tally/bill
                     body includes reviewMode + predictionUuid
                     → prediction moves off needs_review
                     → response may include replacementId (next bill)
                                       │
                                       ▼
                     All Bills / sync queue
                     thirdPartySyncStatus → Tally (separate sync UX)
```

### Status vocabulary

**File / Bill Uploads (API → UI title)**

| API `status` | UI label |
|---|---|
| `file_uploaded` | Extracting |
| `file_data_extracted` | Extracting |
| `file_data_enriched` | Extracting |
| `file_hitl_success` | **Completed** |
| `file_hitl_rejected` | **Failed** |
| `file_data_partially_extracted` | Partially Extracted |

**Live Mode A sequence (synthetic page, 5s poll, via `AiaApClient.push_bill`):**  
`file_uploaded` → `file_hitl_success` in **~21s** (intermediates `file_data_extracted` / `file_data_enriched` still not observed at 5s — either skipped or shorter than the poll interval). Prediction `needs_review` on **`tally/review-vouchers`** when bill-status `fileCategory=voucher`.

**Prediction / Needs Review**

| Value | Role |
|---|---|
| `needs_review` | In HITL queue (confirmed live) |
| `reviewed` | Enum in shared constants |
| `under_review` | Enum in shared constants |
| `completed` | Enum in shared constants |

**Failures:** `statusMessage` on `bill-status` rows is human-readable and often machine-usable (string). Richer detail via `file-status-error-details` when `isErrorDetails` is set. Rejected spreadsheet uploads in this tenant carried messages like missing mandatory columns / vendor not found (no customer identifiers reproduced here).

---

## 4. The 12-page verdict

**Verdict: server-side enforcement. Not a client-only wall.**

Evidence:

1. **No client constant or string** matching `limit is 12`, `PDF has … pages`, `pageLimit`, or `maxPages=12` exists in the AP / app bundles downloaded for this build. The only `maxPages` hit is React Query pagination, unrelated.
2. The known production message  
   `Extraction failed: PDF has {n} pages; limit is 12.`  
   was previously observed as a **Bill Uploads / `statusMessage`** on a `file_hitl_rejected` row ([`REVIEW_VOUCHER_SCHEMA.md`](REVIEW_VOUCHER_SCHEMA.md) §3b) — i.e. it arrived **after** upload+registration, from the pipeline.
3. Live `bill-status` in this tenant shows successful `file_hitl_success` rows with `totalPages` up to **12**, and `prediction.payload.fileMetadata.pages: 12` on a needs-review item — consistent with a hard cap of 12 inclusive, applied by extraction.
4. The upload client does **not** gate on page count before steps 1–4; it always registers via `POST bill-status`.

**Implication for batching:** assume the limit is real on the extraction path. Softening it requires a server change (or bypassing extraction — §5). Client-side removal would not help.

*(This tenant’s recent rejects were spreadsheet validation errors, not page-limit PDFs; the page-limit message was not re-fetched live in this pass, but the absence of any client check is conclusive for “not client-only.”)*

---

## 5. Write path — what we know / don’t

### Approve is not a separate micro-endpoint

**Approve / Approve & Next** is a `<form>` submit (`type="submit"`). The handler builds a Tally voucher body and:

```
POST /api/accounts-payable/tally/bill
Content-Type: application/json
```

When the form is in review mode with a prediction id:

```json
{
  "reviewMode": true,
  "predictionUuid": "{billFilePredictionUuid}",
  "companyId": "{companyId}",
  "ucUuid": "{ucUuid}",
  "billDate": "...",
  "voucherDate": "...",
  "effDate": "...",
  "vchAmtLc": "...",
  "vchAmt": "...",
  "taxableAmount": "...",
  "totalTaxAmount": "...",
  "totalLedgerAmount": "...",
  "rcmAmount": "...",
  "billFileUuid": "{fileUuid}",
  "billFileName": "{fileName}",
  "exchangeRate": 1,
  "ledgerType": "merchant",
  "lines": { "items": [/*…*/], "ledgers": [/*…*/], "taxes": [/*…*/] },
  "apBills": [/*…*/],
  "...spread of form fields from prediction.payload.data (vendor, GST, addresses, vchType, …)"
}
```

Success toast: “Bill saved” / “Ready to sync to Tally.” Response may include `replacementId` for Approve & Next navigation.

Zoho tenants use `POST|PUT /api/accounts-payable/zoho/bill` with a parallel builder (`reviewMode` / `predictionUuid` when approving from HITL).

### Can we post a voucher without uploading a PDF?

**Yes — in the client model.**

- UI route: `/accounts-payable/create-bill` (“Create Bill”).
- Same endpoint family: `POST /api/accounts-payable/tally/bill`.
- `reviewMode` / `predictionUuid` are **only** attached when `mode === "review"` **and** a prediction id exists.
- `billFileUuid` / `billFileName` are optional (included when an attachment was uploaded on the create form).

So the 12-page extractor limit **does not apply** if we create vouchers directly and never call `POST bill-status` / never send a PDF through HITL extraction. That path still needs valid master UUIDs (vendor, ledgers, taxes, GST treatments) and whatever server validation Approve uses — unknown without a write test.

### Still unknown (blocked by read-only)

- Exact live Approve response body and error schema (only inferred).
- Whether server accepts `POST tally/bill` with empty `billFileUuid` in all tenants / feature flags.
- Whether a service credential exists distinct from NextAuth cookies.
- Full All Bills filter vocabulary (`vchType` values, sync states).
- Whether page-limit is configurable per tenant.

---

## 6. Integration proposal (adapter seam only — no code this pass)

Mirror `SplitterProvider`: one narrow port, swappable implementation, rest of the POC stays ignorant of AIA wire format.

```
pdfsplit (bounds) ──► packed ≤12-page PDFs
                         │
                         ▼
              AiaApClient  (interface)
                 │
     ┌───────────┼────────────┐
     ▼           ▼            ▼
 CookieSession  FutureSvc   Fake/Mock
 (dev only)     Token        (tests)
                 │
                 ▼
        AIA undocumented HTTP
```

Suggested interface responsibilities (single module, e.g. `vendors/aia_ap/` or `src/pdfsplit/integrations/aia_ap.py`):

1. **`upload_and_enqueue(pdf_bytes, *, company_id, file_name) -> {fileUuid}`**  
   Implements DMS steps 1–4. Used when we want AIA extraction + Needs Review.
2. **`wait_for_terminal_status(fileUuid) -> file_hitl_success | file_hitl_rejected`**  
   Prefer Ably if we take a dependency; otherwise poll `GET bill-status` ≤10s.
3. **`get_review_voucher(billId) -> prediction envelope`**  
   Read path for demos / diffing our instrument extraction against AIA.
4. **`create_bill(voucher_dto) -> …`** (optional later)  
   Direct `POST tally/bill` **without** `reviewMode` — only after write-path sandbox testing proves it.

POC wiring:

- Split + `packing.pack_documents(..., max_pages=12)` remains the product boundary.
- Adapter is the **only** file that knows paths, cookie/header auth, and status enums.
- Do **not** scatter `/api/accounts-payable/...` strings through the web app.

Two product modes to decide with AP team:

| Mode | Uses 12-page limit? | HITL in AIA UI? |
|---|---|---|
| A. Split → upload each PDF → AIA extract → Needs Review | Yes | Yes |
| B. Split → (optional) our instrument extract → `POST tally/bill` | No | No (or different UX) |

Default recommendation until write tests exist: **Mode A** behind the adapter; design the interface so Mode B is a second method, not a rewrite.

---

## 7. Blockers / next asks

| Gap | Why blocked | What we need |
|---|---|---|
| Confirm sandbox vs production company | Session shows one company; no safe company directory on GET | AP team: list of companies on this user + explicit **test tenant** |
| Live Approve POST capture | Read-only rule | Test tenant + permission to Approve one synthetic bill |
| Direct voucher create without PDF | Bundle says yes; server may disagree | Same write window: `POST tally/bill` with no `billFileUuid` |
| Service credential | Cookie replay is fragile / non-compliant for automation | Machine user, API key, or internal mTLS from AIA platform |
| Re-observe page-limit message live | No >12-page reject in current `bill-status` pages | Either upload a 13-page PDF in **sandbox only**, or AP team confirms server constant |
| Ably payload schema | Not subscribed this pass | Optional: connect with token on test tenant |
| Zoho parity | This session is `toolConnected: tally` | Separate discovery if Zoho tenants matter |

---

## Compliance confirmations

- **No writes:** no Approve, Approve & Next, Delete, Save, Add, or Upload was invoked. No form was submitted. Only `GET`/`OPTIONS` HTTP and static JS downloads.
- **No uploads:** no file was posted to DMS or `bill-status`.
- **Credentials:** raw session JSON is only under `.secrets/` (gitignored). This doc and the tracked repo contain **no** cookie values, CSRF secrets, or bearer tokens.
- **No customer data in this doc:** company / vendor / invoice / amount / GSTIN / UUID values omitted or replaced with `{companyId}`, `{billId}`, `{fileUuid}`, `{uuid}`, `{redacted}`.

---

*Discovery date: 2026-08-12. Frontend build id observed in static paths: `dczXA09dfjLg8D6pvM2p2`. Internal AP can change without notice — treat this as a map, not a contract.*
