#!/usr/bin/env bash
set -euo pipefail

IDLE_LINE='- [ ] Check to generate preview'
TICKED_LINE='- [x] Check to generate preview'

preview_pr_line1() {
  local body="$1"
  printf '%s' "$body" | sed -n '1p'
}

preview_pr_rest() {
  local body="$1"
  printf '%s' "$body" | sed '1d'
}

preview_pr_has_checkbox() {
  local line1="$1"
  [[ "$line1" == "$IDLE_LINE" || "$line1" == "$TICKED_LINE" || "$line1" =~ ^-\ \[[xX]\]\ https:// ]]
}

preview_pr_should_start() {
  local line1="$1"
  [[ "$line1" == "$TICKED_LINE" ]]
}

preview_pr_is_running_marker() {
  local line1="$1"
  [[ "$line1" =~ ^-\ \[[xX]\]\ https:// ]]
}

preview_pr_set_line1() {
  local body="$1"
  local new_line="$2"
  local rest
  rest="$(preview_pr_rest "$body")"
  if [[ -n "$rest" ]]; then
    printf '%s\n%s' "$new_line" "$rest"
  else
    printf '%s\n' "$new_line"
  fi
}

preview_pr_ensure_idle_checkbox() {
  local body="$1"
  local line1
  line1="$(preview_pr_line1 "$body")"
  if preview_pr_has_checkbox "$line1"; then
    printf '%s' "$body"
    return 0
  fi
  if [[ -z "$body" ]]; then
    printf '%s\n' "$IDLE_LINE"
  else
    printf '%s\n\n%s' "$IDLE_LINE" "$body"
  fi
}

preview_pr_set_url() {
  local body="$1"
  local url="$2"
  preview_pr_set_line1 "$body" "- [x] $url"
}

preview_pr_reset_idle() {
  local body="$1"
  preview_pr_set_line1 "$body" "$IDLE_LINE"
}
