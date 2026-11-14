// ING-1 reference: read the mailbox the way n8n's "Email Trigger (IMAP)" node does with
// format "simple". Uses the published @n8n/imap package that n8n 2.47.7 (stable) ships, and
// mirrors the node's glue in packages/nodes-base/nodes/EmailReadImap/v2/utils.ts @ n8n@2.47.7:
//   search criteria ["UNSEEN"] -> { seen: false }; fetch query { headers: true, bodyStructure: true };
//   typeVersion 2 -> no batch limit; textHtml/textPlain via downloadText(); headers via parseHeaders()
//   and the first value of cc/date/from/subject/to promoted to the top level.
// Unlike the node, nothing is marked \Seen here (postProcessAction "read" is the node's job).
import { ImapSimple, parseHeaders } from '@n8n/imap';

const TOP_LEVEL_HEADERS = ['cc', 'date', 'from', 'subject', 'to'];

export const IMAP_REFERENCE = {
  n8n: 'n8n@2.47.7 (stable)',
  package: '@n8n/imap@0.33.0 (imapflow 1.7.1, libmime 5.4.2)',
  node: 'n8n-nodes-base.emailReadImap typeVersion 2, format "simple"',
};

export async function captureSimpleItems({ host, port, secure, user, password, mailbox }) {
  const connection = await ImapSimple.connect({ host, port, secure, user, password, allowUnauthorizedCerts: true });
  try {
    await connection.openBox(mailbox);
    const messages = await connection.search({ seen: false }, { headers: true, bodyStructure: true });
    const items = [];
    for (const message of messages) {
      const headers = message.headers && parseHeaders(message.headers);
      if (!headers) throw new Error(`message UID ${message.uid} has no headers`);
      const json = {
        textHtml: await connection.downloadText(message, 'html'),
        textPlain: await connection.downloadText(message, 'plain'),
        metadata: {},
      };
      for (const [name, values] of Object.entries(headers)) {
        if (!values.length) continue;
        if (TOP_LEVEL_HEADERS.includes(name)) json[name] = values[0];
        else json.metadata[name] = values[0];
      }
      items.push(json);
    }
    return items;
  } finally {
    connection.end();
  }
}
