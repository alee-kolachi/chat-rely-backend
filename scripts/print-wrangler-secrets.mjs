#!/usr/bin/env node
/**
 * Prints wrangler secret put commands for keys in .env.example (excluding comments/blanks).
 * Run from repo root: node scripts/print-wrangler-secrets.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const example = readFileSync(join(root, ".env.example"), "utf8");
const keys = new Set();

for (const line of example.split("\n")) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) continue;
  const eq = trimmed.indexOf("=");
  if (eq === -1) continue;
  keys.add(trimmed.slice(0, eq));
}

console.log("# Paste values when prompted:\n");
for (const key of [...keys].sort()) {
  console.log(`npx wrangler secret put ${key}`);
}
