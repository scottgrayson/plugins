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

preview_actions_read_avg_seconds() {
  local metrics_dir="${PREVIEW_METRICS_DIR:-.preview-metrics}"
  local f="$metrics_dir/avg_ready_seconds"
  if [[ -f "$f" ]]; then
    local v
    v="$(tr -d '[:space:]' <"$f")"
    if [[ "$v" =~ ^[0-9]+$ ]] && (( v > 0 && v < 7200 )); then
      echo "$v"
      return 0
    fi
  fi
  echo "${PREVIEW_DEFAULT_AVG_SECONDS:-61}"
}

preview_actions_write_avg_seconds() {
  local elapsed="$1"
  local metrics_dir="${PREVIEW_METRICS_DIR:-.preview-metrics}"
  mkdir -p "$metrics_dir"
  local prev new
  prev="$(preview_actions_read_avg_seconds)"
  # EMA so one slow run does not dominate; integer seconds.
  new="$(python3 -c "print(max(1, round(0.4 * $elapsed + 0.6 * $prev)))")"
  printf '%s\n' "$new" >"$metrics_dir/avg_ready_seconds"
  printf '%s\n' "$elapsed" >>"$metrics_dir/ready_samples.log"
  preview_log "ready in ${elapsed}s (avg deploy estimate now $(preview_pr_format_avg "$new"))"
}

preview_actions_mark_loading() {
  local avg_seconds="${1:-$(preview_actions_read_avg_seconds)}"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_set_loading "${body:-}" "$avg_seconds")"
  preview_actions_set_body "$new_body"
  preview_log "loading: $(preview_pr_loading_line "$avg_seconds")"
}

preview_actions_mark_ready() {
  local url="$1"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_set_url "${body:-}" "$url")"
  preview_actions_set_body "$new_body"
  preview_log "READY: $url"
}

preview_actions_mark_stopped() {
  local reason="${1:-stopped}"
  local body new_body
  body="$(preview_actions_get_body)"
  new_body="$(preview_pr_reset_idle "${body:-}")"
  preview_actions_set_body "$new_body"
  preview_log "Preview stopped ($reason)"
}
