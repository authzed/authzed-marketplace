#!/usr/bin/env node
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const errors = [];

function readJSON(relPath) {
  const fullPath = join(ROOT, relPath);
  if (!existsSync(fullPath)) {
    throw new Error(`Missing file: ${relPath}`);
  }
  return JSON.parse(readFileSync(fullPath, 'utf8'));
}

function readText(relPath) {
  const fullPath = join(ROOT, relPath);
  if (!existsSync(fullPath)) {
    throw new Error(`Missing file: ${relPath}`);
  }
  return readFileSync(fullPath, 'utf8');
}

function assertPathExists(relPath, label) {
  const fullPath = join(ROOT, relPath);
  if (!existsSync(fullPath)) {
    errors.push(`${label}: path does not exist: ${relPath}`);
  }
}

function findPluginEntry(marketplace, name, label) {
  const entry = (marketplace.plugins || []).find((p) => p.name === name);
  if (!entry) {
    errors.push(`${label}: no plugin entry named "${name}"`);
  }
  return entry || {};
}

function checkParity(map, fieldName) {
  const entries = Object.entries(map);
  const [firstLabel, firstValue] = entries[0];
  for (const [label, value] of entries.slice(1)) {
    if (value !== firstValue) {
      errors.push(
        `${fieldName} mismatch: "${firstLabel}" has ${JSON.stringify(firstValue)}, ` +
        `"${label}" has ${JSON.stringify(value)}`
      );
    }
  }
}

// --- Load all manifests ---
const claudePlugin = readJSON('spicedb-dev/.claude-plugin/plugin.json');
const codexPlugin = readJSON('spicedb-dev/.codex-plugin/plugin.json');
const rootClaudeMarketplace = readJSON('.claude-plugin/marketplace.json');
const rootCodexMarketplace = readJSON('.agents/plugins/marketplace.json');
const devClaudeMarketplace = readJSON('spicedb-dev/.claude-plugin/marketplace.json');

const rootClaudeEntry = findPluginEntry(rootClaudeMarketplace, 'spicedb-dev', 'root .claude-plugin/marketplace.json');
const rootCodexEntry = findPluginEntry(rootCodexMarketplace, 'spicedb-dev', 'root .agents/plugins/marketplace.json');
const devClaudeEntry = findPluginEntry(devClaudeMarketplace, 'spicedb-dev', 'spicedb-dev/.claude-plugin/marketplace.json');

// --- 1. Name parity ---
checkParity({
  'spicedb-dev/.claude-plugin/plugin.json': claudePlugin.name,
  'spicedb-dev/.codex-plugin/plugin.json': codexPlugin.name,
  'root .claude-plugin/marketplace.json entry': rootClaudeEntry.name,
  'root .agents/plugins/marketplace.json entry': rootCodexEntry.name,
  'dev .claude-plugin/marketplace.json entry': devClaudeEntry.name,
}, 'name');

// --- 2. Version parity ---
checkParity({
  'spicedb-dev/.claude-plugin/plugin.json': claudePlugin.version,
  'spicedb-dev/.codex-plugin/plugin.json': codexPlugin.version,
  'root .claude-plugin/marketplace.json entry': rootClaudeEntry.version,
  'root .agents/plugins/marketplace.json entry': rootCodexEntry.version,
  'dev .claude-plugin/marketplace.json entry': devClaudeEntry.version,
}, 'version');

// --- 3. Description parity ---
checkParity({
  'spicedb-dev/.claude-plugin/plugin.json': claudePlugin.description,
  'spicedb-dev/.codex-plugin/plugin.json': codexPlugin.description,
  'root .claude-plugin/marketplace.json entry': rootClaudeEntry.description,
  'root .agents/plugins/marketplace.json entry': rootCodexEntry.description,
  'dev .claude-plugin/marketplace.json entry': devClaudeEntry.description,
}, 'description');

// --- 4. Referenced paths exist ---
assertPathExists(join('spicedb-dev', codexPlugin.skills ?? ''), 'spicedb-dev/.codex-plugin/plugin.json "skills"');
assertPathExists(rootCodexEntry.source?.path ?? '', 'root .agents/plugins/marketplace.json "source.path"');

// --- 5. Cross-reference integrity ---
const codexTools = readText('spicedb-dev/skills/authorization-planner/references/codex-tools.md');
if (!codexTools.includes('codex-agent-dispatch.md')) {
  errors.push('codex-tools.md no longer references codex-agent-dispatch.md');
}

const installGuide = readText('spicedb-dev/.codex/INSTALL.md');
if (!installGuide.includes('authzed/authzed-marketplace')) {
  errors.push('.codex/INSTALL.md no longer references the authzed/authzed-marketplace marketplace');
}
if (!installGuide.includes('~/.codex/skills/spicedb-dev-authorization-planner')) {
  errors.push('.codex/INSTALL.md no longer documents the manual fallback symlink path');
}

// --- Report ---
if (errors.length > 0) {
  console.error(`verify-manifests: ${errors.length} problem(s) found:\n`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}

console.log('verify-manifests: all manifests in sync.');
