# AURA user journey

The normal workflow keeps source connection, analysis, report editing,
approval, and generation as distinct stages.

```mermaid
flowchart TD
    Start[Open AURA] --> Source{Connect a source}
    Source -->|Excel .xlsx| Inspect[Inspect and profile workbook]
    Source -->|SQL Server| Discover[Discover schema and select tables]
    Discover --> Import[Import bounded read-only snapshot]
    Inspect --> ValidateSource[Review source findings]
    Import --> ValidateSource
    ValidateSource --> Ask[Ask data questions or request a report]
    Ask --> Clarify{Clarification needed?}
    Clarify -->|Yes| Answer[Answer clarification] --> Ask
    Clarify -->|No| Draft[Create or edit report draft]
    Draft --> Preview[Review preview and field bindings]
    Preview --> Approval{Approval required?}
    Approval -->|Edit| Draft
    Approval -->|Approve plan| ValidateReport[Validate report plan]
    ValidateReport -->|Invalid| Repair[Revise or prepare a reviewed fix]
    Repair --> Draft
    ValidateReport -->|Valid| Generate[Generate PBIP project]
    Generate --> Verify[Download or open in Power BI Desktop]
```

Important boundaries:

- SQL imports are bounded snapshots, not live Power BI connections.
- Excel formulas are not recalculated; saved formula results may be stale.
- Browser previews are approximate and must be verified in Power BI Desktop.
- A chat request cannot silently approve or generate a report.
- Generated PBIP projects are local artifacts; AURA does not publish to Power
  BI Service or create `.pbix` files.
