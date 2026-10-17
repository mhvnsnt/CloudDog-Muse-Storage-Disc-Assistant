# DISK ANALYSIS — 2026-10-17 (dogfood run)

**Method:** `cloud_dog.py scan` (dust fast path, stdlib fallback) + fclones
duplicate grouping + lane-activity audit. **Read-only:** nothing deleted,
nothing moved. All sizes own-eyes verified via `du`/`df` this session.

## Snapshot

- Disk: `/dev/mapper/rv` 100G — **86.2% used, 11G free** at 21:00 CDT
  (was 90% when this task started; guardian + lanes are actively clearing —
  `wt-all-agents-law` 2.6G disappeared mid-run).
- CloudDog ceiling: 75%. Still **over ceiling**.

### CloudDog scan output (verbatim)

| Dir | Size |
|---|---|
| tools | 9.5G |
| western-heart | 8.0G |
| cj-rig-wt | 7.0G |
| cape-jones | 5.5G |
| game-reverse-engineering | 5.3G |
| worktrees | 4.5G |
| doa4-bass | 4.5G |
| terabox-staging | 4.2G |
| weight-bakeoff-scratch | 3.3G |

## The 5 issues

### 1. `cj-rig-wt/.rig_tmp` — 6.1G of UNTRACKED Blender temp (biggest single win)

`git status` shows `?? .rig_tmp/` — untracked. Contents: `cj_clean.blend`,
`cj_bound.blend`, `cj_cape_sep.blend`, `cj_direct.blend`, `cj_weighted.blend`
(+ `.blend1` backups each) — the rig lane's working files. The rig work is
MERGED (cape-jones PR #104); this worktree sits on `main`, clean, last
touched 07:40 today. **~6.1G recoverable** once the rig coordinator confirms
no lane needs the intermediates. This is exactly what CloudDog's register →
verify → reap loop exists for.

### 2. Worktree sprawl — 8+ full checkouts of cape-jones (~21G)

`git worktree list` in `~/workspace/cape-jones` shows the cj-*/wt-* dirs are
**worktrees, not independent clones** — each carries the full 2.6G tree.
fclones proof (own run, this session): the two SMALLEST quiet lanes
(`cj-pinocchio-fix` 157M + `cj-bible-pupil` 694M) share **220 redundant files
/ 63.5MB byte-identical**. The base tree is the same everywhere; the unique
per-lane content is a thin layer on top.

**Lane activity audit (processes + last write + branch state):**

| Lane | State | Branch | Origin | Verdict |
|---|---|---|---|---|
| wt-cape-jones-scout | ACTIVE (blender, 20:51) | lane/scout-resume-20261017 | pushed | SACRED |
| cj-cape-lane1 | ACTIVE (render, 20:51) | lane/cape-rebuild-20261017 | pushed | SACRED |
| cj-sitting-poses | ACTIVE (blender, 20:43) | lane/sitting-pose-library-v2-20261017 | pushed | SACRED |
| cj-hands-work | ACTIVE (20:50) | lane/hands-finger-verify-20261017 | **1 local commit NOT pushed** | SACRED — push it |
| cj-idle-render | ACTIVE (render, 20:48) | lane/idle-render-20261017 | **no upstream** | SACRED — set upstream + push |
| cj-donor-anims | ACTIVE (20:26) | lane/donor-anims-20261017 | **1 local commit NOT pushed** | SACRED — push it |
| cj-bible-pupil | quiet (15:24) | docs/pupil-canon-20261017 | pushed | review, then prune worktree |
| cj-pinocchio-fix | quiet (08:29) | fix/pinocchio-artifacts-20261017 | pushed | prune candidate |
| cj-rig-anim-work | quiet (18:47) | rig/cape-jones-cleanup-20261017 | pushed, PR merged | prune candidate (+6.1G tmp, see #1) |
| worktrees/cape-jones-anim-20261017 | quiet (15:37) | animation/mocap-starter-20261017 | NOT on origin | push first, then prune |
| worktrees/cape-jones-opencode-20261017 | quiet (15:03) | tools/opencode-20261017 | NOT on origin | push first, then prune |
| cj-rev2-scratch | stale (02:50, no git) | n/a | n/a | 71M scratch — clear |
| cj-part-aa | empty dir | n/a | n/a | remove |

**~9–12G recoverable** from pruning quiet/stale worktrees after their
branches are pushed + merged. Worktrees are cheap to recreate; the disk is not.

### 3. `doa4-bass` — 4.6G, recovery DONE, sources still on disk

Breakdown: `extracted/` 3.0G + `rebuild_tmp/` 1.1G + `extracted_afs/` 516M.
The lane re-created the mirror tar from `extracted/` and uploaded all 6 parts
to the western-heart release `doa4-mirror-20261017`, each verified
(name+size+state=uploaded), local parts cleared via delete-as-you-go. Last
lane write 12:36 today. **~4.6G recoverable** — but this is teardown source
material: needs the owner's call per NEVER-DELETE-GAME-FILES / teardown
decoding may still be in progress (bass/DOA lanes). Do NOT auto-clear.

### 4. `terabox-staging` — 4.2G, uploads PENDING, must stay

`def-jam/` 3.5G (game ISO staging, curl resume logs) + `game-re/` 704M with
`MANIFEST.md` (21 batches, ALL PENDING — 2026-10-16 plan to TeraBox
`/western-heart/teardowns/`). The blitz-20261015 manifest explicitly marks
Tekken 6 / SFEX3 / Eastern Mind **DO NOT DELETE** (game-files law). Staging
dirs exist because browser uploads are serialized ≤225MB — they clear only
after TeraBox verify. **0G recoverable now**; this is a pipeline backlog, not
waste. Def Jam needs owner go-ahead regardless of verify state.

### 5. `game-reverse-engineering/.git` — 5.1G of pack holding moved data

Working tree is ~200M; the 5.1G is all in `.git`. Last commit (2026-10-16):
"disk-clear: data moved to GitHub releases" — the blobs went to releases but
**the history still holds them**, so the pack never shrank. Fix: fresh
shallow re-clone of `mhvnsnt/game-reverse-engineering` (all real data is on
GitHub + releases). **~4–5G recoverable**. Owner-approved op only — never
clobber a repo dir; clone to a new path, verify, then swap.

Related: `weight-bakeoff-scratch/blends/` 3.1G — bakeoff intermediates; the
winning W5r skinning + proofs are IN the repo (`docs/research/bakeoff-proofs/`,
PR #104 merged). **~3.1G recoverable** once the coordinator confirms the
bakeoff isn't being re-run (last write 11:59 today — give it a day).

## Watch items (not actions)

- **`wh-full` shallow clone in flight** (`git clone --depth 1 --no-checkout
  mhvnsnt/western-heart`, index-pack since 20:20): a SECOND local copy of the
  8G western-heart repo is being pulled onto an 86%-full disk. Per
  NEVER-KILL-IN-PROGRESS it finishes — but the owner should know the disk
  budget didn't include it. `western-heart/` local is already on main @
  4173f796, clean.
- **CloudDog registry is unused by real lanes** — only 6 `cdtest-*` entries.
  Lanes clear by hand; nobody registers verified uploads, so `enforce --apply`
  is a no-op on real data. The guardian runs the engine but the lanes don't
  feed it. Wire registration into the lane completion path or the engine stays
  decorative.
- **Push-after-every-turn is slipping on lanes**: `cj-donor-anims` and
  `cj-hands-work` each hold 1 unpushed commit; `cj-idle-render` has no
  upstream. Unpushed work is unbacked-up work on a 86%-full disk.
- `tools/` 9.5G (ghidra 1.3G, blender 1.3G, aider-pkgs 641M, trackA-*,
  tekken-decrypt, etc.) — legitimate toolchain, leave alone. `aider-pkgs`
  smells like reinstallable cache; verify before touching.

## Action list (expected recovery)

| # | Action | GB | Gate |
|---|---|---|---|
| 1 | Clear `cj-rig-wt/.rig_tmp` (untracked, merged work) | ~6.1 | rig coordinator confirms |
| 2 | Prune quiet worktrees (pinocchio-fix, rig-anim-work, anim, opencode) | ~9.6 | branches pushed + merged |
| 3 | Clear `doa4-bass` intermediates | ~4.6 | OWNER decision (game-files law) |
| 4 | Clear `weight-bakeoff-scratch/blends` | ~3.1 | coordinator confirms bakeoff done |
| 5 | Shallow re-clone game-reverse-engineering | ~4.5 | owner-approved, no-clobber swap |
| 6 | Remove `cj-part-aa` (empty), `cj-rev2-scratch` (71M) | ~0.1 | none |
| | **Realistic total** | **~15–25G** | → disk 86% → ~65% |

## Guidance for the team (bidirectional)

1. **Stop making full worktrees per lane.** One repo, N full checkouts is the
   structural cause of this crisis. Prefer `git worktree` with `--no-checkout`
   + sparse checkout for lanes that only touch part of the tree, or short-lived
   worktrees pruned the moment the branch merges. A lane's worktree should die
   with its PR.
2. **Untracked temp dirs are the silent killer.** `.rig_tmp` (6.1G) never
   appeared in any scan summary because it's hidden and untracked. Add
   `.*_tmp`, `.scratch`, `*scratch*` patterns to the guardian's watch list —
   CloudDog already has the machinery; point it at dot-directories.
3. **Register real clears.** Every lane that verifies an upload to GitHub or
   TeraBox should `cloud_dog.py register` it. The engine can't reap what it
   doesn't know about, and the audit trail is the proof the owner trusts.
4. **No second full clones over the ceiling.** `wh-full` + `western-heart`
   doubles an 8G repo while the disk is at 86%. Disk-budget any clone/fetch
   over 1G through the guardian first.
5. **Push discipline is backup discipline.** Unpushed lane commits on a nearly
   full disk are one `rm -rf` from being gone. The push-after-every-turn law
   exists for exactly this moment.
