let session: Promise<string> | undefined;

async function token() {
  session ??= fetch('/api/session').then(async response => {
    if (!response.ok) throw Error('本地后端尚未就绪，请检查启动窗口。');
    return (await response.json()).token as string;
  }).catch(error => { session = undefined; throw error; });
  return session;
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await rawRequest(path, options);
  return response.json() as Promise<T>;
}

export async function rawRequest(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers);
  if (options.method && options.method !== 'GET') headers.set('X-Session-Token', await token());
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api' + path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: '服务响应异常，请检查启动窗口。' }));
    throw Error(typeof body.detail === 'string' ? body.detail : `操作失败（${response.status}）。`);
  }
  return response;
}

export function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
