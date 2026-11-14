// ING-1 reference: read the mailbox the way the production n8n's "Email Trigger (IMAP)" node does
// with format "simple". Production runs n8n 2.40.3 (owner-confirmed, D-017), which ships
// @n8n/imap@0.26.0: the node-imap based client (imap 0.8.19, quoted-printable, utf8, iconv-lite).
// The glue mirrors packages/nodes-base/nodes/EmailReadImap/v2/utils.ts @ n8n@2.40.3:
//   search criteria ['UNSEEN']; fetch options { bodies: ['TEXT', 'HEADER'], markSeen: false, struct: true };
//   typeVersion 2 -> no batch limit; textHtml/textPlain via downloadText(); the HEADER part (parsed by
//   node-imap's parseHeader) gives the first value of cc/date/from/subject/to at the top level.
// Unlike the node, nothing is marked \Seen here (postProcessAction "read" is the node's job).
import { ImapSimple } from '@n8n/imap';

const TOP_LEVEL_HEADERS = ['cc', 'date', 'from', 'subject', 'to'];
const FETCH_OPTIONS_SIMPLE = { bodies: ['TEXT', 'HEADER'], markSeen: false, struct: true };

export const IMAP_REFERENCE = {
  n8n: 'n8n@2.40.3 (production, owner-confirmed)',
  package: '@n8n/imap@0.26.0 (imap 0.8.19, quoted-printable 1.0.1, utf8 3.0.0, iconv-lite 0.6.3)',
  node: 'n8n-nodes-base.emailReadImap typeVersion 2, format "simple"',
};

export async function captureSimpleItems({ host, port, secure, user, password, mailbox }) {
  const connection = await ImapSimple.connect({ host, port, secure, user, password, allowUnauthorizedCerts: true });
  try {
    await connection.openBox(mailbox);
    const messages = await connection.search(['UNSEEN'], FETCH_OPTIONS_SIMPLE);
    const items = [];
    for (const message of messages) {
      const header = message.parts.find((part) => part.which === 'HEADER');
      if (!header?.body) throw new Error(`message UID ${message.attributes.uid} has no HEADER part`);
      const json = {
        textHtml: await connection.downloadText(message, 'html'),
        textPlain: await connection.downloadText(message, 'plain'),
        metadata: {},
      };
      for (const [name, values] of Object.entries(header.body)) {
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
