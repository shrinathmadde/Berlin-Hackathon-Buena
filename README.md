# Hackathon Dataset: WEG Immanuelkirchstraße 26

Synthetic dataset simulating the complete data ecosystem of a German property management firm.
The property is a **Wohnungseigentümergemeinschaft (WEG)** — a condominium association —
managed by *Huber & Partner Immobilienverwaltung GmbH*.

---

## The Property

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

---

## Directory Structure

```
data/hackathon/
├── stammdaten/          # Master data (static reference records)
├── bank/                # Full bank statement history (2024–2025)
├── emails/              # Archive emails (Jan 2024 – Jan 2026)
├── briefe/              # Printed letters as PDFs (Apr 2024 – Oct 2025)
└── incremental/         # Simulated daily feeds (10 days, Jan 2026)
    ├── day-01/
    ├── day-02/
    ...
    └── day-10/
```

---

## 1. `stammdaten/` — Master Data

The ground truth for all entities. These records change rarely (new owner, new tenant, etc.).

### `stammdaten.json`
Single JSON file containing the entire property graph: `liegenschaft` → `gebaeude` → `einheiten`, each unit embedded with its current `eigentuemer` and `mieter`. Use this as the authoritative join document.

### `eigentuemer.csv` — Owners (35 records)
| Column | Description |
|---|---|
| `id` | `EIG-001` … `EIG-035` |
| `anrede` / `vorname` / `nachname` / `firma` | Name fields (person or company) |
| `email`, `telefon` | Contact |
| `iban`, `bic` | Bank account for Hausgeld payments |
| `einheit_ids` | Semicolon-separated list of owned units (`EH-XXX`) |
| `selbstnutzer` | Boolean — owner lives in the unit |
| `sev_mandat` | Boolean — has SEV (Sondereigentumsverwaltung) mandate |
| `beirat` | Boolean — member of the elected owners' council |
| `sprache` | Preferred language (`de` / `en`) |

### `mieter.csv` — Tenants (26 records)
| Column | Description |
|---|---|
| `id` | `MIE-001` … `MIE-026` |
| `einheit_id` | The unit they rent |
| `eigentuemer_id` | The landlord owner for that unit |
| `mietbeginn` / `mietende` | Lease start / end (end empty = active) |
| `kaltmiete` | Net cold rent (€/month) |
| `nk_vorauszahlung` | Utility advance payment (€/month) |
| `kaution` | Security deposit (€) |
| `iban`, `bic` | Tenant's bank account |
| `sprache` | Preferred language |

### `einheiten.csv` — Units (52 records)
| Column | Description |
|---|---|
| `id` | `EH-001` … `EH-052` |
| `haus_id` | `HAUS-12`, `HAUS-14`, or `HAUS-16` |
| `einheit_nr` | Unit number (e.g., `WE 01`, `TG 18`, `GE 37`) |
| `lage` | Floor/position (e.g., `1. OG links`) |
| `typ` | `Wohnung` (apartment), `Tiefgarage` (underground parking), `Gewerbe` (commercial) |
| `wohnflaeche_qm` | Area in m² |
| `zimmer` | Number of rooms |
| `miteigentumsanteil` | Co-ownership share (denominator ~10,000) |

### `dienstleister.csv` — Service Providers (16 records)
| Column | Description |
|---|---|
| `id` | `DL-001` … `DL-016` |
| `firma` | Company name |
| `branche` | Service category (e.g., `Hausmeisterdienst`, `Heizungswartung`, `Strom Allgemein`) |
| `ansprechpartner` | Contact person |
| `email`, `telefon` | Contact |
| `iban`, `bic` | Account for invoice payments |
| `ust_id`, `steuernummer` | Tax identifiers |
| `vertrag_monatlich` | Monthly flat rate (€, 0 = ad-hoc) |
| `stundensatz` | Hourly rate (€) |

Service types covered: caretaker, elevator maintenance, heating, stairwell cleaning, gardening, chimney sweep, building insurance, electricity, gas, water, waste, electrician, plumbing, roofing, lock systems, facade cleaning.

---

## 2. `bank/` — Bank Statement History

Full transaction history for the WEG's main account (Jan 2024 – Dec 2025).

### `bank_index.csv` — 1,619 transactions
| Column | Description |
|---|---|
| `id` | `TX-00001` … |
| `datum` | Transaction date |
| `typ` | `CREDIT` (incoming) or `DEBIT` (outgoing) |
| `betrag` | Amount in € |
| `kategorie` | `hausgeld`, `miete`, `dienstleister`, `versorger`, `sonstige` |
| `gegen_name` | Counterparty name |
| `verwendungszweck` | Payment reference / purpose |
| `referenz_id` | Links to `MIE-XXX`, `DL-XXX`, etc. |
| `error_types` | Intentional data quality issues (for testing robustness) |

**Category breakdown:**
| Category | Count | Meaning |
|---|---|---|
| `hausgeld` | 806 | Monthly HOA fees from owners |
| `miete` | 624 | Rent payments from tenants |
| `dienstleister` | 155 | Payments to service providers |
| `sonstige` | 26 | Miscellaneous |
| `versorger` | 8 | Utility provider payments |

### `kontoauszug_2024_2025.csv`
Same data as `bank_index.csv` in bank-statement CSV format.

### `kontoauszug_2024_2025.camt053.xml`
Same data in **CAMT.053** ISO 20022 XML format (standard bank statement format used by German banks).

---

## 3. `emails/` — Archive Emails

**6,546 `.eml` files** covering Jan 2024 – Jan 2026, organized into 25 monthly subdirectories (`YYYY-MM/`).

**Filename format:** `YYYYMMDD_HHMMSS_EMAIL-NNNNN.eml`

Emails are plain-text MIME messages (German and occasionally English) between the property manager (`info@huber-partner-verwaltung.de`) and owners, tenants, service providers, and utility companies.

There is no global index CSV for archive emails — the index lives in the incremental data for newer emails. Archive emails must be parsed from the `.eml` files directly.

**Common subjects/senders:** invoice submissions, repair reports, owner assembly invitations, Hausgeld queries, tenant move-in/out, SEV reports, legal notices.

---

## 4. `briefe/` — Printed Letters (PDFs)

**135 PDFs** organized by month (`YYYY-MM/`), covering Apr 2024 – Oct 2025.

**Filename format:** `YYYYMMDD_<type>_LTR-NNNN.pdf`

**Letter types:**
| Type in filename | Count | Meaning |
|---|---|---|
| `etv` | 72 | Eigentümerversammlung (owner assembly) — invitations & minutes |
| `hausgeld` | 35 | Hausgeld statements sent to owners |
| `bka` | 13 | Beschlusskatalog (resolution catalogue) |
| `mahnung` | 10 | Dunning / payment reminders |
| `mieterhoehung` | 3 | Rent increase notices |
| `kuendigung` | 2 | Lease termination notices |

---

## 5. `incremental/` — Daily Feeds (10 Days)

Simulates real-time data arriving over 10 working days starting **2026-01-01**.
This is the primary test harness for the Context Engine's **surgical update** capability.

Each `day-NN/` folder contains:

```
day-01/
├── incremental_manifest.json   # Metadata for this delta
├── emails_index.csv            # Index of new emails this day
├── rechnungen_index.csv        # Index of new invoices this day
├── emails/
│   └── 2026-01/               # New .eml files
├── rechnungen/
│   └── 2026-01/               # New invoice PDFs
└── bank/
    ├── bank_index.csv          # Cumulative bank index (growing)
    └── kontoauszug_delta.csv   # Only new transactions this day
```

### `incremental_manifest.json`
```json
{
  "schema_version": 1,
  "day_index": 1,
  "content_date": "2026-01-01",
  "seed": 42,
  "difficulty": "medium",
  "emails_written": 4,
  "invoices_written": 1,
  "bank_transactions_written": 1
}
```

### `emails_index.csv` (incremental)
Same schema as archive emails but with a structured index:

| Column | Description |
|---|---|
| `id` | `EMAIL-06547` … (continues archive numbering) |
| `datetime` | ISO 8601 timestamp |
| `thread_id` | `THR-INN-XXXX` — groups replies into threads |
| `direction` | `incoming` or `outgoing` |
| `from_email` / `to_email` | Sender / recipient |
| `subject` | Email subject |
| `category` | Two-level category (see below) |
| `sprache` | Language (`de` / `en`) |
| `error_types` | Intentional data quality issues |
| `filename` | `.eml` filename |
| `month_dir` | Month subdirectory (`2026-01`) |

**Email categories** (across all 10 incremental days):
| Category | Meaning |
|---|---|
| `dienstleister/rechnung` | Service provider invoice |
| `dienstleister/bericht` | Service report |
| `dienstleister/mahnung` / `nachtrag` | Dunning / supplement |
| `eigentuemer/rechtlich` | Owner legal matter |
| `eigentuemer/sev` | SEV (rental management) communication |
| `eigentuemer/abrechnung` | Owner settlement/statement |
| `eigentuemer/modernisierung` | Modernization/renovation |
| `eigentuemer/verkauf` | Unit sale |
| `mieter/info` | Tenant general inquiry |
| `mieter/schaden` | Damage report |
| `mieter/kaution` | Deposit matter |
| `mieter/kuendigung` | Tenant termination |
| `mieter/nachbarn` | Neighbor complaint |
| `mieter/schluessel` | Key/access issue |
| `mieter/rechtlich` | Tenant legal matter |
| `versorger/versorger` | Utility provider communication |

### `rechnungen_index.csv` (incremental)
| Column | Description |
|---|---|
| `id` | `INV-NNNNN` |
| `rechnungsnr` | Invoice number (e.g., `INV-2026-0195`) |
| `datum` | Invoice date |
| `dienstleister_id` | Links to `DL-XXX` |
| `dienstleister_firma` | Provider name |
| `empfaenger` | Recipient (property manager) |
| `netto` / `mwst` / `brutto` | Net, VAT, gross amounts (€) |
| `iban` | Provider IBAN to pay |
| `filename` | PDF filename |
| `month_dir` | Month directory |

---

## Entity ID Reference

| Prefix | Entity | Range |
|---|---|---|
| `LIE-` | Property (Liegenschaft) | `LIE-001` |
| `HAUS-` | Building | `HAUS-12`, `HAUS-14`, `HAUS-16` |
| `EH-` | Unit (Einheit) | `EH-001` … `EH-052` |
| `EIG-` | Owner (Eigentümer) | `EIG-001` … `EIG-035` |
| `MIE-` | Tenant (Mieter) | `MIE-001` … `MIE-026` |
| `DL-` | Service Provider (Dienstleister) | `DL-001` … `DL-016` |
| `TX-` | Bank transaction | `TX-00001` … `TX-01619` |
| `EMAIL-` | Email | `EMAIL-00001` … `EMAIL-06586` |
| `INV-` | Invoice | `INV-00195` … |
| `LTR-` | Letter (Brief) | `LTR-0001` … `LTR-0133` |
| `THR-` | Email thread (incremental) | `THR-INN-XXXX` |

---

## Data Timeline

```
Jan 2024                                        Jan 2026
    |---- Archive emails (6,546) --------------------|
    |---- Bank statements (1,619 txns) ---------|
              |---- Briefe PDFs (135) ------|
                                            |-- 10 incremental days -->
```

- **Archive** covers 2 years of operational history to seed the initial context file.
- **Incremental** simulates day-by-day incoming data to test surgical context updates.

---

## Key Design Notes for the Challenge

1. **Identity aliasing**: The same person appears as `eigentuemer` in `stammdaten.json`, `EIG-XXX` in CSVs, and by email address in `.eml` files. The engine must resolve these to the same entity.

2. **Signal vs. noise**: Most emails are routine (rent payment confirmations, utility invoices). The engine must recognize which emails change facts worth storing in the context file (e.g., a new owner, an open repair ticket, a legal dispute) vs. which are noise.

3. **Surgical patching**: Incremental feeds arrive one day at a time. Regenerating the full context file each time is too expensive and destroys human edits. The engine must identify the affected section(s) and patch only those.

4. **Multi-language**: Some owners and tenants prefer English (`sprache: en`). Service providers from the UK are also present. The context file should handle this gracefully.

5. **Intentional data quality issues**: The `error_types` column in index files flags rows with deliberately introduced inconsistencies (wrong amounts, missing references, etc.) to test robustness.
