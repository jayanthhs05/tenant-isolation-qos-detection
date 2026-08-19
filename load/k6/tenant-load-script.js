// Continuous steady-rate load against one tenant's Service, for the fairness track's
// victim-side QoS signal (docs/architecture.md). Run against a co-located tenant while a
// noisy-neighbor scenario (scenarios/fairness/) is injected against a *different* tenant, and
// tail its `--out json=` output with monitors/resource-monitor/qos_monitor.py to turn rolling
// p95 latency into a canonical Event.
//
// Usage:
//   kubectl -n tenant-b port-forward svc/notes-api 18080:80 &
//   TARGET_URL=http://localhost:18080/healthz k6 run --out json=/tmp/k6-tenant-b.jsonl \
//     load/k6/tenant-load-script.js
import http from 'k6/http';
import { sleep } from 'k6';

const TARGET_URL = __ENV.TARGET_URL || 'http://localhost:8080/healthz';
const VUS = Number(__ENV.VUS || 2);
const DURATION = __ENV.DURATION || '10m';

export const options = {
  scenarios: {
    steady_load: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
};

export default function () {
  http.get(TARGET_URL);
  sleep(0.05);
}
