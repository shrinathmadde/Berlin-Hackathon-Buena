# Buena Context Engine — WEG Immanuelkirchstraße 26

Hackathon submission for the **Buena Context Engine** track.

The goal: produce a single, living context store per property that an AI agent can query
in microseconds and patch surgically as new information arrives. Instead of a markdown
file, this implementation uses **SQL as the storage format** — structured tables for clean
entities, a dedicated `facts` table for everything that doesn't fit a column, and a
`source_events` table for full provenance.

> Organizers confirmed the brief cares about **speed of update** and **speed of query**,
> not the on-disk format. SQL wins on both axes for this dataset.

---

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

OpenAPI UI: <http://localhost:8000/docs>

The default database is SQLite (`./context_engine.db`). Override with `DATABASE_URL` to
swap in Postgres.

---

## Repository Layout

```
.
├── app/                       # FastAPI + SQLModel implementation
│   ├── main.py                # mounts all routers, creates tables on startup
│   ├── database.py            # engine + session factory
│   ├── models.py              # all SQLModel tables
│   └── routers/
│       ├── crud.py            # generic CRUD factory for structured tables
│       ├── facts.py           # /facts + supersede / conflict operations
│       └── context.py         # /context/{entity}/{id} aggregate views
├── data/hackathon/            # Provided synthetic dataset (see Dataset section)
└── requirements.txt
```

---

## Architecture

Two storage layers, one provenance layer:

```
┌──────────────────────┐     ┌──────────────────────┐
│  Structured tables   │     │     facts table      │
│  ────────────────    │     │  ────────────────    │
│  properties          │     │  fact_id             │
│  buildings           │     │  property_id    ───┐ │
│  units      ──┐      │     │  entity_type       │ │
│  owners       │      │     │  entity_id         │ │
│  tenants      │      │     │  category          │ │
│  service_     │      │     │  statement         │ │
│   providers   │      │     │  source_event_id ─┐│ │
│  bank_        │      │     │  superseded_by    ││ │
│   transactions│      │     │  status           ││ │
│  invoices     │      │     │                   ││ │
└───────┬──────┘       │     └────────────────────┘│ │
        │              │                           │ │
        │              │     ┌──────────────────────┘ │
        │              │     │                        │
        │              │     ▼                        ▼
        │              │  ┌──────────────────┐   (entity_id refers to
        │              └──┤  source_events   │    rows in the structured
        │                 │  ──────────────  │    tables on the left,
        │                 │  event_id        │    by string PK)
        └────────────────►│  source_type     │
            (provenance   │  source_path     │
             on invoices) │  thread_id       │
                          │  received_at     │
                          │  raw_content     │
                          └──────────────────┘
```

**Why this shape:**

- **Structured tables** answer fast lookups: *"who owns EH-014?"* is one indexed query.
- **`facts`** holds anything unstructured (preferences, open issues, council decisions,
  complaints). One row per fact — never a concatenated blob — so updates stay surgical.
- **`source_events`** anchors every fact to the email / PDF / CSV row it came from,
  satisfying the brief's "traced to its source" requirement.
- **Supersession chain** (`superseded_by`) preserves history without bloating active
  queries: live questions filter `WHERE status = 'active'`.

---

## Database Schema

### Structured entity tables

| Table | PK | Purpose |
|---|---|---|
| `properties` | `property_id` (`LIE-001`) | The condominium association (1 row for the whole hackathon dataset). Holds manager contact, WEG and reserve account IBANs. |
| `buildings` | `building_id` (`HAUS-12/14/16`) | Physical buildings within a property. |
| `units` | `unit_id` (`EH-001`…`EH-052`) | Individual apartments / commercial units / parking spots. |
| `owners` | `owner_id` (`EIG-001`…`EIG-035`) | Persons or companies that own one or more units. |
| `tenants` | `tenant_id` (`MIE-001`…`MIE-026`) | Renters of a specific unit, with lease terms and bank account. |
| `service_providers` | `provider_id` (`DL-001`…`DL-016`) | Caretakers, contractors, utilities. |
| `bank_transactions` | `transaction_id` (`TX-NNNNN`) | Every line item from the WEG account. |
| `invoices` | `invoice_id` (`INV-NNNNN`) | Provider invoices, linked to their PDF source event. |

### Provenance + flexible layer

| Table | PK | Purpose |
|---|---|---|
| `source_events` | `event_id` (`EMAIL-NNNNN` / `LTR-NNNN` / `INV-NNNNN` / `TX-NNNNN`) | One row per ingested document. Every fact and invoice can point back here. |
| `facts` | `fact_id` (`FACT-<hex>`) | The flexible layer. One row per atomic fact, with entity binding, provenance, and supersession chain. |

### Foreign-key relations

```
properties ──┬─< buildings ──< units ─┬─< tenants
             │                        │
             │                        └── owner_id ──> owners
             │
             ├─< bank_transactions
             ├─< invoices ──> service_providers
             │            └── source_event_id ──> source_events
             │
             ├─< source_events
             └─< facts ─┬─> source_events
                        ├─> facts (superseded_by, self-ref)
                        └── (entity_type, entity_id)  ── soft-FK to any
                                                         structured table
```

The `facts` table uses a **soft foreign key** pattern: `entity_type` names the table
(`owner`, `tenant`, `unit`, `building`, `service_provider`, `property`) and `entity_id`
holds the PK value of that row. This keeps the table polymorphic without dozens of
nullable FKs.

### Column reference

#### `properties`
`property_id`, `name`, `street`, `postal_code`, `city`, `country`, `built_year`,
`renovated_year`, `manager_name`, `manager_street`, `manager_postal_code`,
`manager_city`, `manager_email`, `manager_phone`, `manager_iban`, `manager_bic`,
`manager_bank`, `manager_tax_number`, `weg_account_iban`, `weg_account_bic`,
`weg_account_bank`, `reserve_account_iban`, `reserve_account_bic`.

#### `buildings`
`building_id`, `property_id` → properties, `house_number`, `units_count`, `floors`,
`has_elevator`, `built_year`.

#### `units`
`unit_id`, `building_id` → buildings, `property_id` → properties, `owner_id` → owners,
`unit_number`, `location`, `type` (Wohnung/Tiefgarage/Gewerbe), `area_sqm`, `rooms`,
`ownership_share`.

#### `owners`
`owner_id`, `salutation`, `first_name`, `last_name`, `company`, `street`, `postal_code`,
`city`, `country`, `email` (indexed), `phone`, `iban`, `bic`, `is_self_user` (selbstnutzer),
`has_sev_mandate`, `is_council_member` (beirat), `language`.

#### `tenants`
`tenant_id`, `salutation`, `first_name`, `last_name`, `email` (indexed), `phone`,
`unit_id` → units, `landlord_owner_id` → owners, `lease_start`, `lease_end`,
`cold_rent`, `utility_advance`, `deposit`, `iban`, `bic`, `language`.

#### `service_providers`
`provider_id`, `company`, `branch` (indexed), `contact_person`, `email` (indexed),
`phone`, `street`, `postal_code`, `city`, `country`, `iban`, `bic`, `vat_id`,
`tax_number`, `style`, `language`, `monthly_contract`, `hourly_rate`.

#### `bank_transactions`
`transaction_id`, `property_id` → properties, `booking_date` (indexed), `direction`
(CREDIT/DEBIT), `amount`, `category` (indexed), `counterparty_name`, `purpose`,
`reference_id` (indexed; soft-link to `MIE-`/`DL-`/`EIG-`), `error_types`.

#### `invoices`
`invoice_id`, `invoice_number`, `invoice_date` (indexed), `provider_id` →
service_providers, `provider_company`, `recipient`, `property_id` → properties,
`net_amount`, `vat_amount`, `gross_amount`, `iban`, `paid_transaction_id` →
bank_transactions, `source_event_id` → source_events, `error_types`.

#### `source_events`
`event_id`, `source_type` (indexed; email/pdf_letter/pdf_invoice/bank_tx/csv_import),
`property_id` → properties, `source_path`, `received_at` (indexed), `thread_id`
(indexed), `direction`, `from_address` (indexed), `to_address`, `subject`, `category`
(indexed), `language`, `raw_content`, `error_types`.

#### `facts`
`fact_id`, `property_id` → properties, `entity_type` (indexed), `entity_id` (indexed),
`category` (indexed), `statement`, `source_event_id` → source_events,
`extracted_at` (indexed), `superseded_by` → facts (self-ref), `status` (indexed;
active/superseded/conflicted), `confidence`.

---

## API Endpoints

Every structured table gets a uniform set of routes via the generic CRUD factory:

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/{prefix}` | List with `limit` + `offset` |
| `GET`    | `/{prefix}/{id}` | Fetch one |
| `POST`   | `/{prefix}` | Create (ID must be supplied) |
| `PATCH`  | `/{prefix}/{id}` | Partial update — surgical update path for structured fields |
| `DELETE` | `/{prefix}/{id}` | Delete |

Mounted prefixes: `/properties`, `/buildings`, `/units`, `/owners`, `/tenants`,
`/providers`, `/transactions`, `/invoices`, `/events`.

### Facts (the flexible layer)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/facts` | Filter by `property_id`, `entity_type`, `entity_id`, `category`, `status` (default `active`) |
| `GET`  | `/facts/{id}` | Fetch one |
| `POST` | `/facts` | Create a new fact tied to an entity + source event |
| `POST` | `/facts/{id}/supersede` | **Surgical update.** Inserts a new active fact, marks the old one `superseded`, links them via `superseded_by` |
| `POST` | `/facts/{id}/conflict` | Mark a fact `conflicted` for human review |
| `DELETE` | `/facts/{id}` | Delete |

### Aggregate context views

Single-call "give me everything about X" — what an AI agent would hit before drafting a reply.

| Path | Returns |
|---|---|
| `GET /context/property/{id}` | Property row + buildings + unit count + active facts |
| `GET /context/unit/{id}` | Unit + current owner + active tenant + active facts |
| `GET /context/owner/{id}` | Owner + their units + active facts |
| `GET /context/tenant/{id}` | Tenant + their unit + landlord + active facts |
| `GET /context/provider/{id}` | Provider + last 20 invoices + active facts |
| `GET /context/source/{event_id}` | Source event + every fact derived from it |

### Health

`GET /health` → `{"status": "ok"}`

---

## How updates stay surgical

When a new email arrives saying *"the owner now prefers email instead of WhatsApp"*:

1. The ingestion pipeline writes a row into `source_events` (`event_id = EMAIL-XXXXX`).
2. The extractor finds the existing fact for `(owner=EIG-007, category=communication_preference)`.
3. It calls `POST /facts/{id}/supersede` with the new statement and the new
   `source_event_id`.
4. The endpoint inserts one new row, updates one column on the old row, commits.
   Two writes, microseconds. The old fact is preserved with full history; future
   queries that filter `status = 'active'` only see the new one.

No file parsing, no diffing, no risk of stomping on a human edit elsewhere in the
document — by construction, the update only touches the affected fact row.

---

## Dataset

The provided synthetic dataset lives in `data/hackathon/`. It simulates the complete
data ecosystem of a German property management firm: master records, two years of
emails, bank statements, printed letters, and a 10-day incremental feed.

### The Property

| Field | Value |
|---|---|
| ID | `LIE-001` |
| Name | WEG Immanuelkirchstraße 26 |
| Address | Immanuelkirchstraße 26, 10405 Berlin |
| Built | 1928, renovated 2008 |
| Manager | Huber & Partner Immobilienverwaltung GmbH |

**3 buildings**, 52 units total:

| Building | ID | Units | Floors | Elevator |
|---|---|---|---|---|
| Nr. 12 | `HAUS-12` | 18 | 5 | Yes |
| Nr. 14 | `HAUS-14` | 20 | 5 | Yes |
| Nr. 16 | `HAUS-16` | 14 | 4 | No |

### Directory structure

```
data/hackathon/
├── stammdaten/          # Master data (static reference records)
├── bank/                # Full bank statement history (2024–2025)
├── emails/              # Archive emails (Jan 2024 – Jan 2026)
├── briefe/              # Printed letters as PDFs (Apr 2024 – Oct 2025)
└── incremental/         # Simulated daily feeds (10 days, Jan 2026)
```

### `stammdaten/` — Master Data

Ground truth for all entities. These records change rarely.

- **`stammdaten.json`** — Full property graph: `liegenschaft → gebaeude → einheiten`,
  with embedded current `eigentuemer` and `mieter`.
- **`eigentuemer.csv`** — 35 owners. Columns: `id`, `anrede`, `vorname`, `nachname`,
  `firma`, `email`, `telefon`, `iban`, `bic`, `einheit_ids` (semicolon-separated),
  `selbstnutzer`, `sev_mandat`, `beirat`, `sprache`.
- **`mieter.csv`** — 26 tenants. Columns: `id`, `einheit_id`, `eigentuemer_id`,
  `mietbeginn`, `mietende`, `kaltmiete`, `nk_vorauszahlung`, `kaution`, `iban`,
  `bic`, `sprache`.
- **`einheiten.csv`** — 52 units. Columns: `id`, `haus_id`, `einheit_nr`, `lage`,
  `typ` (Wohnung/Tiefgarage/Gewerbe), `wohnflaeche_qm`, `zimmer`, `miteigentumsanteil`.
- **`dienstleister.csv`** — 16 service providers. Columns: `id`, `firma`, `branche`,
  `ansprechpartner`, `email`, `iban`, `bic`, `ust_id`, `steuernummer`,
  `vertrag_monatlich`, `stundensatz`.

Service types covered: caretaker, elevator maintenance, heating, stairwell cleaning,
gardening, chimney sweep, building insurance, electricity, gas, water, waste,
electrician, plumbing, roofing, lock systems, facade cleaning.

### `bank/` — Bank Statement History

| File | Format |
|---|---|
| `bank_index.csv` | 1,619 transactions, indexed format |
| `kontoauszug_2024_2025.csv` | Same data, bank-statement format |
| `kontoauszug_2024_2025.camt053.xml` | Same data, ISO 20022 CAMT.053 XML |

Columns in `bank_index.csv`: `id`, `datum`, `typ` (CREDIT/DEBIT), `betrag`, `kategorie`,
`gegen_name`, `verwendungszweck`, `referenz_id`, `error_types`.

| Category | Count | Meaning |
|---|---|---|
| `hausgeld` | 806 | Monthly HOA fees from owners |
| `miete` | 624 | Rent payments from tenants |
| `dienstleister` | 155 | Payments to service providers |
| `sonstige` | 26 | Miscellaneous |
| `versorger` | 8 | Utility provider payments |

### `emails/` — Archive Emails

6,546 `.eml` files (Jan 2024 – Jan 2026), organised in monthly subdirectories
(`YYYY-MM/`). Filename: `YYYYMMDD_HHMMSS_EMAIL-NNNNN.eml`. No global index — parse the
files directly. Newer incremental emails come with structured indexes (see below).

### `briefe/` — Printed Letters (PDFs)

135 PDFs (Apr 2024 – Oct 2025), organised by month. Filename:
`YYYYMMDD_<type>_LTR-NNNN.pdf`.

| Type | Count | Meaning |
|---|---|---|
| `etv` | 72 | Owner assembly invitations / minutes |
| `hausgeld` | 35 | Hausgeld statements to owners |
| `bka` | 13 | Beschlusskatalog (resolution catalogue) |
| `mahnung` | 10 | Dunning notices |
| `mieterhoehung` | 3 | Rent increase notices |
| `kuendigung` | 2 | Lease termination notices |

### `incremental/` — Daily Feeds (10 Days)

Simulates real-time data arriving over 10 working days starting **2026-01-01** —
the primary test harness for the surgical-update capability. Each `day-NN/` folder:

```
day-01/
├── incremental_manifest.json   # day metadata + write counts
├── emails_index.csv            # new emails this day
├── rechnungen_index.csv        # new invoices this day
├── emails/2026-01/             # the .eml files
├── rechnungen/2026-01/         # the invoice PDFs
└── bank/
    ├── bank_index.csv          # cumulative bank index
    └── kontoauszug_delta.csv   # only new transactions this day
```

`emails_index.csv` columns: `id`, `datetime`, `thread_id`, `direction`, `from_email`,
`to_email`, `subject`, `category`, `sprache`, `error_types`, `filename`, `month_dir`.

**Email categories** in incremental data: `dienstleister/{rechnung,bericht,mahnung,nachtrag}`,
`eigentuemer/{rechtlich,sev,abrechnung,modernisierung,verkauf}`,
`mieter/{info,schaden,kaution,kuendigung,nachbarn,schluessel,rechtlich}`,
`versorger/versorger`.

`rechnungen_index.csv` columns: `id`, `rechnungsnr`, `datum`, `dienstleister_id`,
`dienstleister_firma`, `empfaenger`, `netto`, `mwst`, `brutto`, `iban`, `filename`,
`month_dir`.

### Entity ID Reference

| Prefix | Entity | Range | Maps to table |
|---|---|---|---|
| `LIE-` | Property (Liegenschaft) | `LIE-001` | `properties` |
| `HAUS-` | Building | `HAUS-12/14/16` | `buildings` |
| `EH-` | Unit (Einheit) | `EH-001`…`EH-052` | `units` |
| `EIG-` | Owner (Eigentümer) | `EIG-001`…`EIG-035` | `owners` |
| `MIE-` | Tenant (Mieter) | `MIE-001`…`MIE-026` | `tenants` |
| `DL-` | Service Provider | `DL-001`…`DL-016` | `service_providers` |
| `TX-` | Bank transaction | `TX-00001`…`TX-01619` | `bank_transactions` |
| `EMAIL-` | Email | `EMAIL-00001`…`EMAIL-06586` | `source_events` |
| `INV-` | Invoice | `INV-00195`… | `invoices` (+ `source_events`) |
| `LTR-` | Letter (Brief) | `LTR-0001`…`LTR-0133` | `source_events` |
| `THR-` | Email thread (incremental) | `THR-INN-XXXX` | `source_events.thread_id` |
| `FACT-` | Fact (generated) | `FACT-<hex>` | `facts` |

### Data timeline

```
Jan 2024                                        Jan 2026
    |---- Archive emails (6,546) --------------------|
    |---- Bank statements (1,619 txns) ---------|
              |---- Briefe PDFs (135) ------|
                                            |-- 10 incremental days -->
```

---

## Design Notes for the Challenge

1. **Identity aliasing**: An owner appears as `eigentuemer` in `stammdaten.json`,
   `EIG-XXX` in CSVs, and by email address in `.eml` files. Resolved at ingestion time
   to the canonical `owner_id`.
2. **Signal vs. noise**: 90% of emails are routine. Only ones that change facts
   produce `facts` rows. Routine ones still get `source_events` rows for audit, but
   no fact is extracted.
3. **Surgical patching**: New information triggers one `INSERT` + one column
   `UPDATE` on the supersession chain — never a full file rewrite.
4. **Multi-language**: Owners, tenants, and providers carry a `language` field
   (`de`/`en`); responses can be drafted in the right language without re-detection.
5. **Intentional data quality issues**: The `error_types` column in CSVs flags
   rows with deliberately introduced inconsistencies for robustness testing.

---

## Note on SQLModel quirks

`SQLModel(table=True)` skips Pydantic field coercion in `__init__`, so ISO date strings
arriving over HTTP would reach the DB as strings. The CRUD factory in `app/routers/crud.py`
runs `model.model_validate(payload)` on every create/patch to force coercion — this is
the canonical workaround.
