#!/usr/bin/env bash
#
# Detach a freshly cloned copy of this template from its origin and reset its
# git history to a single clean commit.
#
# There is no git "post-clone" hook (the .git directory is never transferred by
# a clone), so this is run manually once after cloning the template:
#
#     ./scripts/fresh-start.sh
#
# It removes the existing .git directory (dropping all history AND the original
# remote), re-initialises the repo on branch `dev` (the team trunk; there is no
# `main`), and creates a single "Initial commit" so the new project starts from
# a clean slate. The working tree (your files) is left untouched.
#
# Pass --force to skip the confirmation prompt.

set -euo pipefail

force=0
[ "${1:-}" = "--force" ] && force=1

# Resolve the repo root (parent of this script's directory) so the script works
# regardless of the current working directory.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname "$script_dir")"
cd "$repo_root"

if [ ! -d .git ]; then
    echo "No .git directory found in $repo_root - nothing to reset." >&2
    exit 1
fi

origin="$(git remote get-url origin 2>/dev/null || true)"

echo
echo "This will PERMANENTLY delete all git history in:"
echo "  $repo_root"
[ -n "$origin" ] && echo "and detach it from origin ($origin)."
echo "The working tree (your files) is left untouched."
echo

if [ "$force" -ne 1 ]; then
    read -r -p "Type 'yes' to continue: " answer
    if [ "$answer" != "yes" ]; then
        echo "Aborted. Nothing changed."
        exit 0
    fi
fi

# Dropping .git removes both the history and every configured remote.
rm -rf .git

# `dev` is the team trunk; there is no `main`.
git init -b dev
git add -A
git commit -m "Initial commit" >/dev/null

echo
echo "Done. Fresh history on branch 'dev', no remote configured."
echo "Add your own remote when ready, e.g.:"
echo "  git remote add origin <your-new-repo-url>"
echo "  git push -u origin dev"
