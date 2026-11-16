import { ExcelProcessingStage, ExcelUploadProgress } from '../services/api';

const stageLabels: Record<ExcelProcessingStage, string> = {
  reading_workbook: 'Reading workbook',
  detecting_tables: 'Detecting tables and headers',
  profiling_columns: 'Profiling columns and types',
  reading_formula_results: 'Reading saved formula results',
  loading_cached_profile: 'Loading saved workbook profile',
  analyzing_workbook: 'Analyzing workbook with AI',
  finalizing: 'Preparing data source',
};

export function UploadProgress({ name, progress, onCancel, compact = false }: {
  name: string;
  progress: ExcelUploadProgress;
  onCancel: () => void;
  compact?: boolean;
}) {
  const label = progress.phase === 'uploading' ? 'Uploading workbook' : stageLabels[progress.stage];
  const percent = progress.phase === 'uploading' ? progress.percent : null;
  if (compact) {
    return <div className="upload-compact">
      <span className="upload-spinner" aria-hidden="true" />
      <strong title={name}>{name}</strong>
      <span className="upload-compact-stage" role="status">{label}…</span>
      <button type="button" onClick={onCancel}>Cancel</button>
    </div>;
  }
  return <div className="upload-card">
    <svg className="upload-card-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h4" /></svg>
    <div className="upload-card-body">
      <div className="upload-card-label"><strong title={name}>{name}</strong><span role="status">{label}…</span></div>
      <div className={`upload-card-track${percent === null ? ' is-indeterminate' : ''}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} {...(percent === null ? {} : { 'aria-valuenow': percent })}>
        <div style={percent === null ? undefined : { width: `${percent}%` }} />
      </div>
    </div>
    <button type="button" onClick={onCancel}>Cancel</button>
  </div>;
}
