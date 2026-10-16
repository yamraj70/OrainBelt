# ORION Database Enterprise — Client Demonstration

**Purpose:** Demonstrate a familiar Orianbelt desktop that answers from approved database documentation and opens real read-only database connections only with employee approval.

## Demo preparation

- Install Python 3.11 64-bit and run `scripts/INSTALL_WINDOWS.bat`.
- Create an administrator using `scripts/CREATE_ADMIN.bat`.
- Start `scripts/START_SERVER.bat` and `scripts/START_DESKTOP.bat`, then sign in.
- The nine `.txt` knowledge files are fictional, and `data/demo_assets.sqlite` contains fictional asset rows.

## Suggested walkthrough

| Ask or click | Expected outcome |
| --- | --- |
| Which databases do we have? | Lists documented database names with TXT sources; no claim of live connectivity |
| What is a database? | Definition from database fundamentals TXT |
| Describe finance_reporting tables | Shows documented table/schema details with source citations |
| Connect to database | Opens connection form and explicit approval step |
| Select demo_sqlite | Verifies the actual SQLite file read-only |
| Browse Tables → main.assets | Shows rows returned from the live SQLite database |
| Sync Knowledge | Re-indexes changed TXT documents on demand (admin only) |
| Gear → Model status | Reports whether the optional GGUF model is enabled, found, or loaded |

## Scope and limitations

The client demonstration does not include voice, web browsing, employee file transfers, background monitoring, write SQL, or a MySQL live connector. PostgreSQL and SQL Server require the relevant Python drivers and actual network credentials; these were not verified against a customer database. Qwen GGUF inference is optional and disabled by default to keep the demo responsive without extra dependencies. The Windows installer build script is included, but a compiled executable is not included.

## Client security expectations

Do not use real credentials or customer data in the fictional documents. Use read-only database accounts. Before production, validate the Windows UI, remote database drivers, TLS certificates, network/firewall rules, data retention, and customer-specific access policies.
