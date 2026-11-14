// Minimal n8n runtime shim: runs Code nodes and expressions exactly as written in baseline/*.json.
// Only what the baseline uses is implemented; anything else throws (ReferenceError/TypeError),
// so a missing piece can never silently change an output.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

export const sha256 = (text) => createHash('sha256').update(text, 'utf8').digest('hex');

export function loadWorkflow(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

export function getNode(workflow, name) {
  const node = workflow.nodes.find((n) => n.name === name);
  if (!node) throw new Error(`node "${name}" not found in workflow "${workflow.name}"`);
  return node;
}

/**
 * Code node (typeVersion 2, mode "Run Once for All Items"). The jsCode is a function body that
 * receives `$input` and returns `[{ json }]`. Inputs are deep-copied so cases cannot leak state.
 */
export function codeNode(workflow, name) {
  const node = getNode(workflow, name);
  const code = node.parameters.jsCode;
  const fn = new Function('$input', code); // trusted: the owner's own workflow code
  return {
    name,
    source: { workflow: workflow.name, node: name, type: node.type, typeVersion: node.typeVersion, code_sha256: sha256(code) },
    run(inputs) {
      const items = inputs.map((json) => ({ json: structuredClone(json) }));
      const $input = { all: () => items, first: () => items[0], last: () => items.at(-1) };
      return fn($input).map((item) => item.json);
    },
  };
}

/**
 * n8n expression: a parameter string starting with "=". A value that is exactly one `{{ expr }}`
 * keeps the expression's type; mixed templates concatenate, rendering undefined/null as ''.
 * `ctx` supplies the n8n globals the expression uses ($json, $now, $(...)).
 */
export function evalExpression(template, ctx) {
  if (typeof template !== 'string' || !template.startsWith('=')) return template;
  const body = template.slice(1);
  const names = Object.keys(ctx);
  const values = Object.values(ctx);
  const evaluate = (expr) => new Function(...names, `return (${expr});`)(...values);

  const single = body.match(/^\{\{([\s\S]*)\}\}$/);
  if (single && !single[1].includes('}}')) return evaluate(single[1]);

  return body.replace(/\{\{([\s\S]*?)\}\}/g, (_, expr) => {
    const value = evaluate(expr);
    return value === undefined || value === null ? '' : String(value);
  });
}

/** Set node (typeVersion 3.4, manual mapping, includeOtherFields unset = false). */
export function setNode(workflow, name) {
  const node = getNode(workflow, name);
  const assignments = node.parameters.assignments.assignments;
  return {
    name,
    source: {
      workflow: workflow.name,
      node: name,
      type: node.type,
      typeVersion: node.typeVersion,
      assignments_sha256: sha256(JSON.stringify(assignments)),
    },
    run(json) {
      const out = {};
      for (const a of assignments) {
        const value = evalExpression(a.value, { $json: structuredClone(json) });
        out[a.name] = a.type === 'string' && value !== undefined && value !== null ? String(value) : value;
      }
      return out;
    },
  };
}

/** Column mapping of a Postgres insert/update node: { column: value } with expressions resolved. */
export function postgresColumns(workflow, name) {
  const node = getNode(workflow, name);
  const mapping = node.parameters.columns.value;
  return {
    name,
    source: { workflow: workflow.name, node: name, type: node.type, mapping_sha256: sha256(JSON.stringify(mapping)) },
    run(ctx) {
      return Object.fromEntries(Object.entries(mapping).map(([col, expr]) => [col, evalExpression(expr, ctx)]));
    },
  };
}

/** A node parameter that is an expression template (e.g. Telegram `text`). */
export function templateParam(workflow, name, param) {
  const node = getNode(workflow, name);
  const template = node.parameters[param];
  return {
    name,
    source: { workflow: workflow.name, node: name, parameter: param, template_sha256: sha256(template) },
    additionalFields: node.parameters.additionalFields ?? {},
    run(ctx) {
      return evalExpression(template, ctx);
    },
  };
}

/** n8n's `$('node')` accessor over a map of { nodeName: json }. */
export function nodeAccessor(outputs) {
  return (nodeName) => {
    if (!(nodeName in outputs)) throw new Error(`$('${nodeName}') not provided`);
    return { item: { json: outputs[nodeName] }, first: () => ({ json: outputs[nodeName] }) };
  };
}
