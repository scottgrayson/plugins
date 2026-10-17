#!/usr/bin/env bash
set -euo pipefail

LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$LIB/common.sh"
# shellcheck source=pr_description.sh
source "$LIB/pr_description.sh"

preview_actions_require_pr() {
  [[ -n "${PREVIEW_PR_NUMBER:-}" ]] || preview_die "PREVIEW_PR_NUMBER is required"
  [[ -n "${GITHUB_REPOSITORY:-}" ]] || preview_die "GITHUB_REPOSITORY is required"
}

preview_actions_get_body() {
  preview_actions_require_pr
  gh api "repos/$GITHUB_REPOSITORY/pulls/$PREVIEW_PR_NUMBER" --jq 'if .body == null then "" else .body end'
}

preview_actions_set_body() {
  local body="$1"
  preview_actions_require_pr
  # GITHUB_TOKEN description edits do not create new workflow runs, so reset/mark cannot loop.
  jq -n --arg body "$body" '{body: $body}' \
    | gh api "repos/$GITHUB_REPOSITORY/pulls/$PREVIEW_PR_NUMBER" -X PATCH --input - >/dev/null
}

preview_actions_comment() {
  local message="$1"
  preview_actions_require_pr
  gh api "repos/$GITHUB_REPOSITORY/issues/$PREVIEW_PR_NUMBER/comments" -f body="$message" >/dev/null
}

preview_actions_ensure_checkbox() {
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_ensure_idle_checkbox "${body:-}")"
  if [[ "$new_body" != "$body" ]]; then
    preview_actions_set_body "$new_body"
    preview_log "inserted idle preview checkbox on PR #$PREVIEW_PR_NUMBER"
  else
    preview_log "preview checkbox already present on PR #$PREVIEW_PR_NUMBER"
  fi
}

preview_actions_should_start() {
  local body line1
  body="$(preview_actions_get_body)"
  line1="$(preview_pr_line1 "${body:-}")"
  if preview_pr_should_start "$line1"; then
    echo "yes"
  else
    echo "no"
  fi
}

preview_actions_mark_ready() {
  local url="$1"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_set_url "${body:-}" "$url")"
  preview_actions_set_body "$new_body"
  preview_actions_comment "Preview ready: $url"
}

preview_actions_mark_stopped() {
  local reason="${1:-stopped}"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_reset_idle "${body:-}")"
  preview_actions_set_body "$new_body"
  preview_actions_comment "Preview stopped ($reason)"
}
