# Decision log

Every decision that shapes the experiment is recorded here: scope, assumptions, and deviations
from the baseline spec. Add new entries at the bottom and never delete old ones. If a decision is
reversed, add a new entry that supersedes it.

Statuses: `accepted` · `proposed` (waiting for the owner) · `superseded by D-xxx`

---

### D-001: Tool B is Prefect OSS · `accepted`

"perfect" in the original request means **Prefect**. The owner confirmed this on 2026-11-14.
We use the open-source, self-hosted Prefect server and workers. No Prefect Cloud (see D-006).

### D-002: Spec-first, parity-gated · `accepted`

Both implementations are built from [`baseline/workflow-spec.md`](baseline/workflow-spec.md),
not from each other and not by mechanically translating the n8n JSON. Parity (criterion C1) gates
the comparison.

### D-003: Keep the baseline quirks by default · `accepted`

Quirks Q-1 to Q-16 in the spec keep their baseline behavior unless an entry here says otherwise.
Any deviation is applied the **same way** in both tools.

### D-004: Mocks by default, real services optional · `accepted`

Runs are local and reproducible: GreenMail stands in for IMAP, and HTTP mocks stand in for the
Telegram Bot API and Gemini. A `real` mode using credentials from `.env` is supported as an
end-to-end smoke test only. Measurements use mocks.
*Consequence:* each tool must let us point Telegram and Gemini at another base URL. Whether it
can do that natively is a data point for C10.

### D-005: Keep the two-workflow split · `accepted`

Ingest and summarize/send stay as separate flows in both tools (Q-9). Both tools can express
either shape, and keeping the split keeps the comparison simple.

### D-006: Self-hosted OSS editions only · `accepted`

Kestra open-source and the self-hosted Prefect server: no Kestra Enterprise and no Prefect Cloud.
Features gated behind paid editions are noted under C12 but not used.

### D-007: Reconstructed schema until the real DDL arrives · `accepted`

We use the schema in [`baseline/data-model.md`](baseline/data-model.md). `translate_language`
defaults to `'Brazilian Portuguese'` as an assumption (Q-14). Replace it when the production
schema is provided.

### D-008: Error-path `model_used` = primary model (Q-3) · `accepted`

In n8n, `pg-update-ai-error` writes `model_used = $json.model` from the error item, which is
probably empty. Both tools write the **primary model name** (`GEMINI_MODEL`) instead. This is a
deliberate deviation from the baseline. The owner confirmed it on 2026-11-14.

### D-009: n8n is not a contestant · `accepted`

n8n is **not** run locally and isn't measured. The `baseline/` exports are used only as the
source of the behavior spec. The comparison covers Kestra and Prefect only. The owner decided
this on 2026-11-14.

### D-010: Redact the admin chat ID in the baseline before publishing · `accepted`

The repo is public. The owner approved one edit to `baseline/EmailSummarizerFromPgCron.json`
before the first commit: in node `telegram-send-failed-exec`, `chatId` was changed from the real
Telegram ID to `"REDACTED_TELEGRAM_ALERT_CHAT_ID"`. No other byte changed. Implementations read
the value from `TELEGRAM_ALERT_CHAT_ID`. Apart from this one approved change, `baseline/` stays read-only.

### D-011: Model names keep the `models/` prefix in config · `accepted`

`GEMINI_MODEL=models/gemini-flash-lite-latest` and `GEMINI_MODEL_FALLBACK=models/gemini-2.5-flash`
are written exactly as the baseline has them. The prefixed string is what n8n stores in `model_used`
(SUM-6, and SUM-7a per D-008) and shows in the Telegram header (SND-2), so parity needs it. Each
implementation strips the prefix only when it builds the API URL. This is a config convention,
not a behavior deviation.

### D-012: The Telegram mock validates and renders like Telegram · `accepted`

The mock parses MarkdownV2 and HTML and rejects invalid text with Telegram-style 400 errors. On
success it returns the rendered plain text in `result.text`. Two reasons: SND-4 stores
`result.text`, so the rendered value matters for parity; and escaping bugs (F-3, Q-15) should
come up naturally rather than only through injected faults. Known approximations are listed in
[infra.md](infra.md#mock-telegram). Phase 7 (real Telegram) is where any gaps would show.

### D-013: Fixtures are loaded with IMAP APPEND, not SMTP · `accepted`

The plan said SMTP. APPEND puts each message straight into the `n8n` folder (the Gmail label)
with no flags, so it's UNSEEN, and keeps the original `Date` as INTERNALDATE. Delivering over
SMTP would put mail in INBOX and need a server-side filter. Ingest behavior doesn't depend on
how mail arrived in the folder.
