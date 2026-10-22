# Disk-Usage Analyzers & Duplicate Finders for CloudDog

**Researched:** 2026-10-17 | **Purpose:** candidate pull-ins for CloudDog (disk-hygiene engine: fast scan → verify vs cloud → delete local copy)

## Quick comparison

| Tool | Job | License | Parallel | Machine output | Ubuntu install | Verdict |
|---|---|---|---|---|---|---|
| `fclones` | duplicate finder | MIT | ✅ yes | ✅ `--format json` | cargo / prebuilt binary | **#1 — duplicate engine** |
| `dust` | disk-usage tree | Apache-2.0 | ✅ yes | ✅ `dust -j` (JSON) | `apt install du-dust` | **#2 — scan engine** |
| `dua` | disk-usage analyzer + TUI delete | MIT | ✅ yes | ❌ text only | `apt install dua-cli` / cargo | #3 — scanner alt |
| `jdupes` | duplicate finder | MIT | ❌ single-threaded | ❌ text only | `apt install jdupes` | #4 — lightweight fallback |
| `czkawka` | dup finder + cleaners (multi-mode) | MIT | ✅ yes | ⚠️ CLI exists, clunky | flatpak / prebuilt binary | #5 — multi-mode niche |
| `diskus` | total dir size (one number) | MIT/Apache-2.0 | ✅ yes | ❌ text (one line) | .deb from releases / cargo | #6 — fast gate for thresholds |

---

## 1. `dust` (du + rust) — github.com/bootandy/dust

**License:** Apache-2.0 (free/open-source)

**What it does:** A more intuitive `du` replacement. Walks a directory tree in parallel and prints a visual tree of the largest entries with size bars and percentages — no `du | sort | head` plumbing needed. Intelligently surfaces the largest subdirectories and files without a deep dive.

**Why it's relevant to CloudDog:** It's the scan-side candidate. CloudDog needs to know *where* the bytes are before deciding what to verify-then-delete; `dust -j` emits the whole tree as JSON, so CloudDog can ingest per-directory sizes programmatically instead of parsing human-oriented text. Flags like `-z 100M` (minimum size) and `-n 50` (top-N) let CloudDog focus on space worth reclaiming.

**Key CLI for CloudDog:**
```
dust -j /home/user              # JSON tree of the whole directory (pipe to jq)
dust -j -d 3 /path              # JSON, depth-limited
dust -n 50 -z 100M /path        # top-50 entries over 100M, human-readable
dust -b /path                   # byte counts instead of human units
dust -p /path                   # full paths in output
dust -r /path                   # reverse sort (heaviest first)
dust --skip-total /path         # drop the total row
```
**Performance:** Multi-threaded; ~2x faster than plain `du` on warm cache. Notably *not* the fastest single-number tool — on a 15GB/400k-file benchmark `dust -d0` was ~21s vs `diskus` ~1.7s vs `du` ~17.8s (cold cache). The cost buys the tree breakdown, which is what CloudDog wants.

**Drawbacks / reasons not to use:** The default display deliberately *hides* smaller entries (it shows what fits/stands out), so a naive parse of the pretty output under-counts — always use `-j` (full JSON) or flags like `-n`/`-z` to control this. Not the right tool when you only need a single total size (that's `diskus`'s job).

**Install (Ubuntu/Debian):** `sudo apt install du-dust` (in Debian 13 / Ubuntu archives, near-current) · or `cargo install du-dust` · or prebuilt binary from GitHub releases.

---

## 2. `dua` (Disk Usage Analyzer) — github.com/Byron/dua-cli

**License:** MIT (© Byron Villegas Moya)

**What it does:** Fast disk-usage analyzer with two modes: `aggregate` (one-shot summary of directory sizes) and an interactive TUI (`dua i`) that lets you browse the tree and mark files/directories for deletion (with multi-stage confirmation; its deletes are faster than `rm` on huge trees).

**Why it's relevant to CloudDog:** A scanner alternative to dust — "parallel by default and will max out your SSD," typically 40–60% faster than `du` on large trees. The interactive TUI is irrelevant to CloudDog (headless engine), but the `aggregate` subcommand gives the one-shot summary CloudDog would call, and it can already delete superfluous data fast if CloudDog ever wants a "delete confirmed list" helper.

**Key CLI for CloudDog:**
```
dua aggregate /path             # one-shot size summary
dua aggregate --depth 2 /path   # indented tree, 2 levels
dua aggregate --byte-format bytes|metric|binary  # exact bytes for machine use
dua aggregate --no-sort --no-total /path         # stable, parseable listing
dua aggregate --apparent-size /path
```
**Performance:** Parallel by default, saturates SSD I/O; frequently faster than `dust` and far faster than `du`. Current release v2.46.0 (Oct 2026), actively maintained.

**Drawbacks / reasons not to use:** **No JSON output** — `aggregate` prints human-formatted text, so CloudDog would have to parse it (with `--byte-format bytes --no-total --no-sort` it's the most parse-friendly, but still text). TUI features add binary weight CloudDog won't use (`cargo install dua-cli --no-default-features` trims it). On the benchmark table it ran slower than `dust` on the cold-cache total-size test.

**Install (Ubuntu/Debian):** `sudo apt install dua-cli` (packaged) · or `cargo install dua-cli --no-default-features` (headless) · or prebuilt musl binary from GitHub releases.

---

## 3. `diskus` — github.com/sharkdp/diskus

**License:** MIT/Apache-2.0 dual (sharkdp's standard — confirm in the repo LICENSE; sharkdp projects are conventionally dual-licensed)

**What it does:** A minimal, single-purpose alternative to `du -sh`: prints the total size of a directory — one line, nothing else.

**Why it's relevant to CloudDog:** It's the fastest "how big is this thing" probe CloudDog can call cheaply and often — e.g., the disk-space-guardian loop checking whether a staging dir crossed a threshold, or sizing a candidate folder before a verification run. ~10x faster than `du` on cold cache (1.75s vs 17.8s on a 15GB/400k-file tree), ~2x on warm cache. Also usable as a Rust library crate (`diskus`) if CloudDog ever wants to embed it.

**Key CLI for CloudDog:**
```
diskus /var/log                            # "9.59 GB (9,587,408,896 bytes)" — one line
diskus -b /var/log | cut -d' ' -f1        # apparent size, byte field for scripts
diskus --directories=included /path        # count directory entries (default, like du -s)
diskus --threads 4 /path                   # pin thread count
```
**Performance:** The fastest of the three `du` replacements on this page for a *total* size; parallelism across all cores. Fixed non-interactive output designed for scripts (`SIZE=$(diskus /backup)`).

**Drawbacks / reasons not to use:** It answers only "how big" — no per-directory/per-file breakdown, no tree, **no JSON**, so it cannot replace `dust`/`dua` for locating *what* is big. Complementary, not a substitute.

**Install (Ubuntu/Debian):** NOT in apt — download the `.deb` from GitHub releases (`diskus_0.9.0_amd64.deb` → `sudo dpkg -i`), or `cargo install diskus`. Arch/Nix have it packaged.

---

## 4. `fclones` — github.com/pkolaczk/fclones

**License:** MIT (free/open-source)

**What it does:** An efficient duplicate-file finder and remover. Two-stage workflow: `group` identifies sets of byte-identical files (progressive multi-stage hashing: size → prefix hash → suffix hash → full hash), then `remove`/`link`/`dedupe`/`move` act on the report. Built for automation.

**Why it's relevant to CloudDog:** This is the duplicate-detection engine CloudDog wants. A disk-hygiene engine that deletes local copies must know what's redundant: `fclones group --format json` gives CloudDog a machine-readable duplicate report (size + hash + path lists) it can join against its verified-upload registry to decide what's safe to reap. The `--cache` hash database makes repeat scans on the same tree dramatically cheaper (only rehashes changed files) — ideal for CloudDog's recurring guardian loops.

**Key CLI for CloudDog:**
```
fclones group /path1 /path2                        # find duplicates (human text)
fclones group --format json /path > dups.json      # MACHINE-READABLE report for CloudDog
fclones group --cache /path                        # persistent hash cache; fast rescan
fclones group --min-size 1M --rf-over 2 /path      # files >1M with 2+ replicas
fclones group --name '*.jar' --path '**/node_modules/**' /path   # filters
find . -name '*.jpg' | fclones group --stdin --depth 0            # piped input
fclones remove --dry-run < dups.json               # act on a reviewed report (two-stage safe)
fclones link | fclones dedupe | fclones move       # alt actions: hardlink/reflink/move
```
Tuning: `--hash-fn` (metro default, fastest 128-bit non-crypto; blake3/sha256/sha512/sha3 available), auto-tunes parallelism and I/O strategy by device type (SSD vs HDD).

**Performance:** Best-in-class on its own benchmark (1.46M files, 316GB, NVMe SSD): **34.59s / 266MB** vs czkawka 2:09/1.4GB vs rmlint 2:28/942MB vs jdupes 5:01/332MB vs fdupes 5:46/342MB. Parallel in every I/O- and CPU-heavy stage, low memory footprint, doesn't push data out of the page cache (Linux).

**Drawbacks / reasons not to use:** Not in apt — `cargo install fclones` (long compile) or prebuilt binary from GitHub releases. The hash cache (`--cache`) is keyed to invocation context; CloudDog must call it consistently (same working dir / path specs) to benefit. Remover commands are powerful — CloudDog must keep its own "only delete if verified on store" gate in front of them, never hand `remove` a raw report.

**Install (Ubuntu/Debian):** `cargo install fclones` · or prebuilt binary from GitHub releases. No apt package.

---

## 5. `czkawka` — github.com/qarmin/czkawka

**License:** MIT (free/open-source)

**What it does:** Multi-mode storage cleaner: duplicate files, *similar images* (fuzzy), empty folders, big files, temporary files, broken/invalid symlinks, bad extensions, same music. GUI-first, with a separate CLI binary (`czkawka_cli`).

**Why it's relevant to CloudDog:** It's the Swiss-army option — one binary covers duplicates plus the "lint" CloudDog's guardian loops care about (empty folders, temp files, oversized files, broken symlinks). The fuzzy similar-image mode is something none of the other five tools do, which could matter for media-heavy trees.

**Key CLI for CloudDog:**
```
czkawka_cli dup -d /path                 # duplicate scan (CLI is a separate binary)
czkawka_cli big-file -d /path            # largest files
czkawka_cli empty-folders -d /path       # empty directories
czkawka_cli broken-files -d /path        # broken files/symlinks
```
(Flags/filters per subcommand; verify JSON export flag on the release in use — recent versions support scriptable result saving. The exact CLI surface has shifted across the 3.x releases.)

**Performance:** Multi-threaded and fast, but heavier than fclones: on fclones' benchmark (1.46M files) czkawka took **2:09 with 1.4GB peak memory** vs fclones' 34.59s/266MB. Fine for periodic sweeps, not for hot loops.

**Drawbacks / reasons not to use:** Deployment is awkward — the CLI is a separate download from GitHub releases (or Flatpak `com.github.qarmin.czkawka`, which needs Flatpak on the VM); **no apt package**. Heavier and slower than fclones for pure duplicate detection. GUI-centric project: the CLI is a second-class citizen and its flags/output formats move between releases, which is fragile for an automation engine to depend on. Overkill if CloudDog only needs duplicates + sizes.

**Install (Ubuntu/Debian):** `flatpak install flathub com.github.qarmin.czkawka` (CLI via the flatpak run) · or download `linux_czkawka_cli` from GitHub releases and install to `/usr/local/bin/czkawka-cli`. No apt.

---

## 6. `jdupes` — github.com/jbruchon/jdupes

**License:** MIT (© 2015–2023 Jody Bruchon)

**What it does:** A faster, enhanced fork of the classic `fdupes` (C, single binary, no dependencies). Finds duplicate files by size + partial/full hashing with byte-level confirmation, and can delete, hardlink, or symlink the redundant copies.

**Why it's relevant to CloudDog:** The zero-friction fallback. It's in Ubuntu's apt repos, tiny, dependency-free, and dead simple — `jdupes -r -S /path` in a cron with zero setup. The `-y` hash-database option makes repeat scans of the same tree nearly free (down to "directory scan + a couple seconds"), which maps to CloudDog's recurring-loop pattern. Useful where a Rust toolchain / cargo build isn't available.

**Key CLI for CloudDog:**
```
jdupes -r /path                          # recursive duplicate listing
jdupes -r -S /path                       # with file sizes shown
jdupes -r -d /path                       # interactive delete (prompts per group)
jdupes -r -L /path                       # replace dups with hardlinks
jdupes -r -s /path                       # replace dups with symlinks
jdupes -r -y /path                       # hash DB: repeat scans nearly free (same cwd + path specs!)
jdupes -r -N /path                       # skip zero-length files
jdupes -r -X only /path                  # name-pattern filter
```
**Performance:** Single-threaded — the slowest here on big trees: **5:01** on the 316GB benchmark vs fclones' 34.59s. But with a warm `-y` hash database, repeat runs collapse to scan time + seconds (~1s for 100K+ files). Tunable I/O chunk size (`-C`) helps on spinning disks.

**Drawbacks / reasons not to use:** Single-threaded means first scans of TB-scale trees are slow (minutes, not seconds). **No JSON output** — text parsing only. The `-y` hash DB is brittle: it keys on exact path strings, so `jdupes -y . foo/` ≠ `jdupes -y . ./foo/` — CloudDog must invoke it with byte-identical arguments every run or the cache is wasted. Author activity has slowed (last copyright 2023).

**Install (Ubuntu/Debian):** `sudo apt install jdupes` (in Ubuntu/Debian repos) · or build from GitHub source.

---

## RECOMMENDATION — ranked for CloudDog integration

**CloudDog needs two capabilities: (A) fast size scanning ("where are the bytes?") and (B) duplicate detection ("what's redundant?"). No single tool does both well, so the integration is a pair, not a pick.**

### Tier 1 — integrate these two

1. **`fclones` — the duplicate engine (capability B).** Fastest duplicate finder in the field (34.59s vs minutes for everything else on 316GB), lowest memory (266MB), and the only one designed for automation: `--format json` reports CloudDog can ingest directly, `--cache` makes recurring guardian scans cheap, and the two-stage `group` → `remove` workflow matches CloudDog's verify-then-delete philosophy (CloudDog's own registry check sits between the report and any deletion). MIT, actively developed. Install once from the GitHub release binary (no apt, one-time cost).
2. **`dust` — the scan engine (capability A).** The only scanner here with JSON output (`dust -j`), which is what an engine needs — text-tree parsing is fragile, and `dust -j` hands CloudDog the full per-directory tree with byte counts. `-z`/`-n`/`-d` flags let the guardian focus on space worth reclaiming. In apt as `du-dust` (near-current), parallel, Apache-2.0. Use JSON mode always; ignore the pretty display.

### Tier 2 — fallbacks and complements

3. **`jdupes` — the apt-native fallback for duplicates.** If fclones' binary/cargo install is ever a problem (minimal VM, no Rust toolchain), `jdupes` is one `apt install` away, dependency-free, and its `-y` hash DB makes repeat scans cheap. Keep it as the documented fallback in CloudDog's pull-in log, not the primary.
4. **`diskus` — the fast gate.** Not a scanner replacement, but the cheapest possible "is this dir over the threshold?" check the guardian loop can call every pass (one line, ~10x faster than `du`). Use it for threshold/trigger decisions, then hand off to `dust`/`fclones` for the actual analysis.

### Tier 3 — probably skip

5. **`dua`** — good tool, wrong fit for an engine: no JSON output (CloudDog would parse human text), and its headline feature (interactive TUI with deletion) is useless headless. Revisit only if `dust -j` proves inadequate.
6. **`czkawka`** — the multi-mode cleaners (similar images, temp files, empty folders, broken symlinks) are genuinely useful, but it's slower/heavier than fclones, the CLI is a second-class citizen with shifting flags, and there's no apt package. Consider a one-off evaluation later if CloudDog wants fuzzy similar-image detection — nothing else on this list does that.

### Suggested integration sketch
- **Guardian loop:** `diskus` for cheap threshold checks → `dust -j -z 100M` for "where are the bytes" reports → `fclones group --format json --cache` for the duplicate report → join against CloudDog's verified-upload registry → delete only verified, keep-one-copy duplicates (or `fclones link` to hardlink-dedupe instead of deleting).
- **Never hand `fclones remove` or `jdupes -d` a raw report** — CloudDog's store-verification gate stays in front of every destructive action, per the disk-storing/moving law.

*Note: this ranking aligns with the CloudDog standing pull-in order noted in the lane's orders (dust → fclones → sqlite).*
