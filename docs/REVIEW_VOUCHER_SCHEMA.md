# Production review-voucher schema (research)

Read-only browser exploration of AI Accountant Accounts Payable review UI, mapped to the aia-pdf-split V2 extraction contract.

| | |
|---|---|
| **Date** | 2026-08-11 |
| **Company session** | `{redacted company}` (`companyId` `{uuid}`) |
| **Voucher URL** | `https://app.aiaccountant.com/accounts-payable/review-voucher/{billId}?returnTo=…` |
| **Bill / file** | `{redacted}.pdf` — queue position **Bills 2 of 3** |
| **ERP target** | Tally (`thirdPartyProduct: "tally"`) |
| **Auth** | Existing browser session (no credentials entered) |
| **Mutations** | None — Approve / Delete / Save not clicked; Additional Fields dialog opened then **Cancelled** |

**Visuals (schematics):** browser PNG host paths were not writable into this workspace; annotated SVG captures of what was on screen:

- [`docs/review-voucher/01-voucher-overview.svg`](review-voucher/01-voucher-overview.svg) — header form, vendor warning, PDF preview pane, Approve & Next
- [`docs/review-voucher/02-line-items.svg`](review-voucher/02-line-items.svg) — Item Mode line table + ledger/tax footers
- [`docs/review-voucher/03-needs-review-list.svg`](review-voucher/03-needs-review-list.svg) — Purchases → Needs Review / Bill Uploads list

**Primary API (read):**  
`GET /api/accounts-payable/tally/review-vouchers/{billId}?companyId=…`  
Payload shape used below is under `prediction.payload.data` (plus sibling confidence / file status fields).

---

## 1. Field inventory (voucher header / party / coding)

Observed on **Item Mode** UI + prediction payload. `Accounting Mode` click was intercepted by sticky chrome — treat Accounting Mode–only fields as **unknown**.

| Name (UI) | API / payload key(s) | Type | Required? | Editable? | Validation / warning |
|---|---|---|---|---|---|
| GST Registration | company GST / registration UUID (UI; exact payload key not fully traced) | master select | Yes (*) | Yes | Must select branch registration |
| Voucher Type | `vchType` | enum / master | Yes (*) | Yes | Observed: Purchase |
| Voucher No | `vchNo` / `vchNoMethod` | string | No (auto) | **Readonly** | “Auto Generated” |
| Voucher Date | UI-only or separate from billDate (not clearly a payload key on this bill) | date | Yes (*) | Yes | Reviewer date (observed Aug 11, 2026) |
| Bill Date | `billDate` | date | Yes (*) | Yes | From extraction |
| Supplier Invoice No. | `supInvNo` | string | Yes (*) | Yes | Empty on this voucher |
| Vendor Name | `vendorName` (+ vendor UUID when matched) | string + master | Yes (*) | Yes | **Master miss:** extracted vendor name + “Vendor does not exist in masters” + Add — closest prod analogue to POC `UNGROUNDED` / unresolved coding |
| Cost Centre Class | cost-centre class UUID / flags | master / toggle | No | Yes (often gated) | Disabled until prerequisites |
| Cost Centre | cost centre UUID / `isCostCentreOn` | master / toggle | No | Yes (gated) | Often disabled |
| Purchase Account | `purchaseAccountName` / `purchaseAccountUuid` | GL / ledger master | Unknown* | Yes | Coding — master match or manual |
| Narration / Description | `description` | string | No | Yes | Free text |
| Vendor GSTIN | `gstin` (+ `additionalData`) | tax id | No | Partial | Extracted; also under Additional Details |
| Source of Supply | `sourceOfSupply` | state / place | No | Yes (Additional) | GST place-of-supply |
| Destination of Supply | `destinationOfSupply` | state / place | No | Yes (Additional) | GST place-of-supply |
| Billing Address | `billingAddress` | address | No | Yes (Additional) | “From Your Bill” |
| Shipping Address | `shippingAddress` | address | No | Yes (Additional) | “From Your Bill” |
| Reverse Charge | `isReverseChargeApplied` | bool | No | Yes (Taxes) | Affects tax ledgers |
| Amount (voucher) | `vchAmt` / `vchAmtLc` | money | Implied | Display + derived | LC = local currency twin |
| Mode / Terms of Payment | `additionalData.modeTermsOfPayment` (and related) | string | No | Additional Details | Payment terms analogue |
| Import / Receipt / Order extras | `additionalData.*` | various | No | Modal tabs | Opened then Cancelled — full field list not re-audited after cancel |
| Currency | not prominent as separate control on this Tally voucher | — | Unknown | Unknown | Amounts treated as INR context in UI; **unknown** as first-class editable |

\*Requiredness for Purchase Account / ledgers depends on Tally company config — not hard-starred on every control in Item Mode for this bill.

---

## 2. Line-item / ledger / tax column tables

### 2a. Items table (Item Mode)

| Column (UI) | Payload (`lines.items[]`) | Editable? | Notes |
|---|---|---|---|
| Description | `itemDesc` | Yes | OCR text |
| Item | `itemNameDisplay` / `itemUuidId` | Yes | **Master** match or empty |
| Godown / Location | `godownUuid` | Yes | **Coding** — often empty |
| Quantity | `quantity` | Yes | |
| Unit Rate | `unitPrice` | Yes | |
| Discount | `discount*` fields | Yes | |
| Amount | `lineAmount` / `lineAmountLc` | Yes | |
| *(not always a visible column)* HSN/SAC | `hsnSac` | Yes / display | Present on PDF & payload; UI column visibility config-dependent |
| UOM | `uom` | Yes | |
| Item type / SKU | `itemType`, `skuCode` | Partial | |
| Account | `accountUuid` | Coding | May surface more in Accounting Mode (**unknown** here) |

### 2b. Ledgers table

| Column (UI) | Payload (`lines.ledgers[]`) | Editable? | Notes |
|---|---|---|---|
| Description | `itemDesc` | Yes | |
| Ledger Name | `accountName` / `accountUuid` | Yes | **GL coding** — master |
| Cost Centre | `isCostCentreOn` + CC UUID | Yes / gated | |
| Amount | `lineAmount` | Yes | |

### 2c. Taxes table

| Column (UI) | Payload (`lines.taxes[]`) | Editable? | Notes |
|---|---|---|---|
| Reverse Charges | ties to `isReverseChargeApplied` | Yes | |
| Ledger Name | tax ledger UUID/name | Yes | Empty `taxes[]` on this voucher |
| Cost Centre | CC | Gated | |
| Amount | amount | Yes | |

### 2d. Footer totals (UI)

Sub Total · GST · TDS · Other Taxes · Grand Total — derived / editable depending on line fill; TDS / Other Taxes not first-class on V2.

---

## 3. Workflow states and transitions

### 3a. Purchases tabs

| Tab | Meaning (observed) |
|---|---|
| **All Bills** | Approved / synced voucher list for ERP |
| **Needs Review** | Files / bills awaiting human HITL (`predictionStatus` / review queue). Count observed: **3** |
| **Bill Uploads** | Upload pipeline status per file |

### 3b. File / prediction statuses observed

| Status | Where | Meaning |
|---|---|---|
| `needs_review` | review voucher prediction | Human must review before approve |
| `file_hitl_success` | file metadata | Extraction pipeline succeeded enough to open review |
| `file_hitl_rejected` | Bill Uploads / bill-status | Hard fail — e.g. **31-page PDF:** `"Extraction failed: PDF has 31 pages; limit is 12."` |
| Completed / Failed | Bill Uploads UI | april.pdf & invoice.pdf Completed; large PDF Failed |

### 3c. Review actions (UI — not executed)

| Action | Effect (inferred from labels / API; not clicked) |
|---|---|
| **Approve & Next** | Accept voucher → queue advances; sync toward Tally AP voucher list |
| **Delete Bill** | Remove bill from queue |
| **Field Configuration** | UI layout / which columns show |
| **Additional Details** | Modal for GST/address/import/order (Cancelled — no write) |
| Back to Needs Review | `returnTo` query |

### 3d. Multi-document / upload model

- **One uploaded file → one review voucher** in the Needs Review queue (`Bills N of M` walks files, not logical docs inside a PDF).
- Needs Review list shows **file names** (`april.pdf`, `invoice.pdf`, …), not per-page document splits.
- Production AP **hard-fails** uploads over **12 pages** (confirmed via bill-status message). Multi-logical-document packets inside one PDF are **out of scope** for current prod AP review unless pre-split upstream.
- POC multi-doc split (`page_start`/`page_end` per `ExtractedDocumentV2`) is therefore **EXTRA** relative to current prod AP voucher unit.

### 3e. Approve payload shape

Approve body itself was **not** captured (read-only). Closest visible ERP shape is the review prediction:

`prediction.payload.data` with `vendorName`, `supInvNo`, `billDate`, `dueDate`, `vchType`, amounts, GST/addresses, and `lines.{items,ledgers,taxes}[]` carrying master UUIDs. Approved vouchers appear under All Bills for Tally sync — exact POST wire format: **unknown**.

Confidence sibling fields observed: extraction / enrichment / overall at **100** on this bill (UI + API).

---

## 4. POC V2 contract (citations)

From [`src/pdfsplit/extraction.py`](../src/pdfsplit/extraction.py):

| Construct | Lines |
|---|---|
| `FieldValue` | 191–199 |
| `PartyInfo` | 202–205 |
| `TaxLineV2` / `TotalsV2` | 208–219 |
| `BankRails` | 222–228 |
| `LineItemV2` | 231–237 |
| `OtherField` / `ExtractedDocumentV2` | 240–265 |

UI surface [`web/src/components/PageDataPanel.tsx`](../web/src/components/PageDataPanel.tsx):

| UI area | Lines (approx) |
|---|---|
| `UNGROUNDED` badge | 24–25, 72–79 |
| Seller / Buyer / References / Totals / tax_lines | 143–291 |
| Line items: Description, Qty, Unit price, Amount only | 293–307 |
| `other_fields` editor | further below in same V2 panel |
| **Bank rails** | modeled in extraction.py 222–228; **not shown** in PageDataPanel V2 view |

---

## 5. Mapping: production ↔ ExtractedDocumentV2

### 5a. Production → V2

| Production field | V2 path | Notes |
|---|---|---|
| `vendorName` | `seller.name` | Name only; master UUID **MISSING** |
| Vendor / seller address | `seller.address` | |
| `gstin` (vendor) | `seller.tax_id` | GSTIN as tax_id |
| Buyer / bill-to name | `buyer.name` | Weak on this Tally purchase UX (branch GST Registration is company-side) |
| Billing address | `buyer.address` *or* `other_fields` | Prod also has shipping separately → shipping **MISSING** as first-class |
| Company / buyer GST | **MISSING** (or `buyer.tax_id` overloaded) | UI “GST Registration” is branch master |
| `supInvNo` | `document_id` | Rename mismatch |
| `billDate` | `issue_date` | Rename mismatch |
| Voucher Date | **MISSING** | Distinct from bill date in UI |
| `dueDate` | `due_date` | |
| `vchType` | `doc_type` (loose) | Prod is ERP voucher type; V2 is document class string |
| `vchNo` / auto | **MISSING** / N/A | Readonly ERP |
| `vchAmt` / Grand Total | `totals.total` | |
| Sub Total | `totals.subtotal` | |
| GST / tax lines | `totals.tax_lines[]` | Prod also has dedicated `lines.taxes[]` ledgers |
| TDS / Other Taxes | **MISSING** | |
| `description` (narration) | `other_fields[]` or **MISSING** | No dedicated narration field |
| `modeTermsOfPayment` | `payment_terms` | |
| PO / order refs (`additionalData`) | `po_number` / `other_fields` | |
| Currency | `currency` | Weakly surfaced in this Tally UI |
| `purchaseAccount*` | **MISSING** | GL coding |
| Cost centre / class | **MISSING** | Coding |
| Place of supply (source/dest) | **MISSING** | GST India |
| Reverse charge | **MISSING** | |
| `lines.items[].itemDesc` | `line_items[].description` | |
| qty / unitPrice / lineAmount | `quantity` / `unit_price` / `amount` | |
| `hsnSac`, `uom`, discount, `skuCode`, `itemType` | **MISSING** on `LineItemV2` | |
| Item master / godown / account UUID | **MISSING** | Coding |
| `lines.ledgers[]` | **MISSING** as separate collection | Only single `line_items` |
| `lines.taxes[]` | partial via `totals.tax_lines` | Different model (ledger vs rate lines) |
| Confidence scores | `confidence` | Aggregate float vs extraction/enrichment/overall |
| Master-miss vendor warning | `ungrounded` / UI badge | Related but not identical — prod is **master resolution**, V2 is **verbatim grounding** ([PageDataPanel.tsx](../web/src/components/PageDataPanel.tsx) L24–25) |
| Page range / multi-doc | `page_start` / `page_end` | **EXTRA** vs one-file-one-voucher |
| Bank rails | `bank.*` | **EXTRA** for this AP review page (unused in UI) |

### 5b. V2 → production (EXTRA / weak)

| V2 field | Production | Notes |
|---|---|---|
| `FieldValue.source_text` / `pages` | not in review form | Grounding evidence — POC-only |
| `ungrounded[]` | no identical flag | Closest: vendor master miss |
| `billing_period` | rare / Additional | Often empty |
| `bank` (`BankRails`) | not on review-voucher Item Mode | EXTRA |
| `doc_type` free string | `vchType` constrained | Semantic mismatch |
| `other_fields` open bag | `additionalData` + leftovers | Useful escape hatch |
| Multi-doc `page_start`/`page_end` | one voucher per upload file | EXTRA until packing productized |

---

## 6. Coding fields: extraction vs suggestion vs manual

| Coding field | How it gets filled (this session) |
|---|---|
| Vendor master | Extraction supplies **name string**; master UUID unresolved → warning + Add. Not pure OCR UUID. |
| Item master | Display name / UUID often empty — **suggestion or manual** |
| Godown | Empty — **manual / config** |
| Purchase / ledger account | Payload may carry names/UUIDs when enrichment matches; on this bill many empty — **suggestion + manual** |
| Cost centre | Toggle/class gated — **manual** after prerequisites |
| GST Registration (company branch) | **Manual / session default** select — not invoice OCR |
| HSN/SAC | **Extraction** from bill lines when present |
| Tax ledgers | Empty here; normally enrichment + reviewer |

Rule of thumb for the POC: extract **strings and amounts**; treat **UUIDs / masters** as post-extraction enrichment, not OCR targets.

---

## 7. Recommended target schema for the POC

Goal: keep V2 open/grounded, but align names and add India/Tally AP gaps so export can feed a review-voucher–shaped payload.

### Add (first-class)

1. **Header aliases or rename** toward prod: `supplier_invoice_no` (`supInvNo`), `bill_date`, optional `voucher_date`, `voucher_type`, `narration`.
2. **Line item extensions** on `LineItemV2`: `hsn_sac`, `uom`, `discount`, `sku` / `item_type` (all `FieldValue`).
3. **Dual line collections** (or typed discriminant): `item_lines` vs `ledger_lines` vs keep `totals.tax_lines` + optional `tax_ledger_lines`.
4. **GST / place-of-supply block**: `source_of_supply`, `destination_of_supply`, `shipping_address`, `reverse_charge` (bool or FieldValue).
5. **Coding stubs** (nullable, no UUID required in OCR stage): `purchase_account_name`, per-line `account_name`, `godown_name`, `cost_centre_name` — strings only; document that UUIDs are enrichment.
6. **Totals**: `tds`, `other_taxes` (or tax_lines with a `kind`).

### Rename / map (compat layer OK)

| Keep V2 | Map to prod on export |
|---|---|
| `document_id` | `supInvNo` |
| `issue_date` | `billDate` |
| `seller.*` | vendor party |
| `totals.total` | `vchAmt` / Grand Total |
| `doc_type` | hint for `vchType` only when values align |

### Drop or de-emphasize for AP review parity

- Do not require `bank` for AP voucher POC demos (keep optional).
- Do not pretend multi-doc page ranges are the prod voucher unit until packing/split is productized upstream of AP upload.

### Keep (POC strengths)

- `FieldValue` + `ungrounded` + page tags — superior HITL signal; map master-miss separately when integrating.
- `other_fields` — overflow for `additionalData` until first-class.

---

## 8. Unknowns / role-gated

- Full **Accounting Mode** field set (click blocked by sticky header).
- Exact **Approve** HTTP body and Tally sync DTO.
- Whether HSN/Discount columns are always visible vs Field Configuration.
- Full Additional Details field list after Cancel (modal not left open).
- Non-Tally ERP review schemas (Xero/QuickBooks/etc.) — out of session scope.

---

## 9. Screenshot index

| Path | Contents |
|---|---|
| `docs/review-voucher/01-voucher-overview.svg` | Review chrome, required header fields, vendor master warning, Approve & Next |
| `docs/review-voucher/02-line-items.svg` | Item / ledger / tax column layout schematic |
| `docs/review-voucher/03-needs-review-list.svg` | Needs Review + Bill Uploads relationship, 12-page fail callout |
