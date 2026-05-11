#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# init_public_repo.sh — copy the current working tree into a brand-new public
# repository with a single clean initial commit. Preserves the private repo's
# history by NOT touching this checkout.
#
# Usage:
#   scripts/release/init_public_repo.sh /absolute/path/to/new/public-repo
#
# What it does:
#   1. Snapshots the current tree (respecting .gitignore via `git ls-files`)
#      into the target directory.
#   2. Initialises a fresh git repo there with main as the default branch.
#   3. Creates ONE initial commit titled "feat: initial public release (v0.1.0)".
#   4. Prints the commands you need to add the GitHub remote, push, and tag.
#
# Safety:
#   • The target directory must not already be a git repository.
#   • If the target exists and is non-empty, the script aborts.
#   • This script never pushes anything itself — that step is left to you so
#     you can sanity-check the snapshot first.
#
# Why bother?
#   The private repo's history contains intermediate scaffolding, refactors,
#   and (potentially) experimental artefacts that are not relevant to the
#   public artefact accompanying the paper. A single clean v0.1.0 commit is
#   what we want collaborators and reviewers to see when they `git log`.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /absolute/path/to/new/public-repo" >&2
    exit 1
fi

TARGET="$1"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ ! -d "$SRC/.git" ]]; then
    echo "[init_public_repo] Error: $SRC is not a git repository." >&2
    exit 1
fi

if [[ -e "$TARGET" ]]; then
    if [[ -d "$TARGET" && -z "$(ls -A "$TARGET")" ]]; then
        :   # empty dir is fine
    else
        echo "[init_public_repo] Error: $TARGET exists and is non-empty. Aborting." >&2
        exit 1
    fi
fi

VERSION="$(grep -m1 '^version' "$SRC/pyproject.toml" | sed -E 's/.*"([^"]+)".*/\1/')"
if [[ -z "$VERSION" ]]; then
    echo "[init_public_repo] Error: could not parse version from pyproject.toml" >&2
    exit 1
fi

echo "[init_public_repo] Source       : $SRC"
echo "[init_public_repo] Target       : $TARGET"
echo "[init_public_repo] Package ver  : $VERSION"

mkdir -p "$TARGET"

# Copy every file that git tracks (respects .gitignore). We deliberately skip
# .git so the destination starts with a clean history. We tolerate files that
# are tracked but missing on disk (e.g. an uncommitted `git mv`) — these are
# skipped with a warning, since the snapshot reflects the working tree.
echo "[init_public_repo] Copying tracked files…"
missing=0
( cd "$SRC" && git ls-files -z ) | (
    cd "$TARGET"
    while IFS= read -r -d '' rel; do
        if [[ ! -e "$SRC/$rel" ]]; then
            echo "[init_public_repo]   skipping missing file: $rel" >&2
            missing=$((missing + 1))
            continue
        fi
        mkdir -p "$(dirname "$rel")"
        cp -p "$SRC/$rel" "$rel"
    done
    if [[ $missing -gt 0 ]]; then
        echo "[init_public_repo]   ($missing tracked files were not present on disk;"\
             "commit the staged moves before snapshotting for a release.)" >&2
    fi
)

# Also pick up files that exist in the working tree but are not yet committed:
#   (a) staged additions (`git mv`, `git add` on new files)
#   (b) untracked-but-not-gitignored files (newly created docs, scripts, etc.)
# Together with the tracked-file copy above, this means the snapshot mirrors
# what `git status` shows you would commit if you ran `git add -A`.
extra_files=$(
    cd "$SRC" \
    && {
        git diff --cached --name-only --diff-filter=A
        git ls-files --others --exclude-standard
    } | sort -u
)
if [[ -n "$extra_files" ]]; then
    n=$(echo "$extra_files" | grep -c .)
    echo "[init_public_repo] Adding $n staged / untracked file(s) from working tree…"
    while IFS= read -r rel; do
        [[ -z "$rel" ]] && continue
        if [[ -e "$SRC/$rel" ]]; then
            mkdir -p "$TARGET/$(dirname "$rel")"
            cp -p "$SRC/$rel" "$TARGET/$rel"
        fi
    done <<< "$extra_files"
fi

# Initialise fresh git history.
echo "[init_public_repo] Initialising fresh repo…"
(
    cd "$TARGET"
    git init -q -b main
    git add -A
    git commit -q -m "feat: initial public release (v$VERSION)

ComfyClaw: An Agentic Harness for Skill-Evolving Image Generation Workflows.
Reference implementation accompanying the paper by Li, Liu, Chen, Wu, Liu,
Zhou, Xie, Wu, and Sun (2026). MIT-licensed."
)

cat <<EOF

[init_public_repo] Done. New repository initialised at:
    $TARGET

Next steps (run yourself after sanity-checking the snapshot):

    cd "$TARGET"
    gh repo create Moms-Organic-Agent-Lab/comfyclaw --public --source=. --remote=origin
    # or, if the remote already exists:
    #   git remote add origin git@github.com:Moms-Organic-Agent-Lab/comfyclaw.git

    git push -u origin main
    git tag -a v$VERSION -m "v$VERSION — first public release"
    git push origin v$VERSION

If you prefer to bring the GitHub Release UI into play, do not push the tag
yet — instead, draft a Release from the GitHub web UI and let it create the
tag; the release.yml workflow will pick up the tag push and build the wheel.
EOF
