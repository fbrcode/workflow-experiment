# Documentation index

| Doc | Purpose |
|---|---|
| [next-steps.md](next-steps.md) | **Start here.** Pending owner actions and Claude's next moves |
| [plan.md](plan.md) | Phases, checklist, open questions, planned layout and ports |
| [decisions.md](decisions.md) | Decision log: assumptions, scope, deviations from the baseline |
| [infra.md](infra.md) | Shared local infra: services, mock APIs and fault injection, fixtures matrix |
| [golden.md](golden.md) | Golden vectors: provenance, file format, how ports use them, findings G-1 to G-9 |
| [baseline/workflow-spec.md](baseline/workflow-spec.md) | **The contract.** Behavior spec derived from the n8n exports |
| [baseline/data-model.md](baseline/data-model.md) | The production Postgres schema and what its defaults mean for behavior |
| [baseline/prod-schema.md](baseline/prod-schema.md) | Owner-provided psql `\d+` output from production, verbatim |
| [comparison/criteria.md](comparison/criteria.md) | Evaluation dimensions, scoring, fault-injection scenarios, node mapping template |
| [reference/](reference/) | External material provided by the owner (e.g. the original comparison doc) |
| [kestra/README.md](kestra/README.md) | Kestra: setup, architecture, versions, verification |
| [kestra/node-mapping.md](kestra/node-mapping.md) | Kestra construct per n8n node, native or custom |
| [kestra/effort-log.md](kestra/effort-log.md) | Kestra friction, doc gaps and workarounds |
| [prefect/README.md](prefect/README.md) | Prefect: setup, architecture, versions, verification |
| [prefect/node-mapping.md](prefect/node-mapping.md) | Prefect construct per n8n node: native, library or custom |
| [prefect/effort-log.md](prefect/effort-log.md) | Prefect friction, doc gaps and workarounds |
| `comparison/results.md` *(Phase 6)* | Final scorecard and recommendation |
