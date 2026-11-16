# Frontend layer

`src/App.tsx` is the application composition root. API request/response types
and HTTP calls are centralized in `src/services/api.ts`; shared styling is in
`src/index.css`.

When adding UI features, keep network calls in `services/`, reusable display
elements in `components/`, and stateful view behavior in `hooks/`. This keeps
the React layer independent of backend implementation details.

On the home screen, Excel opens the file picker without changing views. SQL
Server opens a connection dialog; after testing the connection, users select
and import tables before the source becomes available to the assistant. The
same source actions are available from the report workspace.
The More button shows additional common Power BI data sources as selectable
demo placeholders; they do not connect data.
Excel upload shows a compact spinner and the current server-reported inspection
stage on the home screen. In the report workspace, transfer progress is shown
when measurable and inspection is indeterminate. Stages can repeat as each
worksheet is processed; cached profiles and AI analysis have their own labels.
Users can cancel an active upload.
After inspection, the source summary shows the number of detected tables and
the workbook-wide count of formula cells (including zero). Saved Excel sources
show the count again when their workbook profile contains it; SQL sources do
not show a formula-cell count.
The UI uses `POST /api/datasource/excel/stream`, which emits newline-delimited
JSON progress events followed by a result or error event. The existing
`POST /api/datasource/excel` endpoint continues to return a normal JSON result.
Reverse proxies must not buffer the streaming response. Cancellation stops
inspection at the next processing checkpoint.

New report returns to the home composer after confirmation and retains the
connected source. Changing the table scope requires confirmation before clearing
the current draft and source-scoped conversation memory. Reopening a published
report restores the conversation recorded at generation when it is available;
older reports without a saved conversation are identified as such. Account
dialogs are placeholders and do not save settings or feedback.
Recent chats have a More options menu to rename or delete their saved
conversation. Renaming a chat does not rename its published report. Deleting a
chat removes its saved conversation from the report audit and from Recents,
but preserves the published report and PBIP; other audit records are retained.
Newly generated reports show their source name when it was recorded at upload;
older reports may not contain that metadata.
Published-report thumbnails load actual first-page preview data as cards enter
the viewport; unavailable sources or previews are reported on the card.
