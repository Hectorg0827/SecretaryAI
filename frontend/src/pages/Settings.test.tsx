import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { IntegrationRow, isTrustedConsentUrl } from './Settings';

describe('customer onboarding consent redirect safety', () => {
  it('accepts only the expected HTTPS Intuit and Google consent hosts', () => {
    expect(isTrustedConsentUrl('https://appcenter.intuit.com/connect/oauth2?state=synthetic', 'appcenter.intuit.com')).toBe(true);
    expect(isTrustedConsentUrl('https://accounts.google.com/o/oauth2/v2/auth?state=synthetic', 'accounts.google.com')).toBe(true);
  });

  it('rejects forged hosts and non-HTTPS redirects', () => {
    const host = 'appcenter.intuit.com';
    expect(isTrustedConsentUrl('https://appcenter.intuit.com.attacker.invalid/connect', host)).toBe(false);
    expect(isTrustedConsentUrl('http://appcenter.intuit.com/connect', host)).toBe(false);
    expect(isTrustedConsentUrl('javascript:alert(1)', host)).toBe(false);
    expect(isTrustedConsentUrl('https://user:pass@appcenter.intuit.com/connect', host)).toBe(false);
    expect(isTrustedConsentUrl('/relative-path', host)).toBe(false);
  });
});

describe('QuickBooks reauthorization entrypoint', () => {
  it('shows reconnect without forcing a disconnect when credentials are saved', () => {
    const markup = renderToStaticMarkup(
      <IntegrationRow
        label="QuickBooks Online"
        description="Cloud accounting"
        connected
        loading={false}
        onConnect={() => {}}
        onDisconnect={() => {}}
        connectLabel="Connect with Intuit"
        reconnectLabel="Reconnect"
        canManage
      />,
    );
    expect(markup).toContain('Reconnect');
    expect(markup).toContain('Disconnect');
  });
});
