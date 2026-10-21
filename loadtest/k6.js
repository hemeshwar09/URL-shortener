// Usage: k6 run loadtest/k6.js
// Run once with REDIS_URL set and once without, then compare p99 latency.
import http from "k6/http";
import { check } from "k6";

export const options = { vus: 50, duration: "30s",
  thresholds: { http_req_duration: ["p(99)<100"] } };

const BASE = __ENV.BASE_URL || "http://localhost:8000";

export function setup() {
  const r = http.post(`${BASE}/api/shorten`, JSON.stringify({ url: "https://example.com" }),
    { headers: { "Content-Type": "application/json" } });
  return { code: r.json("code") };
}

export default function (data) {
  const res = http.get(`${BASE}/${data.code}`, { redirects: 0 });
  check(res, { "302": (r) => r.status === 302 });
}
