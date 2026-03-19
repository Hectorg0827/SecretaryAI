import React, { useState } from 'react';
import clsx from 'clsx';
import { Settings as SettingsIcon, Database, Mail, Package, Zap, ChevronRight, Check, AlertTriangle } from 'lucide-react';
import { api } from '../lib/api';
import toast from 'react-hot-toast';

interface Section { id: string; label: string; icon: React.ElementType; }

const SECTIONS: Section[] = [
  { id: 'integrations', label: 'Integrations',     icon: Database  },
  { id: 'email',        label: 'Email Settings',    icon: Mail      },
  { id: 'inventory',    label: 'Inventory Alerts',  icon: Package   },
  { id: 'agent',        label: 'Desktop Agent',     icon: Zap       },
];

function IntegrationRow({ label, connected, description }: { label: string; connected: boolean; description: string }) {
  return (
    <div className="flex items-center gap-4 py-4 border-b border-slate-100 last:border-0">
      <div className="flex-1">
        <div className="text-sm font-medium text-slate-800">{label}</div>
        <div className="text-xs text-slate-400 mt-0.5">{description}</div>
      </div>
      <div className={clsx(
        'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-lg',
        connected ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500',
      )}>
        {connected ? <Check size={11} /> : <AlertTriangle size={11} />}
        {connected ? 'Connected' : 'Not configured'}
      </div>
      <button className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium">
        Configure <ChevronRight size={12} />
      </button>
    </div>
  );
}

function SettingRow({ label, description, children }: { label: string; description?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 py-4 border-b border-slate-100 last:border-0">
      <div className="flex-1">
        <div className="text-sm font-medium text-slate-800">{label}</div>
        {description && <div className="text-xs text-slate-400 mt-0.5">{description}</div>}
      </div>
      {children}
    </div>
  );
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className={clsx(
        'relative w-10 h-5 rounded-full transition-colors flex-shrink-0',
        value ? 'bg-blue-600' : 'bg-slate-200',
      )}
    >
      <span className={clsx(
        'absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform',
        value && 'translate-x-5',
      )} />
    </button>
  );
}

export function Settings() {
  const [active, setActive] = useState('integrations');
  const [emailSummarize, setEmailSummarize]     = useState(true);
  const [emailNotify,    setEmailNotify]         = useState(true);
  const [invLow,         setInvLow]              = useState(true);
  const [invCritical,    setInvCritical]         = useState(true);
  const [invOutOfStock,  setInvOutOfStock]        = useState(true);
  const [lowThreshold,   setLowThreshold]        = useState('8');
  const [criticalThreshold, setCriticalThreshold] = useState('4');

  const save = () => toast.success('Settings saved');

  const activeSection = SECTIONS.find((s) => s.id === active)!;
  const ActiveIcon = activeSection.icon;

  return (
    <div className="flex h-screen bg-slate-50">
      {/* Sidebar */}
      <div className="w-52 flex-shrink-0 bg-white border-r border-slate-100 pt-6">
        <div className="px-4 mb-4">
          <div className="flex items-center gap-2 text-slate-800">
            <SettingsIcon size={16} />
            <span className="text-sm font-bold">Settings</span>
          </div>
        </div>
        <nav className="px-2 space-y-0.5">
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            return (
              <button
                key={s.id}
                onClick={() => setActive(s.id)}
                className={clsx(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors text-left',
                  active === s.id
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800',
                )}
              >
                <Icon size={14} />
                {s.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-8">
        <div className="max-w-2xl">
          <div className="flex items-center gap-2.5 mb-6">
            <ActiveIcon size={18} className="text-slate-600" />
            <h1 className="text-lg font-bold text-slate-900">{activeSection.label}</h1>
          </div>

          <div className="bg-white rounded-xl border border-slate-100 shadow-sm px-5">
            {active === 'integrations' && (
              <>
                <IntegrationRow
                  label="QuickBooks Desktop"
                  connected={false}
                  description="Sync invoices, payments, and inventory from QuickBooks"
                />
                <IntegrationRow
                  label="QuickBooks Online"
                  connected={false}
                  description="Cloud-based accounting sync via OAuth"
                />
                <IntegrationRow
                  label="Gmail"
                  connected={true}
                  description="Monitor and respond to customer emails"
                />
                <IntegrationRow
                  label="Google Sheets"
                  connected={false}
                  description="Export reports and sync data to Sheets"
                />
              </>
            )}

            {active === 'email' && (
              <>
                <SettingRow label="AI Email Summarization" description="Automatically summarize incoming emails with priority and action needed">
                  <Toggle value={emailSummarize} onChange={setEmailSummarize} />
                </SettingRow>
                <SettingRow label="Email Notifications" description="Send desktop notifications for high-priority emails">
                  <Toggle value={emailNotify} onChange={setEmailNotify} />
                </SettingRow>
                <SettingRow label="Smart Reply Model" description="AI model used for drafting replies">
                  <select className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white">
                    <option>claude-sonnet-4-6</option>
                    <option>claude-haiku-4-5</option>
                  </select>
                </SettingRow>
              </>
            )}

            {active === 'inventory' && (
              <>
                <SettingRow label="Low Stock Alert" description="Alert when stock drops below threshold weeks of supply">
                  <div className="flex items-center gap-2">
                    <Toggle value={invLow} onChange={setInvLow} />
                    <input
                      type="number"
                      value={lowThreshold}
                      onChange={(e) => setLowThreshold(e.target.value)}
                      className="w-16 text-sm border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-400 text-center"
                    />
                    <span className="text-xs text-slate-400">wks</span>
                  </div>
                </SettingRow>
                <SettingRow label="Critical Stock Alert" description="Alert when stock drops to critical level">
                  <div className="flex items-center gap-2">
                    <Toggle value={invCritical} onChange={setInvCritical} />
                    <input
                      type="number"
                      value={criticalThreshold}
                      onChange={(e) => setCriticalThreshold(e.target.value)}
                      className="w-16 text-sm border border-slate-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-400 text-center"
                    />
                    <span className="text-xs text-slate-400">wks</span>
                  </div>
                </SettingRow>
                <SettingRow label="Out-of-Stock Alert" description="Alert immediately when any item reaches zero">
                  <Toggle value={invOutOfStock} onChange={setInvOutOfStock} />
                </SettingRow>
              </>
            )}

            {active === 'agent' && (
              <>
                <SettingRow label="Auto-start on Login" description="Launch desktop agent when you log into your computer">
                  <Toggle value={true} onChange={() => {}} />
                </SettingRow>
                <SettingRow label="Watched Folders" description="Folders scanned every 30 minutes for CSV/Excel reports">
                  <button className="text-xs text-blue-600 hover:text-blue-700 font-medium border border-blue-200 rounded-lg px-2.5 py-1.5 bg-blue-50 hover:bg-blue-100 transition-colors">
                    Manage folders
                  </button>
                </SettingRow>
                <SettingRow label="Heartbeat Interval" description="How often the agent checks in with the server">
                  <select className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white">
                    <option>5 minutes</option>
                    <option>10 minutes</option>
                    <option>15 minutes</option>
                  </select>
                </SettingRow>
              </>
            )}
          </div>

          <div className="mt-5 flex justify-end">
            <button
              onClick={save}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
            >
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
