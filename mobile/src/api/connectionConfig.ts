/** Validate the build-configured API destination before sending credentials. */
export function validatedApiBase(raw: string, devMode: boolean): string {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw new Error('A valid SecretaryAI API address is required.');
  }

  const hostname = url.hostname.toLowerCase();
  const loopback = ['localhost', '127.0.0.1', '[::1]', '10.0.2.2'].includes(hostname);
  const placeholder = hostname === 'example.com' || hostname.endsWith('.example.com') ||
    hostname.includes('placeholder') || hostname.includes('changeme');
  const trustedTransport = url.protocol === 'https:' || (devMode && loopback && url.protocol === 'http:');

  if (!trustedTransport || url.username || url.password || url.search || url.hash ||
      placeholder || (!devMode && loopback)) {
    throw new Error('SecretaryAI requires a secure, configured API server.');
  }

  return url.toString().replace(/\/+$/, '');
}
