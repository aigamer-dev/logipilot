// LogiPilot key vault: hands the app its API keys at startup, so no real key lives in the repo.
// One access token (VAULT_TOKEN) is shared with judges; rotate or delete it to cut access off.
const KEYS = ["TYPESAFE_API_KEY", "OLLAMA_KEY", "GCP_SA_KEY_JSON", "GOOGLE_CLOUD_PROJECT"];

function safeEqual(a, b) {
  // Constant-time comparison so the token can't be guessed byte by byte from timing.
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/v1/keys") {
      return new Response("Not found", { status: 404 });
    }
    const token = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
    if (!env.VAULT_TOKEN || !safeEqual(token, env.VAULT_TOKEN)) {
      return new Response("Unauthorized", { status: 401 });
    }
    const body = Object.fromEntries(KEYS.filter((k) => env[k]).map((k) => [k, env[k]]));
    return Response.json(body, { headers: { "Cache-Control": "no-store" } });
  },
};
