# Documentation index

| Doc | Purpose |
|---|---|
| [next-steps.md](next-steps.md) | **Start here.** Pending owner actions and Claude's next moves |
| [plan.md](plan.md) | Phases, checklist, open questions, planned layout and ports |
| [decisions.md](decisions.md) | Decision log: assumptions, scope, deviations from the baseline |
| [infra.md](infra.md) | Shared local infra: services, mock APIs and fault injection, fixtures matrix |
| [golden.md](golden.md) | Golden vectors: provenance, file format, how ports use them, findings G-1 to G-9 |
| [parity.md](parity.md) | Parity suite: how it runs both stacks, the scenarios, results, and where the tools differ within the contract |
| [baseline/workflow-spec.md](baseline/workflow-spec.md) | **The contract.** Behavior spec derived from the n8n exports |
| [baseline/data-model.md](baseline/data-model.md) | The production Postgres schema and what its defaults mean for behavior |
| [baseline/prod-schema.md](baseline/prod-schema.md) | Owner-provided psql `\d+` output from production, verbatim |
| [comparison/criteria.md](comparison/criteria.md) | Evaluation dimensions, scoring, fault-injection scenarios, node mapping template |
| [comparison/results.md](comparison/results.md) | **The result.** Scorecard, measurements, evidence per dimension, recommendation |
| [reports/retool-replacement.md](reports/retool-replacement.md) | **The Retool replacement.** Retool Workflows vs Kestra vs Prefect vs AWS Step Functions on promotion, spec-driven development, control-table gates, human steps, 30-minute Postgres work, Lambda, ECS tasks, webhooks, AWS services, hosting and price; options and recommendation |
| [reports/kestra.md](reports/kestra.md) | Kestra for the Retool replacement: running it, the development lifecycle, pros and cons per capability |
| [reports/prefect.md](reports/prefect.md) | Prefect for the Retool replacement: running it, the development lifecycle, pros and cons per capability |
| [reports/step-functions.md](reports/step-functions.md) | AWS Step Functions for the Retool replacement, from AWS's docs: the design, the development lifecycle, pros and cons per capability, a pilot plan |
| [probes.md](probes.md) | Capability probes: a 30-minute Postgres statement, a control-table gate with a human step, and a webhook, in both tools |
| [comparison/data/](comparison/data/) | Raw evidence with date, host, versions and command: parity runs, measurements, line counts, UI screenshots |
| [reference/](reference/) | External material provided by the owner (e.g. the original comparison doc) |
| [kestra/README.md](kestra/README.md) | Kestra: setup, architecture, versions, verification |
| [kestra/node-mapping.md](kestra/node-mapping.md) | Kestra construct per n8n node, native or custom |
| [kestra/effort-log.md](kestra/effort-log.md) | Kestra friction, doc gaps and workarounds |
| [prefect/README.md](prefect/README.md) | Prefect: setup, architecture, versions, verification |
| [prefect/node-mapping.md](prefect/node-mapping.md) | Prefect construct per n8n node: native, library or custom |
| [prefect/effort-log.md](prefect/effort-log.md) | Prefect friction, doc gaps and workarounds |
