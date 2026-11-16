import { ChangeEvent, CSSProperties, Fragment, useEffect, useId, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ReportThumbnail } from './components/ReportThumbnail';
import { UploadProgress } from './components/UploadProgress';
import { MoreDataSources } from './components/MoreDataSources';
import {
  getConversationMemory,
  clearConversationMemory,
  startNewChatSession,
  getCurrentIdentity,
  listSavedReports,
  getSavedReportConversation,
  renameSavedConversation,
  deleteSavedConversation,
  DataTable,
  AIStatus,
  CurrentIdentity,
  PlanClarification,
  ReportPreview,
  ExcelUploadProgress,
  ReportPlan,
  SavedReport,
  downloadReport,
  getAIStatus,
  generateReport,
  repairReport,
  openReportInPowerBI,
  planReport,
  previewReport,
  uploadExcel,
  confirmExcelTable,
  askData,
  getKpiSuggestions,
  AnalysisAnswer,
  editReport,
  routeAgent,
  getWorkbookUnderstanding,
  getRelationships,
  getPreparationSuggestions,
  beginAgentRun,
  getActiveAgentTraceId,
  endAgentRun,
  approveReport,
  discoverSqlSchema,
  materializeSqlSource,
  testSqlConnection,
  AgentRun,
  approveAgentExecution,
  resumeAgentExecution,
  cancelAgentExecution,
  startAgentExecution,
  listAgentRunHistory,
  getAgentRunAudit,
  AgentRunAudit,
  AgentRunHistoryItem,
  linkAgentRunReport,
} from './services/api';

type Validation = {
  passed: boolean;
  errors: string[];
  warnings: string[];
  calculationAccuracy?: {
    passed: boolean;
    engine: string;
    checks: Array<{ type: string; passed: boolean }>;
    errors: string[];
  };
};

export function EvidenceValue({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <>—</>;
  if (Array.isArray(value)) return <>{value.map((item, index) => <Fragment key={index}><EvidenceValue value={item} />{index < value.length - 1 ? ', ' : null}</Fragment>)}</>;
  if (typeof value === 'object') return <dl className="evidence-fields">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{humanizeFieldName(key)}</dt><dd><EvidenceValue value={item} /></dd></div>)}</dl>;
  return <>{typeof value === 'number' && Number.isSafeInteger(value) ? new Intl.NumberFormat().format(value) : String(value)}</>;
}

function ReportReadyCard() {
  return <div className="report-ready-card"><strong><span aria-hidden="true">✓</span> PBIP is ready</strong><ul><li>✓ Generating the PBIP project</li><li>✓ Validating the project</li></ul><div>Validation passed</div></div>;
}

function AuraLogoMark({ compact = false }: { compact?: boolean }) {
  const gradientId = `aura-orbit-${useId().replace(/:/g, '')}`;

  return (
    <svg className={compact ? 'aura-logo-mark is-compact' : 'aura-logo-mark'} width={compact ? '29' : '71'} height={compact ? '29' : '71'} viewBox="0 0 64 64" aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} gradientUnits="userSpaceOnUse" x1="52.293741908591755" y1="15.681733137912559" x2="7.730674415730677" y2="45.852278429270505">
          <stop offset="0" stopColor="#F40000" />
          <stop offset=".55" stopColor="#F08A4B" />
          <stop offset="1" stopColor="#EDD382" stopOpacity=".55" />
        </linearGradient>
      </defs>
      <rect x="18" y="18" width="28" height="28" rx="8" fill="var(--fg)" />
      <rect x="24" y="32" width="4" height="8" rx="1.2" fill="var(--surface)" />
      <rect x="30" y="28" width="4" height="12" rx="1.2" fill="var(--surface)" />
      <rect x="36" y="24" width="4" height="16" rx="1.2" fill="var(--surface)" />
      <path d="M52.91 13.47L53.92 13.86L54.86 14.32L55.74 14.85L56.54 15.46L57.26 16.14L57.87 16.90L58.38 17.73L58.77 18.62L59.04 19.54L59.19 20.50L59.22 21.47L59.14 22.46L58.96 23.44L58.69 24.44L58.32 25.44L57.87 26.44L57.34 27.44L56.72 28.45L56.04 29.46L55.27 30.47L54.44 31.48L53.53 32.49L52.56 33.50L51.53 34.50L50.44 35.48L49.29 36.46L48.08 37.42L46.83 38.37L45.53 39.29L44.19 40.20L42.81 41.07L41.40 41.92L39.96 42.73L38.50 43.52L37.02 44.26L35.52 44.97L34.02 45.63L32.51 46.25L31.00 46.83L29.49 47.36L27.99 47.84L26.51 48.27L25.05 48.64L23.61 48.96L22.20 49.23L20.83 49.44L19.49 49.60L18.19 49.69L16.94 49.73L15.74 49.71L14.60 49.63L13.50 49.49L12.47 49.29L11.51 49.03L10.61 48.71L9.78 48.33L9.03 47.89L8.35 47.39L7.76 46.83L7.26 46.22A0.60 0.60 0 0 1 8.20 45.48L8.20 45.48L8.64 45.95L9.14 46.37L9.72 46.75L10.38 47.08L11.10 47.37L11.90 47.61L12.77 47.80L13.71 47.93L14.71 48.02L15.78 48.05L16.89 48.02L18.06 47.94L19.28 47.80L20.55 47.61L21.85 47.37L23.18 47.07L24.55 46.71L25.94 46.30L27.35 45.85L28.77 45.34L30.21 44.79L31.64 44.19L33.08 43.54L34.51 42.86L35.94 42.13L37.34 41.37L38.72 40.57L40.08 39.74L41.41 38.89L42.70 38.01L43.96 37.10L45.16 36.18L46.32 35.24L47.43 34.29L48.47 33.33L49.46 32.37L50.38 31.41L51.23 30.45L52.02 29.50L52.72 28.56L53.35 27.64L53.90 26.74L54.37 25.86L54.76 25.02L55.06 24.22L55.28 23.47L55.43 22.76L55.49 22.11L55.49 21.52L55.43 21.00L55.31 20.53L55.14 20.11L54.93 19.73L54.67 19.39L54.35 19.08L53.96 18.78L53.51 18.51L52.98 18.27L52.37 18.06L51.68 17.90Z" fill={`url(#${gradientId})`} stroke="#EEEADF" strokeWidth="2.6" paintOrder="stroke" />
      <path d="M52.293741908591755 4.681733137912559C52.293741908591755 12.16173313791256 55.81374190859176 15.681733137912559 63.293741908591755 15.681733137912559C55.81374190859176 15.68173313791256 52.293741908591755 19.20173313791256 52.293741908591755 26.68173313791256C52.293741908591755 19.20173313791256 48.77374190859175 15.681733137912559 41.293741908591755 15.681733137912559C48.77374190859175 15.681733137912559 52.293741908591755 12.16173313791256 52.293741908591755 4.681733137912559Z" fill="#F40000" stroke="#EEEADF" strokeWidth="3" paintOrder="stroke" />
    </svg>
  );
}

function DarLogo() {
  return (
    <svg className="dar-logo" width="181" height="60" viewBox="0 0 181 60" fill="none" aria-hidden="true">
      <path d="M102.922 59.6003H118.416L107.169 33.0054C105.395 28.8092 101.281 26.0825 96.7249 26.0825H89.696L102.922 59.6003Z" fill="black" />
      <path d="M74.8062 5.83906L53.7696 59.5993H69.2637L84.7947 17.1878C85.3423 15.6925 86.764 14.6981 88.3564 14.6981H98.8079C100.462 14.6981 105.933 13.1475 109.203 10.1586C112.134 7.47895 114.4 2.12622 114.4 1.41748L81.2741 1.41748C78.4104 1.41748 75.8497 3.17231 74.8062 5.83906Z" fill="black" />
      <path d="M43.8028 5.38397C39.1078 2.73964 33.4953 1.41748 26.9655 1.41748H1.87019C0.938094 1.41748 0.146719 2.09571 0.0749666 3.02504C-0.0674219 4.86925 -0.105004 7.8847 0.884809 9.80036C3.57852 15.0137 15.147 14.5312 15.147 14.5312H26.8036C31.9303 14.5312 35.8698 16.0422 38.6221 19.0643C41.3743 22.0864 42.7505 25.9179 42.7505 30.559C42.7505 35.2001 41.3743 39.0316 38.6221 42.0537C35.8698 45.0758 31.9303 46.5868 26.8036 46.5868H18.9414C16.8458 46.5868 15.147 44.888 15.147 42.7924V26.0259C15.147 23.9303 13.4481 22.2314 11.3525 22.2314H0.252395V59.7005H26.9655C33.4953 59.7005 39.1078 58.4054 43.8028 55.815C48.5518 53.1707 52.1405 49.6359 54.569 45.2107C57.0514 40.7855 58.2926 35.9016 58.2926 30.559C58.2926 25.2164 57.0514 20.3325 54.569 15.9073C52.1405 11.4821 48.5518 7.97432 43.8028 5.38397Z" fill="black" />
      <path d="M156.762 1.41748C160.809 1.41752 164.317 2.25385 167.285 3.92675C170.253 5.59967 172.493 7.83931 174.004 10.6455C175.569 13.4516 176.351 16.5547 176.351 19.9544C176.351 23.84 175.353 27.3212 173.356 30.3972C171.413 33.4192 168.661 35.6051 165.099 36.9542L180.965 59.7006H164.902C164.267 59.7006 163.674 59.383 163.322 58.8544L150.282 39.2573C149.93 38.7287 149.338 38.4111 148.703 38.4111H140.203C139.155 38.4111 138.305 39.2605 138.305 40.3083V57.8034C138.305 58.8512 137.456 59.7006 136.408 59.7006H124.913C124.913 59.7006 124.913 46.3187 124.913 38.4111C124.913 37.3872 126.515 31.6258 129.656 28.9274C132.399 26.571 137.218 25.6214 138.305 25.6214C144.759 25.6214 154.819 25.6214 154.819 25.6214C156.762 25.6214 158.3 25.1083 159.433 24.083C160.62 23.0577 161.214 21.6813 161.214 19.9544C161.214 18.2817 160.62 16.9326 159.433 15.9074C158.3 14.882 156.762 14.3696 154.819 14.3696H138.305C138.305 14.3696 134.324 14.3696 129.264 8.67786C126.62 6.23101 123.573 1.41748 123.573 1.41748H156.762Z" fill="black" />
      <path d="M118.974 0C118.974 11.2209 128.071 20.3173 139.291 20.3173C128.071 20.3173 118.974 29.4136 118.974 40.6345C118.974 29.4136 109.878 20.3173 98.6569 20.3173C109.878 20.3173 118.974 11.2209 118.974 0Z" fill="#F40000" />
    </svg>
  );
}

function visualTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    clusteredColumnChart: 'Clustered Column Chart',
    clusteredBarChart: 'Clustered Bar Chart',
    stackedColumnChart: 'Stacked Column Chart',
    stackedBarChart: 'Stacked Bar Chart',
    hundredPercentStackedColumnChart: '100% Stacked Column Chart',
    hundredPercentStackedBarChart: '100% Stacked Bar Chart',
    lineChart: 'Line Chart',
    areaChart: 'Area Chart',
    stackedAreaChart: 'Stacked Area Chart',
    pieChart: 'Pie Chart',
    donutChart: 'Donut Chart',
    lineClusteredColumnComboChart: 'Combo Chart',
    scatterChart: 'Scatter Chart',
    waterfallChart: 'Waterfall Chart',
    funnel: 'Funnel Chart',
    treemap: 'Treemap',
    gauge: 'Gauge',
    table: 'Table',
    map: 'Map',
    filledMap: 'Filled Map',
    ribbonChart: 'Ribbon Chart',
    decompositionTree: 'Decomposition Tree',
    card: 'KPI Card',
    multiRowCard: 'Multi-row Card',
    kpi: 'KPI',
    pivotTable: 'Matrix',
    slicer: 'Slicer',
  };
  return labels[type] || type.replace(/([A-Z])/g, ' $1').trim();
}

function humanizeFieldName(name: string): string {
  return name
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim();
}

const supportedPreviewTypes = new Set([
  'card', 'multiRowCard', 'kpi', 'gauge', 'slicer', 'pivotTable', 'table',
  'pieChart', 'donutChart', 'lineChart', 'areaChart', 'stackedAreaChart',
  'clusteredBarChart', 'clusteredColumnChart', 'stackedBarChart', 'stackedColumnChart',
  'hundredPercentStackedBarChart', 'hundredPercentStackedColumnChart',
  'lineClusteredColumnComboChart', 'lineStackedColumnComboChart', 'scatterChart',
  'waterfallChart', 'funnel', 'treemap', 'map', 'filledMap', 'ribbonChart', 'decompositionTree',
]);

function runStatusLabel(status: AgentRun['status']): string {
  return {
    running: 'Running',
    awaiting_approval: 'Awaiting approval',
    completed: 'Completed',
    needs_user: 'Needs your input',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }[status];
}

function auditEventLabel(event: string): string {
  const labels: Record<string, string> = {
    planned: 'Plan created',
    approved: 'Plan approved',
    approval_invalidated: 'Approval invalidated',
    user_guidance_received: 'User guidance received',
    user_guidance_rejected: 'Guidance did not match an available option',
    step_started: 'Step started',
    step_completed: 'Step completed',
    step_failed: 'Step failed',
    step_repair: 'Step retry',
    user_help_required: 'Waiting for user input',
    run_completed: 'Run completed',
    run_failed: 'Run failed',
    run_cancelled: 'Run cancelled',
    cancellation_requested: 'Cancellation requested',
    trace_linked: 'Trace linked',
    report_linked: 'Report linked',
    source_context_changed: 'Source context updated',
  };
  return labels[event] || humanizeFieldName(event);
}

function formatAuditTimestamp(value?: string): string {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatAuditDuration(start?: string, end?: string): string {
  if (!start || !end) return 'Not recorded';
  const milliseconds = new Date(end).getTime() - new Date(start).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return 'Not recorded';
  const seconds = Math.floor(milliseconds / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function visualBindings(visual: ReportPlan['visuals'][number]): Array<[string, string]> {
  const fieldName = (field: { field: string } | undefined) => humanizeFieldName(field?.field || '');
  const extras: Array<[string,string]> = [];
  for (const key of ['target','minimum','maximum','trend','size'] as const) {
    if (visual[key]) extras.push([humanizeFieldName(key),fieldName(visual[key])]);
  }
  if (visual.tooltips?.length) extras.push(['Tooltips',visual.tooltips.map(fieldName).join(', ')]);
  if (visual.columns?.length) extras.push(['Columns',visual.columns.map(fieldName).join(', ')]);
  if (['card', 'multiRowCard', 'kpi', 'gauge'].includes(visual.type)) return [['Value', fieldName(visual.value)],...extras];
  if (visual.type === 'slicer') return [['Filter field', fieldName(visual.field)]];
  if (visual.type === 'pivotTable' || visual.type === 'table') return [
    ['Rows', (visual.rows || []).map(fieldName).join(', ')],
    ['Values', (visual.values || []).map(fieldName).join(', ')],
    ...extras,
  ];
  return [
    ...extras,
    ['Category', (visual.categoryHierarchy || (visual.category ? [visual.category] : [])).map(fieldName).join(' → ')],
    [visual.comparison ? 'Measures' : 'Value', (visual.values || (visual.value ? [visual.value] : [])).map(fieldName).join(', ')],
    ...([visual.legend, ...(visual.legendBreakdown || [])].filter(Boolean).length > 0
      ? [['Legend', [visual.legend, ...(visual.legendBreakdown || [])].filter(Boolean).map((field) =>
        fieldName(field),
      ).join(', ')] as [string, string]]
      : []),
  ];
}

function App() {
  const [startup, setStartup] = useState<'pending' | 'ready' | 'failed'>('pending');
  const [startupError, setStartupError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const resetPromise = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let active = true;
    if (!resetPromise.current) {
      resetPromise.current = (async () => {
        const key = 'autobi_last_source:' + (sessionStorage.getItem('autobi_user') || 'demo-user');
        const previousSource = sessionStorage.getItem(key);
        if (previousSource) {
          try {
            await clearConversationMemory(previousSource);
          } catch (error) {
            // A browser session can outlive its in-memory backend source (for
            // example after a backend restart). That stale ID is already
            // cleared from the user's perspective, so it must not block a
            // fresh workspace.
            if (!(error instanceof Error) || !/source not found/i.test(error.message)) throw error;
          }
          sessionStorage.removeItem(key);
        }
        startNewChatSession();
      })();
    }
    resetPromise.current.then(() => {
      if (active) setStartup('ready');
    }).catch((resetError: unknown) => {
      if (active) {
        setStartupError(resetError instanceof Error ? resetError.message : 'Unknown error');
        setStartup('failed');
      }
    });
    return () => { active = false; };
  }, [attempt]);

  if (startup !== 'ready') {
    return <main className="app-shell" role="status" aria-live="polite">
      {startup === 'pending' ? <p>Starting a fresh workspace…</p> : <>
        <p>Could not clear the previous conversation. The workspace has not been reset: {startupError}</p>
        <button className="button button-dark" type="button" onClick={() => {
          resetPromise.current = null;
          setStartup('pending');
          setAttempt(current => current + 1);
        }}>Retry reset</button>
      </>}
    </main>;
  }
  return <Workspace />;
}

function Workspace() {
  type View = 'home' | 'new-report' | 'search' | 'published';
  const [sourceId, setSourceId] = useState('');
  const [tables, setTables] = useState<DataTable[]>([]);
  const [formulaCellCount, setFormulaCellCount] = useState<number | null>(null);
  const [selectedTable, setSelectedTable] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [savedSourceName, setSavedSourceName] = useState('');
  const [requestText, setRequestText] = useState('');
  const [plan, setPlan] = useState<ReportPlan | null>(null);
  const [planHistory, setPlanHistory] = useState<ReportPlan[]>([]);
  const [planHistoryIndex, setPlanHistoryIndex] = useState(-1);
  const [pageIndex, setPageIndex] = useState(0);
  const [draftSuperseded, setDraftSuperseded] = useState(false);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [previewMessage, setPreviewMessage] = useState('');
  const [previewFilters, setPreviewFilters] = useState<Record<string, string[]>>({});
  const [syncedPreviewFilters, setSyncedPreviewFilters] = useState<Record<string, string[]>>({});
  const [planApproved, setPlanApproved] = useState(false);
  const [reportId, setReportId] = useState('');
  const [validation, setValidation] = useState<Validation | null>(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [clarification, setClarification] = useState<PlanClarification | null>(null);
  const [analysisQuestion, setAnalysisQuestion] = useState('Why are we below budget?');
  const [analysisAnswer, setAnalysisAnswer] = useState<AnalysisAnswer | null>(null);
  const [kpis, setKpis] = useState<Array<{ name: string; table: string; formula: string; dax: string; explanation: string }>>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatMessages, setChatMessages] = useState<Array<{ role: 'user' | 'assistant'; text: string; evidence?: AnalysisAnswer }>>([]);
  const threadRef = useRef<HTMLDivElement>(null);
  const followThreadRef = useRef(true);
  const [newQuestion, setNewQuestion] = useState(false);
  const [sourceType, setSourceType] = useState<'excel' | 'sql'>('excel');
  const [operation, setOperation] = useState('');
  const [clarificationRequest, setClarificationRequest] = useState('');
  const [approvedKpis, setApprovedKpis] = useState<string[]>([]);
  const [sql, setSql] = useState({ server: '', database: '', authentication: 'sql' as 'sql' | 'windows', username: '', password: '' });
  const [sqlConnectionId, setSqlConnectionId] = useState('');
  const [sqlPendingSourceId, setSqlPendingSourceId] = useState('');
  const [sqlDiscoveredTables, setSqlDiscoveredTables] = useState<DataTable[]>([]);
  const [sqlDialogOpen, setSqlDialogOpen] = useState(false);
  const [sqlSelectedTables, setSqlSelectedTables] = useState<string[]>([]);
  const [sqlApprovedMeasures, setSqlApprovedMeasures] = useState<string[]>([]);
  const [artifactState, setArtifactState] = useState<'empty' | 'draft' | 'changed' | 'generated' | 'failed'>('empty');
  const [lastOperation, setLastOperation] = useState('');
  const [mappingResult, setMappingResult] = useState<Array<{ table: string; fields: Array<{ field: string; role: string }> }>>([]);
  const [relationshipResult, setRelationshipResult] = useState<Array<{ source_table: string; source_column: string; target_table: string; target_column: string }>>([]);
  const [agentExecution, setAgentExecution] = useState<AgentRun | null>(null);
  const [auditNotice, setAuditNotice] = useState('');
  const [runHistoryOpen, setRunHistoryOpen] = useState(false);
  const [runHistory, setRunHistory] = useState<AgentRunHistoryItem[]>([]);
  const [runHistoryHasMore, setRunHistoryHasMore] = useState(false);
  const [runHistoryLoading, setRunHistoryLoading] = useState(false);
  const [runHistoryError, setRunHistoryError] = useState('');
  const [savedReports, setSavedReports] = useState<SavedReport[]>([]);
  const [savedReportsLoading, setSavedReportsLoading] = useState(false);
  const [savedReportsError, setSavedReportsError] = useState('');
  const [recentMenu, setRecentMenu] = useState<string | null>(null);
  const [renamingRecent, setRenamingRecent] = useState<string | null>(null);
  const [recentName, setRecentName] = useState('');
  const [recentBusy, setRecentBusy] = useState<string | null>(null);
  const [recentError, setRecentError] = useState('');
  const [identity, setIdentity] = useState<CurrentIdentity | null>(null);
  const [identityError, setIdentityError] = useState('');
  const [selectedAudit, setSelectedAudit] = useState<AgentRunAudit | null>(null);
  const [selectedAuditLoading, setSelectedAuditLoading] = useState(false);
  const [selectedAuditError, setSelectedAuditError] = useState('');
  const [planValidated, setPlanValidated] = useState(false);
  const [currentView, setCurrentView] = useState<View>('home');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarSearchOpen, setSidebarSearchOpen] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const [accountDialog, setAccountDialog] = useState<'Settings' | 'Data connections' | 'Help & feedback' | null>(null);
  const [uploadProgress, setUploadProgress] = useState<ExcelUploadProgress | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [savedReportsNotice, setSavedReportsNotice] = useState('');
  const operationController = useRef<AbortController | null>(null);
  const previewController = useRef<AbortController | null>(null);
  const previewSequence = useRef(0);
  const memorySequence = useRef(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sqlDialogRef = useRef<HTMLDivElement>(null);
  const accountDialogRef = useRef<HTMLDivElement>(null);
  const recentMenuRef = useRef<HTMLUListElement>(null);
  const sqlDialogBusy = useRef(isLoading);
  sqlDialogBusy.current = isLoading;
  const chatSubmissionLock = useRef(false);
  const appliedAgentSteps = useRef(new Set<string>());

  const recordPlan = (nextPlan: ReportPlan, reset = false) => {
    setPlan(nextPlan);
    if (reset) {
      setPlanHistory([nextPlan]);
      setPlanHistoryIndex(0);
      return;
    }
    setPlanHistory(current => {
      const next = [...current.slice(0, planHistoryIndex + 1), nextPlan];
      setPlanHistoryIndex(next.length - 1);
      return next;
    });
  };

  const restorePlanHistory = (direction: -1 | 1) => {
    const nextIndex = planHistoryIndex + direction;
    if (nextIndex < 0 || nextIndex >= planHistory.length) return;
    setPlan(planHistory[nextIndex]);
    setPlanHistoryIndex(nextIndex);
    setPageIndex(0);
    setPreview(null);
    setPreviewFilters({});
    setSyncedPreviewFilters({});
    setPlanApproved(false);
    setArtifactState('changed');
    setStatus(direction < 0 ? 'Restored the previous report draft.' : 'Restored the next report draft.');
    if (sourceId) {
      void previewReport(sourceId, planHistory[nextIndex]).then(setPreview).catch((error: unknown) => {
        setPreviewMessage(error instanceof Error ? error.message : 'Could not refresh the restored report preview.');
      });
    }
  };

  useEffect(() => {
    const thread = threadRef.current;
    if (thread && followThreadRef.current) thread.scrollTop = thread.scrollHeight;
  }, [chatMessages, preview, agentExecution?.status, reportId, currentView, operation, error, validation]);

  const refreshRunHistory = async () => {
    setRunHistoryLoading(true);
    setRunHistoryError('');
    try {
      const page = await listAgentRunHistory();
      setRunHistory(page.runs);
      setRunHistoryHasMore(page.hasMore);
    } catch (historyError) {
      setRunHistoryError(historyError instanceof Error ? historyError.message : 'Run history could not be loaded.');
    } finally {
      setRunHistoryLoading(false);
    }
  };

  const refreshSavedReports = async () => {
    setSavedReportsLoading(true);
    setSavedReportsError('');
    try {
      const page = await listSavedReports();
      setSavedReports(page.reports);
    } catch (reportsError) {
      setSavedReportsError(reportsError instanceof Error ? reportsError.message : 'Published reports could not be loaded.');
    } finally {
      setSavedReportsLoading(false);
    }
  };

  const refreshIdentity = async () => {
    setIdentityError('');
    try {
      setIdentity(await getCurrentIdentity());
    } catch (identityError) {
      setIdentityError(identityError instanceof Error ? identityError.message : 'Account identity could not be loaded.');
    }
  };

  const loadMoreRunHistory = async () => {
    if (runHistoryLoading || !runHistoryHasMore) return;
    setRunHistoryLoading(true);
    setRunHistoryError('');
    try {
      const page = await listAgentRunHistory(50, runHistory.length);
      setRunHistory((current) => [...current, ...page.runs]);
      setRunHistoryHasMore(page.hasMore);
    } catch (historyError) {
      setRunHistoryError(historyError instanceof Error ? historyError.message : 'Older run history could not be loaded.');
    } finally {
      setRunHistoryLoading(false);
    }
  };

  const openRunHistory = () => {
    setRunHistoryOpen(true);
    setSelectedAudit(null);
    setSelectedAuditError('');
    void refreshRunHistory();
  };

  const loadHistoricalAudit = async (runId: string) => {
    setSelectedAuditLoading(true);
    setSelectedAuditError('');
    try {
      setSelectedAudit(await getAgentRunAudit(runId));
    } catch (historyError) {
      setSelectedAuditError(historyError instanceof Error ? historyError.message : 'Run details could not be loaded.');
    } finally {
      setSelectedAuditLoading(false);
    }
  };

  useEffect(() => {
    void refreshRunHistory();
    void refreshSavedReports();
    void refreshIdentity();
  }, []);

  useEffect(() => {
    if (!sqlDialogOpen) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = sqlDialogRef.current;
    (dialog?.querySelector<HTMLElement>('input:not(:disabled)') || dialog?.querySelector<HTMLElement>('button:not(:disabled)'))?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !sqlDialogBusy.current) {
        event.preventDefault();
        setSqlDialogOpen(false);
        setSql((current) => ({ ...current, password: '' }));
        setError('');
        setStatus('');
      }
      if (event.key !== 'Tab' || !dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)'));
      if (!focusable.length) return;
      if (event.shiftKey && document.activeElement === focusable[0]) {
        event.preventDefault();
        focusable[focusable.length - 1].focus();
      } else if (!event.shiftKey && document.activeElement === focusable[focusable.length - 1]) {
        event.preventDefault();
        focusable[0].focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, [sqlDialogOpen]);

  useEffect(() => {
    if (!accountDialog) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = accountDialogRef.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setAccountDialog(null);
      } else if (event.key === 'Tab' && dialog) {
        const buttons = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
        if (buttons.length && event.shiftKey && document.activeElement === buttons[0]) {
          event.preventDefault();
          buttons[buttons.length - 1].focus();
        } else if (buttons.length && !event.shiftKey && document.activeElement === buttons[buttons.length - 1]) {
          event.preventDefault();
          buttons[0].focus();
        }
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { document.removeEventListener('keydown', onKeyDown); previousFocus?.focus(); };
  }, [accountDialog]);

  useEffect(() => {
    if (!recentMenu) return;
    const dismiss = (event: MouseEvent) => {
      if (event.target instanceof Node && !recentMenuRef.current?.contains(event.target)) setRecentMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setRecentMenu(null);
    };
    document.addEventListener('mousedown', dismiss);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', escape); };
  }, [recentMenu]);

  const sidebarRecentRuns = savedReports
    .filter((report) => !report.chatDeleted && (report.chatTitle || report.title).toLowerCase().includes(searchQuery.trim().toLowerCase()))
    .slice(0, sidebarSearchOpen ? 12 : 4);
  const filteredRuns = runHistory.filter((run) =>
    `${run.reportName || ''} ${run.goal} ${run.sourceType || ''}`.toLowerCase().includes(searchQuery.trim().toLowerCase()),
  );
  const openRunFromList = (runId: string) => {
    setSidebarCollapsed(true);
    setCurrentView('new-report');
    setRunHistoryOpen(true);
    setSelectedAudit(null);
    setSelectedAuditError('');
    void loadHistoricalAudit(runId);
  };
  const profileLabel = identity ? (identity.isDemo ? 'Demo user' : identity.userId) : identityError ? 'Identity unavailable' : 'Loading account…';
  const profileInitials = identity
    ? (identity.isDemo ? 'DU' : identity.userId.split(/[.@_-]+/).filter(Boolean).map((part) => part[0]).join('').slice(0, 2).toUpperCase() || 'U')
    : '…';
  const saveRecentName = async (report: SavedReport, value: string) => {
    const title = value.trim();
    setRenamingRecent(null);
    if (!title || title === (report.chatTitle || report.title)) return;
    setRecentError('');
    setRecentBusy(report.reportId);
    try {
      const result = await renameSavedConversation(report.reportId, title);
      setSavedReports((current) => current.map((item) => item.reportId === report.reportId ? { ...item, chatTitle: result.title } : item));
    } catch (requestError) {
      setRecentError(requestError instanceof Error ? requestError.message : 'Could not rename the chat.');
    } finally {
      setRecentBusy(null);
    }
  };
  const deleteRecent = async (report: SavedReport) => {
    setRecentMenu(null);
    if (!window.confirm(`Delete the chat "${report.chatTitle || report.title}"? The published report and PBIP will be kept.`)) return;
    setRecentError('');
    setRecentBusy(report.reportId);
    try {
      await deleteSavedConversation(report.reportId);
      setSavedReports((current) => current.map((item) => item.reportId === report.reportId ? { ...item, chatDeleted: true } : item));
      if (reportId === report.reportId) setChatMessages([{ role: 'assistant', text: 'This saved conversation was deleted. The published report is still available.' }]);
    } catch (requestError) {
      setRecentError(requestError instanceof Error ? requestError.message : 'Could not delete the chat.');
    } finally {
      setRecentBusy(null);
    }
  };
  const downloadSavedReport = async (report: SavedReport) => {
    setSavedReportsError('');
    setSavedReportsNotice('');
    try {
      await downloadReport(report.reportId);
    } catch (downloadError) {
      setSavedReportsError(downloadError instanceof Error ? downloadError.message : 'Report download failed.');
    }
  };
  const openSavedReport = async (report: SavedReport) => {
    if (!report.sourceId) {
      setSavedReportsError(`The data source for "${report.title}" is unavailable, so its preview cannot be reopened.`);
      return;
    }
    setSidebarSearchOpen(false);
    setSearchQuery('');
    setSidebarCollapsed(true);
    setCurrentView('new-report');
    setError('');
    setStatus('');
    clearGeneratedState();
    setSelectedFile(null);
    setSavedSourceName(report.sourceName || '');
    setSourceType(report.sourceType === 'sql' ? 'sql' : 'excel');
    setPreview(null);
    setPreviewMessage('');
    setPreviewFilters({});
    setSyncedPreviewFilters({});
    setSourceId(report.sourceId);
    setTables([]);
    setFormulaCellCount(null);
    setSelectedTable('');
    recordPlan(report.reportSpec, true);
    setDraftSuperseded(false);
    setPageIndex(0);
    setReportId(report.reportId);
    setValidation({ passed: true, errors: [], warnings: [] });
    setPlanApproved(true);
    setPlanValidated(true);
    setArtifactState('generated');
    setChatMessages([{ role: 'assistant', text: 'Loading saved conversation…' }]);
    setLastOperation('open saved report');
    setOperation('Opening saved report…');
    setIsLoading(true);
    const sequence = ++previewSequence.current;
    void getSavedReportConversation(report.reportId).then((history) => {
      if (sequence !== previewSequence.current) return;
      setChatMessages((current) => current.length === 1 && current[0].text === 'Loading saved conversation…'
        ? history.available ? history.conversation : [{ role: 'assistant', text: 'No conversation was saved for this report. You can ask about it or describe a change.' }]
        : current);
    }).catch((historyError: unknown) => {
      if (sequence !== previewSequence.current) return;
      setError(`Saved conversation could not be loaded: ${historyError instanceof Error ? historyError.message : 'Unknown error'}`);
      setChatMessages((current) => current.length === 1 && current[0].text === 'Loading saved conversation…' ? [] : current);
    });
    try {
      const reportPreview = await previewReport(report.sourceId, report.reportSpec);
      if (sequence !== previewSequence.current) return;
      setPreview(reportPreview);
      setStatus(`Opened ${report.title}.`);
    } catch (openError) {
      if (sequence !== previewSequence.current) return;
      setError(openError instanceof Error ? openError.message : 'Could not load the saved report preview.');
    } finally {
      if (sequence === previewSequence.current) {
        setOperation('');
        setIsLoading(false);
      }
    }
  };
  const openSavedReportInPowerBI = async (report: SavedReport) => {
    setSavedReportsError('');
    setSavedReportsNotice('');
    try {
      const result = await openReportInPowerBI(report.reportId);
      setSavedReportsNotice(result.message);
    } catch (openError) {
      setSavedReportsError(openError instanceof Error ? openError.message : 'Could not open Power BI Desktop.');
    }
  };

  const startOperation = (label: string) => {
    operationController.current?.abort();
    const controller = new AbortController();
    operationController.current = controller;
    setOperation(label);
    setLastOperation(label);
    setIsLoading(true);
    return controller.signal;
  };
  const finishOperation = () => {
    operationController.current = null;
    setOperation('');
    setIsLoading(false);
  };
  const cancelOperation = () => {
    if (agentExecution?.status === 'running') void cancelAgentExecution(agentExecution.runId).then(setAgentExecution).catch(() => undefined);
    operationController.current?.abort();
    operationController.current = null;
    previewController.current?.abort();
    setOperation('');
    setIsLoading(false);
    setStatus('Operation cancelled. You can retry when ready.');
  };

  const invalidateGeneratedReport = () => {
    setReportId('');
    setValidation(null);
    setPlanApproved(false);
    setArtifactState((current) => current === 'generated' || current === 'failed' ? 'changed' : current === 'empty' ? 'draft' : current);
    setPlanValidated(false);
  };

  const clearGeneratedState = () => {
    previewController.current?.abort();
    ++previewSequence.current;
    setPlan(null);
    setDraftSuperseded(false);
    setPageIndex(0);
    setPreview(null);
    setPreviewMessage('');
    setPreviewFilters({});
    setSyncedPreviewFilters({});
    setPlanApproved(false);
    setReportId('');
    setValidation(null);
    setClarification(null);
    setAnalysisAnswer(null);
    setKpis([]);
    setApprovedKpis([]);
    setArtifactState('empty');
    setMappingResult([]);
    setRelationshipResult([]);
    setAgentExecution(null);
    setAuditNotice('');
    setPlanValidated(false);
    setChatMessages([]);
    setNewQuestion(false);
    appliedAgentSteps.current.clear();
  };

  useEffect(() => {
    const sourceKey = 'autobi_last_source:' + (sessionStorage.getItem('autobi_user') || 'demo-user');
    if (!sourceId) { sessionStorage.removeItem(sourceKey); return; }
    sessionStorage.setItem(sourceKey, sourceId);
    let active = true;
    const sequence = ++memorySequence.current;
    getConversationMemory(sourceId).then(memory => {
      if (!active || sequence !== memorySequence.current) return;
      setTables(current => current.length ? current : memory.tables);
      setFormulaCellCount(memory.formulaCellCount);
      setChatMessages(current => current.length ? current : memory.conversation);
      if (memory.reportSpec) setPlan(current => current || memory.reportSpec);
    }).catch(() => { if (active && sequence === memorySequence.current) setError('Conversation memory could not be restored. Please retry before relying on earlier context.'); });
    return () => { active = false; };
  }, [sourceId]);

  const resetWorkspace = async (disconnectSource: boolean) => {
    if (isLoading) return 'cancelled';
    const confirmation = disconnectSource
      ? 'Reset the workspace? This clears the current conversation and draft and disconnects the data source. Saved reports and run history are kept.'
      : 'Start a new report? This clears the current conversation and draft, but keeps the connected data source. Saved reports and run history are kept.';
    if (!window.confirm(confirmation)) return 'cancelled';
    ++memorySequence.current;
    setIsLoading(true);
    setError('');
    setLastOperation('');
    try {
      if (sourceId) await clearConversationMemory(sourceId);
      clearGeneratedState();
      setChatInput('');
      setRequestText('');
      setClarificationRequest('');
      setAnalysisQuestion('Why are we below budget?');
      setStatus('');
      startNewChatSession();
      if (disconnectSource) {
        setSourceId('');
        sessionStorage.removeItem('autobi_last_source:' + (sessionStorage.getItem('autobi_user') || 'demo-user'));
        setTables([]);
        setFormulaCellCount(null);
        setSelectedTable('');
        setSelectedFile(null);
        setSavedSourceName('');
        if (fileInputRef.current) fileInputRef.current.value = '';
        setSourceType('excel');
        setSql({ server: '', database: '', authentication: 'sql', username: '', password: '' });
        setSqlConnectionId('');
        setSqlPendingSourceId('');
        setSqlDiscoveredTables([]);
        setSqlSelectedTables([]);
        setSqlApprovedMeasures([]);
      }
      return 'success';
    } catch {
      setError('Conversation memory could not be cleared. The workspace was not reset; please retry.');
      return 'failed';
    } finally {
      setIsLoading(false);
    }
  };

  const startNewReport = async () => {
    if (isLoading) return;
    if (plan || chatMessages.length || chatInput.trim() || requestText.trim() || clarification || agentExecution) {
      const result = await resetWorkspace(false);
      if (result !== 'success') return;
    }
    setSidebarCollapsed(false);
    setCurrentView('home');
  };

  const changeScope = async (nextTable: string) => {
    if (nextTable === selectedTable || isLoading) return;
    if (!window.confirm('Changing scope clears the current draft and conversation. Saved reports are kept. Continue?')) return;
    ++memorySequence.current;
    setIsLoading(true);
    setError('');
    try {
      if (sourceId) await clearConversationMemory(sourceId);
      clearGeneratedState();
      startNewChatSession();
      setSelectedTable(nextTable);
      setChatInput('');
      setRequestText('');
      setStatus('Scope changed. Start a new report or conversation.');
    } catch (scopeError) {
      setError(`Scope could not be changed: ${scopeError instanceof Error ? scopeError.message : 'Conversation memory could not be cleared.'}`);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    getAIStatus().then(setAiStatus).catch((statusError) => {
      setError(statusError instanceof Error ? statusError.message : 'AI status could not be loaded.');
    });
  }, []);

  const tableKey = (table: DataTable) => table.schema ? `${table.schema}.${table.name}` : table.name;
  const selectedModelTable = tables.find((table) => tableKey(table) === selectedTable);
  const sourceDetails = `${tables.length} ${tables.length === 1 ? 'table' : 'tables'}${sourceType === 'excel' && formulaCellCount !== null ? ` · ${formulaCellCount.toLocaleString()} formula ${formulaCellCount === 1 ? 'cell' : 'cells'}` : ''}`;

  const uploadWorkbook = async (file: File) => {
    setSelectedFile(file);
    setSavedSourceName('');
    setSourceId('');
    setTables([]);
    setFormulaCellCount(null);
    setSelectedTable('');
    clearGeneratedState();
    setError('');
    setStatus('');
    setUploadProgress({ phase: 'uploading', percent: null });
    const signal = startOperation('Inspecting workbook');
    try {
      const result = await uploadExcel(file, signal, (progress) => {
        if (!signal.aborted) setUploadProgress(progress);
      });
      setSourceType('excel');
      setSourceId(result.sourceId);
      setTables(result.sheets);
      setFormulaCellCount(result.formulaCellCount);
      // Leave the report scope open by default. The assistant can choose the
      // relevant table from the request; selecting a table below pins the run
      // to that table explicitly.
      setSelectedTable('');
      setAiStatus((current) => current ? { ...current, enabled: result.aiEnabled } : current);
      const uploadStatus = result.aiAnalyzed
        ? `Discovered ${result.sheets.length} table${result.sheets.length === 1 ? '' : 's'} and analyzed the workbook with AI.`
        : `Discovered ${result.sheets.length} table${result.sheets.length === 1 ? '' : 's'} from the workbook.`;
      setStatus([uploadStatus, ...result.warnings].join(' '));
    } catch (requestError) {
      if (!signal.aborted) setError(requestError instanceof Error ? requestError.message : 'Workbook upload failed');
    } finally {
      if (operationController.current?.signal === signal) {
        setUploadProgress(null);
        finishOperation();
      }
    }
  };

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file) await uploadWorkbook(file);
  };

  const handlePlan = async () => {
    if (!sourceId || !requestText.trim()) {
      setError('Connect a source and describe the report before planning.');
      return;
    }
    setError('');
    invalidateGeneratedReport();
    setClarification(null);
    setClarificationRequest('');
    setAgentExecution(null);
    appliedAgentSteps.current.clear();
    setPreview(null);
    setPreviewMessage('');
    if (aiStatus?.enabled) setStatus('AI is mapping your request to the uploaded workbook fields…');
    const signal = startOperation('Planning report');
    const agentRunId = beginAgentRun();
    try {
      const result = await planReport(sourceId, requestText, selectedTable || undefined, signal);
      if ('status' in result) {
        setClarification(result);
        setClarificationRequest(requestText);
        setPlan(null);
        setDraftSuperseded(false);
        setStatus('The request needs clarification before AURA can create a report plan.');
        return;
      }
      setClarification(null);
      const resolvedTable = result.planningEvidence?.resolvedTable;
      if (resolvedTable && tables.some((table) => tableKey(table) === resolvedTable)) {
        setSelectedTable(resolvedTable);
      }
      setPageIndex(0);
        recordPlan(result, true);
      setDraftSuperseded(false);
      setPlanValidated(true);
      setChatMessages((current) => [...current, {
        role: 'assistant',
        text: `I've drafted ${result.page}. Check the preview and tell me what you'd like to change.`,
      }]);
      setStatus(aiStatus?.enabled
        ? 'AI plan created and validated. Review the resolved fields before generation.'
        : 'Plan created. Review the resolved fields before generation.');
      try {
        setPreview(await previewReport(sourceId, result, {}, signal));
      } catch (previewError) {
        if (!signal.aborted) setPreviewMessage(previewError instanceof Error ? previewError.message : 'Dashboard preview could not be loaded.');
      }
    } catch (requestError) {
      if (!signal.aborted) {
        setDraftSuperseded(true);
        setError(requestError instanceof Error ? requestError.message : 'Report planning failed');
      }
    } finally {
      endAgentRun(agentRunId);
      if (operationController.current?.signal === signal) finishOperation();
    }
  };

  const handleConfirmTable = async () => {
    const review = selectedModelTable?.parsingReview;
    if (!review) return;
    setIsLoading(true);
    setError('');
    try {
      await confirmExcelTable(sourceId, selectedTable, review.range, review.headerRow);
      setTables((current) => current.map((table) =>
        table.parsingReview?.range === review.range && table.sourceSheet === selectedModelTable?.sourceSheet
          ? { ...table, parsingReview: { ...table.parsingReview, confirmed: true } } : table));
      setStatus('Detected table range and header confirmed. You can create a report plan.');
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : 'Table confirmation failed.');
    } finally {
      setIsLoading(false);
    }
  };

  const updatePreviewFilter = async (field: string, selected: string[]) => {
    if (!sourceId || !plan) return;
    const filters = { ...previewFilters, [field]: selected };
    const slicer = currentPage?.visuals.find((visual) => visual.type === 'slicer' && visual.field && `${visual.field.table}.${visual.field.field}` === field);
    const syncedFilters = slicer?.syncGroup ? { ...syncedPreviewFilters, [slicer.syncGroup]: selected } : syncedPreviewFilters;
    setPreviewFilters(filters);
    setSyncedPreviewFilters(syncedFilters);
    previewController.current?.abort();
    const controller = new AbortController();
    previewController.current = controller;
    const sequence = ++previewSequence.current;
    try {
      const result = await previewReport(sourceId, plan, filters, controller.signal, currentPage?.page, syncedFilters);
      if (sequence === previewSequence.current) {
        setPreview(result);
        setPreviewMessage('');
      }
    } catch (previewError) {
      if (!controller.signal.aborted && sequence === previewSequence.current) setPreviewMessage(previewError instanceof Error ? previewError.message : 'Dashboard preview could not be updated.');
    }
  };

  const retryPreview = async () => {
    if (!sourceId || !plan) return;
    setPreviewMessage('');
    const signal = startOperation('Loading report preview');
    try {
      setPreview(await previewReport(sourceId, plan, previewFilters, signal, currentPage?.page, syncedPreviewFilters));
    } catch (previewError) {
      if (!signal.aborted) setPreviewMessage(previewError instanceof Error ? previewError.message : 'Dashboard preview could not be loaded.');
    } finally {
      if (operationController.current?.signal === signal) finishOperation();
    }
  };

  const handleGenerate = async () => {
    if (!sourceId || !plan || !planValidated || !planApproved) return;
    setError('');
    const signal = startOperation('Generating report');
    const agentRunId = beginAgentRun();
    const traceId = getActiveAgentTraceId();
    try {
      const userMessages = chatMessages.filter((message) => message.role === 'user');
      const result = await generateReport(sourceId, plan, {
        userRequest: agentExecution?.goal || userMessages[userMessages.length - 1]?.text || requestText,
        conversation: chatMessages.slice(-50).map(({role,text}) => ({role,text:text.slice(0,4000)})),
      }, signal);
      setReportId(result.reportId);
      void refreshSavedReports();
      setValidation(result.validation);
      setArtifactState(result.validation.passed ? 'generated' : 'failed');
      setStatus(result.validation.passed ? 'Report generated and validated successfully.' : 'Report generated, but validation needs attention.');
      setChatMessages((current) => [...current, {
        role: 'assistant',
        text: result.validation.passed
          ? 'The PBIP project is generated and validated. You can open it in Power BI Desktop on this machine or download the project archive.'
          : 'The PBIP project was generated, but validation found issues. Review the validation details before opening or downloading it.',
      }]);
      if (!result.validation.passed) setError(result.validation.errors.join('; ') || 'Report validation failed');
      if (agentExecution) {
        try {
          setAgentExecution(await linkAgentRunReport(agentExecution.runId, result.reportId, plan.page, traceId || undefined));
        } catch (auditError) {
          const message = auditError instanceof Error ? auditError.message : 'The generated report could not be linked to run history.';
          setStatus((current) => `${current} Run history update failed: ${message}`);
        }
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Report generation failed');
    } finally {
      endAgentRun(agentRunId);
      finishOperation();
      void refreshRunHistory();
    }
  };

  const handleRepair = async () => {
    if (!sourceId || !reportId || !plan || !planValidated || validation?.passed !== false) return;
    setError('');
    const signal = startOperation('Preparing report fix');
    try {
      const result = await repairReport(reportId, plan, signal);
      const revisedPreview = await previewReport(sourceId, result.reportSpec, {}, signal);
      if (signal.aborted) return;
      invalidateGeneratedReport();
      recordPlan(result.reportSpec);
      setDraftSuperseded(false);
      setPageIndex(0);
      setPreview(revisedPreview);
      setPreviewFilters({});
      setSyncedPreviewFilters({});
      setPreviewMessage('');
      setPlanValidated(true);
      const message = `I prepared a fix:\n${result.changes.join('\n')}\nReview and approve the revised plan before generating a new PBIP.`;
      setStatus('Review and approve the proposed fix before generating a new PBIP.');
      setChatMessages((current) => [...current, { role: 'assistant', text: message }]);
    } catch (requestError) {
      if (!signal.aborted) setError(requestError instanceof Error ? requestError.message : 'Could not prepare a report fix.');
    } finally {
      if (operationController.current?.signal === signal) finishOperation();
    }
  };

  const handleApprovePlan = async () => {
    if (!sourceId || !plan || !planValidated || !preview) return;
    setError('');
    const signal = startOperation('Approving report plan');
    try {
      await approveReport(sourceId, plan, 'web-user', signal);
      setPlanApproved(true);
      setStatus('Report plan approved. Generate the PBIP when ready.');
      setChatMessages((current) => [...current, {
        role: 'assistant',
        text: 'The report plan is approved. You can now generate the PBIP.',
      }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Report approval failed');
    } finally {
      finishOperation();
    }
  };

  const handleAskData = async () => {
    if (!sourceId || !analysisQuestion.trim()) return;
    setError('');
    setIsLoading(true);
    try {
      setAnalysisAnswer(await askData(sourceId, analysisQuestion));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Analysis could not be completed.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleKpis = async () => {
    if (!sourceId) return;
    setError('');
    setIsLoading(true);
    try {
      setKpis((await getKpiSuggestions(sourceId)).suggestions);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'KPI suggestions could not be loaded.');
    } finally { setIsLoading(false); }
  };

  const applyAgentExecution = async (run: AgentRun, signal: AbortSignal) => {
    for (const step of run.steps.filter((item) => item.status === 'completed')) {
      const stepKey = `${run.runId}:${step.index}`;
      if (appliedAgentSteps.current.has(stepKey)) continue;
      appliedAgentSteps.current.add(stepKey);
      const result = step.result || {};
      if (step.action === 'respond') setChatMessages((current) => [...current, { role: 'assistant', text: String(result.message || 'How can I help?') }]);
      else if (step.action === 'clarify') setChatMessages((current) => [...current, { role: 'assistant', text: String(result.question || 'What should I change?') }]);
      else if (step.action === 'answer_data_question' || step.action === 'answer_schema' || step.action === 'investigate_data') {
        const answer = result as unknown as AnalysisAnswer;
        setAnalysisAnswer(answer);
        const followUp = answer.nextStep ? ` ${answer.nextStep}` : '';

        setChatMessages((current) => [...current, { role: 'assistant', text: `${answer.answer}${followUp}`, evidence: answer }]);
      } else if (step.action === 'suggest_kpis') {
        const suggestions = (result.suggestions || []) as typeof kpis;
        setKpis(suggestions);
        setChatMessages((current) => [...current, { role: 'assistant', text: `I found ${suggestions.length} KPI ${suggestions.length === 1 ? 'idea' : 'ideas'} grounded in the available measures. You can review and approve them below.` }]);
      } else if (step.action === 'understand_workbook') {
        const tablesResult = (result.tables || []) as typeof mappingResult;
        setMappingResult(tablesResult);
        setChatMessages((current) => [...current, { role: 'assistant', text: typeof result.assistantMessage === 'string' ? result.assistantMessage : 'I reviewed the workbook structure and mapped the fields by their likely business role. I’ve summarized the mappings below for you to check.' }]);
      } else if (step.action === 'inspect_relationships') {
        setRelationshipResult((result.relationships || []) as typeof relationshipResult);
        setChatMessages((current) => [...current, { role: 'assistant', text: 'I checked how the tables relate to one another. The relationships I found are summarized below.' }]);
      } else if (step.action === 'prepare_data') {
        setChatMessages((current) => [...current, { role: 'assistant', text: String(result.proposal || 'Preparation proposal is ready.') }]);
      } else if (step.action === 'plan_dashboard') {
        const reportPlan = result as unknown as ReportPlan;
        invalidateGeneratedReport();
        setPageIndex(0);
        recordPlan(reportPlan, true);
        setDraftSuperseded(false);
        setPlanValidated(true);
        setChatMessages((current) => [...current, { role: 'assistant', text: result.assistantMessage ? String(result.assistantMessage) : `I’ve drafted a report with ${(reportPlan.additionalPages || []).reduce((n, p) => n + p.visuals.length, reportPlan.visuals.length)} ${!reportPlan.additionalPages && reportPlan.visuals.length === 1 ? 'visual' : 'visuals'} across ${1 + (reportPlan.additionalPages?.length || 0)} ${(reportPlan.additionalPages?.length || 0) === 0 ? 'page' : 'pages'}. Take a look at the preview, and tell me what you’d like to change.` }]);
        try {
          setPreview(await previewReport(sourceId, reportPlan, {}, signal));
          setPreviewMessage('');
        } catch (previewError) {
          if (!signal.aborted) setPreviewMessage(previewError instanceof Error ? `The report plan is ready, but the preview could not be refreshed: ${previewError.message}` : 'The report plan is ready, but the preview could not be refreshed.');
        }
      } else if (step.action === 'edit_report') {
        const revised = result.reportSpec as ReportPlan;
        invalidateGeneratedReport();
        setPageIndex(0);
        recordPlan(revised);
        setDraftSuperseded(false);
        setPlanValidated(true);
        setPreviewFilters({});
        setSyncedPreviewFilters({});
        setChatMessages((current) => [...current, { role: 'assistant', text: result.assistantMessage ? String(result.assistantMessage) : `Done — I updated the report. ${((result.changes || []) as string[]).join(' ')}` }]);
        try {
          setPreview(await previewReport(sourceId, revised, {}, signal));
          setPreviewMessage('');
        } catch (previewError) {
          if (!signal.aborted) setPreviewMessage(previewError instanceof Error ? `The report was updated, but the preview could not be refreshed: ${previewError.message}` : 'The report was updated, but the preview could not be refreshed.');
        }
      }
    }
  };

  const handleChat = async (overrideQuestion?: string, overrideTable?: string, recordUserMessage = true, resumeExisting = false, clarificationAnswer?: string, startNew = false) => {
    const question = (overrideQuestion ?? chatInput).trim();
    if (!sourceId || !question || chatSubmissionLock.current) return;
    chatSubmissionLock.current = true;
    followThreadRef.current = true;
    setError('');
    setChatInput('');
    const executionTable = overrideTable || selectedTable || undefined;
    const conversation = recordUserMessage ? [...chatMessages, { role: 'user' as const, text: question }] : chatMessages;
    if (recordUserMessage) setChatMessages(conversation);
    const history = conversation.slice(-50).map(({role, text}) => ({role, text: text.slice(0, 4000)}));
    const resume = !startNew && agentExecution?.status === 'needs_user' && (resumeExisting || !newQuestion);
    setNewQuestion(false);
    const signal = startOperation('Working on your request');
    const telemetryRunId = beginAgentRun();
    const traceId = getActiveAgentTraceId();
    setAuditNotice('');
    try {
      const run = resume && agentExecution
        ? await resumeAgentExecution(agentExecution.runId, { conversation: history, reportSpec: plan || undefined, table: executionTable, traceId: traceId || undefined, clarificationAnswer: clarificationAnswer || question }, signal)
        : await startAgentExecution({ previousRunId: newQuestion || startNew ? undefined : agentExecution?.runId, sourceId, request: question, hasReport: Boolean(plan), table: executionTable, sourceType, traceId: traceId || undefined, conversation: history, reportSpec: plan || undefined, clarificationAnswer }, signal);
      setAgentExecution(run);
      const resolvedTable = run.auditContext?.selectedTable;
      if (resolvedTable && tables.some((table) => tableKey(table) === resolvedTable)) setSelectedTable(resolvedTable);
      await applyAgentExecution(run, signal);
      if (run.status === 'needs_user') {
        const reason = (run.help?.message || 'Which field would you like to use?').replace(/^(?:ValueError|RuntimeError):\s*/, '');
        const prompt = reason.startsWith('Could not resolve measure from request:')
          ? `I couldn’t match “${reason.split(':').slice(1).join(':').trim()}” to a measure. Which measure should I use?`
          : reason;
        setChatMessages(current => [...current, {role:'assistant', text: prompt}]);
      } else if (run.status === 'failed') {
        if (run.steps.some((step) => step.action === 'plan_dashboard' || step.action === 'edit_report')) {
          setPlanApproved(false);
          setPlanValidated(false);
          setDraftSuperseded(true);
        }
        const message = run.help?.message || 'AI planning failed. Check the AI configuration and try again.';
        setError(message);
        setChatMessages(current => [...current, {role:'assistant', text: message}]);
      }
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : 'I could not complete that request.';
      setError(message);
      setChatMessages((current) => [...current, { role: 'assistant', text: message }]);
    } finally {
      endAgentRun(telemetryRunId);
      finishOperation();
      chatSubmissionLock.current = false;
      void refreshRunHistory();
    }
  };

  const handleApproveExecution = async () => {
    if (!agentExecution) return;
    setError('');
    const signal = startOperation('Executing approved plan');
    const telemetryRunId = beginAgentRun();
    const traceId = getActiveAgentTraceId();
    try {
      await approveAgentExecution(agentExecution.runId, agentExecution.planVersion, traceId || undefined, signal);
      const run = await resumeAgentExecution(agentExecution.runId, { conversation: chatMessages.slice(-50).map(({role,text}) => ({role,text:text.slice(0,4000)})), reportSpec: plan || undefined, table: selectedTable || undefined, traceId: traceId || undefined }, signal);
      setAgentExecution(run);
      const resolvedTable = run.auditContext?.selectedTable;
      if (resolvedTable && tables.some((table) => tableKey(table) === resolvedTable)) setSelectedTable(resolvedTable);
      await applyAgentExecution(run, signal);
      if (run.status === 'needs_user') setChatMessages(current => [...current, {role: 'assistant', text: run.help?.message || 'I need a little more information to continue. Please choose an option below or type your clarification.'}]);
      if (run.status === 'failed') {
        if (run.steps.some((step) => step.action === 'plan_dashboard' || step.action === 'edit_report')) {
          setPlanApproved(false);
          setPlanValidated(false);
          setDraftSuperseded(true);
        }
        const message = run.help?.message || 'AI planning failed. Check the AI configuration and try again.';
        setError(message);
        setChatMessages(current => [...current, {role: 'assistant', text: message}]);
      }
      if (run.status === 'completed') setError('');
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Approval could not be completed.');
    } finally {
      endAgentRun(telemetryRunId);
      finishOperation();
      void refreshRunHistory();
    }
  };

  const copyAuditValue = async (label: string, value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setAuditNotice(`${label} copied.`);
    } catch (copyError) {
      setError(copyError instanceof Error ? `Could not copy ${label.toLowerCase()}: ${copyError.message}` : `Could not copy ${label.toLowerCase()}.`);
    }
  };

  const downloadAuditSummary = async (runId: string) => {
    try {
      const summary = await getAgentRunAudit(runId);
      const blob = new Blob([JSON.stringify(summary, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${runId}-audit.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setAuditNotice('Audit summary downloaded from the saved run record.');
    } catch (downloadError) {
      setError(downloadError instanceof Error ? downloadError.message : 'Audit summary could not be downloaded.');
    }
  };

  const handleExecutionGuidance = (option: string) => {
    const table = /^Use ([^[]+)\[/.exec(option)?.[1];
    if (table && tables.some((item) => tableKey(item) === table)) setSelectedTable(table);
    void handleChat(option, table, true, true, option);
  };

  const handleClarification = async (answer: string) => {
    const userMessages = chatMessages.filter((message) => message.role === 'user');
    const original = clarificationRequest || requestText || userMessages[userMessages.length - 1]?.text || '';
    const normalizedAnswer = answer.trim().toLocaleLowerCase();
    const matchingTables = tables
      .map(tableKey)
      .filter((table) => clarification?.question.includes(table));
    const namedTable = tables.map(tableKey).find((table) => normalizedAnswer.includes(table.toLocaleLowerCase()));
    // A confirmation can switch to a table only when the clarification names
    // exactly one available table. This is schema-driven, not tied to any
    // workbook, sheet, or domain vocabulary.
    const affirmative = /^(?:yes|y|confirm|approve)(?:\b|\s)/.test(normalizedAnswer);
    const clarificationTable = namedTable || (affirmative && matchingTables.length === 1 ? matchingTables[0] : undefined);
    const targetTable = clarificationTable || selectedTable;
    if (clarificationTable) setSelectedTable(clarificationTable);
    setClarification(null);
    setClarificationRequest('');
    setChatMessages((current) => [...current, { role: 'user', text: answer }]);
    await handleChat(original, targetTable, false, false, answer);
  };

  const pages = plan ? [plan, ...(plan.additionalPages || [])] : [];
  const currentPage = pages[pageIndex] || pages[0];
  const previewPages = preview ? [preview, ...(preview.additionalPages || [])] : [];
  // Page names are the stable contract between the report plan and preview
  // response.  Prefer them over a positional lookup so a partial response
  // can never show Page 4 or 5 as a misleading blank canvas.
  const currentPreview = previewPages.find((page) => page.page === currentPage?.page) || previewPages[pageIndex] || null;
  const totalVisuals = pages.reduce((n, page) => n + page.visuals.length, 0);
  const draftVersion = Math.max(planHistoryIndex + 1, 1);
  const draftReadyForApproval = Boolean(plan && preview && planValidated && !draftSuperseded);

  const selectPage = (index: number) => {
    previewController.current?.abort();
    ++previewSequence.current;
    setPreviewFilters({});
    setPageIndex(index);
    if (plan && (Object.keys(previewFilters).length || Object.keys(syncedPreviewFilters).length)) {
      const seq = previewSequence.current;
      void previewReport(sourceId, plan, {}, undefined, pages[index]?.page, syncedPreviewFilters).then(result => {
        if (seq === previewSequence.current) setPreview(result);
      }).catch(() => {
        if (seq === previewSequence.current) setPreviewMessage('Refresh the preview to clear the previous filter.');
      });
    }
  };

  const approveKpiProposal = (kpi: { name: string; table: string }) => {
    const key = `${kpi.table}.${kpi.name}`;
    setApprovedKpis((current) => current.includes(key) ? current : [...current, key]);
    if (plan && currentPage && !currentPage.visuals.some((visual) => visual.type === 'card' && visual.value?.table === kpi.table && visual.value.field === kpi.name)) {
      invalidateGeneratedReport();
      const updated: ReportPlan = { ...currentPage, visuals: [{ type: 'card', title: kpi.name, value: { table: kpi.table, field: kpi.name, fieldType: 'measure' } }, ...currentPage.visuals] };
      recordPlan(pageIndex === 0 ? updated : { ...plan, additionalPages: plan.additionalPages?.map((p, i) => i === pageIndex - 1 ? updated : p) });
      setDraftSuperseded(false);
      setPlanValidated(true);
      setPreview(null);
      setPreviewMessage('KPI approved and added to the plan. Refreshing the preview requires regeneration of preview data.');
    }
  };

  const handleSqlConnect = async () => {
    setError('');
    setSqlPendingSourceId('');
    setSqlDiscoveredTables([]);
    const signal = startOperation('Testing SQL connection');
    try {
      const connection = await testSqlConnection(sql, signal);
      setSqlConnectionId(connection.connectionId);
      setOperation('Discovering SQL schema');
      const schema = await discoverSqlSchema(connection.connectionId, signal);
      setSqlPendingSourceId(schema.sourceId);
      setSqlDiscoveredTables(schema.tables);
      const keys = schema.tables.map(tableKey);
      setSqlSelectedTables(keys);
      setSqlApprovedMeasures([]);
      setStatus(`Discovered ${schema.tables.length} SQL tables. Select tables to import.`);
    } catch (requestError) {
      if (!signal.aborted) setError(requestError instanceof Error ? requestError.message : 'SQL connection failed.');
    } finally { if (operationController.current?.signal === signal) finishOperation(); }
  };

  const handleSqlMaterialize = async () => {
    if (!sqlPendingSourceId || !sqlConnectionId || sqlSelectedTables.length === 0) return;
    setError('');
    const signal = startOperation('Importing selected SQL tables');
    try {
      await materializeSqlSource(sqlPendingSourceId, sqlConnectionId, sqlSelectedTables, sqlApprovedMeasures, signal);
      clearGeneratedState();
      setSourceId(sqlPendingSourceId);
      setSourceType('sql');
      setFormulaCellCount(null);
      setTables(sqlDiscoveredTables.filter((table) => sqlSelectedTables.includes(tableKey(table))));
      setSelectedTable(sqlSelectedTables[0]);
      setSelectedFile(null);
      setSavedSourceName('');
      setSqlDialogOpen(false);
      setSql((current) => ({ ...current, password: '' }));
      setStatus('Selected SQL tables are ready for report planning.');
    } catch (requestError) {
      if (!signal.aborted) setError(requestError instanceof Error ? requestError.message : 'SQL table import failed.');
    } finally { if (operationController.current?.signal === signal) finishOperation(); }
  };

  const openSqlDialog = () => {
    setError('');
    setSqlConnectionId('');
    setSqlPendingSourceId('');
    setSqlDiscoveredTables([]);
    setSqlSelectedTables([]);
    setSqlApprovedMeasures([]);
    setSqlDialogOpen(true);
  };
  const closeSqlDialog = () => {
    if (isLoading) return;
    setSqlDialogOpen(false);
    setSql((current) => ({ ...current, password: '' }));
    setError('');
    setStatus('');
  };

  const handleDownload = async () => {
    if (!reportId || validation?.passed !== true) return;
    setError('');
    setIsLoading(true);
    try {
      await downloadReport(reportId);
      setStatus('PBIP project download started. Extract it, then open the .pbip file in Power BI Desktop.');
      setChatMessages((current) => [...current, { role: 'assistant', text: 'The PBIP archive download has started. Extract the complete archive, then open its .pbip file in Power BI Desktop.' }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Download failed');
    } finally {
      setIsLoading(false);
    }
  };

  const handleOpenInPowerBI = async () => {
    if (!reportId || validation?.passed !== true) return;
    setError('');
    setIsLoading(true);
    try {
      const result = await openReportInPowerBI(reportId);
      setStatus(result.message);
      setChatMessages((current) => [...current, { role: 'assistant', text: result.message }]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Could not open Power BI Desktop');
    } finally {
      setIsLoading(false);
    }
  };

  const retryLastOperation = () => {
    setError('');
    if (lastOperation === 'Preparing report fix') return void handleRepair();
    if (lastOperation.includes('generat') || artifactState === 'failed') return void handleGenerate();
    if (lastOperation.includes('workbook') && selectedFile) return void uploadWorkbook(selectedFile);
    if (lastOperation.includes('SQL connection')) return void handleSqlConnect();
    if (lastOperation.includes('SQL tables')) return void handleSqlMaterialize();
    if (lastOperation.includes('SQL')) return void handleSqlConnect();
    if (lastOperation === 'Planning report') return void handlePlan();
    const lastUserMessage = [...chatMessages].reverse().find((message) => message.role === 'user')?.text;
    if (lastUserMessage) return void handleChat(lastUserMessage, undefined, false);
    if (requestText.trim()) return void handlePlan();
  };

  const canRetryLastOperation = (
    (lastOperation.includes('workbook') && Boolean(selectedFile))
    || lastOperation.includes('SQL')
    || lastOperation.includes('generat')
    || artifactState === 'failed'
    || chatMessages.some((message) => message.role === 'user')
    || Boolean(requestText.trim())
  );
  const sourceError = Boolean(error && (
    lastOperation.includes('workbook')
    || lastOperation.includes('SQL')
  ));
  const sourceOperation = operation.includes('workbook') || operation.includes('SQL');
  const readyMessageIndex = reportId && validation?.passed
    ? chatMessages.reduce((found, message, index) => message.role === 'assistant' && (
      message.text === 'The PBIP project is generated and validated. You can open it in Power BI Desktop on this machine or download the project archive.'
      || message.text === 'The report plan is approved. You can now generate the PBIP.'
    ) ? index : found, -1)
    : -1;

  return (
    <div className="app-shell">
      <a className="skip-to-main" href="#main">Skip to main content</a>
      <input ref={fileInputRef} className="visually-hidden" type="file" accept=".xlsx" aria-label="Choose an Excel workbook" onChange={handleUpload} disabled={isLoading} />
      {accountDialog && <div className="sql-dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) setAccountDialog(null); }}>
        <div ref={accountDialogRef} className="sql-dialog account-dialog" role="dialog" aria-modal="true" aria-labelledby="account-dialog-title">
          <div className="sql-dialog-heading">
            <h2 id="account-dialog-title">{accountDialog}</h2>
            <button type="button" className="sql-dialog-close" aria-label="Close" onClick={() => setAccountDialog(null)}>×</button>
          </div>
          <p className="sql-dialog-description">{accountDialog === 'Settings' ? 'Profile and report defaults are not available yet.' : accountDialog === 'Data connections' ? 'Saved data connections are not available yet. Connect a source from the report composer.' : 'Help and feedback are not available yet.'}</p>
          <div className="sql-dialog-actions"><button type="button" className="sql-dialog-secondary" onClick={() => setAccountDialog(null)}>Close</button></div>
        </div>
      </div>}
      {sqlDialogOpen && <div className="sql-dialog-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) closeSqlDialog(); }}>
        <div ref={sqlDialogRef} className="sql-dialog" role="dialog" aria-modal="true" aria-labelledby="sql-dialog-title">
          <div className="sql-dialog-heading">
            <h2 id="sql-dialog-title">{sqlPendingSourceId ? 'Select tables' : 'Connect SQL Server'}</h2>
            <button type="button" className="sql-dialog-close" aria-label="Close" disabled={isLoading} onClick={closeSqlDialog}>×</button>
          </div>
          {sqlPendingSourceId ? <>
            <p className="sql-dialog-description">Choose the tables to import for your report.</p>
            <div className="sql-dialog-tables">
              {sqlDiscoveredTables.length === 0 && <p>No tables were found for this connection.</p>}
              {sqlDiscoveredTables.map((table) => {
                const key = tableKey(table);
                return <div key={key}>
                  <label><input type="checkbox" disabled={isLoading} checked={sqlSelectedTables.includes(key)} onChange={(event) => setSqlSelectedTables((current) => event.target.checked ? [...current, key] : current.filter((item) => item !== key))} />{key}</label>
                  {sqlSelectedTables.includes(key) && table.measureProposals?.map((measure) => {
                    const measureKey = `${key}.${measure.name}`;
                    return <label className="sql-dialog-measure" key={measureKey}><input type="checkbox" disabled={isLoading} checked={sqlApprovedMeasures.includes(measureKey)} onChange={(event) => setSqlApprovedMeasures((current) => event.target.checked ? [...current, measureKey] : current.filter((item) => item !== measureKey))} />Approve {measure.name} ({measure.aggregation} of {measure.column})</label>;
                  })}
                </div>;
              })}
            </div>
            {error && <p className="sql-dialog-error" role="alert">{error}</p>}
            {operation && <p className="sql-dialog-progress" role="status">{operation}…</p>}
            <div className="sql-dialog-actions">
              <button type="button" className="sql-dialog-secondary" disabled={isLoading} onClick={() => { setSqlPendingSourceId(''); setError(''); }}>Back</button>
              {isLoading ? <button type="button" className="sql-dialog-secondary" onClick={cancelOperation}>Cancel operation</button> : <button type="button" className="sql-dialog-primary" disabled={sqlSelectedTables.length === 0} onClick={() => void handleSqlMaterialize()}>Import selected tables</button>}
            </div>
          </> : <form onSubmit={(event) => { event.preventDefault(); void handleSqlConnect(); }}>
            <label>Server<input autoFocus aria-label="SQL Server" placeholder="Server name" value={sql.server} disabled={isLoading} onChange={(event) => setSql({ ...sql, server: event.target.value })} required /></label>
            <label>Database<input aria-label="SQL database" placeholder="Database name" value={sql.database} disabled={isLoading} onChange={(event) => setSql({ ...sql, database: event.target.value })} required /></label>
            <label>Authentication<select aria-label="SQL authentication" value={sql.authentication} disabled={isLoading} onChange={(event) => setSql({ ...sql, authentication: event.target.value as 'sql' | 'windows' })}><option value="sql">SQL authentication</option><option value="windows">Windows authentication</option></select></label>
            {sql.authentication === 'sql' && <><label>Username<input aria-label="SQL username" autoComplete="username" value={sql.username} disabled={isLoading} onChange={(event) => setSql({ ...sql, username: event.target.value })} required /></label><label>Password<input aria-label="SQL password" type="password" autoComplete="current-password" value={sql.password} disabled={isLoading} onChange={(event) => setSql({ ...sql, password: event.target.value })} required /></label></>}
            {error && <p className="sql-dialog-error" role="alert">{error}</p>}
            {operation && <p className="sql-dialog-progress" role="status">{operation}…</p>}
            <div className="sql-dialog-actions">
              {isLoading ? <button type="button" className="sql-dialog-secondary" onClick={cancelOperation}>Cancel operation</button> : <><button type="button" className="sql-dialog-secondary" onClick={closeSqlDialog}>Cancel</button><button type="submit" className="sql-dialog-primary">Connect</button></>}
            </div>
          </form>}
        </div>
      </div>}
      <aside className={`aura-sidebar ${sidebarCollapsed ? 'is-collapsed' : ''} ${sidebarSearchOpen ? 'is-search-open' : ''}`} aria-label="Aura navigation">
        <div className="sidebar-brand">
          <button className="dar-mark" type="button" aria-label="DAR, Aura home" onClick={() => setCurrentView('home')}><DarLogo /></button>
          <button className="sidebar-collapsed-logo" type="button" aria-label="Aura home" onClick={() => setCurrentView('home')}><svg className="sidebar-spark" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 0C10 5.52 14.48 10 20 10c-5.52 0-10 4.48-10 10C10 14.48 5.52 10 0 10 5.52 10 10 5.52 10 0Z" /></svg></button>
          <button className="sidebar-collapse" type="button" aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}>
            <svg xmlns="http://www.w3.org/2000/svg" width="17" height="17" fill="none" viewBox="0 0 24 24" aria-hidden="true"><path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 3v18M7.8 3h8.4c1.6802 0 2.5202 0 3.162.327a3 3 0 0 1 1.311 1.311C21 5.2798 21 6.1198 21 7.8v8.4c0 1.6802 0 2.5202-.327 3.162a3 3 0 0 1-1.311 1.311C18.7202 21 17.8802 21 16.2 21H7.8c-1.6802 0-2.5202 0-3.162-.327a3 3 0 0 1-1.311-1.311C3 19.4802 3 18.6402 3 16.96V7.8c0-1.6802 0-2.5202.327-3.162a3 3 0 0 1 1.311-1.311C5.2798 3 6.1198 3 7.8 3" /></svg>
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="Main">
          <button type="button" className={currentView === 'home' ? 'is-active' : ''} aria-current={currentView === 'home' ? 'page' : undefined} onClick={() => setCurrentView('home')}><span aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M9 21v-7.4c0-.56 0-.84.109-1.054a1 1 0 0 1 .437-.437C9.76 12 10.04 12 10.6 12h2.8c.56 0 .84 0 1.054.109a1 1 0 0 1 .437.437C15 12.76 15 13.04 15 13.6V21M11.018 2.764 4.235 8.039c-.453.353-.68.529-.843.75a2 2 0 0 0-.318.65C3 9.704 3 9.991 3 10.565V17.8c0 1.12 0 1.68.218 2.108a2 2 0 0 0 .874.874C4.52 21 5.08 21 6.2 21h11.6c1.12 0 1.68 0 2.108-.218a2 2 0 0 0 .874-.874C21 19.48 21 18.92 21 17.8v-7.235c0-.574 0-.861-.074-1.126a2 2 0 0 0-.318-.65c-.163-.221-.39-.397-.843-.75l-6.783-5.275a1 1 0 0 0-1.244 0Z" /></svg></span><span className="sidebar-label">Home</span></button>
          <button type="button" className={currentView === 'new-report' ? 'is-active' : ''} aria-current={currentView === 'new-report' ? 'page' : undefined} disabled={isLoading} onClick={() => void startNewReport()}><span aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span><span className="sidebar-label">New report</span></button>
          <button type="button" className={sidebarSearchOpen ? 'is-active' : ''} aria-expanded={sidebarSearchOpen} onClick={() => { if (!sidebarSearchOpen) setSidebarCollapsed(false); setSidebarSearchOpen((open) => !open); setSearchQuery(''); }}><span aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="7.3" /><path d="m16.2 16.2 4.3 4.3" /></svg></span><span className="sidebar-label">Search</span></button>
          <button type="button" className={currentView === 'published' ? 'is-active' : ''} aria-current={currentView === 'published' ? 'page' : undefined} onClick={() => setCurrentView('published')}><span aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20 12.5V6.8c0-1.68 0-2.52-.327-3.162a3 3 0 0 0-1.311-1.311C17.72 2 16.88 2 15.2 2H8.8c-1.68 0-2.52 0-3.162.327a3 3 0 0 0-1.311 1.311C4 4.28 4 5.12 4 6.8v10.4c0 1.68 0 2.52.327 3.162a3 3 0 0 0 1.311 1.311C6.28 22 7.12 22 8.8 22H12m-4-11h8m-8 4h3m5-8H8m6.5 12 2 2 4.5-4.5" /></svg></span><span className="sidebar-label">Published reports</span></button>
        </nav>
        {sidebarSearchOpen && <label className="sidebar-search">
          <span aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="7.3" /><path d="m16.2 16.2 4.3 4.3" /></svg></span>
          <input autoFocus type="search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search reports" aria-label="Search recent reports" />
          <button type="button" aria-label="Close search" onClick={() => { setSidebarSearchOpen(false); setSearchQuery(''); }}>×</button>
        </label>}
        <section className="sidebar-recents" aria-labelledby="sidebar-recents-title">
          <h2 id="sidebar-recents-title">Recents</h2>
          <ul ref={recentMenuRef}>{sidebarRecentRuns.map((report) => (
            <li className="recent-item" key={report.reportId}>{renamingRecent === report.reportId ? <input autoFocus aria-label="Chat name" maxLength={200} value={recentName} onChange={(event) => setRecentName(event.target.value)} onBlur={(event) => void saveRecentName(report, event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); if (event.key === 'Escape') setRenamingRecent(null); }} /> : <>
              <button className="recent-open" type="button" disabled={recentBusy === report.reportId} title={`Open the chat for ${report.chatTitle || report.title}`} onClick={() => void openSavedReport(report)}><span>{report.chatTitle || report.title}</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12.5V6.8c0-1.68 0-2.52-.327-3.162a3 3 0 0 0-1.311-1.311C17.72 2 16.88 2 15.2 2H8.8c-1.68 0-2.52 0-3.162.327a3 3 0 0 0-1.311 1.311C4 4.28 4 5.12 4 6.8v10.4c0 1.68 0 1.68.327 3.162a3 3 0 0 0 1.311 1.311C6.28 22 7.12 22 8.8 22H12m-4-11h8m-8 4h3m5-8H8m6.5 12 2 2 4.5-4.5" /></svg></button>
              <button className="recent-more" type="button" disabled={recentBusy === report.reportId} aria-label={`More options for ${report.chatTitle || report.title}`} aria-haspopup="menu" aria-expanded={recentMenu === report.reportId} onClick={() => setRecentMenu((current) => current === report.reportId ? null : report.reportId)}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" /></svg></button>
              {recentMenu === report.reportId && <div className="recent-menu" role="menu" aria-label={`Options for ${report.chatTitle || report.title}`}>
                <button type="button" role="menuitem" onClick={() => { setRecentName(report.chatTitle || report.title); setRenamingRecent(report.reportId); setRecentMenu(null); }}>Rename</button>
                <button type="button" role="menuitem" className="recent-delete" onClick={() => void deleteRecent(report)}>Delete</button>
              </div>}
            </>}</li>
          ))}</ul>
          {recentError && <p className="sidebar-search-empty" role="alert">{recentError}</p>}
          {sidebarSearchOpen && sidebarRecentRuns.length === 0 && <p className="sidebar-search-empty">{searchQuery.trim() ? 'No reports match.' : 'No recent reports yet.'}</p>}
        </section>
        <button className="sidebar-profile" type="button" aria-label={`${profileLabel}, account menu`} aria-expanded={profileMenuOpen} title={identityError || undefined} onClick={() => setProfileMenuOpen((open) => !open)}>
          <span className="profile-avatar" aria-hidden="true">{profileInitials}</span>
          <span className="sidebar-label"><strong>{profileLabel}</strong><small>{identity?.isDemo ? 'Demo account' : identity ? 'Signed-in account' : identityError ? 'Could not load account' : 'Loading account…'}</small></span>
        </button>
        {profileMenuOpen && <div className="profile-menu" role="menu" aria-label="Account menu">
          <button type="button" role="menuitem" onClick={() => { setProfileMenuOpen(false); setAccountDialog('Settings'); }}>Settings</button>
          <button type="button" role="menuitem" onClick={() => { setProfileMenuOpen(false); setAccountDialog('Data connections'); }}>Data connections</button>
          <button type="button" role="menuitem" onClick={() => { setProfileMenuOpen(false); setAccountDialog('Help & feedback'); }}>Help &amp; feedback</button>
          <button type="button" role="menuitem" onClick={() => { setProfileMenuOpen(false); setStatus('Sign out is not available for the demo user.'); }}>Log out</button>
        </div>}
      </aside>
      <div className="app-content">
      {currentView === 'home' ? <main id="main" className="aura-home">
        <div className="home-brand" aria-label="Aura">
          <span className="home-brand-title"><span className="home-brand-icon"><AuraLogoMark /></span><strong>AURA</strong></span>
          <small className="home-brand-copy">AI-driven Understanding &amp; Reporting Accelerator</small>
        </div>
        <h1>What report do you <em>need today?</em></h1>
        <p className="home-intro">Connect your data, describe the report in plain words, and review a preview before Aura generates the PBIP.</p>
        <form className="home-composer" aria-label="Create a report" onSubmit={(event) => { event.preventDefault(); if (!isLoading && sourceId && requestText.trim()) { setSidebarCollapsed(true); setCurrentView('new-report'); void handleChat(requestText, undefined, true, false, undefined, true); } }}>
          <label className="visually-hidden" htmlFor="home-report-request">Describe the report</label>
          <div className="home-prompt-row">
            <span className="home-prompt-spark" aria-hidden="true"><svg viewBox="0 0 20 20"><path d="M10 0C10 5.52 14.48 10 20 10c-5.52 0-10 4.48-10 10C10 14.48 5.52 10 0 10 5.52 10 10 5.52 10 0Z" /></svg></span>
            <textarea id="home-report-request" rows={2} maxLength={4000} value={requestText} onChange={(event) => setRequestText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Tab' && !requestText) { event.preventDefault(); setRequestText('Create a two-page procurement report: Spend Overview and Supplier Risk'); } else if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
            {!requestText && <span className="home-prompt-suggestion" aria-hidden="true">Create a two-page procurement report: Spend Overview and Supplier Risk<kbd>Tab</kbd></span>}
          </div>
          <div className="home-composer-actions">
            <div className="home-source-actions">
              {operation === 'Inspecting workbook' && selectedFile && uploadProgress ? <UploadProgress compact name={selectedFile.name} progress={uploadProgress} onCancel={cancelOperation} /> : sourceId ? <div className="home-source-chip"><span aria-hidden="true">✓</span><strong>{sourceType === 'excel' ? selectedFile?.name || savedSourceName || 'Excel data' : 'SQL Server'}</strong><small>· {sourceDetails}</small><button type="button" aria-label="Remove data source" disabled={isLoading} onClick={() => void resetWorkspace(true)}>×</button></div> : <>
              <button type="button" className="home-secondary-button" disabled={isLoading} onClick={() => fileInputRef.current?.click()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15v1.2c0 1.68 0 2.52-.327 3.162a3 3 0 0 1-1.311 1.311C18.72 21 17.88 21 16.2 21H7.8c-1.68 0-2.52 0-3.162-.327a3 3 0 0 1-1.311-1.311C3 18.72 3 17.88 3 16.2V15m14-7-5-5m0 0L7 8m5-5v12" /></svg>Upload Excel</button>
              <button type="button" className="home-secondary-button" disabled={isLoading} onClick={openSqlDialog}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 5c0 1.657-4.029 3-9 3S3 6.657 3 5m18 0c0-1.657-4.029-3-9-3S3 3.343 3 5m18 0v14c0 1.66-4 3-9 3s-9-1.34-9-3V5m18 7c0 1.66-4 3-9 3s-9-1.34-9-3" /></svg>SQL Server</button>
              <MoreDataSources disabled={isLoading} />
              </>}
            </div>
            <button type="submit" className="home-submit-button" disabled={isLoading || !sourceId || !requestText.trim()}>Describe report <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10.5 13.5 10.5-10.5m-10.37 10.83 2.63 6.76c.23.6.35.89.51.98a.5.5 0 0 0 .46 0c.17-.09.28-.38.52-.98l6.59-16.89c.21-.54.31-.81.26-.98a.5.5 0 0 0-.32-.32c-.17-.06-.44.05-.98.26L3.41 9.25c-.6.23-.89.35-.98.52a.5.5 0 0 0 0 .46c.09.17.38.28.98.51l6.76 2.63c.12.05.18.07.23.11a.5.5 0 0 1 .12.12c.04.05.06.11.11.23Z" /></svg></button>
          </div>
        </form>
        <p className="home-helper" id="home-hint">{sourceId ? 'Your data is connected. Describe what you want to explore.' : 'Upload Excel or connect SQL Server first, then press Tab to use the suggestion and Enter to send.'}</p>
        {error && <p className="home-error" role="alert">{error}</p>}
        {status && !error && <p className="home-source-status" role="status">{status}</p>}
        <section className="home-reports" aria-labelledby="home-reports-title">
          <div className="home-section-heading">
            <h2 id="home-reports-title">Recent published reports</h2>
            <button type="button" onClick={() => setCurrentView('published')}>View all <span aria-hidden="true">→</span></button>
          </div>
          <div className="home-report-grid">
            {savedReportsLoading && <p role="status">Loading published reports…</p>}
            {savedReportsError && <p className="library-error" role="alert">{savedReportsError}</p>}
            {!savedReportsLoading && !savedReportsError && savedReports.length === 0 && <p className="library-empty">No published reports yet. Generate a validated PBIP to see it here.</p>}
            {savedReports.slice(0, 2).map((report) => (
              <button className="home-report-card" type="button" key={report.reportId} onClick={() => void openSavedReport(report)}>
                <span className="home-report-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20 12.5V6.8c0-1.68 0-2.52-.327-3.162a3 3 0 0 0-1.311-1.311C17.72 2 16.88 2 15.2 2H8.8c-1.68 0-2.52 0-3.162.327a3 3 0 0 0-1.311 1.311C4 4.28 4 5.12 4 6.8v10.4c0 1.68 0 2.52.327 3.162a3 3 0 0 0 1.311 1.311C6.28 22 7.12 22 8.8 22H12m-4-11h8m-8 4h3m5-8H8m6.5 12 2 2 4.5-4.5" /></svg></span>
                <span><strong>{report.title}</strong><small>{report.pageCount} {report.pageCount === 1 ? 'page' : 'pages'} · {report.visualCount} visuals · {new Date(report.updatedAt).toLocaleString()}</small></span>
              </button>
            ))}
          </div>
        </section>
      </main> : currentView === 'search' ? <main id="main" className="library-view">
        <p className="library-eyebrow">AURA WORKSPACE</p>
        <h1>Search your reports</h1>
        <p className="library-intro">Find a previous report request in your run history.</p>
        <label className="library-search"><span aria-hidden="true">⌕</span><input type="search" value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search reports and requests" aria-label="Search reports and requests" /></label>
        {runHistoryError && <p className="library-error" role="alert">{runHistoryError}</p>}
        {runHistoryLoading ? <p className="library-empty" role="status">Loading recent runs…</p> : filteredRuns.length ? <ul className="library-list">{filteredRuns.map((run) => (
          <li key={run.runId}><button type="button" onClick={() => openRunFromList(run.runId)}><span className={`run-status is-${run.status}`}>{runStatusLabel(run.status)}</span><strong>{run.reportName || run.goal || 'Untitled run'}</strong><small>{run.goal}</small></button></li>
        ))}</ul> : <p className="library-empty">{runHistory.length ? 'No runs match your search.' : 'No report runs found yet.'}</p>}
      </main> : currentView === 'published' ? <main id="main" className="published-view">
        <p className="library-eyebrow">AURA WORKSPACE</p>
        <h1>Published reports</h1>
        <p className="library-intro">Reports generated and validated in your workspace.</p>
        {savedReportsError && <p className="library-error" role="alert">{savedReportsError}</p>}
        {savedReportsNotice && <p className="library-notice" role="status">{savedReportsNotice}</p>}
        {savedReportsLoading ? <p className="published-loading" role="status">Loading published reports…</p> : savedReports.length === 0 && !savedReportsError ? <section className="published-empty">
          <span className="published-empty-icon" aria-hidden="true">▤</span>
          <h2>No published reports yet</h2>
          <p>Reports you generate and validate will show up here.</p>
          <button type="button" disabled={isLoading} onClick={() => void startNewReport()}>Create a new report</button>
        </section> : <div className="published-grid">
          {savedReports.map((report) => (
            <article className="published-card" key={report.reportId}>
              <ReportThumbnail report={report} onOpen={() => void openSavedReport(report)} />
              <div className="published-card-body">
                <h2>{report.title}</h2>
                <p>{report.sourceName || (report.sourceType === 'sql' ? 'SQL Server' : report.sourceType === 'excel' ? 'Excel data' : 'Source unavailable')} · {new Date(report.updatedAt).toLocaleDateString()}</p>
                <div className="published-card-actions">
                  <button type="button" onClick={() => void downloadSavedReport(report)}>Download PBIP</button>
                  <button type="button" onClick={() => void openSavedReportInPowerBI(report)}>Open in Power BI</button>
                  <button type="button" onClick={() => void openSavedReport(report)}>Open chat</button>
                </div>
              </div>
            </article>
          ))}
        </div>}
      </main> : <>
      <header className="topbar">
        <h1 className="studio-title">{plan?.page || 'Untitled report'}</h1>
        <div className="report-state">
          <span className={`artifact-chip is-${artifactState}`}>{artifactState === 'generated' && <svg aria-hidden="true" viewBox="0 0 16 16"><path d="m3.25 8.1 2.9 2.9 6.6-6.35" /></svg>}{artifactState === 'empty' ? 'No draft yet' : artifactState === 'draft' ? `v${draftVersion} Draft` : artifactState === 'changed' ? `v${draftVersion} Draft` : artifactState === 'failed' ? 'Validation failed' : `v${draftVersion} Generated`}</span>
        </div>
        <div className="topbar-actions">
          {draftReadyForApproval && !planApproved && agentExecution?.status !== 'awaiting_approval' && (!reportId || validation?.passed === false) && <button className="studio-primary-action" type="button" disabled={isLoading} onClick={handleApprovePlan}>Approve report plan</button>}
          {!draftSuperseded && planApproved && plan && planValidated && agentExecution?.status !== 'awaiting_approval' && (!reportId || validation?.passed === false) && <button className="studio-primary-action" type="button" disabled={isLoading} onClick={validation?.passed === false ? handleRepair : handleGenerate}>{validation?.passed === false ? 'Prepare fix' : 'Generate PBIP'}</button>}
          {agentExecution?.status === 'awaiting_approval' && <button className="studio-primary-action" type="button" disabled={isLoading} onClick={handleApproveExecution}>Approve change</button>}
          {reportId && validation?.passed === true && <>
            <button className="header-button powerbi-open" type="button" onClick={handleOpenInPowerBI} disabled={isLoading}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-7 7M19 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h4" /></svg>Open in Power BI</button>
            <button className="header-button download-report" type="button" onClick={handleDownload} disabled={isLoading} title="Download PBIP project archive">↓ Download PBIP</button>
          </>}
        </div>
      </header>

      {runHistoryOpen && <div className="run-history-overlay" role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) setRunHistoryOpen(false);
      }}>
        <section className="run-history-dialog" role="dialog" aria-modal="true" aria-labelledby="run-history-title">
          <header className="run-history-heading">
            <div><h2 id="run-history-title">Run history</h2><p>Saved agent runs for your account.</p></div>
            <div><button type="button" className="button button-outline" onClick={() => void refreshRunHistory()} disabled={runHistoryLoading}>Refresh</button><button type="button" className="run-history-close" aria-label="Close run history" onClick={() => setRunHistoryOpen(false)}>×</button></div>
          </header>
          <div className="run-history-content">
            <section className="run-history-list" aria-label="Saved runs">
              {runHistoryLoading && <p role="status">Loading saved runs…</p>}
              {runHistoryError && <p className="run-history-error" role="alert">{runHistoryError}</p>}
              {!runHistoryLoading && !runHistoryError && runHistory.length === 0 && <p>No saved agent runs yet.</p>}
              {runHistory.map((item) => <button type="button" key={item.runId} className={`run-history-item ${selectedAudit?.run.runId === item.runId ? 'is-selected' : ''}`} onClick={() => void loadHistoricalAudit(item.runId)}>
                <span className={`run-status is-${item.status}`}>{runStatusLabel(item.status)}</span>
                <strong>{item.reportName || item.goal || 'Untitled run'}</strong>
                <small>{item.goal}</small>
                <span className="run-history-item-meta">{formatAuditTimestamp(item.createdAt)} · {item.completedStepCount}/{item.stepCount} steps</span>
                <code>{item.runId}</code>
              </button>)}
              {runHistoryHasMore && <button className="button button-outline" type="button" onClick={() => void loadMoreRunHistory()} disabled={runHistoryLoading}>{runHistoryLoading ? 'Loading…' : 'Load older runs'}</button>}
            </section>
            <section className="run-history-detail" aria-label="Selected saved run">
              {selectedAuditLoading && <p role="status">Loading run record…</p>}
              {selectedAuditError && <p className="run-history-error" role="alert">{selectedAuditError}</p>}
              {!selectedAuditLoading && !selectedAuditError && !selectedAudit && <p className="run-history-placeholder">Select a saved run to review its audit record.</p>}
              {selectedAudit && <div className="history-audit-record">
                <div className="history-audit-heading">
                  <div><span className={`run-status is-${selectedAudit.run.status}`}>{runStatusLabel(selectedAudit.run.status)}</span><h3>{selectedAudit.run.reportName || 'Agent run'}</h3></div>
                  <button className="button button-outline" type="button" onClick={() => void downloadAuditSummary(selectedAudit.run.runId)}>Download audit JSON</button>
                </div>
                <dl className="audit-metadata">
                  <div><dt>Request</dt><dd>{selectedAudit.run.request}</dd></div>
                  <div><dt>Source</dt><dd>{(selectedAudit.run.sourceType || 'unknown').toUpperCase()} · {selectedAudit.run.selectedTable || selectedAudit.run.sourceId || 'Not recorded'}</dd></div>
                  <div><dt>Started</dt><dd>{formatAuditTimestamp(selectedAudit.run.startedAt || undefined)}</dd></div>
                  <div><dt>Last updated</dt><dd>{formatAuditTimestamp(selectedAudit.run.lastUpdatedAt || undefined)}</dd></div>
                  <div><dt>Elapsed</dt><dd>{formatAuditDuration(selectedAudit.run.startedAt || undefined, selectedAudit.run.lastUpdatedAt || undefined)}</dd></div>
                  <div><dt>Planner</dt><dd>{selectedAudit.run.fallbackUsed ? 'Rules fallback used' : selectedAudit.run.planner}</dd></div>
                </dl>
                <div className="audit-identifiers">
                  <div><span>Run ID</span><code>{selectedAudit.run.runId}</code><button type="button" onClick={() => void copyAuditValue('Run ID', selectedAudit.run.runId)}>Copy</button></div>
                  {selectedAudit.run.traceIds.map((traceId) => <div key={traceId}><span>Trace ID</span><code>{traceId}</code><button type="button" onClick={() => void copyAuditValue('Trace ID', traceId)}>Copy</button></div>)}
                </div>
                {auditNotice && <p className="audit-notice" role="status">{auditNotice}</p>}
                <h4 className="audit-timeline-title">Activity timeline</h4>
                <ol className="audit-timeline">
                  {selectedAudit.run.events.map((event, index) => <li key={`${event.at}-${index}`}>
                    <time dateTime={event.at}>{formatAuditTimestamp(event.at)}</time>
                    <div><strong>{auditEventLabel(event.event)}{event.action ? ` · ${humanizeFieldName(event.action)}` : ''}</strong>
                      {event.step !== undefined && <small>Step {event.step + 1}{event.attempt ? ` · attempt ${event.attempt}` : ''}</small>}
                      {event.approver && <small>Approved by {event.approver}</small>}
                      {event.error && <em>{event.error}</em>}
                    </div>
                  </li>)}
                </ol>
                <details className="audit-steps">
                  <summary>Step outcomes</summary>
                  <ol>{selectedAudit.run.steps.map((step) => <li key={step.index} className={`is-${step.status}`}><span>{step.status === 'completed' ? '✓' : step.status === 'running' ? '…' : step.status === 'needs_user' ? '!' : step.index + 1}</span><div><strong>{humanizeFieldName(step.action)}</strong><small>{step.reason}</small>{step.error && <em>{step.error}</em>}</div><b>{step.attempts > 1 ? `${step.attempts} attempts` : ''}</b></li>)}</ol>
                </details>
              </div>}
            </section>
          </div>
        </section>
      </div>}

      <main id="main" className="workspace">
        <section className="intro-row">
          <div>
            <p className="eyebrow">DRAFT WORKSPACE <span>01 / 03</span></p>
            <h1>Build a report from your data.</h1>
            <p className="intro-copy">Connect a source, choose a model table, and review the report plan before it becomes a PBIR draft.</p>
          </div>
          <div className="draft-stamp"><span>WORKSPACE</span><strong>LOCAL DRAFT</strong></div>
        </section>

        <div className="progress-track" aria-label="Report workflow">
          <div className="progress-step is-current"><span>01</span><strong>Source</strong></div><i />
          <div className={`progress-step ${plan ? 'is-current' : ''}`}><span>02</span><strong>Plan</strong></div><i />
          <div className={`progress-step ${validation?.passed ? 'is-current' : ''}`}><span>03</span><strong>Export</strong></div>
        </div>

        <div className="work-grid">
          <nav className="workflow-sequence" aria-label="AURA report workflow">
            <span className={sourceId ? 'is-complete' : 'is-active'}><b>1</b>Connect data</span>
            <i />
            <span className={sourceId && !plan ? 'is-active' : plan ? 'is-complete' : ''}><b>2</b>Describe report</span>
            <i />
            <span className={plan && artifactState !== 'generated' ? 'is-active' : plan ? 'is-complete' : ''}><b>3</b>Review</span>
            <i />
            <span className={artifactState === 'generated' ? 'is-complete' : artifactState === 'failed' ? 'is-failed' : ''}><b>4</b>Generate</span>
          </nav>
          <section className="work-panel source-panel">
            <div className="panel-heading">
              <div><span className="step-number">1</span><h2>Connect data</h2></div>
              <span className="subtle-label">SOURCE</span>
            </div>
            {sourceId ? <div className="studio-source-summary">
              <span className="connection-dot is-connected" />
              <strong title={selectedFile?.name || savedSourceName || sourceId}>{selectedFile?.name || savedSourceName || (sourceType === 'sql' ? 'SQL Server' : 'Connected source')}</strong>
              <span>· {sourceDetails}</span>
            </div> : <div className="studio-source-actions">
              {operation === 'Inspecting workbook' && selectedFile && uploadProgress ? <UploadProgress name={selectedFile.name} progress={uploadProgress} onCancel={cancelOperation} /> : <>
                <button className="home-secondary-button" type="button" disabled={isLoading} onClick={() => fileInputRef.current?.click()}>Upload Excel</button>
                <button className="home-secondary-button" type="button" disabled={isLoading} onClick={openSqlDialog}>SQL Server</button>
                <MoreDataSources disabled={isLoading} />
              </>}
            </div>}
            {sourceError && <div className="task-feedback is-failed source-error" role="alert">
              <span className="task-indicator" aria-hidden="true" />
              <div><strong>Couldn’t complete this data source step</strong><p>{error}</p></div>
              {canRetryLastOperation && <button type="button" onClick={retryLastOperation}>Retry</button>}
            </div>}
            {operation && operation !== 'Inspecting workbook' && sourceOperation && !sqlDialogOpen && <div className="task-feedback is-running source-progress" role="status"><span className="task-indicator" aria-hidden="true" /><div><strong>{operation}</strong><p>AURA is preparing your data source…</p></div><button type="button" onClick={cancelOperation}>Cancel</button></div>}

            {tables.length > 0 && <div className="model-inspector">
              <label className="select-label" htmlFor="table-select">Scope</label>
              <select id="table-select" value={selectedTable} disabled={isLoading} onChange={(event) => void changeScope(event.target.value)}>
                <option value="">Auto-detect</option>
                {tables.map((table) => <option key={tableKey(table)} value={tableKey(table)}>{tableKey(table)}</option>)}
              </select>
              {selectedModelTable && <details className="studio-source-details" open={Boolean(selectedModelTable.parsingReview && !selectedModelTable.parsingReview.confirmed)}>
                <summary>Source details{selectedModelTable.parsingReview && !selectedModelTable.parsingReview.confirmed ? ' · review required' : ''}</summary>
                <p className="source-summary">Using {selectedModelTable.sourceSheet || selectedModelTable.name}{selectedModelTable.sourceRange ? ` · ${selectedModelTable.sourceRange}` : ''}</p>
                {selectedModelTable.parsingReview && !selectedModelTable.parsingReview.confirmed && <div className="clarification-state">
                  <strong>Check the detected table before planning</strong>
                  <p>Range: {selectedModelTable.parsingReview.range}; header row: {selectedModelTable.parsingReview.headerRow}.</p>
                  <ul>{selectedModelTable.parsingReview.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  <p>Compare the fields and sample rows below with Excel. Confirm only if the range and header are correct.</p>
                  <button type="button" disabled={isLoading} onClick={handleConfirmTable}>Confirm detected table</button>
                </div>}
                <details className="field-details"><summary><strong>{selectedModelTable.columns.length}</strong> fields discovered <span>Show field details</span></summary><ul className="field-list">{selectedModelTable.columns.map((column) => <li key={column.name}><span>{column.name}</span><small>{column.dataType || 'unknown'}</small></li>)}</ul></details>
                {selectedModelTable.sampleRows && selectedModelTable.sampleRows.length > 0 && <div className="sample-preview">
                  <p>Sample rows</p>
                  <div className="sample-table-scroll"><table><thead><tr>{selectedModelTable.columns.map((column) => <th key={column.name}>{column.name}</th>)}</tr></thead>
                    <tbody>{selectedModelTable.sampleRows.map((row, rowIndex) => <tr key={rowIndex}>{selectedModelTable.columns.map((column) => {
                      const value = row[column.name];
                      const displayValue = value == null
                        ? '—'
                        : `${String(value)}${column.formatString?.endsWith('\\%') ? '%' : ''}`;
                      return <td key={column.name}>{displayValue}</td>;
                    })}</tr>)}</tbody>
                  </table></div>
                </div>}
              </details>}
            </div>}
          </section>

          <section className="work-panel builder-panel assistant-panel">
            <div className="panel-heading">
              <div><span className="step-number">02</span><h2>Report request</h2></div>
              <span className="subtle-label">NATURAL LANGUAGE</span>
            </div>
            <div className="panel-divider" />
            <div className="panel-heading plan-heading">
              <div><span className="step-number">03</span><h2>Review plan</h2></div>
              {plan && <span className="plan-badge">{totalVisuals} VISUAL{totalVisuals === 1 ? '' : 'S'} READY</span>}
            </div>
            {plan ? <div className="plan-review">
              <h3>{currentPage.page}</h3>
              {currentPage.visuals.map((visual, index) => <div className="visual-plan" key={`${visual.type}-${index}`}>
                <div className="visual-type"><span className="chart-glyph" aria-hidden="true">{visual.type === 'lineChart' || visual.type === 'areaChart' ? '⌁' : visual.type === 'pieChart' || visual.type === 'donutChart' ? '◔' : visual.type === 'clusteredBarChart' ? '☰' : '▥'}</span><strong>{visualTypeLabel(visual.type)}</strong></div>
                {visualBindings(visual).map(([label, binding]) => <div className="binding-row" key={label}><span>{label}</span><strong>{binding}</strong></div>)}
              </div>)}
            </div> : <div className="empty-plan"><span aria-hidden="true">⌁</span><p>Your resolved visual and field bindings will appear here.</p></div>}
            <div className={`assistant-chat ${sourceId ? 'is-connected' : 'is-disconnected'}`}>
              <div className="panel-heading chat-heading">
                <div><span className="studio-chat-spark" aria-hidden="true">✦</span><h2>Ask Aura</h2><span className="studio-chat-subtitle">· questions or changes</span></div>
              </div>
              <div ref={threadRef} className="chat-thread" role="log" aria-label="Conversation" aria-live="polite" tabIndex={0} onScroll={(event) => {
                const thread = event.currentTarget;
                followThreadRef.current = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 48;
              }}>
                {chatMessages.length === 0 && <div className="assistant-welcome">
                  <span className="assistant-welcome-mark" aria-hidden="true"><AuraLogoMark compact /></span>
                  <strong>{sourceId ? 'What would you like to learn from your data?' : 'Connect your data to start with Aura.'}</strong>
                  <p>{sourceId ? 'Ask a question about your data or describe the report you want to build.' : 'Upload an Excel workbook or connect to SQL Server. I’ll help you explore it and build a report.'}</p>
                </div>}
                {chatMessages.map((message, index) => <Fragment key={index}><div className={`chat-message is-${message.role}`}>
                  {message.role === 'assistant' ? <div className="chat-answer"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>{message.text}</ReactMarkdown></div> : message.text}
                  {message.evidence && message.evidence.findings.length > 0 && <details className="chat-evidence"><summary>Supporting data</summary><p>{message.evidence.method}</p>
                    {message.evidence.findings.map((row, i) => <div className="evidence-finding" key={i}><EvidenceValue value={row} /></div>)}</details>}
                </div>{index === readyMessageIndex && <ReportReadyCard />}</Fragment>)}
                {((operation && !sourceOperation) || (error && !sourceError)) && <div className={`task-feedback ${error && !sourceError ? 'is-failed' : 'is-running'}`} role={error && !sourceError ? 'alert' : 'status'}>
                  <span className="task-indicator" aria-hidden="true" />
                  <div><strong>{error && !sourceError ? 'I hit a problem' : operation}</strong>{!(error && chatMessages[chatMessages.length - 1]?.role === 'assistant' && chatMessages[chatMessages.length - 1]?.text === error) && <p>{error && !sourceError ? error : 'I’m working on that now…'}</p>}</div>
                  {error && !sourceError && canRetryLastOperation && !(planApproved && plan && planValidated && agentExecution?.status !== 'awaiting_approval' && (!reportId || validation?.passed === false)) && <button type="button" onClick={retryLastOperation}>Retry</button>}
                </div>}
                {clarification && <div className="clarification-state">
                  <strong>{clarification.question}</strong>
                  {clarification.options && <div className="clarification-options">{clarification.options.map((option) =>
                    <button key={option} type="button" disabled={isLoading} onClick={() => void handleClarification(option)}>{option}</button>,
                  )}</div>}
                </div>}
                {draftSuperseded && <div className="conversation-action draft-superseded" role="alert"><span aria-hidden="true">!</span><div><strong>Draft not available for approval</strong><p>The visible preview is your last successful draft. The latest requested change failed, so it cannot be approved or generated. Revise the request or retry after correcting the reported binding issue.</p></div></div>}
                {draftReadyForApproval && !planApproved && (!reportId || validation?.passed === false) && agentExecution?.status !== 'awaiting_approval' && <div className="conversation-action approval-prompt"><span aria-hidden="true">✓</span><div><strong>Review the preview</strong><p>Use the header to approve the report plan. PBIP generation will be a separate step.</p></div></div>}
                {planApproved && plan && planValidated && agentExecution?.status !== 'awaiting_approval' && (!reportId || validation?.passed === false) && <div className="conversation-action approval-prompt"><span aria-hidden="true">✓</span><div><strong>Report plan approved</strong><p>{validation?.passed === false ? 'Use Prepare fix to review a proposed repair.' : 'Use the header to generate the Power BI project when you’re ready.'}</p></div></div>}
                {agentExecution?.status === 'awaiting_approval' && <div className="conversation-action approval-prompt"><span aria-hidden="true">✓</span><div><strong>Ready for your approval</strong><p>Use the header to approve this change. Nothing will be applied until you approve.</p></div></div>}
                {agentExecution?.status === 'needs_user' && !newQuestion && <div className="conversation-action guidance-prompt"><span aria-hidden="true">?</span><div><strong>Reply to continue</strong>{agentExecution.help?.options && agentExecution.help.options.length > 0 ? <div className="conversation-options">{agentExecution.help.options.map((option) => <button type="button" key={option} onClick={() => handleExecutionGuidance(option)}>{option.replace(/^Use /, '')}</button>)}</div> : <p>Type your clarification below.</p>}<button type="button" disabled={isLoading} onClick={() => setNewQuestion(true)}>Ask a new question</button></div></div>}
                {reportId && validation?.passed === true && readyMessageIndex < 0 && <ReportReadyCard />}
                {kpis.length > 0 && <details className="result-section"><summary>KPI suggestions <span>{kpis.length}</span></summary><div className="kpi-proposals">{kpis.map((kpi) => { const key = `${kpi.table}.${kpi.name}`; const approved = approvedKpis.includes(key); return <article className="kpi-proposal" key={key}><div><strong>{kpi.name}</strong><span>{kpi.table}</span></div><code>{kpi.dax || kpi.formula}</code><p>{kpi.explanation}</p><button type="button" disabled={approved} onClick={() => approveKpiProposal(kpi)}>{approved ? 'Approved' : plan ? 'Approve and add to plan' : 'Approve proposal'}</button></article>; })}</div></details>}
                {validation && !validation.passed && <div className="validation-state failed">
                  <strong>Validation needs attention</strong>
                  {validation.calculationAccuracy && <p>{validation.calculationAccuracy.checks.filter((check) => check.passed).length}/{validation.calculationAccuracy.checks.length} calculation checks passed across measures, totals, filters, and joins.</p>}
                  {validation.errors.length > 0 && <ul>{validation.errors.map((item) => <li key={item}>{item}</li>)}</ul>}
                  {validation.warnings.map((item) => <p key={item}>{item}</p>)}
                </div>}
                {(mappingResult.length > 0 || relationshipResult.length > 0 || agentExecution || (validation && validation.passed)) && <details className="conversation-details" key={agentExecution?.runId || reportId || 'details'}>
                  <summary>Workbook and run details</summary>
                {mappingResult.length > 0 && <details className="result-section"><summary>Field mappings <span>{mappingResult.length} {mappingResult.length === 1 ? 'table' : 'tables'}</span></summary><div className="assistant-results">{mappingResult.map((table) => <article key={table.table}><strong>{table.table}</strong><div className="result-tags">{table.fields.slice(0, 8).map((field) => <span key={field.field}>{field.field}<small>{field.role}</small></span>)}</div>{table.fields.length > 8 && <p>+{table.fields.length - 8} more fields</p>}</article>)}</div></details>}
                {relationshipResult.length > 0 && <details className="result-section"><summary>Relationships <span>{relationshipResult.length}</span></summary><div className="assistant-results">{relationshipResult.map((relationship) => <article key={`${relationship.source_table}.${relationship.source_column}-${relationship.target_table}.${relationship.target_column}`}><strong>{relationship.source_table} → {relationship.target_table}</strong><p>{relationship.source_column} matches {relationship.target_column}</p></article>)}</div></details>}
              {agentExecution && <details className="execution-details">
                <summary><span>Audit trail</span><small>{agentExecution.completedSteps.length}/{agentExecution.steps.length} steps · {runStatusLabel(agentExecution.status)}</small></summary>
                <section className="execution-card" aria-label="Agent execution progress">
                  <div className="execution-heading"><div><strong>Run record</strong><span>Plan v{agentExecution.planVersion.slice(0, 8)}</span></div><span className={`run-status is-${agentExecution.status}`}>{runStatusLabel(agentExecution.status)}</span></div>
                  <dl className="audit-metadata">
                    <div><dt>Request</dt><dd>{agentExecution.goal}</dd></div>
                    <div><dt>Source</dt><dd>{(agentExecution.auditContext?.sourceType || sourceType).toUpperCase()} · {agentExecution.auditContext?.selectedTable || selectedTable || 'No table selected'}</dd></div>
                    <div><dt>Report</dt><dd>{agentExecution.auditContext?.reportName || plan?.page || 'No report plan'}{agentExecution.auditContext?.reportId || reportId ? ` · ${agentExecution.auditContext?.reportId || reportId}` : ''}</dd></div>
                    <div><dt>Started</dt><dd>{formatAuditTimestamp(agentExecution.createdAt || agentExecution.events?.[0]?.at)}</dd></div>
                    <div><dt>Last updated</dt><dd>{formatAuditTimestamp(agentExecution.updatedAt || agentExecution.events?.[agentExecution.events.length - 1]?.at)}</dd></div>
                    <div><dt>Elapsed</dt><dd>{formatAuditDuration(agentExecution.createdAt || agentExecution.events?.[0]?.at, agentExecution.updatedAt || agentExecution.events?.[agentExecution.events.length - 1]?.at)}</dd></div>
                    <div><dt>Planner</dt><dd>{agentExecution.fallbackUsed ? 'Rules fallback used' : agentExecution.planner}</dd></div>
                    {agentExecution.approvals?.map((approval, index) => <div key={`${approval.approvedAt}-${index}`}><dt>Approved by</dt><dd>{approval.approver} · {formatAuditTimestamp(approval.approvedAt)}</dd></div>)}
                  </dl>
                  <div className="audit-identifiers">
                    <div><span>Run ID</span><code>{agentExecution.runId}</code><button type="button" onClick={() => void copyAuditValue('Run ID', agentExecution.runId)}>Copy</button></div>
                    {(agentExecution.traceIds || []).map((traceId) => <div key={traceId}><span>Trace ID</span><code>{traceId}</code><button type="button" onClick={() => void copyAuditValue('Trace ID', traceId)}>Copy</button></div>)}
                  </div>
                  <div className="audit-actions">
                    <button className="button button-outline" type="button" onClick={() => void downloadAuditSummary(agentExecution.runId)}>Download audit JSON</button>
                    <small>Includes run metadata and event history; excludes raw tool output, workbook rows, and credentials.</small>
                  </div>
                  {auditNotice && <p className="audit-notice" role="status">{auditNotice}</p>}
                  <h3 className="audit-timeline-title">Activity timeline</h3>
                  {(agentExecution.events || []).length > 0 ? <ol className="audit-timeline">
                    {agentExecution.events?.map((event, index) => <li key={`${event.at}-${index}`}>
                      <time dateTime={event.at}>{formatAuditTimestamp(event.at)}</time>
                      <div><strong>{auditEventLabel(event.event)}{event.action ? ` · ${humanizeFieldName(event.action)}` : ''}</strong>
                        {event.step !== undefined && <small>Step {event.step + 1}{event.attempt ? ` · attempt ${event.attempt}` : ''}</small>}
                        {event.approver && <small>Approved by {event.approver}</small>}
                        {event.error && <em>{event.error}</em>}
                      </div>
                    </li>)}
                  </ol> : <p className="audit-empty">No activity events were returned for this run.</p>}
                  <details className="audit-steps">
                    <summary>Step outcomes</summary>
                  <ol>{agentExecution.steps.map((step) => <li key={step.index} className={`is-${step.status}`}><span>{step.status === 'completed' ? '✓' : step.status === 'running' ? '…' : step.status === 'needs_user' ? '!' : step.index + 1}</span><div><strong>{humanizeFieldName(step.action)}</strong><small>{step.reason}</small>{step.result && <small>Output validated and passed to the next step.</small>}{step.error && <em>{step.error}</em>}{step.result && <details className="step-evidence"><summary>Technical output</summary><pre>{JSON.stringify(step.result, null, 2)}</pre></details>}</div><b>{step.attempts > 1 ? `${step.attempts} attempts` : ''}</b></li>)}</ol>
                  </details>
                </section>
              </details>}
              {validation?.passed && <div className="validation-state passed">
                <strong>Validation passed</strong>
                {validation.calculationAccuracy && <p>{validation.calculationAccuracy.checks.filter((check) => check.passed).length}/{validation.calculationAccuracy.checks.length} calculation checks passed across measures, totals, filters, and joins.</p>}
                {validation.warnings.map((item) => <p key={item}>{item}</p>)}
              </div>}
              </details>}
              {analysisAnswer && !chatMessages.some(m => m.evidence) && <div className="assistant-results"><h3>Findings</h3><article><strong>{analysisAnswer.answer}</strong><p>{analysisAnswer.method}</p><div className="finding-grid">{analysisAnswer.findings.map((finding, index) => <dl key={index}>{Object.entries(finding).map(([key, value]) => <div key={key}><dt>{humanizeFieldName(key)}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>)}</div></article></div>}
              </div>
              <div className="chat-composer"><textarea rows={2} maxLength={4000} value={chatInput} aria-label="Message Aura" placeholder={!sourceId ? 'Connect a data source to start chatting…' : clarification || (agentExecution?.status === 'needs_user' && !newQuestion) ? 'Reply to the clarification…' : 'Ask a question or describe a report…'} disabled={isLoading || !sourceId} onChange={(event) => setChatInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (clarification && chatInput.trim()) { const answer = chatInput.trim(); setChatInput(''); void handleClarification(answer); } else { void handleChat(); } } }} />{isLoading && operationController.current && !sourceOperation ? <button className="button button-outline" type="button" onClick={cancelOperation}>Stop</button> : <button className="button button-dark" type="button" aria-label="Send" disabled={isLoading || !sourceId || !chatInput.trim()} onClick={() => { if (clarification) { const answer = chatInput.trim(); setChatInput(''); void handleClarification(answer); } else { void handleChat(); } }}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10.5 13.5 10.5-10.5m-10.37 10.83 2.63 6.76c.23.6.35.89.51.98a.5.5 0 0 0 .46 0c.17-.09.28-.38.52-.98l6.59-16.89c.21-.54.31-.81.26-.98a.5.5 0 0 0-.32-.32c-.17-.06-.44.05-.98.26L3.41 9.25c-.6.23-.89.35-.98.52a.5.5 0 0 0 0 .46c.09.17.38.28.98.51l6.76 2.63c.12.05.18.07.23.11a.5.5 0 0 1 .12.12c.04.05.06.11.11.23Z" /></svg></button>}</div>
              <p className="studio-chat-hint">{draftSuperseded ? 'The latest draft failed validation. Revise the request or retry after addressing the reported field binding.' : agentExecution?.status === 'awaiting_approval' ? 'Review and approve the proposed change to continue.' : isLoading && operation === 'Generating report' ? 'Generating the PBIP…' : reportId && validation?.passed ? 'Done. Download the PBIP or open it in Power BI at the top right.' : planApproved && plan && planValidated && validation?.passed === false ? 'Review the validation details, then use Prepare fix at the top right.' : planApproved && plan && planValidated ? 'Next: click Generate PBIP at the top right.' : draftReadyForApproval && !planApproved ? 'When the preview looks right, click Approve report plan at the top right.' : 'Describe the report you need.'}</p>
            </div>

          </section>
        </div>

        {!plan && <section className="dashboard-preview canvas-empty" aria-live="polite">
          <div className="studio-preview-empty">
            <span aria-hidden="true">▤</span>
            <p>{operation === 'Planning report' ? 'Drafting your report…' : 'Describe a report in the chat to see a preview.'}</p>
          </div>
        </section>}

        {plan && <section className="dashboard-preview" aria-live="polite">
          <nav aria-label="Report pages" className="page-tabs">{pages.map((page, i) =>
            <button type="button" aria-pressed={pageIndex === i} key={page.page} onClick={() => selectPage(i)}>{page.page}</button>
          )}<span className="preview-history-controls"><span className="preview-version">Version {Math.max(planHistoryIndex + 1, 1)} of {Math.max(planHistory.length, 1)}</span><button className="preview-history" type="button" disabled={planHistoryIndex <= 0} onClick={() => restorePlanHistory(-1)} aria-label="Undo report edit"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7 4 12l5 5M5 12h9a5 5 0 0 1 0 10" /></svg></button><button className="preview-history" type="button" disabled={planHistoryIndex < 0 || planHistoryIndex >= planHistory.length - 1} onClick={() => restorePlanHistory(1)} aria-label="Redo report edit"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 7 5 5-5 5m4-5h-9a5 5 0 0 0 0 10" /></svg></button></span></nav>
          <div className="preview-heading">
            <div><h2>{currentPage.page}</h2></div>
          </div>
          <div className={`preview-grid ${currentPreview && currentPreview.visuals.length > 4 ? 'is-expanded' : ''}`} data-card-count={currentPreview?.visuals.filter((visual) => visual.type === 'card').length || 0} style={currentPreview ? {
            '--preview-accent': currentPreview.previewTheme.dataColors[0],
            '--preview-background': currentPreview.previewTheme.background,
            '--preview-card': currentPreview.previewTheme.backgroundLight,
            '--preview-text': currentPreview.previewTheme.foreground,
            '--preview-muted': currentPreview.previewTheme.foregroundNeutralSecondary,
          } as CSSProperties : undefined}>
          {!currentPreview && <div className="preview-unavailable" role="status">
            <strong>Preview unavailable for {currentPage.page}</strong>
            <p>{previewMessage || 'The report plan contains this page, but its preview data has not loaded yet.'}</p>
            <button className="button button-outline" type="button" disabled={isLoading} onClick={() => void retryPreview()}>Retry preview</button>
          </div>}
          {currentPreview?.visuals.some((visual) => visual.type === 'slicer') && <div className="preview-slicer-row">
            {currentPreview.visuals.map((visual, index) => {
              if (visual.type !== 'slicer') return null;
              const previewTitle = visual.title || visual.field?.field || visualTypeLabel(visual.type);
              const field = visual.filterKey || `${visual.field?.table}.${visual.field?.field}`;
              return <div className="preview-visual preview-visual--slicer" key={`${visual.type}-${index}`}>
                <div className="slicer-preview"><label><span>{previewTitle}</span><select aria-label={`Filter ${previewTitle}`} value={(previewFilters[field] || (visual.syncGroup ? syncedPreviewFilters[visual.syncGroup] : undefined) || [])[0] || ''} onChange={(event) => void updatePreviewFilter(field, event.target.value ? [event.target.value] : [])}><option value="">All</option>{(visual.options || []).map((option) => <option key={option} value={option}>{option}</option>)}</select></label></div>
              </div>;
            })}
          </div>}
          {currentPreview?.visuals.map((visual, index) => {
            if (visual.type === 'slicer') return null;
            const maximum = Math.max(...visual.data.map((point) => point.value), 0);
            const minimum = Math.min(...visual.data.map((point) => point.value), 0);
            const valueRange = maximum - minimum || 1;
            const lineSeries = visual.series?.length
              ? visual.series
              : [{ name: visual.value?.field || '', data: visual.data }];
            const lineMinimum = Math.min(...lineSeries.flatMap((series) => series.data.map((point) => point.value)), 0);
            const lineMaximum = Math.max(...lineSeries.flatMap((series) => series.data.map((point) => point.value)), 0);
            const lineRange = lineMaximum - lineMinimum || 1;
            const lineColors = currentPreview.previewTheme.dataColors;
            const pieColors = currentPreview.previewTheme.dataColors;
            let pieEnd = 0;
            const pieSlices = visual.data.filter((point) => point.value > 0).map((point, pointIndex) => {
              const start = pieEnd;
              pieEnd += point.value / visual.data.reduce((sum, item) => sum + Math.max(item.value, 0), 0) * 100;
              return { ...point, color: pieColors[pointIndex % pieColors.length], start, end: pieEnd };
            });
            const pieTotal = pieSlices.reduce((sum, point) => sum + point.value, 0);
            const pieBackground = `conic-gradient(${pieSlices.map((point) => `${point.color} ${point.start}% ${point.end}%`).join(', ')})`;
            const waterfallPoints = visual.data.reduce<Array<{ label: string; value: number; start: number; end: number }>>((points, point) => {
              const start = points.length ? points[points.length - 1].end : 0;
              points.push({ label: point.label, value: point.value, start, end: start + point.value });
              return points;
            }, []);
            const waterfallMinimum = Math.min(0, ...waterfallPoints.flatMap((point) => [point.start, point.end]));
            const waterfallMaximum = Math.max(0, ...waterfallPoints.flatMap((point) => [point.start, point.end]));
            const waterfallRange = waterfallMaximum - waterfallMinimum || 1;
            const previewTitle = visual.title || (visual.type === 'card' || visual.type === 'multiRowCard' || visual.type === 'kpi'
              ? visual.value?.field
              : visual.type === 'gauge'
                ? visual.value?.field
                : visualTypeLabel(visual.type));
            return <div className={`preview-visual preview-visual--${visual.type} ${visual.type === 'card' ? 'powerbi-card' : ''}`} key={`${visual.type}-${index}`}>
              <div className="preview-visual-label">
                <strong>{previewTitle}</strong>
                <span>{visualBindings(visual).map(([label, binding]) => `${label}: ${binding}`).join(' · ')}</span>
              </div>
              {!supportedPreviewTypes.has(visual.type) ? <p className="preview-message unsupported-preview"><strong>{visualTypeLabel(visual.type)} preview is not supported.</strong> Generate the PBIP to view this visual in Power BI.</p>
              : ['card', 'multiRowCard', 'kpi', 'gauge'].includes(visual.type) ? <div className={`kpi-preview ${visual.type === 'gauge' ? 'gauge-preview' : ''}`}><strong>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(visual.data[0]?.value || 0)}</strong></div>
              : visual.type === 'table' && visual.tableRows ? <div className="table-preview-scroll"><table className="matrix-preview"><thead><tr>{[...(visual.rows || []), ...(visual.values || [])].map((field, fieldIndex) => <th key={`${field.table}-${field.field}-${fieldIndex}`}>{field.field}</th>)}</tr></thead><tbody>{visual.tableRows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell == null ? '—' : typeof cell === 'number' ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(cell) : String(cell)}</td>)}</tr>)}</tbody></table></div>
              : visual.type === 'pivotTable' || visual.type === 'table' ? <table className="matrix-preview"><thead><tr><th>{visual.rows?.[0]?.field}</th><th>{visual.values?.[0]?.field}</th></tr></thead><tbody>{visual.data.map((point) => <tr key={point.label}><td>{point.label}</td><td>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(point.value)}</td></tr>)}</tbody></table>
              : visual.comparison && visual.series ? <div className="comparison-preview">{visual.series.map((series) => <div key={series.name}><strong>{series.name}</strong>{series.data.map((point) => <p key={point.label}>{point.label}: {new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(point.value)}</p>)}</div>)}</div>
              : visual.data.length > 0 && visual.type === 'waterfallChart' ? <div className="waterfall-preview" role="img" aria-label={`Waterfall chart of ${visual.value?.field || visual.values?.[0]?.field || 'Value'} by ${visual.category?.field || 'Category'}`}>
                {waterfallPoints.map((point) => <div className="waterfall-column" key={point.label}>
                  <span className="chart-value">{new Intl.NumberFormat(undefined, { maximumFractionDigits: 0, signDisplay: 'exceptZero' }).format(point.value)}</span>
                  <div className="waterfall-track"><div className={`waterfall-bar ${point.value < 0 ? 'is-negative' : ''}`} style={{ top: `${(waterfallMaximum - Math.max(point.start, point.end)) / waterfallRange * 100}%`, height: `${Math.abs(point.value) / waterfallRange * 100}%` }} /></div>
                  <span className="chart-category" title={point.label}>{point.label}</span>
                </div>)}
              </div>
              : visual.data.length > 0 && (visual.type === 'pieChart' || visual.type === 'donutChart') ? pieSlices.length > 0 ? <div className="pie-preview" role="img" aria-label={`${visualTypeLabel(visual.type)} of ${visual.value?.field || visual.values?.[0]?.field || 'Value'} by ${visual.category?.field || 'Category'}`}>
                <div className={`pie-graphic ${visual.type === 'donutChart' ? 'is-donut' : ''}`} style={{ backgroundImage: pieBackground }}>
                  {visual.type === 'donutChart' && <span>{new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(pieTotal)}</span>}
                </div>
                <ul className="pie-legend">{pieSlices.map((point) => <li key={point.label}>
                  <span className="pie-key" style={{ backgroundColor: point.color }} />
                  <span className="pie-label" title={point.label}>{point.label}</span>
                  <strong>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(point.value)}</strong>
                </li>)}</ul>
              </div> : <p className="preview-message">Pie charts need positive values to show category shares.</p> : visual.data.length > 0 && (visual.type === 'lineChart' || visual.type === 'areaChart') ? <svg className="chart-line" viewBox="0 0 800 240" role="img" aria-label={`${visual.value?.field || visual.values?.[0]?.field || 'Value'} by ${visual.category?.field || 'Category'}`}>
                <line className="line-axis" x1="24" y1="190" x2="776" y2="190" />
                {visual.legend && lineSeries.map((series, seriesIndex) => <g key={`legend-${series.name}`}>
                  <circle cx={34 + seriesIndex * 190} cy="16" r="4" fill={lineColors[seriesIndex % lineColors.length]} />
                  <text className="line-category" x={44 + seriesIndex * 190} y="20">{series.name}</text>
                </g>)}
                {lineSeries.map((series, seriesIndex) => {
                  const points = series.data.map((point, pointIndex) => {
                    const x = series.data.length === 1 ? 400 : 40 + pointIndex * 720 / (series.data.length - 1);
                    const y = 176 - (point.value - lineMinimum) / lineRange * 140;
                    return { ...point, x, y };
                  });
                  return <g key={series.name}>
                    {visual.type === 'areaChart' && seriesIndex === 0 && <polygon className="area-fill" style={{ fill: lineColors[0], fillOpacity: 0.14 }} points={`${points[0].x},190 ${points.map((point) => `${point.x},${point.y}`).join(' ')} ${points[points.length - 1].x},190`} />}
                    <polyline className="line-series" style={{ stroke: lineColors[seriesIndex % lineColors.length] }} points={points.map((point) => `${point.x},${point.y}`).join(' ')} />
                    {points.map((point) => <circle className="line-point" style={{ stroke: lineColors[seriesIndex % lineColors.length] }} key={`${series.name}-${point.label}`} cx={point.x} cy={point.y} r="5" />)}
                  </g>;
                })}
                {visual.data.map((point, pointIndex) => {
                  if (pointIndex % Math.ceil(visual.data.length / 8) !== 0 && pointIndex !== visual.data.length - 1) return null;
                  const x = visual.data.length === 1 ? 400 : 40 + pointIndex * 720 / (visual.data.length - 1);
                  return <text className="line-category" key={point.label} x={x} y="218" textAnchor="middle">{point.label}</text>;
                })}
              </svg> : visual.data.length > 0 && visual.type === 'clusteredBarChart' ? <div className="chart-bars-horizontal" role="img" aria-label={`${visual.value?.field || visual.values?.[0]?.field || 'Value'} by ${visual.category?.field || 'Category'}`}>
                {visual.data.map((point) => <div className="horizontal-bar-row" key={point.label}>
                  <span className="horizontal-bar-label" title={point.label}>{point.label}</span>
                  <div className="horizontal-bar-track"><i className="horizontal-zero" style={{ left: `${(-minimum / valueRange) * 100}%` }} /><div className={`horizontal-bar ${point.value < 0 ? 'is-negative' : ''}`} style={{ left: `${point.value < 0 ? ((-minimum + point.value) / valueRange) * 100 : (-minimum / valueRange) * 100}%`, width: `${Math.abs(point.value) / valueRange * 100}%` }} /></div>
                  <strong>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(point.value)}</strong>
                </div>)}
              </div> : visual.data.length > 0 ? <div className="chart-columns" role="img" aria-label={`${visual.value?.field || visual.values?.[0]?.field || 'Value'} by ${visual.category?.field || 'Category'}`}>
                {visual.data.map((point) => <div className="chart-column" key={point.label}>
                  <span className="chart-value">{new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(point.value)}</span>
                  <div className="chart-track"><i className="chart-zero" style={{ top: `${maximum / valueRange * 100}%` }} /><div className={`chart-bar ${point.value < 0 ? 'is-negative' : ''}`} style={{ top: `${point.value >= 0 ? (maximum - point.value) / valueRange * 100 : maximum / valueRange * 100}%`, height: `${Math.abs(point.value) / valueRange * 100}%` }} /></div>
                  <span className="chart-category" title={point.label}>{point.label}</span>
                </div>)}
              </div> : <p className="preview-message">No numeric rows were found for this visual.</p>}
              {visual.previewNotice && <p className="preview-message">{visual.previewNotice}</p>}
            </div>;
          })}
          </div>
          {previewMessage && <div className="studio-preview-error" role="alert"><p className="preview-message">{previewMessage}</p>{!preview && <button className="button button-outline" type="button" disabled={isLoading} onClick={() => void retryPreview()}>Retry preview</button>}</div>}
        </section>}

        <footer className={`status-bar ${error ? 'has-error' : ''}`} role="status" aria-live="polite">
          <span className={`connection-dot ${sourceId ? 'is-connected' : ''}`} />
          <p>{sourceError ? 'See the data source message above for details.' : error || status || 'Ready when you are. Connect a source to begin.'}</p>
          {reportId && validation?.passed && <span className="status-report">{reportId}</span>}
        </footer>
      </main>
      </>}
      </div>
    </div>
  );
}

export default App;
