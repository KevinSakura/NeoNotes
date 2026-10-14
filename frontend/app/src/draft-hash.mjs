/** Canonical draft hash v1: IEEE754 numeric tags, sorted JSON keys and UTF-8. */
function normalize(value) {
  if (typeof value === 'number') {
    const bytes = new ArrayBuffer(8);
    new DataView(bytes).setFloat64(0, value === 0 ? 0 : value, false);
    return { '@number': [...new Uint8Array(bytes)].map(b => b.toString(16).padStart(2, '0')).join('') };
  }
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, normalize(value[k])]));
  return value;
}

export async function draftHash(state) {
  const bytes = new TextEncoder().encode(JSON.stringify(normalize(state)));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}
