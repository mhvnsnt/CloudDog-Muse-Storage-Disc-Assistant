# CloudDog laws

The owner's disk laws, quoted VERBATIM. Each entry carries its source.
CloudDog encodes these as code in `clouddog/policy/laws.py`; the "encoded
as" note under each law says how.

---

## NO-LOCAL-CLUTTER

Source: owner 2026-10-17 — ~/AGENTS.md

> STOP cluttering local disk. Disk is a CACHE, not storage. Everything goes
> to TeraBox (5TB, barely used), Google Drive, Kaggle datasets, or GitHub
> releases — THEN clears local. This applies to ALL agents, ALL side chats,
> ALL main chats, every lane, every time. No exceptions. If it's on local
> disk and it's not active work, it gets pushed to a store and cleared. Disk
> stays under 75%.

Encoded as: the whole package. `sweep`/`watch` exist to keep pushing
non-active work to stores; `warn_pct: 75` is the default threshold.

## DISK-STORING-MOVING

Source: owner 2026-10-14 — ~/AGENTS.md

> It is NEVER "disk clearing" — it is disk STORING/MOVING. Nothing is ever
> deleted from local disk to free space until it has been moved to GitHub or
> TeraBox AND the upload verified byte-identical. The sequence is always:
> upload → verify → THEN delete. Say "storing/moving," never "clearing."
> (Extends SAFE-STORAGE-FIRST.)

Encoded as: `store` uploads, then `verify`s, then journals. v1 performs the
upload → verify half; the delete half is a future explicit `reap` step and
is not implemented.

## ONE-STORE RULE

Source: owner 2026-10-16 ~17:41 EDT, the disc cleaner chad chat — ~/AGENTS.md

> His words: "verify that stuff is over To GitHub or to Terrabox it does not
> have to be to both it just has to be safely secured to one or the other
> 100% then it can be cleared."
>
> When a file/folder is verified 100% secured on GitHub OR on TeraBox
> (byte-identical, hash/size verified — pieces alone don't count unless they
> reassemble hash-verified), it MAY be cleared from local disk. No per-file
> ask. Conditions that carry forward: (1) NO teardown/decoding/decryption
> actively in progress on it right now; (2) if the teardown isn't 100% per
> the TEARDOWN-COMPLETION LAW yet, LOG it on the teardown-idle queue
> (`~/workspace/agent-ops/TEARDOWN-IDLE-QUEUE.md`) BEFORE deleting — the
> thread is never lost, it gets done in idle time; (3) log every deletion +
> backup location to `~/workspace/agent-ops/disk-cleanup-log.md`. The
> default is still never delete blindly.

Encoded as: `laws.decide_delete` rule 4 — one verified store is sufficient;
`store.verify(ref)` checks hash + size.

## CONTINUOUS-MIRROR

Source: owner 2026-10-17 ~10:15 CDT, disc cleaner chad chat — ~/AGENTS.md

> His words: "you can start uploading like while this stuff is mid download
> mid upload unverified or active work you can start storing and archiving it
> on GitHub or Terrabox safely."
>
> Don't wait for 100% — continuously store and archive EVERYTHING on disk to
> GitHub/TeraBox (or its proper repo) WHILE it's mid-download, mid-upload,
> unverified, or active work. Upload new arrivals incrementally as they land;
> push active work (WIP commits, branches) continuously. Then verify 100%
> WITH HIM — surface what's stored so he sees it — and clear off disk
> anything verified that isn't needed locally right now. Disk is a cache, not
> storage: everything on it must also live in a store AND remain
> reachable/usable for work (pull-back commands recorded in the log/handoff).
> This is how the disk stays clean while work runs constantly. Mirroring
> never deletes — deletion still follows the ONE-STORE RULE.

Encoded as: the `watch` daemon — every 10 minutes it mirrors the top
unmirrored candidates; "mirroring never deletes" is a v1 invariant.

## DELETE-AS-YOU-GO

Source: owner 2026-10-17 ~11:05 CDT, disc cleaner chad chat — ~/AGENTS.md

> For blob/chunk upload lanes, delete each local file the moment its blob
> verifies hash-identical on the store (recorded SHA == locally computed git
> hash) — disk falls DURING the upload, not after. The lane's progress
> manifest is sacred (never delete it, never let the reaper touch it); the
> lane must still finish its final tree/commit so blobs aren't left
> unreferenced — the watchdog nurses it there.

Encoded as: each `store` verifies per file before journaling it stored, so a
future `reap` can clear file-by-file. (Per-file delete-on-verify is the
`reap` step, not v1.)

## NEVER-DELETE-GAME-FILES

Source: owner 2026-10-14 — ~/AGENTS.md (the Tekken 6 ISO incident)

> On 2026-10-13, the disk cleanup deleted the Tekken 6 PSP ISO (816MB) that
> took the owner a full day to collect — during a Drive store with NO
> manifest entry and NO verification, in direct violation of the checkpoint
> that explicitly forbade deletion until TeraBox verified. It was recovered
> from GitHub, but the violation stands.
>
> GAME FILES ARE NEVER AUTO-DELETED. This covers: ISOs, ROMs, game archives
> (.zip/.rar/.7z of full games), teardown source files, anything in
> game-reverse-engineering/, chimera-roms/, or any teardown lane's source
> material.
>
> 1. No disk cleanup worker, guardian cron, or subagent may delete a game
> file. Ever. Not "after GitHub verify" — GitHub is not enough for these.
> The file stays until it is verified on TERABOX (the permanent home) AND
> the owner has confirmed.
> 2. Game files are treated as IRREPLACEABLE — "re-downloadable" is not a
> justification for deletion. The owner's time collecting them is the cost
> that matters.
> 3. If disk pressure requires action on game files, the ONLY move is:
> upload to TeraBox via the browser lane, verify, report to owner, and WAIT
> for his explicit go-ahead before deleting.
> 4. Any worker that deletes a game file without owner authorization has
> violated a direct owner law.

Encoded as: `laws.is_protected_game_file` + `decide_delete` rule 3 —
protected game files require owner approval AND a verified store; v1 never
deletes anything at all.

## NEVER-KILL-IN-PROGRESS

Source: owner 2026-10-15 — ~/AGENTS.md (the 6GB Gumball incident)

> On 2026-10-15, I killed 8 Gumball downloads that were 70% done (5.8GB of
> 8GB downloaded) and deleted the partial files, because I thought the
> parallel downloads were causing IA throttling for another pipeline. The
> owner corrected me: that 6GB might not have been recoverable if the
> mirrors got taken down — I threw away nearly-complete, potentially
> irreplaceable progress for a hypothetical optimization. The downloads had
> to restart from zero.
>
> NEVER kill, stop, delete, or interrupt anything that is IN PROGRESS —
> downloading, uploading, loading, processing, rendering, extracting —
> without the owner's explicit approval. Especially when it's close to done.
> "It's slowing something else down" is NOT justification. "I have a better
> method" is NOT justification. Let it finish. Progress on disk is real; the
> hypothetical speedup is not. If two pipelines conflict, ASK the owner
> which to keep — do not unilaterally kill one.
>
> This applies to every repo, every lane, every agent, every time. A worker
> that kills in-progress work without owner approval has violated a direct
> owner law.

Encoded as: `laws.is_in_progress` + `decide_delete` rule 1 — in-progress is
never allowed, no override exists.

## NO-SWEEP-INFLIGHT

Source: owner 2026-10-16 — guardian cron body
(`~/workspace/cron.d/minutely/disk-space-guardian__interval@10m.md`)

> **NO-SWEEP-INFLIGHT LAW (owner 2026-10-16):** NEVER delete, move, or "clean
> up" anything that is downloading, uploading, rendering, extracting, or
> otherwise in-flight — no matter how full the disk gets. Download folders,
> staging dirs, partial files (.part, .kaggle-partial, .aria2, split
> chunks), and anything an active lane is writing to are SACRED and
> untouchable. If disk is critical: pause NEW downloads, surface the alert
> to the owner, NEVER sweep in-flight work. A deleted partial can destroy
> unrecoverable progress (mirrors die, sessions expire, quota burns).

Encoded as: `sweep` and the daemon skip in-progress candidates entirely
(`_sweep_skip_reason` / `run_once` skip notes).

## NO-DELETE-BEFORE-SAFE-STORAGE

Source: owner 2026-10-13 — ~/AGENTS.md

> NEVER delete anything currently in use for disk space before it is
> VERIFIED safe on GitHub, TeraBox, or another permanent store. No
> exceptions. If disk is full, the move is: upload first, verify the upload,
> THEN delete. Deleting a working file (like the Tekken 6 ISO) to free space
> before its backup is confirmed is banned. This applies to every agent,
> every lane, every time.

Encoded as: `laws.decide_delete` rule 5 — no verified store, no delete;
`VerificationFailed` is raised (and journaled as FAILED) instead of
journaling a success.

## SAFE-STORAGE-FIRST

Source: owner 2026-10-13 — ~/AGENTS.md

> When disk starts getting full, EVERYTHING goes somewhere safe BEFORE any
> cleanup: TeraBox (5TB Premium) or the GitHub repo it belongs in. Not just
> "don't delete" — actively STORE it. Every piece of work lives in at least
> TWO places: local disk AND (TeraBox OR GitHub). If it's deleted from disk
> or lost, it's still safe in TeraBox/GitHub. No more "we cleared the disk
> and now it's gone" — EVER. This is automatic, not optional. When disk hits
> 80%, start moving the largest verified-safe items to TeraBox/GitHub. When
> it hits 85%, move aggressively. The rule is simple: if it's worth keeping
> on disk, it's worth backing up first.

Encoded as: `sweep` acts at `warn_pct` (default 75) on the largest candidates
first; the daemon mirrors continuously regardless of threshold.

## TOOLS-TO-STORE

Source: owner 2026-10-16 ~16:49 EDT — ~/AGENTS.md

> Every tool, app, connector, or vendor software pulled in gets pushed to the
> appropriate repo and/or TeraBox BEFORE it's disposed from disk. Never
> delete a tool from disk without it living in a store first — even if it's
> "re-downloadable." The repos are the toolbox. Download URLs in a log are
> not the same as having the tool. Wire it up, push it, then clear disk.

Encoded as: no special-casing for tools — a tool directory is a sweep
candidate like anything else and must be stored + verified first.

## PULL-STRAIGHT-TO-STORE

Source: owner 2026-10-16 — ~/AGENTS.md

> When pulling in tools, assets, or files — if you can pull it STRAIGHT to
> TeraBox, GitHub, TorBox, or any other permanent store WITHOUT cluttering
> local disk, DO THAT instead. Don't clone to ~/workspace/tools/ as a
> staging area and then push — clone directly into the repo checkout, wire it
> up there, push, done. Don't download to disk what can go straight to
> TeraBox via browser upload or API. Local disk is for ACTIVE WORK ONLY —
> not a warehouse. Every agent, every repo, every time.

Encoded as: routing guidance — `store --to` sends a file straight to its
permanent home; the GitHub store pushes release assets without staging.
