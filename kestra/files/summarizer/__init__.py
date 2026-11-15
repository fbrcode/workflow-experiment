"""Email Summarizer logic for the Kestra implementation (Phase 3).

Kestra loads this package as namespace files (namespace `experiment.email`) into Python script
tasks. Only the logic that Kestra has no plugin for lives here; everything else is in the flow
YAML. See docs/kestra/node-mapping.md.

- `ported`: the n8n Code/Set node logic (ING-2, ING-3, SUM-3, SUM-4, SND-2), checked against
  tests/golden/vectors byte for byte.
- `imap_simple`: ING-1, reading UNSEEN mail the way n8n's IMAP trigger does.
- `task`: the entry points the flows call (`python -m summarizer.task <step>`).

Standard library only, so the task container is a stock Python image.
"""
