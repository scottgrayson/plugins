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
  jq -n --arg body "$body" '{body: $body}' \
    | gh api "repos/$GITHUB_REPOSITORY/pulls/$PREVIEW_PR_NUMBER" -X PATCH --input - >/dev/null
}

preview_actions_ensure_checkbox() {
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_ensure_idle_checkbox "${body:-}")"
  if [[ "$new_body" != "$body" ]]; then
    preview_actions_set_body "$new_body"
    preview_log "normalized idle preview checkbox on PR #$PREVIEW_PR_NUMBER"
  else
    preview_log "preview checkbox already present on PR #$PREVIEW_PR_NUMBER"
  fi
}

preview_actions_should_start() {
  local body
  body="$(preview_actions_get_body)"
  if preview_pr_should_start "${body:-}"; then
    echo "yes"
  else
    echo "no"
  fi
}

preview_actions_mark_loading() {
  local job_start_epoch="${1:-$(date +%s)}"
  local body new_body started_et last
  body="$(preview_actions_get_body)"
  started_et="$(preview_pr_format_et "$job_start_epoch")"
  last="$(preview_pr_get_last_deploy "${body:-}")"
  new_body="$(preview_pr_set_loading "${body:-}" "$started_et" "$last")"
  preview_actions_set_body "$new_body"
  preview_log "loading: $(preview_pr_line1 "$new_body")"
}

preview_actions_mark_ready() {
  local url="$1"
  local job_start_epoch="${2:-$(date +%s)}"
  local idle_seconds="${3:-300}"
  local deploy_seconds="${4:-}"
  local body new_body created_et
  body="$(preview_actions_get_body)"
  created_et="$(preview_pr_format_et "$job_start_epoch")"
  new_body="$(preview_pr_set_running "${body:-}" "$url" "$created_et" "$idle_seconds")"
  if [[ -n "$deploy_seconds" ]]; then
    new_body="$(preview_pr_set_last_deploy "$new_body" "$deploy_seconds")"
  fi
  preview_actions_set_body "$new_body"
  preview_log "READY: $(preview_pr_line1 "$new_body")"
}

preview_actions_mark_stopped() {
  local reason="${1:-stopped}"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_reset_idle "${body:-}")"
  preview_actions_set_body "$new_body"
  preview_log "Preview stopped ($reason)"
}
