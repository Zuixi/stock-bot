#!/usr/bin/env node
/**
 * check-data-display — 防止新增「页面手写数字格式化」的棘轮门禁（P7/T7.5）。
 *
 * 约定（docs/architecture/frontend/ARCHITECTURE.md §4）：数值展示走
 * `shared/ui/NumberText · ChangeText · DeltaText`，页面不手写 `toFixed`。
 * 存量 54 个文件已登记在 `scripts/data-display-baseline.json`，只允许减少、不允许新增。
 *
 * 用法：
 *   node scripts/check-data-display.mjs           # CI / self_review
 *   node scripts/check-data-display.mjs --update  # 清理存量后刷新基线
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const BASELINE = resolve(HERE, "data-display-baseline.json");
const PATTERN = "toFixed(";
const SCAN_DIRS = ["src/pages", "src/features"];

function offenders() {
  const out = execFileSync("grep", ["-rl", PATTERN, ...SCAN_DIRS], {
    cwd: ROOT,
    encoding: "utf8",
  });
  return [...new Set(out.split("\n").filter(Boolean))].sort();
}

const update = process.argv.includes("--update");
const found = offenders();

if (update) {
  writeFileSync(BASELINE, JSON.stringify({ allow: found }, null, 2) + "\n");
  console.log(`✔ baseline updated: ${found.length} files`);
  process.exit(0);
}

const baseline = JSON.parse(readFileSync(BASELINE, "utf8"));
const allowed = new Set(baseline.allow ?? []);
const added = found.filter((file) => !allowed.has(file));

if (added.length > 0) {
  console.error("✘ 新增了页面手写数字格式化（应走 shared/ui/NumberText · ChangeText）：");
  for (const file of added) console.error(`  - ${file}`);
  console.error("\n修法：改用 shared/ui 原语；确属图表 formatter 等例外，再 --update 基线并评审。");
  process.exit(1);
}

console.log(`✔ data-display ratchet: ${found.length} known / 0 new`);
