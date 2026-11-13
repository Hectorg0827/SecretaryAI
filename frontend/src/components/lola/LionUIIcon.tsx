import type { ReactNode } from 'react';

/**
 * Lion BI's in-house operational pictogram system.
 * All navigation glyphs share a 24px optical grid and orbital terminal.
 * Never depend on an external stock icon font, images or emojis.
 */
export type LionUIIconKind =
  | 'home' | 'inbox' | 'work' | 'dashboard' | 'finance'
  | 'accounts' | 'inventory' | 'insights' | 'logistics'
  | 'compliance' | 'connectors' | 'audit' | 'diagnostics'
  | 'settings' | 'security' | 'user' | 'online' | 'offline' | 'logout'
  | 'arrow' | 'chevron' | 'history' | 'microphone' | 'microphone-off'
  | 'voice' | 'voice-off' | 'notification';

type Props = { kind: LionUIIconKind; size?: number; className?: string };

const glyphs: Record<LionUIIconKind, ReactNode> = {
  home: <>
    <circle cx="12" cy="12" r="8.2" />
    <path d="m7.4 13.3 4.7-5.5 4.6 3.4-4.6 5.8z" />
    <circle cx="12.1" cy="12.1" r="1.5" className="lion-ui-icon__accent" />
  </>,
  inbox: <>
    <path d="M4 7.2h16v9.6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2zM4 13h4.3l1.8 2.4h3.8l1.8-2.4H20" />
    <path d="M7.2 4.5h9.6" className="lion-ui-icon__accent" />
  </>,
  work: <>
    <rect x="3.5" y="8" width="17" height="11.2" rx="2.6" />
    <path d="M9 8V5.4h6V8M3.5 12.2c5.4 3.4 11.6 3.4 17 0" />
    <circle cx="12" cy="14.1" r="1.5" className="lion-ui-icon__accent" />
  </>,
  dashboard: <>
    <path d="M4 11.5V5a1 1 0 0 1 1-1h6.5v7.5zM14.5 4H20v5.5h-5.5zM4 14.5h7.5V20H4zM14.5 12.5H20V20h-5.5z" />
    <path d="M15.8 16.9h2.8" className="lion-ui-icon__accent" />
  </>,
  finance: <>
    <rect x="3.6" y="6" width="16.8" height="13.3" rx="2.3" />
    <path d="M4 10.3h15.8M7.2 15.8h3.2" />
    <path d="m12.7 15.9 2.4-2.2 1.6 1.1 1.6-2" className="lion-ui-icon__accent" />
  </>,
  accounts: <>
    <circle cx="9.2" cy="8.3" r="3.2" />
    <path d="M3.6 19.5v-1.4c0-3 2.3-5 5.6-5 3.2 0 5.6 2 5.6 5v1.4zM15.8 5.5c2.4.2 4 1.9 4 4s-1.6 3.6-4 3.8" />
    <path d="M17.5 15.1c2 .6 3 2.1 3 4" className="lion-ui-icon__accent" />
  </>,
  inventory: <>
    <path d="m12 3.5 8 4.3v8.4l-8 4.3-8-4.3V7.8zM4.2 7.9 12 12l7.8-4.1M12 12v8.4M8 5.6l8 4.5" />
    <path d="M15.8 14.9v2.5" className="lion-ui-icon__accent" />
  </>,
  insights: <>
    <path d="M3.1 12s3.5-6 8.9-6 8.9 6 8.9 6-3.5 6-8.9 6-8.9-6-8.9-6z" />
    <circle cx="12" cy="12" r="3.2" />
    <circle cx="12" cy="12" r="1.1" className="lion-ui-icon__accent" />
  </>,
  logistics: <>
    <path d="M3.2 7h10.6v10H3.2zM13.8 10h3.4l3.4 3.3V17h-6.8M5.5 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4M17.5 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4" />
    <path d="M6.1 10.1h4.3" className="lion-ui-icon__accent" />
  </>,
  compliance: <>
    <path d="M12 3.1 19.2 6v6.3c0 4.5-3.4 7.1-7.2 8.7-3.8-1.6-7.2-4.2-7.2-8.7V6z" />
    <path d="m8.4 12.1 2.5 2.6 4.8-5.2" className="lion-ui-icon__accent" />
  </>,
  connectors: <>
    <path d="M8.4 5.4H6a2.4 2.4 0 0 0-2.4 2.4v8.4A2.4 2.4 0 0 0 6 18.6h2.4M15.6 5.4H18a2.4 2.4 0 0 1 2.4 2.4v8.4A2.4 2.4 0 0 1 18 18.6h-2.4" />
    <path d="M8.4 12h7.2M12.6 9l3 3-3 3" className="lion-ui-icon__accent" />
  </>,
  audit: <>
    <path d="M6 3.8h10.3L19 6.5V20H6zM16 3.8v3h3M9 10h6M9 13.6h5" />
    <path d="m9 17 1.4 1.2 2.4-2.5" className="lion-ui-icon__accent" />
  </>,
  diagnostics: <>
    <circle cx="12" cy="12" r="8.7" />
    <path d="M5.8 12h3l1.8-4.5 2.5 8.8 2.1-4.3h2.9" className="lion-ui-icon__accent" />
  </>,
  settings: <>
    <circle cx="12" cy="12" r="3" />
    <path d="m10 3.3-.5 2.2-2.1 1.2-2.1-.6-2 3.4 1.6 1.6v2l-1.6 1.6 2 3.4 2.1-.6 2.1 1.2.5 2.2h4l.5-2.2 2.1-1.2 2.1.6 2-3.4-1.6-1.6v-2l1.6-1.6-2-3.4-2.1.6-2.1-1.2-.5-2.2z" />
    <path d="M12 10.6v2.8" className="lion-ui-icon__accent" />
  </>,
  security: <>
    <rect x="5.6" y="10.6" width="12.8" height="9.4" rx="2.1" />
    <path d="M8.1 10.6V7.8a3.9 3.9 0 0 1 7.8 0v2.8" />
    <circle cx="12" cy="14.7" r="1.3" className="lion-ui-icon__accent" />
    <path d="M12 16v1.4" className="lion-ui-icon__accent" />
  </>,
  user: <>
    <circle cx="12" cy="8.2" r="3.7" />
    <path d="M4.7 19.3c.2-3.8 2.8-5.7 7.3-5.7s7.1 1.9 7.3 5.7" />
    <path d="M10.5 8.2h3" className="lion-ui-icon__accent" />
  </>,
  online: <>
    <path d="M4.5 9a11 11 0 0 1 15 0M7.5 12.3a6.5 6.5 0 0 1 9 0" />
    <circle cx="12" cy="16.3" r="1.7" className="lion-ui-icon__accent" />
  </>,
  offline: <>
    <path d="M4.5 9a11 11 0 0 1 15 0M7.5 12.3a6.5 6.5 0 0 1 9 0M4.3 19.7 19.7 4.3" />
    <circle cx="12" cy="16.3" r="1.7" className="lion-ui-icon__accent" />
  </>,
  logout: <>
    <path d="M11.6 4.4H5.8a2 2 0 0 0-2 2v11.2a2 2 0 0 0 2 2h5.8M11.2 12h9M16.8 8.3l3.7 3.7-3.7 3.7" />
    <path d="M7.2 12h2.5" className="lion-ui-icon__accent" />
  </>,
  arrow: <>
    <path d="M4 12h15M13.5 6.5 19 12l-5.5 5.5" />
    <path d="M4 16v1.5" className="lion-ui-icon__accent" />
  </>,
  chevron: <path d="m8.5 5.2 6.8 6.8-6.8 6.8" />,
  history: <>
    <path d="M4.5 10a8 8 0 1 1 1.4 6M4 5v5h5" />
    <path d="M12 7.8V12l3 2" className="lion-ui-icon__accent" />
  </>,
  microphone: <>
    <rect x="9.1" y="3.4" width="5.8" height="11" rx="2.9" />
    <path d="M6.4 11.6a5.6 5.6 0 0 0 11.2 0M12 17.2v3.2M9.2 20.4h5.6" />
    <path d="M11.1 6h1.8" className="lion-ui-icon__accent" />
  </>,
  'microphone-off': <>
    <rect x="9.1" y="3.4" width="5.8" height="11" rx="2.9" />
    <path d="M6.4 11.6a5.6 5.6 0 0 0 11.2 0M12 17.2v3.2M9.2 20.4h5.6" />
    <path d="m4.5 4.5 15 15" className="lion-ui-icon__accent" />
  </>,
  voice: <>
    <path d="M4.5 9.5h3.2l4.2-3.7v12.4l-4.2-3.7H4.5zM15.3 8.2c2.3 1.6 2.3 6 0 7.6M17.6 5.8c4.2 3.4 4.2 9 0 12.4" />
  </>,
  'voice-off': <>
    <path d="M4.5 9.5h3.2l4.2-3.7v12.4l-4.2-3.7H4.5zM15.5 9l5 6M20.5 9l-5 6" />
    <path d="M8 13.1v.7" className="lion-ui-icon__accent" />
  </>,
  notification: <>
    <path d="M6.5 9a5.5 5.5 0 0 1 11 0v4.7l2 2.3H4.5l2-2.3zM10 19h4" />
    <circle cx="17.6" cy="5.6" r="1.1" className="lion-ui-icon__accent" />
  </>,
};

export function LionUIIcon({ kind, size = 18, className = '' }: Props) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={`lion-ui-icon ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.55"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {glyphs[kind]}
    </svg>
  );
}
