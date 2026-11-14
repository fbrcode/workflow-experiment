// Golden-vector generator (Phase 2). Runs the ORIGINAL n8n node code and expressions from
// baseline/*.json on (a) the fixture emails, read through n8n's own IMAP code, and (b) the
// hand-written edge cases. Writes one JSON file per spec step to $GOLDEN_OUT.
//
// Needs the shared infra (mail seeded, mock-gemini, mock-telegram, app-db). Run via `make golden`.
// Output is deterministic: no timestamps, fixed "now", stable ordering, sorted keys where needed.
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import pg from 'pg';

import { ALERT_ERRORS, alertEdgeTransform, formatEdges, mapEdges, selectEdges, transformEdges } from './edge-cases.mjs';
import { captureSimpleItems, IMAP_REFERENCE } from './imap-capture.mjs';
import {
  codeNode,
  loadWorkflow,
  nodeAccessor,
  postgresColumns,
  setNode,
  templateParam,
} from './n8n-runtime.mjs';

const env = process.env;
const need = (name) => {
  if (!env[name]) throw new Error(`missing env ${name}`);
  return env[name];
};
const BASELINE = env.BASELINE_DIR ?? '/work/baseline';
const OUT = env.GOLDEN_OUT ?? '/work/vectors';
const FIXED_NOW = '2026-11-14T12:00:00.000-03:00'; // n8n's $now.toISO() in GMT-3, frozen
const DUPLICATE_FIXTURE = '03-the-news-duplicate'; // removed by DUP-1, never summarized
const CHAT_BY_CATEGORY = {
  news: need('TELEGRAM_CHAT_ID_NEWS'),
  finance: need('TELEGRAM_CHAT_ID_FINANCE'),
  tech: need('TELEGRAM_CHAT_ID_TECH'),
};
const TRANSLATE_LANGUAGE_DEFAULT = 'Brazilian Portuguese'; // DB default, D-007 / Q-14

const ingest = loadWorkflow(join(BASELINE, 'EmailSummarizerMainUnique.json'));
const summarizer = loadWorkflow(join(BASELINE, 'EmailSummarizerFromPgCron.json'));

const generator = { script: 'tests/golden/src/generate.mjs', node: process.version, now: FIXED_NOW };

function write(file, spec, source, notes, cases) {
  const doc = { spec, source, generator, notes, cases };
  writeFileSync(join(OUT, file), `${JSON.stringify(doc, null, 2)}\n`);
  console.log(`wrote ${file} (${cases.length} cases)`);
}

// --- external helpers -------------------------------------------------------------------------
async function postJson(url, body, headers = {}) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
  return { status: res.status, body: await res.json() };
}

async function mockSummary(prompt, model) {
  // SUM-5 via mock-gemini: deterministic text for (prompt, model). Includes the n8n agent's
  // default system message (Q-13), as both implementations will send it.
  const url = `${need('GEMINI_BASE_URL')}/v1beta/models/${model.replace(/^models\//, '')}:generateContent`;
  const { status, body } = await postJson(
    url,
    { contents: [{ role: 'user', parts: [{ text: prompt }] }], systemInstruction: { parts: [{ text: 'You are a helpful assistant' }] } },
    { 'x-goog-api-key': need('GEMINI_API_KEY') },
  );
  if (status !== 200) throw new Error(`mock-gemini ${status}: ${JSON.stringify(body)}`);
  return body.candidates[0].content.parts[0].text;
}

async function mockTelegram(chatId, text, parseMode) {
  // What Telegram would do with the message (per mock-telegram, D-012): rendered text or error.
  const url = `${need('TELEGRAM_BASE_URL')}/bot${need('TELEGRAM_BOT_TOKEN')}/sendMessage`;
  const { status, body } = await postJson(url, { chat_id: chatId, text, parse_mode: parseMode });
  return body.ok ? { status, ok: true, result_text: body.result.text } : { status, ok: false, description: body.description };
}

async function resetMocks() {
  for (const base of [need('GEMINI_BASE_URL'), need('TELEGRAM_BASE_URL')]) await fetch(`${base}/_mock/reset`, { method: 'POST' });
}

// --- main -------------------------------------------------------------------------------------
async function main() {
  mkdirSync(OUT, { recursive: true });
  await resetMocks();
  const db = new pg.Client({
    host: need('APP_DB_HOST'),
    port: Number(need('APP_DB_PORT')),
    database: need('APP_DB_NAME'),
    user: need('APP_DB_USER'),
    password: need('APP_DB_PASSWORD'),
  });
  await db.connect();
  const initcap = async (text) => (await db.query('select initcap($1::text) as v', [text])).rows[0].v;
  const dbLocale = (
    await db.query(
      `select d.datctype as ctype, d.datlocprovider as provider, version() as version
         from pg_database d where d.datname = current_database()`,
    )
  ).rows[0];

  // ING-1: n8n's IMAP read of the seeded mailbox --------------------------------------------
  const captured = await captureSimpleItems({
    host: need('IMAP_HOST'),
    port: Number(need('IMAP_PORT')),
    secure: need('IMAP_SECURE') === 'true',
    user: need('IMAP_USER'),
    password: need('IMAP_PASSWORD'),
    mailbox: need('IMAP_MAILBOX'),
  });
  const fixtures = captured
    .map((item) => {
      const fixture = (item.metadata['message-id'] ?? '').match(/^<(.+)@fixtures\.wfx\.local>$/)?.[1];
      if (!fixture) throw new Error(`unexpected message in mailbox: ${item.metadata['message-id']}`);
      const { from, to, subject, date, textHtml, textPlain } = item;
      return { fixture, item: { from, to, subject, date, textHtml, textPlain } };
    })
    .sort((a, b) => a.fixture.localeCompare(b.fixture));
  if (fixtures.length !== 9) throw new Error(`expected 9 unseen fixture emails, found ${fixtures.length}; run make seed`);

  write(
    'ing1-imap-simple.json',
    'ING-1',
    IMAP_REFERENCE,
    'Output of the IMAP trigger (format "simple") for each fixture, read from GreenMail with n8n\'s own IMAP code. ' +
      'Only the fields ING-2 uses (plus "to") are kept. Line endings are exactly as n8n delivers them.',
    fixtures.map(({ fixture, item }) => ({ id: `fixture:${fixture}`, tags: ['fixture'], input: { fixture: `fixtures/emails/${fixture}.eml` }, output: item })),
  );

  // ING-2: select-n8n-content-generic ---------------------------------------------------------
  const select = setNode(ingest, 'select-n8n-content-generic');
  const selectCases = [
    ...fixtures.map(({ fixture, item }) => ({ id: `fixture:${fixture}`, tags: ['fixture'], input: item })),
    ...selectEdges.map((c) => ({ ...c, tags: ['edge'] })),
  ].map((c) => ({ ...c, output: select.run(c.input) }));
  write('ing2-select-content.json', 'ING-2', select.source, 'Set node output: only the five assigned fields.', selectCases);

  // ING-3: map-content ---------------------------------------------------------------------------
  const map = codeNode(ingest, 'map-content');
  const mapCases = [
    ...selectCases.filter((c) => c.tags.includes('fixture')).map((c) => ({ id: c.id, tags: ['fixture'], input: c.output })),
    ...mapEdges.map((c) => ({ ...c, tags: ['edge'] })),
  ].map((c) => ({ ...c, output: map.run([c.input])[0] }));
  write('ing3-map-content.json', 'ING-3', map.source, 'Code node run per item ($input.all() = [input]).', mapCases);

  // ING-4: insert_raw_news column mapping --------------------------------------------------------
  const insert = postgresColumns(ingest, 'insert_raw_news');
  const insertCases = mapCases.map((c) => ({ id: c.id, tags: c.tags, input: c.output, output: insert.run({ $json: c.output }) }));
  write(
    'ing4-insert-raw-news.json',
    'ING-4',
    insert.source,
    'Values written to public.news_email per ingested email. All other columns take DB defaults.',
    insertCases,
  );

  // SUM-3: transform-content on a SUM-1 row ---------------------------------------------------
  const fixtureInserts = insertCases.filter((c) => c.tags.includes('fixture') && !c.id.endsWith(DUPLICATE_FIXTURE));
  const sum1Row = (row, index) => ({
    id: index + 1,
    email_inbox_label: row.email_inbox_label,
    email_date: row.email_date,
    email_from: row.email_from,
    email_subject: row.email_subject,
    to_translate: row.to_translate,
    translate_language: TRANSLATE_LANGUAGE_DEFAULT,
    email_raw_content: row.email_raw_content,
    summary_retry: 0,
  });
  const transform = codeNode(summarizer, 'transform-content');
  const transformCases = [
    ...fixtureInserts.map((c, i) => ({ id: c.id, tags: ['fixture'], input: sum1Row(c.output, i) })),
    ...transformEdges.map((c) => ({ ...c, tags: ['edge'] })),
  ].map((c) => ({ ...c, output: transform.run([c.input])[0] }));
  write(
    'sum3-transform-content.json',
    'SUM-3',
    transform.source,
    'Input is a SUM-1 row. Fixture rows use id = position, translate_language = DB default, summary_retry = 0.',
    transformCases,
  );

  // SUM-4: prompt-builder --------------------------------------------------------------------
  const promptBuilder = codeNode(summarizer, 'prompt-builder');
  const promptCases = transformCases.map((c) => ({ id: c.id, tags: c.tags, input: c.output, output: promptBuilder.run([c.output])[0] }));
  write('sum4-prompt-builder.json', 'SUM-4', promptBuilder.source, 'Model names are hard-coded in the node (D-011).', promptCases);

  // SUM-7b: telegram-send-failed-exec text ----------------------------------------------------
  const alert = templateParam(summarizer, 'telegram-send-failed-exec', 'text');
  const alertInputs = [
    ...promptCases
      .filter((c) => c.tags.includes('fixture'))
      .slice(0, 2)
      .map((c) => ({ id: c.id, tags: ['fixture'], transform: c.input, prompt: c.output, error: ALERT_ERRORS.fixture })),
    {
      id: 'edge:q15-html-in-subject',
      tags: ['edge', 'quirk:Q-15'],
      transform: alertEdgeTransform,
      prompt: promptBuilder.run([alertEdgeTransform])[0],
      error: ALERT_ERRORS.q15,
    },
  ];
  const alertCases = [];
  for (const a of alertInputs) {
    const ctx = {
      $now: { toISO: () => FIXED_NOW },
      $json: { error: a.error },
      $: nodeAccessor({ 'transform-content': a.transform, 'prompt-builder': a.prompt }),
    };
    const text = alert.run(ctx);
    alertCases.push({
      id: a.id,
      tags: a.tags,
      input: { now: FIXED_NOW, error: a.error, 'transform-content': a.transform, 'prompt-builder': a.prompt },
      output: { text, parse_mode: alert.additionalFields.parse_mode },
      mock_telegram: await mockTelegram(need('TELEGRAM_ALERT_CHAT_ID'), text, alert.additionalFields.parse_mode),
    });
  }
  write(
    'sum7b-alert-message.json',
    'SUM-7b',
    alert.source,
    `Alert text with $now frozen at ${FIXED_NOW}. mock_telegram = what the HTML parse gives (D-012).`,
    alertCases,
  );

  // SND-2: format-as-markdown on a SND-1 row ---------------------------------------------------
  const format = codeNode(summarizer, 'format-as-markdown');
  const model = promptCases[0].output.model;
  const fixtureSendRows = [];
  for (const [i, c] of promptCases.filter((p) => p.tags.includes('fixture')).entries()) {
    const row = fixtureInserts[i].output;
    const translated = `${row.to_translate ? 'translated to ' : 'original on '}${TRANSLATE_LANGUAGE_DEFAULT}`;
    fixtureSendRows.push({
      id: c.id,
      tags: ['fixture'],
      input: {
        id: i + 1,
        email_inbox_label: row.email_inbox_label,
        telegram_chat_id: CHAT_BY_CATEGORY[row.email_category],
        email_category: row.email_category,
        email_from: await initcap(row.email_from),
        email_subject: await initcap(row.email_subject),
        email_date: row.email_date,
        translated,
        model_used: model,
        summary_content: await mockSummary(c.output.prompt, model),
        sent_retry: 0,
      },
    });
  }
  const formatCases = [];
  for (const c of [...fixtureSendRows, ...formatEdges.map((e) => ({ ...e, tags: ['edge'] }))]) {
    const output = format.run([c.input])[0];
    formatCases.push({ ...c, output, mock_telegram: await mockTelegram(c.input.telegram_chat_id, output.telegramMessage, 'MarkdownV2') });
  }
  write(
    'snd2-format-as-markdown.json',
    'SND-2',
    format.source,
    'Input is a SND-1 row. Fixture rows: initcap() from the real app-db ' +
      `(${dbLocale.version.split(' on ')[0]}, ctype ${dbLocale.ctype}, locale provider ${dbLocale.provider}), ` +
      'summary_content from mock-gemini for the SUM-4 prompt ' +
      '(primary model), translated per the SND-1 CASE. mock_telegram.result_text is the expected formatted_summary (SND-4) ' +
      'when ok; when not ok the send fails and SND-5 applies.',
    formatCases,
  );

  await db.end();
  await resetMocks();
}

main().then(
  () => process.exit(0),
  (err) => {
    console.error(err);
    process.exit(1);
  },
);
