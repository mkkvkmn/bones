# shellcheck shell=bash
# Helpers for dotenv (KEY=value) and env_vars (export KEY="value") files.
# Sourced by common.sh, so every ship script has them. Needs log_error from common.sh.
#
# Values are read into variables and written to files, never printed: callers report key NAMES
# only, so running a script in front of an agent or a screen share does not leak secrets.
#
# Functions:
#   has_var FILE NAME            NAME= line exists
#   get_var FILE NAME            value of the last NAME= line (unquoted)
#   set_var FILE NAME VALUE      replace NAME= lines keeping style, or append one
#   rand_hex BYTES               random hex from openssl (needs no quoting anywhere)
#   env_keys FILE                key names of active (uncommented) lines, in file order
#   empty_keys FILE              keys with an empty value (optional unless the app needs them)
#   assert_env_keys_present EXAMPLE ACTUAL LABEL
#                                fail naming every key of EXAMPLE missing from ACTUAL

# Template placeholders in ship/env/env_vars.example.
PLACEHOLDER_VALUE_RE='^(CHANGE_ME.*|YOUR_[A-Z_]+|your-email@example\.com)$'

var_line_re() { printf '^[[:space:]]*(export[[:space:]]+)?%s=' "$1"; }

has_var() { grep -E -q "$(var_line_re "$2")" "$1"; }

# Value of the last NAME= line, without surrounding quotes, trailing comment or CR.
get_var() {
  grep -E "$(var_line_re "$2")" "$1" | tail -n 1 | sed -E "s/$(var_line_re "$2")//; s/\r$//; s/^[\"']//; s/[\"'][[:space:]]*(#.*)?$//; s/[[:space:]]+#.*$//; s/[[:space:]]+$//"
}

# Replace every NAME= line (keeping an `export ` prefix and the file's quoting style), or append one.
set_var() {
  local file="$1" name="$2" value="$3" tmp
  # Why not hex only: an existing key moves to _PREV as it is, and older keys are often base64.
  # Refused characters would change meaning in a dotenv or shell file. The value is never shown.
  local unsafe='[[:space:]"'"'"'#\\$`]'
  if [ -z "$value" ] || [[ "$value" =~ $unsafe ]]; then
    log_error "Refusing to write $name: the value is empty or has a space, quote, #, backslash, \$ or backtick. Nothing was changed."
  fi
  tmp="$(mktemp "${file}.XXXXXX")"
  if has_var "$file" "$name"; then
    awk -v name="$name" -v value="$value" '
      {
        line = $0; cr = ""
        if (sub(/\r$/, "", line)) cr = "\r"
        if (match(line, "^[ \t]*(export[ \t]+)?" name "=")) {
          prefix = substr(line, 1, RLENGTH)
          rest = substr(line, RLENGTH + 1)
          q = substr(rest, 1, 1)
          if (q != "\"" && q != "'"'"'") q = ""
          print prefix q value q cr
          next
        }
        print $0
      }' "$file" > "$tmp"
  else
    cat "$file" > "$tmp"
    [ -z "$(tail -c 1 "$file")" ] || printf '\n' >> "$tmp"
    if [[ "$file" == *.sh ]]; then printf 'export %s="%s"\n' "$name" "$value" >> "$tmp"; else printf '%s=%s\n' "$name" "$value" >> "$tmp"; fi
  fi
  chmod 600 "$tmp" 2>/dev/null || true
  mv "$tmp" "$file"
}

rand_hex() { openssl rand -hex "$1"; }

env_keys() {
  grep -E -o '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$1" \
    | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//; s/=$//' \
    | awk '!seen[$0]++'
}

empty_keys() {
  local file="$1" key
  while IFS= read -r key; do
    [ -z "$(get_var "$file" "$key")" ] && printf '%s\n' "$key"
  done < <(env_keys "$file")
  return 0
}

assert_env_keys_present() {
  local example="$1" actual="$2" label="$3" key
  local missing=()
  [ -f "$example" ] || return 0
  while IFS= read -r key; do
    has_var "$actual" "$key" || missing+=("$key")
  done < <(env_keys "$example")
  if [ "${#missing[@]}" -gt 0 ]; then
    log_error "${label} is missing keys listed in ${example#"${REPO_ROOT:-}"/}: ${missing[*]}. Add them (values are not shown here)."
  fi
}
