export type DataTable = {
  name: string;
  schema?: string;
  columns: Array<{ name: string; dataType?: string; formatString?: string | null }>;
  sampleRows?: Array<Record<string, unknown>>;
  metricValues?: string[];
  sourceRange?: string;
  sourceSheet?: string;
  parsingReview?: { reasons: string[]; confirmed: boolean; range: string; headerRow: number } | null;
  measureProposals?: Array<{ name: string; column: string; aggregation: string; status: string }>;
};

export type ReportPlan = {
  additionalPages?: ReportPlan[];
  page: string;
  pageNavigation?: boolean;
  planningEvidence?: {
    planner?: string;
    resolvedTable?: string | null;
    recoveredFrom?: string | null;
  };
  design?: {
    theme?: 'dar_light' | 'executive' | 'corporate' | 'finance' | 'modern' | 'minimal' | 'dark';
    layout?: 'kpi_4_plus_2x2' | 'kpi_grid_4_plus_2x2' | 'kpi_trend_comparison' | 'analytics_dashboard' | 'compact_dashboard';
    visual_style?: 'minimal' | 'standard' | 'data_dense';
    kpi_style?: 'accent_card' | 'accent_strip' | 'clean' | 'outlined';
    slicer_style?: 'compact' | 'horizontal' | 'date';
    show_variance?: boolean;
    show_icons?: boolean;
    auto_kpi_cards?: boolean;
    auto_time_slicers?: boolean;
    colors?: Record<string, string>;
  };
  visuals: Array<{
    type: string;
    title?: string;
    target?: { table: string; field: string; fieldType: string };
    minimum?: { table: string; field: string; fieldType: string };
    maximum?: { table: string; field: string; fieldType: string };
    trend?: { table: string; field: string; fieldType: string };
    size?: { table: string; field: string; fieldType: string };
    columns?: Array<{ table: string; field: string; fieldType: string }>;
    tooltips?: Array<{ table: string; field: string; fieldType: string }>;

    icon?: 'globe' | 'finance' | 'chart' | 'building' | 'product' | 'customer' | 'calendar' | 'location' | 'shopping' | 'currency';
    category?: { table: string; field: string; fieldType: string };
    categoryHierarchy?: Array<{ table: string; field: string; fieldType: string }>;
    value?: { table: string; field: string; fieldType: string };
    values?: Array<{ table: string; field: string; fieldType: string }>;
    rows?: Array<{ table: string; field: string; fieldType: string }>;
    field?: { table: string; field: string; fieldType: string };
    syncGroup?: string;
    legend?: { table: string; field: string; fieldType: string };
    legendBreakdown?: Array<{ table: string; field: string; fieldType: string }>;
    comparison?: boolean | {
      value: { table: string; field: string; fieldType: string };
      label: 'previous_year' | 'previous_period' | 'target' | 'variance';
      direction: 'positive' | 'negative';
    };
  }>;
};

export type PlanClarification = {
  status: 'needs_clarification';
  question: string;
  options?: string[];
};

export type AIStatus = {
  enabled: boolean;
  provider: string | null;
  model: string | null;
  configured: boolean;
};

export type AnalysisAnswer = {
  answer: string;
  method: string;
  sourceFields: string[];
  findings: Array<Record<string, unknown>>;
  filters: Array<Record<string, string>>;
  nextStep?: string;
};

export type ReportPreview = {
  additionalPages?: ReportPreview[];
  page: string;
  previewTheme: {
    dataColors: string[];
    background: string;
    backgroundLight: string;
    foreground: string;
    foregroundNeutralSecondary: string;
    tableAccent: string;
  };
  visuals: Array<ReportPlan['visuals'][number] & {
    previewLimit?: number;
    previewNotice?: string;
    data: Array<{ label: string; value: number }>;
    tableRows?: Array<Array<string | number | boolean | null>>;
    options?: string[];
    filterKey?: string;
    series?: Array<{ name: string; data: Array<{ label: string; value: number }> }>;
  }>;
};

export type AgentRun = {
  runId: string;
  sourceId: string;
  goal: string;
  planVersion: string;
  status: 'running' | 'awaiting_approval' | 'completed' | 'needs_user' | 'failed' | 'cancelled';
  planner: string;
  fallbackUsed: boolean;
  retryLimit: number;
  currentStep: number | null;
  completedSteps: number[];
  steps: Array<{ index: number; action: string; reason: string; status: string; attempts: number; result?: Record<string, unknown>; error?: string | null }>;
  createdAt?: string;
  updatedAt?: string;
  traceIds?: string[];
  auditContext?: { sourceType?: string | null; selectedTable?: string | null; reportName?: string | null; reportId?: string | null };
  approvals?: Array<{ planVersion: string; approver: string; approvedAt: string }>;
  help?: { message: string; step: number; error?: string; options?: string[] };
  events?: Array<{ at: string; event: string; step?: number; action?: string; attempt?: number; error?: string; approver?: string; planVersion?: string; reason?: string; completedSteps?: number[]; repair?: Record<string, unknown> }>;
};

export type AgentRunHistoryItem = {
  runId: string;
  status: AgentRun['status'];
  goal: string;
  sourceId: string;
  createdAt?: string;
  updatedAt?: string;
  sourceType?: string | null;
  selectedTable?: string | null;
  reportName?: string | null;
  stepCount: number;
  completedStepCount: number;
};

export type SavedReport = {
  reportId: string;
  title: string;
  chatTitle: string;
  chatDeleted: boolean;
  status: string;
  pageCount: number;
  visualCount: number;
  updatedAt: string;
  sourceId?: string;
  sourceName?: string | null;
  sourceType?: 'excel' | 'sql' | null;
  reportSpec: ReportPlan;
};

export type CurrentIdentity = {
  userId: string;
  isDemo: boolean;
};

export type AgentRunAudit = {
  format: 'autobi-run-audit';
  version: number;
  exportedAt: string;
  notice: string;
  run: {
    runId: string;
    traceIds: string[];
    status: AgentRun['status'];
    startedAt?: string | null;
    lastUpdatedAt?: string | null;
    sourceId?: string;
    sourceType?: string | null;
    selectedTable?: string | null;
    reportName?: string | null;
    reportId?: string | null;
    request: string;
    planVersion: string;
    planner: string;
    fallbackUsed: boolean;
    approvals: Array<{ approver: string; approvedAt: string; planVersion: string }>;
    steps: Array<{ index: number; action: string; reason: string; status: string; attempts: number; error?: string | null }>;
    events: Array<{ at: string; event: string; step?: number; action?: string; attempt?: number; approver?: string; planVersion?: string; reason?: string; error?: string; completedSteps?: number[]; traceId?: string }>;
  };
};

async function responseError(response: Response, fallback: string): Promise<Error> {
  const body = await response.text();
  try {
    const parsed = JSON.parse(body) as { detail?: string };
    return new Error(parsed.detail || fallback);
  } catch {
    return new Error(body || fallback);
  }
}

let activeAgentRun: { runId: string; traceId: string } | null = null;

export function beginAgentRun(): string {
  activeAgentRun = { runId: crypto.randomUUID(), traceId: crypto.randomUUID().replace(/-/g, '') };
  return activeAgentRun.runId;
}

export function getActiveAgentTraceId(): string | null {
  return activeAgentRun?.traceId || null;
}

export function endAgentRun(agentRunId: string): void {
  if (activeAgentRun?.runId === agentRunId) activeAgentRun = null;
}

type ApiOptions = { signal?: AbortSignal; timeoutMs?: number };

export function getChatSessionId(): string {
  const key = 'autobi_chat_session:' + (sessionStorage.getItem('autobi_user') || 'demo-user');
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(key, id);
  }
  return id;
}

export function startNewChatSession(): string {
  const id = crypto.randomUUID();
  sessionStorage.setItem('autobi_chat_session:' + (sessionStorage.getItem('autobi_user') || 'demo-user'), id);
  return id;
}

function requestSignal(signal?: AbortSignal, timeoutMs = 30000): AbortSignal {
  return signal ? AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs);
}

async function apiFetch<T>(path: string, options: RequestInit = {}, apiOptions: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  const apiToken = sessionStorage.getItem('autobi_api_token');
  if (apiToken) headers.set('Authorization', `Bearer ${apiToken}`);
  headers.set('X-AURA-User', sessionStorage.getItem('autobi_user') || 'demo-user');
  headers.set('X-AURA-Chat-ID', getChatSessionId());
  if (activeAgentRun) {
    const spanId = crypto.randomUUID().replace(/-/g, '').slice(0, 16);
    headers.set('X-Agent-Run-ID', activeAgentRun.runId);
    headers.set('traceparent', `00-${activeAgentRun.traceId}-${spanId}-01`);
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    signal: requestSignal(apiOptions.signal, apiOptions.timeoutMs),
  });

  if (!response.ok) {
    throw await responseError(response, 'Request failed');
  }

  return response.json() as Promise<T>;
}

export type ExcelUploadProgress =
  | { phase: 'uploading'; percent: number | null }
  | { phase: 'processing'; stage: ExcelProcessingStage };

export type ExcelProcessingStage =
  | 'reading_workbook'
  | 'detecting_tables'
  | 'profiling_columns'
  | 'reading_formula_results'
  | 'loading_cached_profile'
  | 'analyzing_workbook'
  | 'finalizing';

type ExcelUploadResult = {
  sourceId: string;
  sourceType: string;
  sheets: DataTable[];
  formulaCellCount: number | null;
  aiEnabled: boolean;
  aiAnalyzed: boolean;
  warnings: string[];
};

const excelProcessingStages: ExcelProcessingStage[] = [
  'reading_workbook', 'detecting_tables', 'profiling_columns', 'reading_formula_results',
  'loading_cached_profile', 'analyzing_workbook', 'finalizing',
];

export async function uploadExcel(file: File, signal?: AbortSignal, onProgress?: (progress: ExcelUploadProgress) => void): Promise<ExcelUploadResult> {
  const form = new FormData();
  form.append('file', file);

  const uploadHeaders = new Headers();
  const apiToken = sessionStorage.getItem('autobi_api_token');
  if (apiToken) uploadHeaders.set('Authorization', `Bearer ${apiToken}`);
  uploadHeaders.set('X-AURA-User', sessionStorage.getItem('autobi_user') || 'demo-user');
  uploadHeaders.set('X-AURA-Chat-ID', getChatSessionId());
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    let responseOffset = 0;
    let pendingText = '';
    let result: ExcelUploadResult | undefined;
    let streamError: Error | undefined;
    const abort = () => request.abort();
    const cleanup = () => signal?.removeEventListener('abort', abort);
    const readEvents = () => {
      pendingText += request.responseText.slice(responseOffset);
      responseOffset = request.responseText.length;
      const lines = pendingText.split('\n');
      pendingText = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event: unknown = JSON.parse(line);
          if (!event || typeof event !== 'object' || !('type' in event)) throw new Error('Invalid event');
          if (event.type === 'progress' && 'stage' in event && typeof event.stage === 'string') {
            const stage = excelProcessingStages.find((value) => value === event.stage);
            if (!stage) throw new Error('Invalid stage');
            onProgress?.({ phase: 'processing', stage });
          } else if (event.type === 'result' && 'result' in event && event.result && typeof event.result === 'object'
            && 'sourceId' in event.result && typeof event.result.sourceId === 'string'
            && 'sheets' in event.result && Array.isArray(event.result.sheets)
            && 'warnings' in event.result && Array.isArray(event.result.warnings)
            && 'sourceType' in event.result && typeof event.result.sourceType === 'string'
            && 'formulaCellCount' in event.result && (event.result.formulaCellCount === null || (typeof event.result.formulaCellCount === 'number' && Number.isSafeInteger(event.result.formulaCellCount) && event.result.formulaCellCount >= 0))
            && 'aiEnabled' in event.result && typeof event.result.aiEnabled === 'boolean'
            && 'aiAnalyzed' in event.result && typeof event.result.aiAnalyzed === 'boolean') {
            result = {
              sourceId: event.result.sourceId,
              sourceType: event.result.sourceType,
              sheets: event.result.sheets,
              formulaCellCount: event.result.formulaCellCount,
              warnings: event.result.warnings,
              aiEnabled: event.result.aiEnabled,
              aiAnalyzed: event.result.aiAnalyzed,
            };
          } else if (event.type === 'error' && 'detail' in event && typeof event.detail === 'string') {
            streamError = new Error(event.detail);
          } else {
            throw new Error('Invalid event');
          }
        } catch {
          streamError = new Error('Excel upload returned an invalid response.');
        }
      }
    };
    if (signal?.aborted) {
      reject(new DOMException('Upload cancelled', 'AbortError'));
      return;
    }
    request.open('POST', '/api/datasource/excel/stream');
    request.timeout = 120000;
    uploadHeaders.forEach((value, key) => request.setRequestHeader(key, value));
    request.upload.onprogress = (event) => onProgress?.({
      phase: 'uploading',
      percent: event.lengthComputable && event.total > 0 ? Math.min(100, Math.round(event.loaded / event.total * 100)) : null,
    });
    request.onprogress = () => {
      if (request.status >= 200 && request.status < 300) readEvents();
    };
    request.onload = () => {
      cleanup();
      if (request.status < 200 || request.status >= 300) {
        void responseError(new Response(request.responseText, { status: request.status || 500 }), 'Excel upload failed').then(reject);
        return;
      }
      readEvents();
      if (streamError) reject(streamError);
      else if (pendingText.trim() || !result) reject(new Error('Excel upload returned an incomplete response.'));
      else resolve(result);
    };
    request.onerror = () => { cleanup(); reject(new Error('Excel upload failed: network error.')); };
    request.ontimeout = () => { cleanup(); reject(new Error('Excel upload timed out.')); };
    request.onabort = () => { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')); };
    signal?.addEventListener('abort', abort, { once: true });
    onProgress?.({ phase: 'uploading', percent: null });
    request.send(form);
  });
}

export async function confirmExcelTable(sourceId: string, table: string, range: string, headerRow: number): Promise<{ confirmed: boolean }> {
  return apiFetch('/datasource/excel/review', {
    method: 'POST', body: JSON.stringify({ sourceId, table, range, headerRow }),
  });
}

export async function testSqlConnection(input: {
  server: string;
  database: string;
  authentication: 'sql' | 'windows';
  username?: string;
  password?: string;
}, signal?: AbortSignal): Promise<{ connected: boolean; connectionId: string }> {
  return apiFetch('/datasource/sql/test', {
    method: 'POST',
    body: JSON.stringify(input),
  }, { signal, timeoutMs: 15000 });
}

export async function discoverSqlSchema(connectionId: string, signal?: AbortSignal): Promise<{ sourceId: string; tables: DataTable[] }> {
  return apiFetch('/datasource/sql/schema', {
    method: 'POST',
    body: JSON.stringify({ connectionId }),
  }, { signal, timeoutMs: 30000 });
}

export async function materializeSqlSource(sourceId: string, connectionId: string, tables: string[], approvedMeasures: string[], signal?: AbortSignal): Promise<{ sourceId: string; materializedTables: string[]; requiresApproval: boolean }> {
  return apiFetch('/datasource/sql/materialize', {
    method: 'POST', body: JSON.stringify({ sourceId, connectionId, tables, approvedMeasures }),
  }, { signal, timeoutMs: 120000 });
}

export async function planReport(sourceId: string, request: string, table?: string, signal?: AbortSignal): Promise<ReportPlan | PlanClarification> {
  return apiFetch('/report/plan', {
    method: 'POST',
    body: JSON.stringify({ sourceId, request, ...(table ? { table } : {}) }),
  }, { signal, timeoutMs: 360000 });
}

export async function getAIStatus(): Promise<AIStatus> {
  return apiFetch('/report/ai-status');
}

export async function routeAgent(sourceId: string, request: string, hasReport: boolean, table?: string, signal?: AbortSignal): Promise<{ action: 'answer_data_question' | 'understand_workbook' | 'suggest_kpis' | 'inspect_relationships' | 'prepare_data' | 'plan_dashboard' | 'edit_report' | 'respond' | 'clarify'; reason: string; message?: string; requiresApproval: boolean; plannedSteps?: Array<{ action: string; reason: string }> }> {
  return apiFetch('/agent/route', { method: 'POST', body: JSON.stringify({ sourceId, request, hasReport, table }) }, { signal, timeoutMs: 60000 });
}

export async function startAgentExecution(input: { previousRunId?: string; sourceId: string; request: string; hasReport: boolean; table?: string; sourceType?: 'excel' | 'sql'; traceId?: string; conversation: Array<{ role: 'user' | 'assistant'; text: string }>; clarificationAnswer?: string; reportSpec?: ReportPlan }, signal?: AbortSignal): Promise<AgentRun> {
  return apiFetch('/agent/runs', { method: 'POST', body: JSON.stringify(input) }, { signal, timeoutMs: 360000 });
}

export async function listAgentRunHistory(limit = 50, offset = 0): Promise<{ runs: AgentRunHistoryItem[]; hasMore: boolean }> {
  return apiFetch<{ runs: AgentRunHistoryItem[]; hasMore: boolean }>(`/agent/runs?limit=${limit}&offset=${offset}`);
}

export async function listSavedReports(limit = 50, offset = 0): Promise<{ reports: SavedReport[]; hasMore: boolean }> {
  return apiFetch<{ reports: SavedReport[]; hasMore: boolean }>(`/report?limit=${limit}&offset=${offset}`);
}

export async function getSavedReportConversation(reportId: string): Promise<{ available: boolean; conversation: Array<{ role: 'user' | 'assistant'; text: string }> }> {
  return apiFetch(`/report/${encodeURIComponent(reportId)}/conversation`);
}

export async function renameSavedConversation(reportId: string, title: string): Promise<{ title: string }> {
  return apiFetch(`/report/${encodeURIComponent(reportId)}/conversation`, { method: 'PATCH', body: JSON.stringify({ title }) });
}

export async function deleteSavedConversation(reportId: string): Promise<{ deleted: boolean }> {
  return apiFetch(`/report/${encodeURIComponent(reportId)}/conversation`, { method: 'DELETE' });
}

export async function getCurrentIdentity(): Promise<CurrentIdentity> {
  return apiFetch<CurrentIdentity>('/identity');
}

export async function getAgentRunAudit(runId: string): Promise<AgentRunAudit> {
  return apiFetch<AgentRunAudit>(`/agent/runs/${encodeURIComponent(runId)}/audit`);
}

export async function linkAgentRunReport(runId: string, reportId: string, reportName: string, traceId?: string): Promise<AgentRun> {
  return apiFetch(`/agent/runs/${encodeURIComponent(runId)}/report`, {
    method: 'POST',
    body: JSON.stringify({ reportId, reportName, traceId }),
  });
}

export async function approveAgentExecution(runId: string, planVersion: string, traceId?: string, signal?: AbortSignal): Promise<AgentRun> {
  return apiFetch(`/agent/runs/${encodeURIComponent(runId)}/approve`, { method: 'POST', body: JSON.stringify({ planVersion, approver: 'web-user', traceId }) }, { signal });
}

export async function resumeAgentExecution(runId: string, context: { conversation: Array<{ role: 'user' | 'assistant'; text: string }>; reportSpec?: ReportPlan; table?: string; traceId?: string; clarificationAnswer?: string }, signal?: AbortSignal): Promise<AgentRun> {
  return apiFetch(`/agent/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST', body: JSON.stringify(context) }, { signal, timeoutMs: 360000 });
}

export async function cancelAgentExecution(runId: string): Promise<AgentRun> {
  return apiFetch(`/agent/runs/${runId}/cancel`, { method: 'POST' });
}

export async function getWorkbookUnderstanding(sourceId: string): Promise<{ tables: Array<{ table: string; fields: Array<{ field: string; role: string }> }> }> {
  return apiFetch('/analysis/understand', { method: 'POST', body: JSON.stringify({ sourceId }) });
}

export async function getRelationships(sourceId: string): Promise<{ relationships: Array<{ source_table: string; source_column: string; target_table: string; target_column: string }>; message: string }> {
  return apiFetch('/analysis/relationships', { method: 'POST', body: JSON.stringify({ sourceId }) });
}

export async function getPreparationSuggestions(sourceId: string): Promise<{ message: string; warnings: string[]; parsingReview: Record<string, unknown> }> {
  return apiFetch('/analysis/preparation', { method: 'POST', body: JSON.stringify({ sourceId }) });
}

export async function askData(sourceId: string, question: string): Promise<AnalysisAnswer> {
  return apiFetch('/analysis/ask', { method: 'POST', body: JSON.stringify({ sourceId, question }) });
}

export async function getKpiSuggestions(sourceId: string): Promise<{ suggestions: Array<{ name: string; table: string; formula: string; dax: string; explanation: string }>; requiresConfirmation: boolean }> {
  return apiFetch('/analysis/kpis', { method: 'POST', body: JSON.stringify({ sourceId }) });
}

export async function previewReport(sourceId: string, reportSpec: ReportPlan, filters: Record<string, string[]> = {}, signal?: AbortSignal, filterPage?: string, syncFilters: Record<string, string[]> = {}): Promise<ReportPreview> {
  return apiFetch('/report/preview', {
    method: 'POST',
    body: JSON.stringify({ sourceId, reportSpec, filters, filterPage, syncFilters }),
  }, { signal, timeoutMs: 30000 });
}

export async function editReport(sourceId: string, reportSpec: ReportPlan, instruction: string, signal?: AbortSignal): Promise<{ reportSpec: ReportPlan; changes: string[]; message: string }> {
  return apiFetch('/report/edit', { method: 'POST', body: JSON.stringify({ sourceId, reportSpec, instruction }) }, { signal, timeoutMs: 60000 });
}

export async function repairReport(reportId: string, reportSpec: ReportPlan, signal?: AbortSignal): Promise<{ reportSpec: ReportPlan; changes: string[] }> {
  return apiFetch(`/report/${encodeURIComponent(reportId)}/repair`, {
    method: 'POST', body: JSON.stringify({ reportSpec }),
  }, { signal, timeoutMs: 30000 });
}

export async function generateReport(sourceId: string, reportSpec: ReportPlan, audit?: { userRequest?: string; conversation?: Array<{ role: 'user' | 'assistant'; text: string }> }, signal?: AbortSignal): Promise<{
  reportId: string;
  status: string;
  validation: {
    passed: boolean;
    errors: string[];
    warnings: string[];
    calculationAccuracy?: { passed: boolean; engine: string; checks: Array<{ type: string; passed: boolean }>; errors: string[] };
  };
}> {
  return apiFetch('/report/generate', {
    method: 'POST',
    body: JSON.stringify({ sourceId, reportSpec, userRequest: audit?.userRequest, conversation: audit?.conversation }),
  }, { signal, timeoutMs: 120000 });
}

export async function approveReport(sourceId: string, reportSpec: ReportPlan, approver = 'web-user', signal?: AbortSignal): Promise<{ approved: boolean }> {
  return apiFetch('/report/approve', { method: 'POST', body: JSON.stringify({ sourceId, reportSpec, approver }) }, { signal });
}

export async function getReportStatus(reportId: string): Promise<any> {
  return apiFetch(`/report/${reportId}/status`);
}

export async function openReportInPowerBI(reportId: string): Promise<{ message: string }> {
  return apiFetch(`/report/${reportId}/open`, { method: 'POST' });
}

export async function downloadReport(reportId: string): Promise<void> {
  const headers = new Headers();
  const apiToken = sessionStorage.getItem('autobi_api_token');
  if (apiToken) headers.set('Authorization', `Bearer ${apiToken}`);
  headers.set('X-AURA-User', sessionStorage.getItem('autobi_user') || 'demo-user');
  const response = await fetch(`/api/report/${reportId}/download`, { headers });
  if (!response.ok) {
    throw new Error('Download failed');
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${reportId}.pbip.zip`;
  link.click();
  URL.revokeObjectURL(url);
}


export async function getConversationMemory(sourceId: string): Promise<{conversation:Array<{role:'user'|'assistant';text:string}>; reportSpec:ReportPlan|null; tables:DataTable[]; formulaCellCount: number | null}> {
  return apiFetch(`/agent/memory/${encodeURIComponent(sourceId)}`);
}
export async function clearConversationMemory(sourceId: string): Promise<{cleared:boolean}> {
  return apiFetch(`/agent/memory/${encodeURIComponent(sourceId)}`, {method:'DELETE'});
}

export type TokenUsage = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  calls_without_usage: number;
  by_operation: Array<{ operation: string; model: string; calls: number; total_tokens: number }>;
  notice: string;
};

export type TokenUsageScope = 'chat' | 'backend' | 'all';

export function getTokenUsage(scope: TokenUsageScope = 'all'): Promise<TokenUsage> {
  return apiFetch<TokenUsage>(`/usage/tokens?scope=${scope}`);
}

export async function downloadTokenUsage(scope: TokenUsageScope = 'all'): Promise<void> {
  const headers = new Headers();
  const token = sessionStorage.getItem('autobi_api_token');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  headers.set('X-AURA-User', sessionStorage.getItem('autobi_user') || 'demo-user');
  headers.set('X-AURA-Chat-ID', getChatSessionId());
  const response = await fetch(`/api/usage/tokens/export?scope=${scope}`, { headers, signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw await responseError(response, 'Token export failed');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `aura-token-usage-${scope}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
