# Invoice Export Format

**Version:** `1.0`
**Produced by:** `app/services/invoice_export.py`
**Endpoint:** `GET /admin/invoices/export?format={xlsx|csv}&from_date=YYYY-MM-DD&to_date=YYYY-MM-DD`
**Fixture (the contract):** `app/tests/fixtures/invoice_export_v1.json`

This is the CA-facing export named by INV-07 and decision D-02 in
`.planning/phases/02-invoice-as-a-first-class-entity/02-CONTEXT.md`. It is **not** a Tally
importer and **not** a GSTR-1 JSON generator — those are explicitly out of scope for this
phase. The column set below carries every field those later serializers would need, so
adding them is a serializer, not a schema migration.

## Grain: one row per invoice line

Each row is one `InvoiceLine`. **Invoice-level fields repeat down every row of a
multi-line invoice.** A CA summing "Taxable Value" or "Line Total" across all rows of a
multi-line invoice will correctly get the line-level breakdown, but summing
**"Invoice Grand Total"** across those same rows over-counts — that column is the same
value repeated once per line, not a per-line amount. Group by "Invoice Number" before
summing "Invoice Grand Total".

## Money convention

- All money and rate columns are stored as `Decimal`, rounded to two decimal places with
  `ROUND_HALF_UP` (`app/services/finance._round_money`).
- **Unit Rate is GST-inclusive** (the price actually charged per unit). Taxable Value is
  back-computed from it: `taxable_value = (quantity * unit_rate) - discount_amount - tax_amount`.
  Do not multiply Unit Rate by Quantity and expect Taxable Value — the tax portion has
  already been removed.
- XLSX cells are written as native numbers (`number_format` applied, no float round-trip
  from `Decimal`). CSV cells are written as `str(Decimal)` — exact text, not a locale- or
  float-rendered value.
- No value in this export is ever passed through `float()`.

## Source of truth

Every value comes from the immutable `Invoice` / `InvoiceLine` snapshot captured at
issuance (INV-01, INV-03). Nothing is re-derived from `Order`, `OrderItem`, `SKU` or
`Retailer` — editing an order after its invoice is issued does not change a previously
exported row.

## Columns

| # | Column | Type | Source | Meaning |
|---|--------|------|--------|---------|
| 1 | Invoice Number | string | `Invoice.invoice_number` | The formatted, unique tax invoice number (`{prefix}{6-digit serial}`). |
| 2 | Invoice Date | date (`YYYY-MM-DD`) | `Invoice.invoice_date` | Date the invoice was issued (dispatch time), not the order's creation date. |
| 3 | Invoice Type | string | `Invoice.invoice_type` | `B2B` if the buyer has a GSTIN on file at issue time, else `B2C`. |
| 4 | Reverse Charge | `Y` / `N` | `Invoice.reverse_charge` | GST reverse-charge applicability. Currently always `N` — no reverse-charge path exists in this codebase. |
| 5 | Supplier GSTIN | string | `Invoice.supplier_gstin` | Ascend's own GSTIN as of issue time. **Blank today** — see Known Data Gaps below. |
| 6 | Buyer Name | string | `Invoice.buyer_name` | Retailer's name, snapshotted at issue. |
| 7 | Buyer GSTIN | string | `Invoice.buyer_gstin` | Retailer's GSTIN, snapshotted at issue. **Blank for every retailer today.** |
| 8 | Place Of Supply | string | `Invoice.place_of_supply` | The buyer's state at issue time — determines the GST supply state per GST law. |
| 9 | Order ID | integer | `Invoice.order_id` | The originating order's ID, for cross-reference back into the app. |
| 10 | Line Number | integer | `InvoiceLine.line_number` | 1-based, stable per invoice; identifies which physical line item this row is. |
| 11 | Description | string | `InvoiceLine.description` | The SKU's name at issue time. |
| 12 | HSN | string | `InvoiceLine.hsn_code` | HSN/SAC code for the line's product, snapshotted from the SKU at issue time. |
| 13 | Quantity | integer | `InvoiceLine.quantity` | Whole units shipped on this line. |
| 14 | UQC | string | `InvoiceLine.uqc` | Unit of measure. Always `PCS` today — see Known Data Gaps below. |
| 15 | Unit Rate | money | `InvoiceLine.unit_rate` | GST-inclusive price per unit as charged. |
| 16 | Discount Amount | money | `InvoiceLine.discount_amount` | Discount applied to this line, in rupees. |
| 17 | Taxable Value | money | `InvoiceLine.taxable_value` | The line's value with GST and discount removed — the base GST is calculated on. |
| 18 | CGST Rate | rate (%) | `InvoiceLine.cgst_rate` | Central GST rate applied to this line. Zero for inter-state lines. |
| 19 | CGST Amount | money | `InvoiceLine.cgst_amount` | Central GST amount for this line. |
| 20 | SGST Rate | rate (%) | `InvoiceLine.sgst_rate` | State GST rate applied to this line. Zero for inter-state lines. |
| 21 | SGST Amount | money | `InvoiceLine.sgst_amount` | State GST amount for this line. |
| 22 | IGST Rate | rate (%) | `InvoiceLine.igst_rate` | Integrated GST rate applied to this line. Zero for intra-state lines. |
| 23 | IGST Amount | money | `InvoiceLine.igst_amount` | Integrated GST amount for this line. |
| 24 | Cess Rate | rate (%) | `InvoiceLine.cess_rate` | Cess rate, if any. No source populates this today — always zero. |
| 25 | Cess Amount | money | `InvoiceLine.cess_amount` | Cess amount, if any. Always zero today. |
| 26 | Line Total | money | `InvoiceLine.line_total` | Taxable Value + total tax for this line (`taxable_value + total_tax_amount`). |
| 27 | Invoice Grand Total | money | `Invoice.grand_total` | The whole invoice's total. **Repeats on every line of a multi-line invoice — do not sum this column across lines of the same invoice.** |

## Known data gaps (as of this export's shipping date)

These are data-entry gaps in the dev database, not defects in the export:

- **Supplier GSTIN is blank.** `company_profile.gstin` is not set. An invoice exported
  today has no supplier GSTIN, which is not a valid tax invoice until this is filled in.
- **Every Buyer GSTIN is blank.** No retailer in the dev database has a GSTIN on file, so
  every invoice types as `B2C`. If a retailer is in fact a GST-registered business, it
  belongs in GSTR-1's B2B table, not B2C.
- **UQC is always `PCS`.** No per-SKU unit of measure exists anywhere in this codebase.

## Change policy

Adding, removing, renaming or reordering any column is a breaking change to the CA's
downstream process. Any such change **must**:

1. Bump `EXPORT_FORMAT_VERSION` in `app/services/invoice_export.py`.
2. Update `app/tests/fixtures/invoice_export_v1.json` (rename the file to match the new
   version if the major version changes) in the **same commit**.
3. Update this document's column table in the same commit.

`app/tests/test_invoice_export.py` fails the build if the generated header row and the
fixture disagree, so this drift cannot happen silently.
