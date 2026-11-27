// 32 hex characters, like NGINX's $request_id.
// crypto.randomUUID only exists in secure contexts (https or localhost); opened
// over plain http on another address it is undefined, so fall back to
// getRandomValues, which is available everywhere.
export function newRequestId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID().replaceAll("-", "");
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
