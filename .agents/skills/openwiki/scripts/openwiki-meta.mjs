#!/usr/bin/env node
/**
 * OpenWiki toolkit. Dependency-free Node >= 18.
 *
 * Run from the target repository root (or pass --cwd <path>).
 *
 * Commands:
 *   context                     Last-run metadata + git change summary since last check.
 *   impact [--check]            Deterministic staleness: which wiki pages are stale (per-page
 *                               `verified` head x `sources` globs x git diff) and which changed
 *                               files no page covers. --check exits 1 when anything is stale.
 *   map <file...>               Which wiki pages should an agent read before touching these
 *                               files? Prints pages, read-when hints, and freshness warnings.
 *   lint [--check]              Mechanical wiki checks: frontmatter, dead links, orphan pages,
 *                               empty source globs, leftover _plan.md, secret patterns, page
 *                               size budget. --check exits 1 on errors.
 *   record <init|update> [--noop] [--pages a.md,b.md] [--all-pages]
 *                               Stamp openwiki/.last-update.json. --noop records "checked, no
 *                               changes" (advances checkedHead only). --all-pages / --pages
 *                               also stamps `verified:` frontmatter on wiki pages.
 *
 * Metadata (openwiki/.last-update.json):
 *   checkedHead  last git head the wiki was VERIFIED against (advances on no-op runs too)
 *   changedHead  last git head at which wiki content actually CHANGED
 *   (legacy `gitHead` from older runs is read as both)
 */
import { execFile } from "node:child_process";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const OPEN_WIKI_DIR = "openwiki";
const METADATA_REL = path.posix.join(OPEN_WIKI_DIR, ".last-update.json");
const PAGE_MIN_WORDS = 120;
const PAGE_MAX_WORDS = 2000;

// ---------------------------------------------------------------- args

function parseArgs(argv) {
  const args = { cwd: process.cwd(), positional: [], flags: new Set(), pages: null };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--cwd") {
      args.cwd = path.resolve(argv[i + 1] ?? ".");
      i += 1;
    } else if (a === "--pages") {
      args.pages = (argv[i + 1] ?? "").split(",").map((p) => p.trim()).filter(Boolean);
      i += 1;
    } else if (a.startsWith("--")) {
      args.flags.add(a.slice(2));
    } else {
      args.positional.push(a);
    }
  }
  return args;
}

// ---------------------------------------------------------------- git

async function runGit(cwd, gitArgs) {
  try {
    const { stdout, stderr } = await execFileAsync("git", ["--no-pager", ...gitArgs], {
      cwd,
      maxBuffer: 8 * 1024 * 1024,
    });
    return { ok: true, out: [stdout.trim(), stderr.trim()].filter(Boolean).join("\n").trim() };
  } catch (error) {
    const out = [error.stdout?.trim(), error.stderr?.trim()].filter(Boolean).join("\n").trim();
    return { ok: false, out: out || `(git error: ${error.message})` };
  }
}

async function getGitHead(cwd) {
  const { out } = await runGit(cwd, ["rev-parse", "HEAD"]);
  return /^[0-9a-f]{40}$/.test(out) ? out : null;
}

async function isValidCommit(cwd, ref) {
  if (!ref || !/^[0-9a-f]{7,40}$/i.test(ref)) return false;
  const { ok } = await runGit(cwd, ["cat-file", "-e", `${ref}^{commit}`]);
  return ok;
}

/** Changed files (committed since `baseline` + uncommitted), repo-relative posix paths. */
async function changedFilesSince(cwd, baseline) {
  const files = new Set();
  if (baseline && (await isValidCommit(cwd, baseline))) {
    const { ok, out } = await runGit(cwd, ["diff", "--name-only", `${baseline}..HEAD`]);
    if (ok) for (const f of out.split("\n")) if (f.trim()) files.add(toPosix(f.trim()));
  }
  const status = await runGit(cwd, ["status", "--porcelain"]);
  if (status.ok) {
    for (const line of status.out.split("\n")) {
      // porcelain v1: "XY path" — but runGit trims output, which can eat the
      // leading space of the first line, so parse by pattern not offset.
      const m = line.match(/^\s*(?:\?\?|!!|[ MADRCU]{1,2})\s+(.+)$/);
      if (!m) continue;
      // handle renames "old -> new"
      const parts = m[1].split(" -> ");
      files.add(toPosix(unquoteGitPath(parts[parts.length - 1].trim())));
    }
  }
  return [...files];
}

function unquoteGitPath(p) {
  return p.startsWith('"') && p.endsWith('"') ? p.slice(1, -1) : p;
}

/** All tracked + untracked-but-not-ignored files in the repo. */
async function listRepoFiles(cwd) {
  const { ok, out } = await runGit(cwd, ["ls-files", "--cached", "--others", "--exclude-standard"]);
  if (!ok || !out) return [];
  return out.split("\n").map((f) => toPosix(unquoteGitPath(f.trim()))).filter(Boolean);
}

function toPosix(p) {
  return p.replaceAll("\\", "/");
}

// ---------------------------------------------------------------- metadata

async function readMetadata(cwd) {
  try {
    const raw = await readFile(path.join(cwd, METADATA_REL), "utf8");
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    // legacy: single gitHead field counts as both heads
    if (parsed.gitHead && !parsed.checkedHead) parsed.checkedHead = parsed.gitHead;
    if (parsed.gitHead && !parsed.changedHead) parsed.changedHead = parsed.gitHead;
    return parsed;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- frontmatter

/**
 * Parses the leading YAML frontmatter block of a wiki page. Supported subset:
 *   key: string value
 *   sources: ["glob", "glob"]      (JSON inline array)
 *   sources:                        (YAML flow array spread over lines —
 *     [                             Prettier reflows frontmatter this way)
 *       "glob",
 *     ]
 *   sources:                        (YAML dash-list)
 *     - glob
 */
function parseFrontmatter(content) {
  const lines = content.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") return { data: null, bodyStart: 0 };
  const data = {};
  let i = 1;
  let currentListKey = null;
  let bracketKey = null;
  let bracketBuffer = "";
  for (; i < lines.length; i += 1) {
    const line = lines[i];
    if (bracketKey !== null) {
      bracketBuffer += line.trim();
      if (line.includes("]")) {
        data[bracketKey] = parseInlineArray(bracketBuffer);
        bracketKey = null;
        bracketBuffer = "";
      }
      continue;
    }
    if (line.trim() === "---") return { data, bodyStart: i + 1 };
    const listItem = line.match(/^\s*-\s+(.*)$/);
    if (listItem && currentListKey) {
      data[currentListKey].push(stripQuotes(listItem[1].trim()));
      continue;
    }
    // a bare "[" opens a multi-line flow array for the pending empty key
    if (currentListKey && line.trim().startsWith("[")) {
      bracketKey = currentListKey;
      currentListKey = null;
      bracketBuffer = line.trim();
      if (line.includes("]")) {
        data[bracketKey] = parseInlineArray(bracketBuffer);
        bracketKey = null;
        bracketBuffer = "";
      }
      continue;
    }
    const kv = line.match(/^([A-Za-z][\w-]*)\s*:\s*(.*)$/);
    if (!kv) continue;
    const [, key, rawValue] = kv;
    const value = rawValue.trim();
    if (value === "") {
      data[key] = [];
      currentListKey = key;
    } else if (value.startsWith("[") && !value.endsWith("]")) {
      // flow array opened on the key line, closed on a later line
      bracketKey = key;
      bracketBuffer = value;
      currentListKey = null;
    } else if (value.startsWith("[")) {
      currentListKey = null;
      data[key] = parseInlineArray(value);
    } else {
      currentListKey = null;
      data[key] = stripQuotes(value);
    }
  }
  return { data: null, bodyStart: 0 }; // unterminated block -> treat as no frontmatter
}

function parseInlineArray(text) {
  const trimmed = text.replace(/,\s*]/g, "]"); // JSON forbids trailing commas
  try {
    return JSON.parse(trimmed.replaceAll("'", '"'));
  } catch {
    return trimmed
      .replace(/^\[/, "")
      .replace(/]$/, "")
      .split(",")
      .map((s) => stripQuotes(s.trim()))
      .filter(Boolean);
  }
}

function stripQuotes(s) {
  return (s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))
    ? s.slice(1, -1)
    : s;
}

/** Sets (or inserts) `verified: <head>` in a page's frontmatter, creating the block if absent. */
function stampVerified(content, head) {
  const short = head.slice(0, 12);
  const lines = content.split(/\r?\n/);
  if (lines[0]?.trim() === "---") {
    const end = lines.findIndex((l, idx) => idx > 0 && l.trim() === "---");
    if (end > 0) {
      const verifiedIdx = lines.findIndex(
        (l, idx) => idx > 0 && idx < end && /^verified\s*:/.test(l.trim()),
      );
      if (verifiedIdx > 0) lines[verifiedIdx] = `verified: ${short}`;
      else lines.splice(end, 0, `verified: ${short}`);
      return lines.join("\n");
    }
  }
  return `---\nverified: ${short}\n---\n\n${content}`;
}

// ---------------------------------------------------------------- glob

/** Minimal glob: ** (any depth), * (within segment), ? (one char). Bare dirs match as dir/**. */
function globToRegExp(glob) {
  const cleaned = toPosix(glob).replace(/#.*$/, "").replace(/\/+$/, "");
  let re = "";
  for (let i = 0; i < cleaned.length; i += 1) {
    const c = cleaned[i];
    if (c === "*") {
      if (cleaned[i + 1] === "*") {
        re += ".*";
        i += 1;
        if (cleaned[i + 1] === "/") i += 1; // "**/" also matches zero dirs
      } else {
        re += "[^/]*";
      }
    } else if (c === "?") {
      re += "[^/]";
    } else {
      re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  const hasWildcard = /[*?]/.test(cleaned);
  return new RegExp(hasWildcard ? `^${re}$` : `^${re}(/.*)?$`);
}

function matchesAny(file, globs) {
  const f = toPosix(file);
  return globs.some((g) => globToRegExp(g).test(f));
}

// ---------------------------------------------------------------- pages

/** Enumerate wiki pages with parsed frontmatter. rel paths are posix, repo-relative. */
async function loadPages(cwd) {
  const wikiDir = path.join(cwd, OPEN_WIKI_DIR);
  const pages = [];
  async function walk(dir) {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.isFile() && entry.name.endsWith(".md") && entry.name !== "_plan.md") {
        const content = await readFile(full, "utf8");
        const { data, bodyStart } = parseFrontmatter(content);
        const body = content.split(/\r?\n/).slice(bodyStart).join("\n");
        pages.push({
          rel: toPosix(path.relative(cwd, full)),
          full,
          content,
          body,
          fm: data,
          title: data?.title ?? null,
          sources: Array.isArray(data?.sources) ? data.sources : null,
          readWhen: data?.["read-when"] ?? null,
          verified: typeof data?.verified === "string" ? data.verified : null,
        });
      }
    }
  }
  await walk(wikiDir);
  return pages;
}

function isQuickstart(page) {
  return page.rel === `${OPEN_WIKI_DIR}/quickstart.md`;
}

function pageLabel(page) {
  return page.title ? `${page.rel} ("${page.title}")` : page.rel;
}

// ---------------------------------------------------------------- command: context

async function commandContext(cwd) {
  const meta = await readMetadata(cwd);
  const out = [];
  out.push(
    "== Last OpenWiki run metadata ==\n" +
      (meta
        ? JSON.stringify(meta, null, 2)
        : "No previous OpenWiki metadata found (treat this as a first run: init)."),
  );
  out.push("== Git change summary ==");
  const section = (title, body) => `$ ${title}\n${body || "(no output)"}`;
  out.push(section("git status --short", (await runGit(cwd, ["status", "--short"])).out));
  out.push(section("git rev-parse HEAD", (await getGitHead(cwd)) ?? "(unknown)"));
  const baseline = meta?.checkedHead ?? meta?.changedHead;
  if (baseline && (await isValidCommit(cwd, baseline))) {
    out.push(
      section(
        `git log ${baseline}..HEAD --name-status --oneline`,
        (await runGit(cwd, ["log", `${baseline}..HEAD`, "--name-status", "--oneline"])).out,
      ),
    );
  } else if (meta?.updatedAt) {
    out.push(
      section(
        `git log --since ${meta.updatedAt} --name-status --oneline`,
        (await runGit(cwd, ["log", "--since", meta.updatedAt, "--name-status", "--oneline"])).out,
      ),
    );
  } else {
    out.push(
      section(
        "git log --max-count=20 --name-status --oneline",
        (await runGit(cwd, ["log", "--max-count=20", "--name-status", "--oneline"])).out,
      ),
    );
  }
  out.push(
    section("git diff --name-status HEAD", (await runGit(cwd, ["diff", "--name-status", "HEAD"])).out),
  );
  process.stdout.write(out.join("\n\n") + "\n");
}

// ---------------------------------------------------------------- command: impact

async function computeImpact(cwd) {
  const meta = await readMetadata(cwd);
  const pages = await loadPages(cwd);
  const fallbackBaseline = meta?.checkedHead ?? meta?.changedHead ?? null;
  const diffCache = new Map();
  async function changedSince(baseline) {
    const key = baseline ?? "(none)";
    if (!diffCache.has(key)) diffCache.set(key, await changedFilesSince(cwd, baseline));
    return diffCache.get(key);
  }
  const stale = [];
  const unknown = [];
  for (const page of pages) {
    if (!page.sources || page.sources.length === 0) {
      if (!isQuickstart(page)) unknown.push(page);
      continue;
    }
    const baseline = page.verified ?? fallbackBaseline;
    if (!baseline || !(await isValidCommit(cwd, baseline))) {
      unknown.push(page);
      continue;
    }
    const changed = (await changedSince(baseline)).filter(
      (f) => !f.startsWith(`${OPEN_WIKI_DIR}/`),
    );
    const hits = changed.filter((f) => matchesAny(f, page.sources));
    if (hits.length > 0) stale.push({ page, baseline, hits });
  }
  // coverage gaps: changed files (since global baseline) matching no page's sources
  const allSourceGlobs = pages.flatMap((p) => p.sources ?? []);
  const globalChanged = (await changedSince(fallbackBaseline)).filter(
    (f) => !f.startsWith(`${OPEN_WIKI_DIR}/`) && !isNoiseFile(f),
  );
  const gaps = allSourceGlobs.length > 0 ? globalChanged.filter((f) => !matchesAny(f, allSourceGlobs)) : [];
  return { meta, pages, stale, unknown, gaps, fallbackBaseline };
}

function isNoiseFile(f) {
  return /(^|\/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|\.gitignore|AGENTS\.md|CLAUDE\.md)$/.test(f);
}

async function commandImpact(cwd, flags) {
  const { pages, stale, unknown, gaps, fallbackBaseline } = await computeImpact(cwd);
  const out = [];
  if (pages.length === 0) {
    out.push("No wiki pages found under openwiki/. Run an init first.");
  }
  out.push(`Baseline (checkedHead): ${fallbackBaseline ?? "(none — no metadata)"}\n`);
  if (stale.length > 0) {
    out.push("== STALE PAGES (edit or re-verify ONLY these) ==");
    for (const { page, baseline, hits } of stale) {
      out.push(`- ${pageLabel(page)}  [verified@${baseline.slice(0, 12)}]`);
      for (const h of hits) out.push(`    changed: ${h}`);
    }
  } else if (pages.length > 0) {
    out.push("== STALE PAGES ==\n(none — all page sources unchanged since their verified heads)");
  }
  if (gaps.length > 0) {
    out.push("\n== COVERAGE GAPS (changed files no wiki page claims) ==");
    for (const f of gaps) out.push(`- ${f}`);
    out.push("(Consider whether these belong in an existing page's scope or need new documentation.)");
  }
  if (unknown.length > 0) {
    out.push("\n== UNKNOWN FRESHNESS (missing/invalid `sources` or `verified` frontmatter) ==");
    for (const p of unknown) out.push(`- ${pageLabel(p)}`);
    out.push("(These pages cannot be impact-tracked. Add frontmatter per references/structure.md.)");
  }
  process.stdout.write(out.join("\n") + "\n");
  if (flags.has("check") && (stale.length > 0 || gaps.length > 0)) process.exit(1);
}

// ---------------------------------------------------------------- command: map

async function commandMap(cwd, files) {
  if (files.length === 0) {
    console.error("Usage: openwiki-meta.mjs map <file...>   (repo-relative paths)");
    process.exit(1);
  }
  const pages = await loadPages(cwd);
  const { stale } = await computeImpact(cwd);
  const staleSet = new Set(stale.map((s) => s.page.rel));
  const out = [];
  for (const raw of files) {
    const file = toPosix(path.isAbsolute(raw) ? path.relative(cwd, raw) : raw);
    const matches = pages.filter((p) => p.sources && matchesAny(file, p.sources));
    out.push(`${file}:`);
    if (matches.length === 0) {
      out.push("  (no wiki page covers this file — rely on source, or extend the wiki)");
      continue;
    }
    for (const p of matches) {
      const freshness = staleSet.has(p.rel)
        ? "STALE — sources changed since last verification; trust source over doc"
        : "fresh";
      out.push(`  read: ${p.rel}  [${freshness}]`);
      if (p.readWhen) out.push(`        when: ${p.readWhen}`);
    }
  }
  const quickstart = pages.find(isQuickstart);
  if (quickstart) out.push(`\nEntrypoint for broader context: ${quickstart.rel}`);
  process.stdout.write(out.join("\n") + "\n");
}

// ---------------------------------------------------------------- command: lint

const SECRET_PATTERNS = [
  [/gh[pousr]_[A-Za-z0-9]{20,}/, "GitHub token"],
  [/sk-[A-Za-z0-9_-]{20,}/, "API secret key (sk-...)"],
  [/AKIA[0-9A-Z]{16}/, "AWS access key"],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key block"],
  [/xox[baprs]-[A-Za-z0-9-]{10,}/, "Slack token"],
];

async function commandLint(cwd, flags) {
  const pages = await loadPages(cwd);
  const repoFiles = new Set(await listRepoFiles(cwd));
  const errors = [];
  const warnings = [];

  const quickstart = pages.find(isQuickstart);
  if (!quickstart) errors.push("openwiki/quickstart.md is missing (required entrypoint).");
  if (existsSync(path.join(cwd, OPEN_WIKI_DIR, "_plan.md")))
    errors.push("openwiki/_plan.md was left behind — delete it (temporary planning file).");

  // link graph for orphan detection
  const linkTargets = new Map(); // page.rel -> Set of resolved wiki-page rels it links to
  for (const page of pages) {
    const links = [...page.body.matchAll(/\[[^\]]*\]\(([^)\s]+)\)/g)].map((m) => m[1]);
    const resolved = new Set();
    for (const link of links) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(link)) continue; // absolute URLs
      const target = link.split("#")[0];
      if (!target) continue; // pure anchor
      const targetRel = toPosix(
        path.relative(cwd, path.resolve(path.dirname(page.full), target)),
      );
      if (targetRel.startsWith("..")) {
        warnings.push(`${page.rel}: link escapes the repository: (${link})`);
        continue;
      }
      const inRepo = repoFiles.has(targetRel) || existsSync(path.join(cwd, targetRel));
      if (!inRepo) errors.push(`${page.rel}: dead link -> ${link}`);
      else if (targetRel.startsWith(`${OPEN_WIKI_DIR}/`)) resolved.add(targetRel);
    }
    linkTargets.set(page.rel, resolved);
  }

  // orphans: BFS from quickstart
  if (quickstart) {
    const reachable = new Set([quickstart.rel]);
    const queue = [quickstart.rel];
    while (queue.length > 0) {
      for (const next of linkTargets.get(queue.shift()) ?? []) {
        if (!reachable.has(next)) {
          reachable.add(next);
          queue.push(next);
        }
      }
    }
    for (const page of pages)
      if (!reachable.has(page.rel))
        errors.push(`${page.rel}: orphan page — not reachable from quickstart.md via links.`);
  }

  for (const page of pages) {
    // frontmatter contract
    if (!page.fm) {
      errors.push(`${page.rel}: missing frontmatter block (title/sources/read-when/verified).`);
    } else {
      if (!page.title) warnings.push(`${page.rel}: frontmatter missing "title".`);
      if (!isQuickstart(page)) {
        if (!page.sources || page.sources.length === 0)
          errors.push(`${page.rel}: frontmatter missing "sources" globs — page cannot be impact-tracked.`);
        if (!page.readWhen) warnings.push(`${page.rel}: frontmatter missing "read-when" hint.`);
      }
      if (page.verified && !/^[0-9a-f]{7,40}$/i.test(page.verified))
        errors.push(`${page.rel}: "verified" is not a git hash: ${page.verified}`);
      if (!page.verified) warnings.push(`${page.rel}: no "verified" head yet (run record with --pages/--all-pages).`);
      // empty source globs
      for (const glob of page.sources ?? []) {
        const re = globToRegExp(glob);
        if (![...repoFiles].some((f) => re.test(f)))
          errors.push(`${page.rel}: sources glob matches no repo files: "${glob}"`);
      }
    }
    // secrets
    for (const [pattern, label] of SECRET_PATTERNS)
      if (pattern.test(page.content)) errors.push(`${page.rel}: possible ${label} in page content.`);
    // size budget
    const words = page.body.split(/\s+/).filter(Boolean).length;
    if (words < PAGE_MIN_WORDS && !isQuickstart(page))
      warnings.push(`${page.rel}: thin page (${words} words < ${PAGE_MIN_WORDS}) — merge into a broader page?`);
    if (words > PAGE_MAX_WORDS)
      warnings.push(`${page.rel}: oversized page (${words} words > ${PAGE_MAX_WORDS}) — split or tighten.`);
  }

  const out = [];
  out.push(`Checked ${pages.length} wiki page(s).`);
  if (errors.length > 0) {
    out.push(`\n== ERRORS (${errors.length}) — must fix ==`);
    for (const e of errors) out.push(`- ${e}`);
  }
  if (warnings.length > 0) {
    out.push(`\n== WARNINGS (${warnings.length}) — judgment call ==`);
    for (const w of warnings) out.push(`- ${w}`);
  }
  if (errors.length === 0 && warnings.length === 0) out.push("Lint clean.");
  process.stdout.write(out.join("\n") + "\n");
  if (flags.has("check") && errors.length > 0) process.exit(1);
}

// ---------------------------------------------------------------- command: record

async function commandRecord(cwd, runCommand, flags, pageList) {
  if (runCommand !== "init" && runCommand !== "update") {
    console.error(`record requires "init" or "update", got: ${runCommand ?? "(none)"}`);
    process.exit(1);
  }
  const head = await getGitHead(cwd);
  if (!head) {
    console.error("Cannot record: no git HEAD (is this a git repository with at least one commit?)");
    process.exit(1);
  }
  const previous = await readMetadata(cwd);
  const noop = flags.has("noop");
  const metadata = {
    updatedAt: new Date().toISOString(),
    command: runCommand,
    checkedHead: head,
    changedHead: noop ? (previous?.changedHead ?? head) : head,
    agent: "claude-code/openwiki-skill",
  };
  const file = path.join(cwd, METADATA_REL);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(metadata, null, 2)}\n`, "utf8");
  const stamped = [];
  if (!noop && (flags.has("all-pages") || runCommand === "init" || pageList)) {
    const pages = await loadPages(cwd);
    const wanted =
      flags.has("all-pages") || runCommand === "init"
        ? pages
        : pages.filter((p) =>
            pageList.some((sel) => p.rel === toPosix(sel) || p.rel === `${OPEN_WIKI_DIR}/${toPosix(sel)}`),
          );
    for (const page of wanted) {
      await writeFile(page.full, stampVerified(page.content, head), "utf8");
      stamped.push(page.rel);
    }
    if (pageList) {
      for (const sel of pageList) {
        const norm = toPosix(sel);
        if (!stamped.some((s) => s === norm || s === `${OPEN_WIKI_DIR}/${norm}`))
          console.error(`warning: --pages entry matched no wiki page: ${sel}`);
      }
    }
  }
  process.stdout.write(
    `Recorded ${METADATA_REL}${noop ? " (no-op: checkedHead advanced, changedHead kept)" : ""}:\n` +
      `${JSON.stringify(metadata, null, 2)}\n` +
      (stamped.length > 0 ? `Stamped verified:${head.slice(0, 12)} on:\n${stamped.map((s) => `- ${s}`).join("\n")}\n` : ""),
  );
}

// ---------------------------------------------------------------- main

const { cwd, positional, flags, pages: pageList } = parseArgs(process.argv.slice(2));
const [subcommand, ...rest] = positional;

switch (subcommand) {
  case "context":
    await commandContext(cwd);
    break;
  case "impact":
    await commandImpact(cwd, flags);
    break;
  case "map":
    await commandMap(cwd, rest);
    break;
  case "lint":
    await commandLint(cwd, flags);
    break;
  case "record":
    await commandRecord(cwd, rest[0], flags, pageList);
    break;
  default:
    console.error(
      [
        "Usage: node openwiki-meta.mjs [--cwd <repo>] <command>",
        "  context",
        "  impact [--check]",
        "  map <file...>",
        "  lint [--check]",
        "  record <init|update> [--noop] [--all-pages] [--pages a.md,b.md]",
      ].join("\n"),
    );
    process.exit(1);
}
