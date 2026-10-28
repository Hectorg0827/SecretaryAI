/** Shared building blocks so every insight page looks and fails the same way. */
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, Info, Loader2 } from 'lucide-react';
import { api } from '../../lib/api';

export type AdvisorySource = 'connected_data' | 'user_supplied';

/** Fields every /api/insights response carries (see backend insights/_common.py). */
export interface AdvisoryFields {
  status: 'analysis_only';
  source: AdvisorySource;
  requires_human_review: true;
  actions_executed: false;
  generated_at: string;
}

export function InsightShell({ title, description, children }: {
  title: string; description: string; children: ReactNode;
}) {
  return (
    <div className="p-6 space-y-5 max-w-6xl">
      <Link to="/insights" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700">
        <ArrowLeft size={12} /> All insights
      </Link>
      <div>
        <h1 className="text-xl font-bold text-slate-900">{title}</h1>
        <p className="text-sm text-slate-500 mt-1">{description}</p>
      </div>
      {children}
    </div>
  );
}

export function AdvisoryNotice({ source }: { source?: AdvisorySource }) {
  const basis = source === 'user_supplied'
    ? 'Calculated from the values you entered.'
    : 'Calculated from your connected business data.';
  return (
    <div role="note" className="flex gap-2 items-start rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">
      <Info size={14} className="mt-0.5 flex-shrink-0" />
      <span>Analysis only. {basis} Recommendations need human review; nothing is sent, ordered or changed.</span>
    </div>
  );
}

export function LoadingState({ label = 'Loading analysis…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
      <Loader2 size={16} className="animate-spin" /> {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex gap-2 items-start rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      <AlertTriangle size={16} className="mt-0.5 flex-shrink-0" />
      <div className="space-y-1">
        <div>{message}</div>
        {onRetry && (
          <button type="button" onClick={onRetry} className="text-xs font-semibold underline">Try again</button>
        )}
      </div>
    </div>
  );
}

/** GET an insight endpoint; an unavailable source is an error, never empty data. */
export function useInsight<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));

  const load = useCallback(() => {
    if (!path) return;
    setLoading(true);
    setError(null);
    api.get<T>(path)
      .then((result) => setData(result))
      .catch((err: unknown) => {
        setData(null);
        setError(err instanceof Error ? err.message : 'This analysis is unavailable right now.');
      })
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => { load(); }, [load]);

  return { data, error, loading, reload: load };
}
