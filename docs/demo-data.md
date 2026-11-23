# Demo and synthetic data

CloudSentinel's demo exists so you can evaluate the product without connecting real infrastructure.
Because it is a monitoring product, it is essential that simulated data is never mistaken for real
measurements. These are the guarantees:

1. **Opt-in only.** The `synthetic` provider and `cloudsentinel seed-demo` only work when
   `CS_DEMO_MODE=true`. On a normal installation synthetic monitors are rejected by the API, skipped by
   the scheduler and hidden from the provider list.
2. **Always flagged.** Every synthetic component, monitor, check result, incident and risk finding is
   stored with `is_synthetic = true`, returned in the API, and shown with a *Synthetic* badge.
3. **Always disclosed.** When synthetic data exists, the console shows a banner, and reports (UI, CSV
   and PDF) state that they include synthetic data.
4. **Removable.** `cloudsentinel clear-demo` deletes all synthetic records and leaves real data intact.
5. **Real pipeline.** Synthetic results go through the same incident, correlation and risk logic as
   real results. Nothing in the demo is hand-crafted to look better than the product behaves.

## What the demo contains

A fictional platform of 13 components (load balancer, DNS, API gateway, services, PostgreSQL, Redis,
message queue, object storage, a Kubernetes cluster and an unmonitored backup server) with 19
dependencies and 12 synthetic monitors. Seven days of history are simulated at 5-minute resolution:

* a recurring Redis outage (every 31 hours) that cascades to the auth and orders services, producing
  correlated incidents;
* a flaky payments service (3 % random failures);
* a catalog API whose latency tripled in the last 24 hours;
* an unmonitored backup server that several components depend on.

The demo Docker Compose stack additionally creates **real** monitors against an nginx container and a
Prometheus container. Those are ordinary monitors without the synthetic flag.
