#!/usr/bin/env bash
# Posts a file as one of the bot's comments on an issue or pull request, or updates that comment when it's already
# there, so new edits or pushes don't pile up comments. Used by .github/workflows/share-translation.yml (the "about
# this file" note) and .github/workflows/author-merge.yml (the pack history note on pull requests).
#
#     GH_TOKEN=… GH_REPO=owner/repo tools/sticky_comment.sh <issue or pull request number> <markdown file> [marker]
#
# The comment is found by the marker on its first line (default: tools/issue_to_pack.py's INFO_MARK); the marker is
# added when the file doesn't start with it.
set -euo pipefail
issue="$1"
file="$2"
mark="${3:-<!-- share-translation:about-this-file -->}"

if [ "$(head -n 1 "$file")" != "$mark" ]; then
  { echo "$mark"; cat "$file"; } > "$file.marked"
  file="$file.marked"
fi

id=$(gh api "repos/$GH_REPO/issues/$issue/comments" --paginate \
  --jq ".[] | select(.user.login == \"github-actions[bot]\" and (.body | startswith(\"$mark\"))) | .id" | tail -n 1)
if [ -n "$id" ]; then
  gh api -X PATCH "repos/$GH_REPO/issues/comments/$id" -F "body=@$file" > /dev/null
  echo "updated comment $id"
else
  gh issue comment "$issue" --body-file "$file"
fi
