#!/usr/bin/env bash
set -euo pipefail

# AWFL demo recorder helper
#
# Subcommands:
#   record <output.cast> [--cmd "awfl"]
#   gif    <input.cast> <output.gif> [--cols N --rows N --fps N --theme THEME]
#   svg    <input.cast> <output.svg> [svg-term options e.g. --speed 16]
#   upload <input.cast>
#
# Examples:
#   ./scripts/record_demo.sh record recordings/awfl-quickstart.cast --cmd "awfl"
#   ./scripts/record_demo.sh gif recordings/awfl-quickstart.cast docs/awfl-quickstart.gif --cols 100 --rows 28 --fps 30 --theme dracula
#   ./scripts/record_demo.sh svg recordings/awfl-quickstart.cast docs/awfl-quickstart.svg --speed 16
#   ./scripts/record_demo.sh upload recordings/awfl-quickstart.cast
#
# Env toggles:
#   SVG_TERM_USE_NPX=1           # force npx svg-term even if a global svg-term exists
#   SVG_TERM_PREFER_GLOBAL=1     # prefer global svg-term even if npx is available
#   SVG_TERM_ENABLE_WINDOW=1     # add --window --no-cursor (off by default for max compatibility)

usage() {
  sed -n '1,45p' "$0" | sed 's/^# \{0,1\}//'
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command not found: $1" 1>&2
    exit 1
  fi
}

# Best-effort parser for asciicast format version from first line JSON header.
# Returns version number or empty string if unknown.
cast_version() {
  local f="$1"
  local line
  line=$(head -n 1 "$f" 2>/dev/null || true)
  # Try to extract an integer after "version":
  if [[ "$line" =~ "version"[[:space:]]*:[[:space:]]*([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo ""
  fi
}

print_agg_install_help() {
  cat 1>&2 <<'EOF'
agg (asciicast→GIF) is not installed.

Install options:
- Cargo (recommended):
    # Install Rust toolchain with rustup (https://rustup.rs/) if needed
    cargo install --git https://github.com/asciinema/agg
    # Ensure $HOME/.cargo/bin is on PATH
    export PATH="$HOME/.cargo/bin:$PATH"
- Homebrew (macOS):
    brew install asciinema-agg
- Docker (no local install):
    docker run --rm -v "$PWD":/data asciinema/asciicast2gif INPUT.cast OUTPUT.gif
  Replace INPUT.cast/OUTPUT.gif with your paths.
- Prebuilt binary:
    Download the latest release for your OS from:
    https://github.com/asciinema/agg/releases
    Then put the 'agg' binary somewhere on your PATH (e.g., /usr/local/bin).

Then re-run your command, e.g.:
  ./scripts/record_demo.sh gif recordings/demo.cast docs/demo.gif
EOF
}

sub_record() {
  need_cmd asciinema
  local cast="$1"; shift
  local cmd=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cmd) cmd="$2"; shift 2;;
      *) echo "error: unknown flag for record: $1" 1>&2; exit 1;;
    esac
  done
  mkdir -p "$(dirname "$cast")"
  echo "Recording to $cast"
  if [[ -n "$cmd" ]]; then
    echo "Starting with command: $cmd"
    asciinema rec -c "$cmd" "$cast"
  else
    asciinema rec "$cast"
  fi
}

sub_gif() {
  # Friendly guidance if agg is missing
  if ! command -v agg >/dev/null 2>&1; then
    print_agg_install_help
    exit 1
  fi

  local in_cast="$1"; shift
  local out_gif="$1"; shift
  # Fail fast on asciicast v3 which agg/svg-term don't support yet.
  local ver
  ver=$(cast_version "$in_cast" || true)
  if [[ -n "$ver" && "$ver" -ge 3 ]]; then
    echo "error: $in_cast is asciicast v$ver, which many exporters (agg/svg-term) don't support yet." 1>&2
    echo "Workarounds:" 1>&2
    echo "- Re-record with asciinema v2 (no install needed):" 1>&2
    echo "    pipx run --spec 'asciinema==2.4.0' asciinema rec $in_cast" 1>&2
    echo "- Or install asciinema v2 and re-record:" 1>&2
    echo "    pipx install 'asciinema==2.4.0' && asciinema rec $in_cast" 1>&2
    exit 2
  fi

  local cols="" rows="" fps="" theme=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cols) cols="$2"; shift 2;;
      --rows) rows="$2"; shift 2;;
      --fps) fps="$2"; shift 2;;
      --theme) theme="$2"; shift 2;;
      *) echo "error: unknown flag for gif: $1" 1>&2; exit 1;;
    esac
  done
  mkdir -p "$(dirname "$out_gif")"
  echo "Exporting GIF to $out_gif"
  # Build args for agg
  args=( -i "$in_cast" -o "$out_gif" )
  [[ -n "$cols" ]] && args+=( --cols "$cols" )
  [[ -n "$rows" ]] && args+=( --rows "$rows" )
  [[ -n "$fps"  ]] && args+=( --fps "$fps" )
  [[ -n "$theme" ]] && args+=( --theme "$theme" )
  agg "${args[@]}"
}

sub_svg() {
  local in_cast="$1"; shift
  local out_svg="$1"; shift

  # Parse extra args, intercept --speed <N> to time-scale the cast before exporting.
  local speed=""
  local extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --speed)
        speed="$2"; shift 2;;
      *)
        extra+=("$1"); shift;;
    esac
  done

  mkdir -p "$(dirname "$out_svg")"
  echo "Exporting SVG to $out_svg"

  # Fail fast on asciicast v3 which svg-term doesn't support.
  local ver
  ver=$(cast_version "$in_cast" || true)
  if [[ -n "$ver" && "$ver" -ge 3 ]]; then
    echo "error: $in_cast is asciicast v$ver, which svg-term doesn't support." 1>&2
    echo "Workarounds:" 1>&2
    echo "- Re-record with asciinema v2 (no install needed):" 1>&2
    echo "    pipx run --spec 'asciinema==2.4.0' asciinema rec $in_cast" 1>&2
    echo "- Or install asciinema v2 and re-record:" 1>&2
    echo "    pipx install 'asciinema==2.4.0' && asciinema rec $in_cast" 1>&2
    exit 2
  fi

  local scaled_cast="$in_cast"
  local tmpdir=""
  if [[ -n "${speed}" && "${speed}" != "1" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
      echo "error: --speed requires python3 to pre-scale asciicast timing" 1>&2
      exit 1
    fi
    # Validate numeric > 0
    if ! awk -v s="${speed}" 'BEGIN{exit !(s+0>0)}'; then
      echo "error: --speed must be a positive number" 1>&2
      exit 1
    fi
    # Create a unique temp directory cross-platform, then write scaled.cast inside it
    tmpdir=$(mktemp -d 2>/dev/null || mktemp -d -t castspeed)
    scaled_cast="$tmpdir/scaled.cast"
    python3 - "$in_cast" "$scaled_cast" "$speed" <<'PY'
import sys, json
inf, outf, factor_s = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    factor = float(factor_s)
    if factor <= 0: raise ValueError
except Exception:
    print("invalid speed factor", file=sys.stderr)
    sys.exit(2)
with open(inf, 'r', encoding='utf-8') as fi, open(outf, 'w', encoding='utf-8') as fo:
    first = True
    header = None
    for line in fi:
        if first:
            first = False
            try:
                header = json.loads(line)
            except Exception:
                header = None
            if isinstance(header, dict):
                if 'duration' in header and isinstance(header['duration'], (int, float)):
                    header['duration'] = header['duration'] / factor
                fo.write(json.dumps(header) + "\n")
            else:
                fo.write(line)
            continue
        s = line.strip()
        if not s:
            fo.write(line)
            continue
        try:
            obj = json.loads(s)
        except Exception:
            fo.write(line)
            continue
        if isinstance(obj, list) and obj and isinstance(obj[0], (int, float)):
            obj[0] = obj[0] / factor
            fo.write(json.dumps(obj, ensure_ascii=False) + "\n")
        else:
            fo.write(line)
PY
  fi

  # Build svg-term args: keep minimal for maximum compatibility (only --out by default)
  local args=( --out "$out_svg" )
  # Optionally add window/no-cursor if explicitly requested
  if [[ "${SVG_TERM_ENABLE_WINDOW:-}" == 1 ]]; then
    args+=( --window --no-cursor )
  fi
  # Append any extra args provided by the user (excluding --speed which we consumed)
  if [[ ${#extra[@]} -gt 0 ]]; then
    args+=( "${extra[@]}" )
  fi

  # Decide runner: prefer npx unless explicitly overridden
  local use_npx=0
  if [[ "${SVG_TERM_USE_NPX:-}" == 1 ]]; then
    use_npx=1
  elif [[ -z "${SVG_TERM_PREFER_GLOBAL:-}" && -n "$(command -v npx || true)" ]]; then
    use_npx=1
  fi

  local -a runner
  if [[ $use_npx -eq 1 ]]; then
    runner=( npx --yes --package=svg-term-cli svg-term )
  else
    runner=( svg-term )
  fi

  # First try piping via stdin (works on all svg-term versions)
  set +e
  "${runner[@]}" "${args[@]}" < "$scaled_cast"
  local rc=$?

  # If still failing and we used global, try npx as a last resort.
  if [[ $rc -ne 0 && $use_npx -eq 0 && -n "$(command -v npx || true)" ]]; then
    echo "global svg-term failed; retrying with npx svg-term-cli..." 1>&2
    runner=( npx --yes --package=svg-term-cli svg-term )
    "${runner[@]}" "${args[@]}" < "$scaled_cast"
    rc=$?
  fi

  # As a fallback for very old builds that require an input flag, try --in once.
  if [[ $rc -ne 0 ]]; then
    echo "still failing; retrying with --in path..." 1>&2
    "${runner[@]}" --in "$scaled_cast" "${args[@]}"
    rc=$?
  fi

  set -e

  # Clean up temp dir if we created one
  if [[ -n "$tmpdir" ]]; then
    rm -rf "$tmpdir" || true
  fi

  if [[ $rc -ne 0 ]]; then
    echo "error: svg-term export failed. Consider upgrading: npm i -g svg-term-cli, or run with SVG_TERM_USE_NPX=1." 1>&2
    exit $rc
  fi
}

sub_upload() {
  need_cmd asciinema
  local cast="$1"; shift
  echo "Uploading $cast to asciinema.org"
  asciinema upload "$cast"
}

main() {
  if [[ $# -lt 1 ]]; then
    usage; exit 1
  fi
  local cmd="$1"; shift
  case "$cmd" in
    -h|--help|help) usage ;;
    record)
      [[ $# -ge 1 ]] || { echo "usage: record <output.cast> [--cmd \"awfl\"]" 1>&2; exit 1; }
      sub_record "$@" ;;
    gif)
      [[ $# -ge 2 ]] || { echo "usage: gif <input.cast> <output.gif> [--cols N --rows N --fps N --theme THEME]" 1>&2; exit 1; }
      sub_gif "$@" ;;
    svg)
      [[ $# -ge 2 ]] || { echo "usage: svg <input.cast> <output.svg> [svg-term options...] (plus optional --speed N)" 1>&2; exit 1; }
      sub_svg "$@" ;;
    upload)
      [[ $# -ge 1 ]] || { echo "usage: upload <input.cast>" 1>&2; exit 1; }
      sub_upload "$@" ;;
    *)
      echo "error: unknown subcommand: $cmd" 1>&2
      usage
      exit 1 ;;
  esac
}

main "$@"
