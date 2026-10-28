import { Link, useParams } from 'react-router-dom';
import { Lightbulb } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { findInsight, insightsForRole, type InsightModule } from './insights/registry';

function InsightIndex({ modules }: { modules: InsightModule[] }) {
  const groups = new Map<string, InsightModule[]>();
  for (const mod of modules) {
    groups.set(mod.meta.group, [...(groups.get(mod.meta.group) ?? []), mod]);
  }
  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="flex items-center gap-2">
        <Lightbulb size={20} className="text-amber-500" />
        <h1 className="text-xl font-bold text-slate-900">Insights</h1>
      </div>
      <p className="text-sm text-slate-500">
        Advisory analysis of your business. Every result needs human review; nothing here sends, orders or changes anything.
      </p>
      {modules.length === 0 && (
        <p className="text-sm text-slate-500">No insights are available for your role.</p>
      )}
      {[...groups.entries()].map(([group, items]) => (
        <section key={group} aria-label={group} className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">{group}</h2>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {items.map(({ meta }) => (
              <Link
                key={meta.slug}
                to={`/insights/${meta.slug}`}
                className="block rounded-xl border border-slate-200 bg-white p-4 hover:border-slate-300 hover:shadow-sm"
              >
                <div className="text-sm font-semibold text-slate-800">{meta.title}</div>
                <div className="text-xs text-slate-500 mt-1">{meta.description}</div>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

export function Insights() {
  const { slug } = useParams();
  const { role } = useAuth();

  if (!slug) return <InsightIndex modules={insightsForRole(role)} />;

  const mod = findInsight(slug, role);
  if (!mod) {
    return (
      <div className="p-6 space-y-2">
        <p className="text-sm text-slate-600">This insight is not available for your role.</p>
        <Link to="/insights" className="text-sm font-semibold text-slate-700 underline">Back to insights</Link>
      </div>
    );
  }
  const Page = mod.default;
  return <Page />;
}
