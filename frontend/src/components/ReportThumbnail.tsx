import { useEffect, useRef, useState } from 'react';
import { previewReport, ReportPreview, SavedReport } from '../services/api';

export function ReportThumbnail({ report, onOpen }: { report: SavedReport; onOpen: () => void }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: '100px' });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || !report.sourceId) return;
    const controller = new AbortController();
    const { additionalPages: _additionalPages, ...firstPage } = report.reportSpec;
    previewReport(report.sourceId, firstPage, {}, controller.signal).then(setPreview).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Preview could not be loaded.');
    });
    return () => controller.abort();
  }, [visible, report.sourceId, report.reportSpec]);

  return <button ref={ref} className="report-thumbnail" type="button" aria-label={`Open ${report.title} in the report workspace`} onClick={onOpen}>
    <span className="thumbnail-toolbar"><i /><i /><i /></span>
    {!report.sourceId ? <span className="thumbnail-message">Source unavailable</span> : error
      ? <span className="thumbnail-message">Preview unavailable: {error}</span> : !preview
        ? <span className="thumbnail-message">Loading preview…</span>
        : <span className="thumbnail-report">
          <strong>{preview.page}</strong>
          <span className="thumbnail-grid">
            {preview.visuals.slice(0, 4).map((visual, index) => <span className="thumbnail-tile" key={`${visual.type}-${index}`}>
              <span className="thumbnail-title">{visual.title || visual.type}</span>
              {visual.data.slice(0, 2).map((point, pointIndex) => <span className="thumbnail-point" key={`${point.label}-${pointIndex}`}>
                <span>{point.label}</span><b>{new Intl.NumberFormat(undefined, { maximumFractionDigits: 1, notation: 'compact' }).format(point.value)}</b>
              </span>)}
              {!visual.data.length && <span className="thumbnail-empty">{visual.options?.length ? `${visual.options.length} filter options` : 'No preview values'}</span>}
            </span>)}
          </span>
        </span>}
  </button>;
}
