import { useState } from 'react';
import { downloadTokenUsage, getTokenUsage, TokenUsage } from '../services/api';

export default function TokenUsagePanel() {
  const [open, setOpen] = useState(false);
  const [usage, setUsage] = useState<TokenUsage | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setBusy(true);
    setError('');
    try { setUsage(await getTokenUsage()); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not load usage'); }
    finally { setBusy(false); }
  }
  return <>
    <button className="header-button" onClick={() => { setOpen(true); void refresh(); }}>Token usage</button>
    {open && <div className="run-history-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setOpen(false); }}>
      <section className="run-history-dialog token-usage-dialog" role="dialog" aria-modal="true" aria-labelledby="token-usage-title" onKeyDown={e => { if (e.key === 'Escape') setOpen(false); }}>
        <header className="run-history-heading">
          <h2 id="token-usage-title">AURA token usage</h2>
          <div><button className="button button-outline" disabled={busy} onClick={() => void refresh()}>Refresh</button><button autoFocus className="run-history-close" aria-label="Close token usage" onClick={() => setOpen(false)}>×</button></div>
        </header>
        <div className="token-usage-content">
          {busy && <p role="status">Loading usage…</p>}
          {error && <p role="alert">{error}</p>}
          {usage && <>
            <p><strong>{usage.total_tokens.toLocaleString()} total tokens</strong> across {usage.calls.toLocaleString()} calls</p>
            <p>Input: {usage.input_tokens.toLocaleString()} · Output: {usage.output_tokens.toLocaleString()} · Calls with unknown usage: {usage.calls_without_usage}</p>
            <table><thead><tr><th>Operation</th><th>Model</th><th>Calls</th><th>Tokens</th></tr></thead><tbody>
              {usage.by_operation.map(row => <tr key={row.operation + ':' + row.model}><td>{row.operation}</td><td>{row.model}</td><td>{row.calls}</td><td>{row.total_tokens.toLocaleString()}</td></tr>)}
            </tbody></table>
            <p>{usage.notice}</p>
            <button className="button button-outline" onClick={() => { void downloadTokenUsage().catch(e => setError(e instanceof Error ? e.message : 'Export failed')); }}>Download usage CSV</button>
          </>}
        </div>
      </section>
    </div>}
  </>;
}
