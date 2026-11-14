import React, { useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';

type MetaLoginResponse = {
  authResponse?: { code?: string };
};
type FacebookSDK = {
  init: (args: { appId: string; cookie: boolean; xfbml: boolean; version: string }) => void;
  login: (
    callback: (response: MetaLoginResponse) => void,
    args: {
      config_id: string; response_type: string; override_default_response_type: boolean;
      extras: { setup: Record<string, never>; featureType: string; sessionInfoVersion: string };
    },
  ) => void;
};

declare global {
  interface Window {
    FB?: FacebookSDK;
    fbAsyncInit?: () => void;
  }
}

type SignupStart = {
  session_id: string; app_id: string; configuration_id: string;
  feature_type: string; connected: false;
};
type SignupFinish = {
  status: string; phone_verified: boolean; connected: false;
  message_access: false;
};

const META_EVENT_ORIGINS = new Set([
  'https://www.facebook.com', 'https://web.facebook.com',
]);

async function loadMetaSDK(appId: string): Promise<FacebookSDK> {
  if (!/^[0-9]{5,35}$/.test(appId)) throw new Error('Invalid Meta app configuration');
  if (window.FB) return window.FB;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://connect.facebook.net/en_US/sdk.js';
    script.async = true;
    script.crossOrigin = 'anonymous';
    const timeout = window.setTimeout(() => reject(new Error('Meta SDK unavailable')), 15000);
    window.fbAsyncInit = () => {
      window.clearTimeout(timeout);
      resolve();
    };
    script.onerror = () => {
      window.clearTimeout(timeout);
      reject(new Error('Meta SDK unavailable'));
    };
    document.head.appendChild(script);
  });
  if (!window.FB) throw new Error('Meta SDK unavailable');
  return window.FB;
}

export function MetaEmbeddedSignup({ onComplete }: { onComplete: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const signup = useRef<{ session?: string; code?: string; waba?: string; phone?: string }>({});
  const finishing = useRef(false);

  const maybeFinish = async () => {
    const { session, code, waba, phone } = signup.current;
    if (!session || !code || !waba || !phone || finishing.current) return;
    finishing.current = true;
    try {
      const result = await api.post<SignupFinish>('/api/lola/continuity/whatsapp/embedded-signup/finish', {
        session_id: session, event: 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING',
        authorization_code: code, waba_id: waba, phone_number_id: phone,
      });
      if (!result.phone_verified || result.connected) throw new Error('Unexpected Meta verification result');
      setMessage('Meta confirmed the eligible number. Connection remains offline pending independent privacy approval.');
      onComplete();
    } catch {
      setMessage('Meta could not verify this number. No messages were connected or synchronized.');
    } finally {
      signup.current = {};
      finishing.current = false;
      setBusy(false);
    }
  };

  useEffect(() => {
    const receiveMetaSession = (event: MessageEvent) => {
      // Meta popup events are untrusted until origin-checked and then verified
      // independently by our backend using the one-time code and Graph API.
      if (!META_EVENT_ORIGINS.has(event.origin)) return;
      let payload: unknown = event.data;
      if (typeof payload === 'string') {
        try { payload = JSON.parse(payload); } catch { return; }
      }
      if (!payload || typeof payload !== 'object') return;
      const data = payload as { type?: string; event?: string; data?: { waba_id?: unknown; phone_number_id?: unknown } };
      if (data.type !== 'WA_EMBEDDED_SIGNUP'
        || data.event !== 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING') return;
      if (typeof data.data?.waba_id !== 'string'
        || typeof data.data?.phone_number_id !== 'string') return;
      signup.current.waba = data.data.waba_id;
      signup.current.phone = data.data.phone_number_id;
      void maybeFinish();
    };
    window.addEventListener('message', receiveMetaSession);
    return () => window.removeEventListener('message', receiveMetaSession);
  }, [onComplete]);

  const launch = async () => {
    if (busy) return;
    setBusy(true); setMessage('');
    signup.current = {};
    try {
      const start = await api.post<SignupStart>('/api/lola/continuity/whatsapp/embedded-signup/start', {});
      if (!start.session_id || !/^[0-9]{5,35}$/.test(start.app_id)
        || !/^[0-9]{5,35}$/.test(start.configuration_id)) {
        throw new Error('Meta app not configured');
      }
      signup.current.session = start.session_id;
      const FB = await loadMetaSDK(start.app_id);
      FB.init({ appId: start.app_id, cookie: true, xfbml: false, version: 'v27.0' });
      FB.login((response) => {
        const code = response.authResponse?.code;
        if (!code) {
          setMessage('Meta signup cancelled or no code returned. No WhatsApp connection created.');
          setBusy(false);
          return;
        }
        signup.current.code = code;
        void maybeFinish();
      }, {
        config_id: start.configuration_id,
        response_type: 'code',
        override_default_response_type: true,
        extras: {
          setup: {},
          featureType: start.feature_type,
          sessionInfoVersion: '3',
        },
      });
    } catch {
      setBusy(false);
      setMessage('Meta Embedded Signup is not available or authorized. No account was connected.');
    }
  };

  return (
    <div className="space-y-2">
      <button type="button" disabled={busy} onClick={() => void launch()}
        className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold disabled:opacity-50">
        {busy ? 'Checking Meta…' : 'Continue with official Meta Embedded Signup'}
      </button>
      <p className="text-xs text-slate-500">
        Uses your existing eligible WhatsApp Business app number. Completion validates
        Meta ownership only—it does not activate Lola message access or send anything.
      </p>
      {message && <p role="status" className="text-xs text-slate-700">{message}</p>}
    </div>
  );
}
