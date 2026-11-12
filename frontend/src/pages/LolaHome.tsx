import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Bell, ChevronRight, Mic, MicOff, Send, ShieldCheck, Sparkles, Volume2, VolumeX, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { ChatInterface } from '../components/chat/ChatInterface';
import { LolaOrb } from '../components/lola/LolaOrb';
import { useAuth } from '../hooks/useAuth';
import { api, type DashboardSummary } from '../lib/api';
import { verifiedDashboardSummary } from '../lib/sourceEvidence';
import { useChatStore } from '../stores/chatStore';
import './lola-home.css';

type SpeechResult = { results: ArrayLike<ArrayLike<{ transcript: string }>> };
type BrowserSpeechRecognizer = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechResult) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};
type SpeechBrowser = Window & {
  SpeechRecognition?: new () => BrowserSpeechRecognizer;
  webkitSpeechRecognition?: new () => BrowserSpeechRecognizer;
};

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
}

function speakWelcome(quality: 'ready' | 'missing' | 'unknown') {
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') return false;
  try {
    window.speechSynthesis.cancel();
    const intro = quality === 'ready'
      ? "I'm Lola. Your business snapshot is ready to review."
      : quality === 'missing'
        ? "I'm Lola. I couldn't verify your business snapshot just now."
        : "I'm Lola. Your workspace is ready.";
    const utterance = new SpeechSynthesisUtterance(`${greeting()}. ${intro}`);
    utterance.rate = 0.97;
    utterance.lang = 'en-US';
    window.speechSynthesis.speak(utterance);
    return true;
  } catch {
    return false;
  }
}

const workspaces = [
  { label: 'Finance', to: '/finance', roles: ['owner', 'manager', 'finance_controller', 'ar_specialist', 'ap_specialist', 'pricing_billbacks'] },
  { label: 'Sales & accounts', to: '/accounts', roles: ['owner', 'manager', 'back_office'] },
  { label: 'Inventory', to: '/inventory', roles: ['owner', 'manager', 'sales_rep', 'back_office'] },
  { label: 'Logistics', to: '/logistics', roles: ['owner', 'manager', 'back_office'] },
  { label: 'Compliance', to: '/compliance', roles: ['owner', 'manager'] },
  { label: 'Insights', to: '/insights', roles: ['owner', 'manager', 'sales_rep', 'back_office'] },
] as const;

export function LolaHome() {
  const { role, hasPermission, canAccessChat, canApprove } = useAuth();
  const canReadSnapshot = hasPermission('view_dashboard');
  const canSeeInventory = hasPermission('view_inventory') || hasPermission('view_all');
  const canSeeAccounts = hasPermission('view_customers') || hasPermission('view_all');
  const [name, setName] = useState<string | null>(null);
  const [preferenceKey, setPreferenceKey] = useState<string | null>(null);
  const [voiceGreeting, setVoiceGreeting] = useState(false);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [snapshotLoading, setSnapshotLoading] = useState(canReadSnapshot);
  const [prompt, setPrompt] = useState('');
  const [chatOpen, setChatOpen] = useState(false);
  const [listening, setListening] = useState(false);
  const [micError, setMicError] = useState('');
  const greetingAttempted = useRef(false);
  const recognitionRef = useRef<BrowserSpeechRecognizer | null>(null);
  const chatBusy = useChatStore((state) => state.isStreaming);
  const sendMessage = useChatStore((state) => state.sendMessage);

  // Privacy: preference keys are derived from the authenticated account, never
  // shared across users on the same browser. No key = session-only/off by default.
  useEffect(() => {
    let cancelled = false;
    void api.get<{ id?: string; full_name?: string; name?: string }>('/auth/me')
      .then((user) => {
        if (cancelled) return;
        setName(user.full_name?.trim() || user.name?.trim() || null);
        if (user.id) {
          const key = `lionbi:lola:voice-greeting:${user.id}`;
          setPreferenceKey(key);
          setVoiceGreeting(localStorage.getItem(key) === 'on');
        }
      }).catch(() => { /* A failed profile read is not consent to auto-play audio. */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!canReadSnapshot) {
      setSnapshotLoading(false);
      return;
    }
    let cancelled = false;
    api.dashboard.summary().then((value) => {
      if (!cancelled) setSummary(verifiedDashboardSummary(value));
    }).catch(() => {
      if (!cancelled) setSummary(null);
    }).finally(() => {
      if (!cancelled) setSnapshotLoading(false);
    });
    return () => { cancelled = true; };
  }, [canReadSnapshot]);

  // Enabled users receive a short, non-sensitive welcome. Browsers may block
  // automatic speech until the user interacts; no microphone starts on login.
  useEffect(() => {
    if (!preferenceKey || snapshotLoading || !voiceGreeting || greetingAttempted.current) return;
    greetingAttempted.current = true;
    speakWelcome(summary ? 'ready' : canReadSnapshot ? 'missing' : 'unknown');
  }, [preferenceKey, snapshotLoading, voiceGreeting, summary, canReadSnapshot]);

  useEffect(() => () => {
    recognitionRef.current?.stop();
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  }, []);

  const toggleGreeting = () => {
    const next = !voiceGreeting;
    setVoiceGreeting(next);
    if (preferenceKey) localStorage.setItem(preferenceKey, next ? 'on' : 'off');
    if (!next && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    if (next) {
      greetingAttempted.current = true;
      speakWelcome(summary ? 'ready' : canReadSnapshot ? 'missing' : 'unknown');
    }
  };

  const submit = (text: string) => {
    const cleaned = text.trim();
    if (!canAccessChat || !cleaned || chatBusy) return;
    setPrompt('');
    setChatOpen(true);
    void sendMessage(cleaned);
  };

  const toggleMicrophone = () => {
    setMicError('');
    if (listening && recognitionRef.current) {
      recognitionRef.current.stop();
      setListening(false);
      return;
    }
    const browser = window as SpeechBrowser;
    const Recognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
    if (!Recognition) {
      setMicError('Voice dictation is unavailable in this browser. You can type to Lola instead.');
      return;
    }
    const recognition = new Recognition();
    recognitionRef.current = recognition;
    recognition.lang = 'en-US';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript;
      if (transcript) setPrompt((previous) => previous ? `${previous} ${transcript}` : transcript);
    };
    recognition.onerror = () => {
      setListening(false);
      setMicError('Microphone access was unavailable. Check browser permissions or type your request.');
    };
    recognition.onend = () => setListening(false);
    try {
      recognition.start();
      setListening(true);
    } catch {
      setListening(false);
      setMicError('Voice dictation could not start. Please type your request.');
    }
  };

  const freshness = summary?.generated_at ? new Date(summary.generated_at).toLocaleString() : null;
  const stale = summary?.snapshot_stale !== false;
  const availableWorkspaces = workspaces.filter((item) => (item.roles as readonly string[]).includes(role));
  const firstName = name?.split(/\s+/)[0];

  const tiles: Array<{ label: string; value: string; detail: string; link: string }> = [];
  if (canApprove && canReadSnapshot) tiles.push({
    label: 'Pending approvals',
    value: summary?.pending_actions == null ? '—' : String(summary.pending_actions),
    detail: summary?.pending_actions == null ? 'Not verified' : 'From the latest snapshot',
    link: '/inbox',
  });
  if (canSeeInventory && canReadSnapshot) tiles.push({
    label: 'Inventory alerts',
    value: summary ? String(summary.inventory_alerts.length) : '—',
    detail: summary ? 'From the latest snapshot' : 'Not verified',
    link: '/inventory',
  });
  if (canSeeAccounts && canReadSnapshot) tiles.push({
    label: 'Accounts needing review',
    value: summary?.accounts ? String(summary.accounts.at_risk + summary.accounts.dormant) : '—',
    detail: summary?.accounts ? 'At-risk + dormant (partial)' : 'Not verified',
    link: '/accounts',
  });

  return (
    <div className="lola-home">
      <header className="lola-home__header">
        <div className="lola-home__breadcrumb"><Sparkles size={15} /> LION BI <span>/</span> LOLA</div>
        <div className="lola-home__header-actions">
          <span className="lola-home__private"><ShieldCheck size={14} /> Your access level: {role.replace(/_/g, ' ')}</span>
          <button className="lola-home__voice-toggle" type="button" aria-label={`Automatic Lola voice greeting ${voiceGreeting ? 'on' : 'off'}`} aria-pressed={voiceGreeting} onClick={toggleGreeting}>
            {voiceGreeting ? <Volume2 size={16} /> : <VolumeX size={16} />}
            <span>Voice greeting</span>
            <span className={`lola-home__switch ${voiceGreeting ? 'lola-home__switch--on' : ''}`} aria-hidden="true"><i /></span>
          </button>
        </div>
      </header>

      <div className="lola-home__scroll">
        <div className="lola-home__inner">
          <section className="lola-home__hero" aria-labelledby="lola-hero-title">
            <div className="lola-home__eyebrow"><span /> YOUR BUSINESS INTELLIGENCE, PERSONIFIED</div>
            <LolaOrb active={chatBusy || listening} />
            <h1 id="lola-hero-title">{greeting()}{firstName ? `, ${firstName}` : ''}<span className="lola-home__period">.</span></h1>
            <p className="lola-home__hero-lead">I'm Lola. What can I help you accomplish today?</p>

            {canAccessChat ? (
              <>
                <form className="lola-home__composer" onSubmit={(event) => { event.preventDefault(); submit(prompt); }}>
                  <Sparkles size={18} className="lola-home__composer-spark" aria-hidden="true" />
                  <label className="sr-only" htmlFor="lola-question">Ask Lola</label>
                  <input id="lola-question" value={prompt} onChange={(event) => setPrompt(event.target.value)}
                    placeholder="Ask Lola anything about your business..."
                    autoComplete="off" disabled={chatBusy} maxLength={4000} />
                  <button type="button" aria-label={listening ? 'Stop voice dictation' : 'Start voice dictation'} aria-pressed={listening}
                    className={`lola-home__mic ${listening ? 'lola-home__mic--active' : ''}`}
                    onClick={toggleMicrophone}>{listening ? <MicOff size={19} /> : <Mic size={19} />}</button>
                  <button type="submit" className="lola-home__send" aria-label="Send to Lola" disabled={!prompt.trim() || chatBusy}><ArrowRight size={19} /></button>
                </form>
                {micError && <p className="lola-home__hint" role="status">{micError}</p>}
                <div className="lola-home__suggestions" aria-label="Suggested questions">
                  {['What needs my attention?', 'Review my business priorities', 'Show inventory risks'].map((question) => (
                    <button key={question} type="button" onClick={() => submit(question)} disabled={chatBusy}>
                      {question} <ChevronRight size={13} />
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <div className="lola-home__restricted" role="status">
                Lola's conversational tools are not yet enabled for this role. Your authorized workspaces are available below.
              </div>
            )}
          </section>

          <section className="lola-home__overview" aria-labelledby="lola-overview-title">
            <div className="lola-home__section-heading">
              <div>
                <p className="lola-home__section-kicker">AT A GLANCE</p>
                <h2 id="lola-overview-title">Your business, in focus.</h2>
              </div>
              {canReadSnapshot && <Link to="/dashboard" className="lola-home__subtle-link">Open detailed dashboard <ArrowRight size={15} /></Link>}
            </div>

            {canReadSnapshot && (
              <div className="lola-home__source-line" role="status">
                {snapshotLoading ? 'Checking authorized business snapshot…' : summary
                  ? `Snapshot ${freshness ?? 'time unavailable'}. ${stale ? 'Freshness is not verified; figures may be historical.' : 'Backend reports snapshot current.'}`
                  : 'Business snapshot unavailable. No missing number should be interpreted as zero.'}
              </div>
            )}
            {tiles.length > 0 && <div className="lola-home__stat-grid">
              {tiles.map((tile) => (
                <Link to={tile.link} key={tile.label} className="lola-home__stat">
                  <span className="lola-home__stat-label">{tile.label} <ArrowRight size={14} /></span>
                  <strong>{tile.value}</strong>
                  <span className="lola-home__stat-detail">{tile.detail}</span>
                </Link>
              ))}
            </div>}

            <div className="lola-home__action-panel">
              <div className="lola-home__action-icon"><Bell size={20} /></div>
              <div className="lola-home__action-copy">
                <h3>Executive briefing</h3>
                <p>{canReadSnapshot
                  ? summary ? 'Review source-backed alerts, priorities and approvals. Lola can explain the details.'
                    : 'The briefing is available when its underlying source can be verified.'
                  : 'Open an authorized workspace to review business activity.'}</p>
              </div>
              {canReadSnapshot && <Link to="/dashboard" className="lola-home__action-link">View briefing <ArrowRight size={16} /></Link>}
            </div>
          </section>

          <section className="lola-home__workspaces" aria-labelledby="lola-workspaces-title">
            <div className="lola-home__section-heading">
              <div>
                <p className="lola-home__section-kicker">YOUR WORKSPACE</p>
                <h2 id="lola-workspaces-title">Explore when you need the details.</h2>
              </div>
            </div>
            <div className="lola-home__workspaces-grid">
              {availableWorkspaces.map((item) => (
                <Link key={item.to} to={item.to}>{item.label}<ArrowRight size={15} /></Link>
              ))}
            </div>
          </section>
          <footer className="lola-home__footer">LION BI <span>·</span> Intelligence with oversight. All business actions remain subject to your permissions and approval policies.</footer>
        </div>
      </div>

      {chatOpen && canAccessChat && (
        <div className="lola-home__dialog-layer">
          <button type="button" className="lola-home__scrim" aria-label="Close Lola conversation" onClick={() => setChatOpen(false)} />
          <section className="lola-home__chat" role="dialog" aria-modal="true" aria-label="Lola conversation">
            <header><span className="lola-home__chat-title"><Sparkles size={17} /> Lola <span>Business assistant</span></span>
              <button type="button" aria-label="Close Lola conversation" onClick={() => setChatOpen(false)}><X size={18} /></button>
            </header>
            <div className="lola-home__chat-body"><ChatInterface /></div>
          </section>
        </div>
      )}
    </div>
  );
}
