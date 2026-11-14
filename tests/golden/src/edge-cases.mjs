// Hand-written inputs that the 9 fixtures don't reach. Every expected OUTPUT is still produced
// by the original n8n code; only the inputs are invented here. IDs are stable: never renumber.

const DATE = 'Sat, 14 Nov 2026 12:00:00 +0000';

/** ING-2 inputs: shaped like the IMAP node's "simple" item. */
export const selectEdges = [
  {
    id: 'edge:link-variants',
    input: {
      from: '"Link Tester" <links@example.example>',
      subject: 'Links',
      date: DATE,
      textPlain:
        'A (https://a.example/x) B (http://b.example) C ( https://c.example) D (https://d.example/a_(b)) ' +
        'E (ftp://e.example) F text(https://f.example/g)text G https://bare.example/h',
      textHtml: '<p><a href="https://x.example">(https://x.example)</a> keep https://bare.example</p>',
    },
  },
  {
    id: 'edge:no-html-part',
    input: { from: 'Plain Only <p@example.example>', subject: 'Plain', date: DATE, textPlain: 'Only text (https://p.example).', textHtml: '' },
  },
];

/** ING-3 inputs: shaped like the select-n8n-content-generic output. */
const mapBase = { subject: 'Edge subject', date: DATE, htmlContent: '<p>html body</p>', plainContent: 'plain body' };
const fromCase = (id, from) => ({ id: `edge:from-${id}`, input: { ...mapBase, from } });
const dateCase = (id, date) => ({ id: `edge:date-${id}`, input: { ...mapBase, from: 'Date Tester <d@x.example>', date } });

export const mapEdges = [
  fromCase('unquoted-morning-brew', 'Morning Brew <crew@morningbrew.example>'),
  fromCase('angle-only', '<only@addr.example>'),
  fromCase('symbols-in-name', '"AI & ML Weekly #3" <x@y.example>'),
  fromCase('extra-spaces', '"  Spaced   Out  Name " <s@o.example>'),
  fromCase('accented-name', '"Café Brasil" <c@b.example>'),
  fromCase('uppercase-rico', '"RICO MATINAL" <m@rico.example>'),
  fromCase('refactoring', '"Refactoring" <luca@refactoring.example>'),
  fromCase('no-space-before-angle', '"Tech Brew"<crew@techbrew.example>'),
  fromCase('inner-quotes', 'Name With "Quote" <q@x.example>'),
  dateCase('utc-comment', 'Sat, 14 Nov 2026 11:05:00 +0000 (UTC)'),
  dateCase('gmt-no-weekday', '14 Nov 2026 02:59:00 GMT'),
  dateCase('month-rollback', 'Sun, 1 Mar 2026 02:00:00 +0000'),
  dateCase('year-end-minus-0300', 'Thu, 31 Dec 2026 23:30:00 -0300'),
  dateCase('single-digit-hour', 'Mon, 9 Nov 2026 8:05:00 +0000'),
  dateCase('est-zone-name', 'Tue, 10 Nov 2026 23:59:59 EST'),
];

/** SUM-3 inputs: shaped like a pg-get-dedup-raw-news (SUM-1) row. */
const rowBase = {
  email_inbox_label: 'edge',
  email_date: 'Sat, 14 Nov 2026 at 09h 05min',
  email_subject: 'Edge subject',
  summary_retry: 0,
};
export const transformEdges = [
  {
    id: 'edge:phrases-and-non-ascii',
    input: {
      ...rowBase,
      id: 101,
      email_from: 'Morning Brew <crew@x.example>',
      to_translate: true,
      translate_language: 'Brazilian Portuguese',
      email_raw_content:
        'View image: https://i.example/1\r\nCaption: Café — ação!\tTab\nFollow image link: https://l.example ' +
        'View image: again 🙂 end. Caption:no-space-kept',
    },
  },
  {
    id: 'edge:from-without-angle',
    input: { ...rowBase, id: 102, email_from: 'The News', to_translate: false, translate_language: 'Brazilian Portuguese', email_raw_content: 'Simple.' },
  },
  {
    id: 'edge:nulls',
    input: { ...rowBase, id: 103, email_from: '', to_translate: false, translate_language: null, email_raw_content: null },
  },
];

/** SND-2 inputs: shaped like a pg-get-summary (SND-1) row. */
const sendBase = {
  email_inbox_label: 'edge',
  telegram_chat_id: '-1001000000003',
  email_category: 'tech',
  email_from: "Lenny's Newsletter",
  email_subject: 'Edge Case',
  email_date: 'Wed, 11 Nov 2026 at 10h 00min',
  translated: 'translated to Brazilian Portuguese',
  model_used: 'models/gemini-flash-lite-latest',
  sent_retry: 0,
};
const summaryCase = (n, id, summary_content, extra = {}) => ({
  id: `edge:${id}`,
  input: { ...sendBase, id: 200 + n, summary_content, ...extra },
});

export const formatEdges = [
  summaryCase(1, 'literal-backslash-n', 'Intro line\\n\\n* First point\\n* Second point'),
  summaryCase(2, 'headings-and-rules', '## Heading Two\n### Heading Three\n#### Heading Four\n---\nBody.'),
  summaryCase(
    3,
    'bullet-variants',
    '*   Star three spaces\n    *   Nested star\n* Star one space\n- Dash one\n    - Nested dash\n-   Dash three spaces',
  ),
  summaryCase(4, 'bold-with-specials', '**Price: $4.25 (up 1.2%)!** and **[tag] #1 + x = y**'),
  summaryCase(5, 'italic-and-lone-star', '*italic words* then a lone * star and 2 * 3 = 6'),
  summaryCase(6, 'double-quotes', 'He said "ship it" and "test it".'),
  summaryCase(7, 'all-specials', 'Specials: _ [ ] ( ) ~ ` > # + - = | { } . ! \\ end'),
  summaryCase(8, 'emoji-and-accents', 'Resumo 🇧🇷: ação, café, coração, “aspas” e ‘simples’.'),
  summaryCase(9, 'bold-across-lines', '**bold that\nspans lines** after'),
  summaryCase(10, 'unclosed-bold', '**never closed and more text.'),
  summaryCase(11, 'empty-summary', ''),
  summaryCase(12, 'too-long-after-escaping', 'A. '.repeat(1400).trim()),
  summaryCase(
    13,
    'typical-llm-output',
    '### Principais tópicos\n\n*   **Mercados:** As bolsas subiram 1,2% (maior alta do mês).\n*   **Juros:** O BC manteve a taxa em 10,50%.\n    *   Decisão unânime.\n\n#### Tecnologia\n- Novo modelo de IA em português - veja mais em example.com/ia\n\n---\n*Resumo gerado automaticamente.*',
  ),
  summaryCase(14, 'specials-in-header', 'Short body.', {
    email_from: 'Ai & Ml Weekly #3',
    email_subject: 'Q3 Results (Final) - Up 12.5%!',
    email_category: 'finance',
    telegram_chat_id: '-1001000000002',
  }),
];

/** SUM-7b: an error string per case, plus a subject that breaks Telegram HTML (quirk Q-15). */
export const ALERT_ERRORS = {
  fixture: 'Mock fault 503 for models/gemini-2.5-flash',
  q15: 'Request failed with status code 500',
};
export const alertEdgeTransform = {
  news_id: 301,
  from: 'Deals Daily',
  subject: 'Deals <today> & more',
  date: 'Sat, 14 Nov 2026 at 09h 05min',
  to_translate: false,
  translate_language: 'Brazilian Portuguese',
  content: 'Deals.',
};
