# AURA - AI-driven Understanding & Reporting Accelerator

AURA connects an Excel workbook or an imported SQL Server dataset to an AI assistant. You can ask questions about the data, create and revise a report draft, review its preview, and generate a Power BI Project (`.pbip`) for Power BI Desktop.

AURA does not publish to Power BI Service or create `.pbix` files. The browser preview is approximate; verify the generated project in Power BI Desktop.

## Capabilities at a glance

- Connect Excel workbooks or import bounded, read-only SQL Server snapshots.
- Profile source tables, fields, measures, relationships, sample rows, and Excel formula-cell usage.
- Answer grounded data questions, including aggregates, rankings, trends, comparisons, distinct values, joins, descriptive statistics, correlations, percentiles, and window-style analysis.
- Suggest KPIs; create, revise, preview, validate, approve, and generate single-page or multi-page Power BI report plans.
- Generate and validate a PBIP/PBIR project for download or opening in Power BI Desktop.
- Preserve owner-and-source scoped conversation memory, run history, approval records, audit exports, and provider-reported token usage.
- Apply bounded retries and schema-aware repair to invalid plans. A failed dashboard or edit request cannot approve or generate a previously displayed draft.

For the implementation-level process flows, agent roles, and tool inventory, see [Agent architecture](docs/AGENTIC_ARCHITECTURE.md).

## Requirements

- Python 3.12 (project baseline; the Docker image uses Python 3.12)
- Node.js 18+ and npm
- Azure OpenAI deployment configured through Azure AI Foundry for the assistant
- Power BI Desktop to open generated projects
- For SQL Server or Azure SQL: Microsoft ODBC Driver for SQL Server and appropriate database access

## Set up and run

From the project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
```

The backend requirements include the dependencies used by `automation`. If you
are installing only the automation tooling, use
`python -m pip install -r automation\requirements.txt`; it is kept as a
wrapper around the same dependency set.

Edit `.env` and configure the Azure settings:

```dotenv
USE_AI=true
AI_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=your-server-side-key
AZURE_AI_FOUNDRY_PROJECT_ENDPOINT=https://your-resource.services.ai.azure.com/api/projects/default
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/openai/v1
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
```

Keep the key in the backend environment; do not commit it or put it in frontend variables. The backend can start without these values, but AI operations will fail until they are configured. `AI_PROVIDER=mock` is intended for testing and development.

Start the backend:

```powershell
.\.venv\Scripts\uvicorn.exe backend.main:app --reload --port 8000 --env-file .env
```

In a second PowerShell terminal, install and start the frontend:

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

Open [http://localhost:5173](http://localhost:5173). The frontend proxies API requests to port `8000`; the backend health endpoint is [http://localhost:8000/health](http://localhost:8000/health). Optional `VITE_DEV_PORT` and `VITE_API_PROXY_TARGET` settings in `frontend\.env.local` change those defaults.

## Working with AURA

### 1. Connect data

- **Excel:** Upload an `.xlsx` workbook. Review detected tables and confirm any flagged ranges or headers.
- **SQL Server:** Enter connection details, discover the schema, select tables and approve the proposed measures, then import the selected tables.

The model table scope defaults to automatic selection. Select a table explicitly if you want to direct the assistant to a particular table. SQL imports are bounded snapshots of up to 10,000 rows per table, not live Power BI connections. SQL connections use read-only mode and expire after 15 minutes.

Excel formulas are not recalculated. AURA uses saved formula results when available, which may be stale.

### 2. Explore the data

After a source is connected and validated, use **Ask AURA** to ask questions
using fields and measures available in that source. The frontend disables
question submission without a connected source, and the backend validates the
source again before it creates an agent run. For example:

```text
What tables and measures are available in this workbook?
What is Total Net Revenue?
What is Total Net Revenue in the North region?
What Customer Segment values are available?
What is the Average Discount %?
```

The names and results depend on the source. Expand **Supporting data** where available to inspect the method and findings. If a field or business definition is ambiguous, answer the clarification before continuing.

KPI suggestions, field mappings and relationships appear in collapsed sections. Expand a section to review its details or approve a suggested KPI.

### 3. Create and change a report draft

Describe the visuals and their fields, for example:

```text
Show Total Net Revenue by Region.
Add a KPI card for Total Net Revenue.
Add a bar chart of Total Net Revenue by Region on the same page.
Add a slicer for Customer Segment on the second page.
```

For a forecast source, you could ask:

```text
Create a forecast dashboard with a line chart of Average GDP Growth % by Year and a slicer for Scenario.
```

Review the draft and field bindings in the report preview next to the assistant (below it on smaller screens). DAR Light is the default theme; the preview uses its palette approximately, while the generated project includes it as a custom Power BI theme. When an edit requires approval, select **Approve change** to proceed. A request targeting the next numbered page can propose creating that page; existing pages are preserved. Review the revised draft before generating a report.

For a broad multi-page request, Aura proposes pages and visuals from the available schema. Specify a page count or describe numbered pages when you need particular coverage; check the proposed pages for missing scenarios before approval.
Synchronized slicers, page navigation buttons, and heatmap formatting are not generated. Add these manually in Power BI Desktop after generation.
Table visuals require at least one source column; they can also include existing measures. Table previews group by the displayed columns, calculate measures for each group, and show a bounded number of distinct rows. Matrix visuals require at least one row column and one measure. If a plan cannot be repaired automatically, specify the table and exact fields in a revised request.

### 4. Approve and generate

Select **Approve report plan** after reviewing the preview, then generate the PBIP. Once validation passes, use **Open in Power BI** or **Download PBIP**. For a download, extract the complete ZIP archive and open its `.pbip` file in Power BI Desktop.

If validation fails only because slicers cannot reach measures on the same page, **Prepare fix** proposes removing those slicers. Review the listed changes and preview, approve the revised plan, and generate again. Other validation failures must be addressed by editing the report or source.

Generation is a separate, explicitly approved step; chat requests do not publish or automatically generate a report.

### 5. Start again or inspect a run

- **New report:** Returns to the home composer, clearing an existing conversation and draft after confirmation while keeping the connected source and selected table.
- **Remove data source:** After confirmation, clears the current workspace and disconnects the source.
- **Scope:** Changing the selected table requires confirmation and clears the current draft and source-scoped conversation memory.

These actions do not delete saved reports, source files or run history. The Published reports view can reopen the conversation captured at generation when an audit record is available; later messages are not part of that snapshot.

Refreshing or reopening the page in the same browser session also resets the workspace without confirmation: the previous source's conversation and draft are cleared, the source is disconnected, and a new chat ID is created. If clearing fails, retry the reset before continuing. This affects the same owner-and-source conversation scope shared by other tabs. Closing the browser session can discard the stored source ID, leaving server-side memory on disk.

## Token usage and session files

By default, runtime data is stored in `backend\.app_data`. If `APP_DATA_DIR` is set, that directory is used instead. The supplied `.env.example` sets `APP_DATA_DIR=./data`, relative to the directory from which the backend is started.

Each backend session stores its run records, audit data and token ledger under:

```text
<APP_DATA_DIR>\sessions\<backend-session>\
  audit\
  runs\
  usage\
    token-usage.sqlite3
    token-usage.csv

<APP_DATA_DIR>\daily-usage\
  daily-usage.sqlite3
  daily-usage.csv
```

Open `token-usage.csv` in Excel. Each row represents an AI call. Filter by `chat_session_id` to separate chats, or sum `total_tokens` for the backend session. **New chat**, **Reset**, and a page refresh create a new chat ID. Calls without an associated chat have a blank chat ID.

Open `daily-usage\daily-usage.csv` to see one row per local calendar day across backend sessions and owners. It reports call count, input, output and total tokens, and calls without reported usage. The day is determined using the backend machine's local time at the time of recording. Collection starts with calls recorded after this feature is enabled; earlier session records are not backfilled, so the first day's total may be incomplete.

Usage is provider-reported: `usage_known` is `0` when usage was not returned, and a blank token count is unknown, not zero. Cached and reasoning tokens are subsets of input and output tokens. SDK retries that do not return usage cannot be measured here; use Azure billing for billed totals. Mock mode does not produce paid token counts.

Daily sums include only reported values; `calls_without_usage` identifies calls whose total is unknown. The CSVs are refreshed after each recorded call. If Excel locks either file, close it so a later call can update it; write failures are reported. SQLite is authoritative. Older session databases are not automatically converted to CSV. Set `TOKEN_USAGE_DB` to override the session database location; its CSV is written alongside it and the daily folder is created in the same parent directory. Keep both databases on persistent storage. Restrict filesystem access: session records and daily totals can include multiple owners.

The owner-scoped endpoints `GET /api/usage/tokens?scope=all|backend|chat` and `GET /api/usage/tokens/export?scope=all|backend|chat` provide summaries and per-call CSV exports. Chat scope requires the `X-AURA-Chat-ID` header; these APIs do not expose the machine-wide daily CSV.

## Analysis and limitations

The assistant can use typed analysis tools or validated, bounded read-only DuckDB queries against imported rows. Supported analyses include aggregates, rankings, trends, distinct values, joins, window functions and descriptive statistics. Queries are limited to 500,000 source rows, 200 result rows, 40 result columns and 100,000 output characters, with a 15-second execution timer; source loading precedes that timer. The DuckDB memory setting is not a process-wide sandbox. Missing definitions, ambiguous joins, mixed grains and subtotal rows still require review. The assistant does not execute generated Python or automatically establish causal inference.

Conversation memory is scoped to the current user and source, not individual browser tabs; chat IDs are separate and only used for tracking. The run service persists approvals and clarifications; it does not use a LangGraph native checkpointer. On shared or multi-instance deployments, evaluate storage, concurrency, retention, access controls and authentication before relying on them.

For the included test workbook, regional and transaction totals represent different grains. Two transaction regions are blank, and the finance columns labelled as variances contain actual gross-profit and margin formulas rather than actual-minus-plan variances. Review workbook definitions and saved formula values rather than assuming labels are correct.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Azure request or configuration error | Check the backend `.env`, deployment, endpoints, key and service availability. Retry only after checking the cause. |
| A field cannot be resolved | Confirm the detected workbook range, table scope and exact field or measure name. Answer any clarification. |
| A report plan fails validation | For multi-page requests, Aura retries a rejected page once with the validation details. If it still fails, review the error and retry with explicit visual types, fields and page targets. |
| An edit is waiting | Select **Approve change** before the assistant executes an approval-required edit. |
| Preview differs from Power BI | The preview is approximate. Generate the project and verify it in Power BI Desktop. |
| CSV is missing or cannot update | Check `APP_DATA_DIR`, wait for an AI call to be recorded, and close the CSV in Excel if it is locked. |

## Security and deployment

API authentication is optional in local development. For shared deployments, enable `AUTH_REQUIRED=true` and configure `AURA_API_TOKENS` as a JSON mapping of bearer tokens to user IDs. The legacy `AUTOBI_API_TOKENS` name is also supported. Use a secret manager or deployment environment variables rather than committing tokens. Configure trusted `CORS_ORIGINS`; credentialed CORS cannot use `*`.

`GET /api/identity` returns the effective user ID for the current request. `GET /api/report` lists only that user's generated, validated reports, including the saved report specification and source ID used to reopen its chat preview. `GET /api/report/{report_id}/conversation` returns the owner-scoped conversation captured at generation when available. The UI uses these endpoints for the home page and Published reports view. User display names and job titles are not currently stored by the application.

The assistant uses bounded tools and validates data and report plans. Data calculations run locally against the source after validation. AI requests can include questions, schema, conversation context and verified results; protect sensitive inputs and review your Azure deployment's data-handling requirements. Audit exports omit raw tool outputs and workbook rows. The default upload limit is 25 MiB, configurable with `MAX_UPLOAD_BYTES`.

## Development

Run backend and automation tests:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests automation\tests -q
```

Build and type-check the frontend:

```powershell
npm --prefix frontend run build
```

The main components are `frontend\src` (React workspace), `backend\api` and `backend\agents` (API and agent workflow), `backend\services` (data, reports and session services), and `automation` (profiling, validation, AI integration and PBIR generation).

For the user journey, see the [process guide](docs/PROCESS_DIAGRAM.md). The
[agent architecture diagram](docs/AGENTIC_ARCHITECTURE.md) shows the workflow,
its agents and their tools.

## Azure App Service container (single-instance pilot)

The root `Dockerfile` builds the frontend and runs it with the FastAPI backend
on port 8000. It includes Microsoft ODBC Driver 18 for SQL Server. Build and
smoke-test the image locally from the repository root:

```powershell
docker build -t aura:local .
docker run --rm -p 8000:8000 -v aura-data:/home/data aura:local
```

Open `http://localhost:8000` and check `http://localhost:8000/health`.
The named Docker volume keeps generated reports and uploaded sources across
container restarts.

For an Azure pilot, push this image to Azure Container Registry (ACR), then
create a Linux App Service configured to use that image. Set these App Service
application settings:

```text
WEBSITES_PORT=8000
WEBSITES_ENABLE_APP_SERVICE_STORAGE=true
APP_DATA_DIR=/home/data
```

Make sure the mounted `/home/data` directory is writable by the container user
(UID 10001).

Set Azure OpenAI values as App Service settings, preferably using Key Vault
references for secrets. Keep the App Service at one instance: source files,
reports, SQLite usage data, and some runtime state are currently local to the
backend instance. Do not enable autoscaling until state and file storage have
been moved to shared services and verified for concurrent use.

This container does not add Microsoft Entra ID sign-in. The current backend
shared-auth option requires AURA bearer tokens, and the frontend has no sign-in
flow to obtain them. Keep an initial pilot private and use non-sensitive data;
integrate and test user authentication and owner isolation before exposing it
to multiple users or the public internet. On Linux, users download generated
PBIP files and open them in Power BI Desktop locally.
