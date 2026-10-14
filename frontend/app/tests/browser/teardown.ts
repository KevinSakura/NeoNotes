import { request } from '@playwright/test';

// Conda's Windows wrapper can swallow termination signals. Stop only the identified
// isolated test server through its session-protected route before Playwright cleans up.
export default async function teardown() {
  const client = await request.newContext({ baseURL: 'http://127.0.0.1:18000', timeout: 5000 });
  try {
    const identityResponse = await client.get('/api/test-server');
    if (!identityResponse.ok()) throw new Error('Cannot identify the isolated browser test server.');
    const identity = await identityResponse.json();
    if (identity.isolated_browser_test !== true || !/^[a-f0-9]{32}$/.test(identity.run_id)) {
      throw new Error('Refusing to stop an unidentified service.');
    }
    const session = await (await client.get('/api/session')).json();
    const result = await client.post('/api/test-server/shutdown', {
      headers: { 'X-Session-Token': session.token }, data: { run_id: identity.run_id },
    });
    if (!result.ok()) throw new Error('Isolated browser test server failed to stop.');
  } finally {
    await client.dispose();
  }
}
