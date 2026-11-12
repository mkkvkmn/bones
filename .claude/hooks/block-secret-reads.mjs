#!/usr/bin/env node
// PreToolUse hook (adapted from core): blocks shell commands that would read secrets.
// The Read deny rules in settings.json cover the file tools and cat/head/tail; this covers
// the rest of the shell. Exit 2 blocks the call and shows stderr to the agent.
import { readFileSync } from 'node:fs';

const CHECKS = [
  {
    why: '.env files (only *.example may be read)',
    test: (cmd) =>
      [...cmd.matchAll(/(?:^|[\s"'`=:/\\(<>|;&])(\.env(?:\.[\w.-]+)?)(?=$|[\s"'`)/\\;&|<>,])/g)].some(
        (m) => !m[1].endsWith('.example'),
      ),
  },
  { why: 'env_vars.*.sh deploy secrets', test: (cmd) => /env_vars\.(?!example\b)[\w.-]*\.sh\b/.test(cmd) },
  { why: '*.key files', test: (cmd) => /[\w-]\.key(?=$|[\s"'`;&|)])/.test(cmd) },
  { why: '~/.secrets', test: (cmd) => /(?:^|[\s"'`=:/\\~])\.secrets(?=$|[\s"'`/\\;&|)])/.test(cmd) },
  {
    why: 'dumping the process environment',
    test: (cmd) =>
      /\bprintenv\b/.test(cmd) ||
      /(?:^|[;&|(]\s*)(?:export\s+-p|set|env)\s*(?:$|[;&|)])/.test(cmd) ||
      /(?<![$\w])env:(?=$|[\s"'`;|)])/i.test(cmd) ||
      /GetEnvironmentVariables/i.test(cmd) ||
      /\/proc\/[^\s/]+\/environ/.test(cmd),
  },
];

let input;
try {
  input = JSON.parse(readFileSync(0, 'utf8'));
} catch {
  process.exit(0);
}
const command = String(input?.tool_input?.command ?? '');
const hit = CHECKS.find((check) => check.test(command));
if (hit) {
  process.stderr.write(`Blocked: this command touches ${hit.why}. Agents never read secrets; ask the user.\n`);
  process.exit(2);
}
process.exit(0);
