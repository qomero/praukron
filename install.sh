#!/bin/sh
set -eu

mode=existing
target=.
target_set=0
link=1
ref=
allow_downgrade=${PRAUKRON_ALLOW_DOWNGRADE:-${PROKRON_ALLOW_DOWNGRADE:-0}}
upgraded=
staged=

# Everything Praukron installs lives in one directory (ADR-024). The only paths
# written outside it are the ones an agent host reads by fixed address, and the
# launcher described below, which goes on the reader's machine (ADR-027).
home=.praukron
chronicle="$home/chronicle"
commands="$home/commands"
runtime="$home/runtime"

# `existing` is the mode almost everyone wants, so installing takes no
# arguments. Both modes stay available, and either may name a target.
while [ $# -gt 0 ]; do
  case $1 in
    new|existing) mode=$1 ;;
    --no-link) link=0 ;;
    --allow-downgrade) allow_downgrade=1 ;;
    --ref)
      [ $# -ge 2 ] && [ -n "$2" ] || { echo "--ref needs a tag or branch" >&2; exit 2; }
      ref=$2
      shift
      ;;
    --ref=*) ref=${1#--ref=} ;;
    -h|--help)
      echo "Usage: install.sh [new|existing] [target-directory] [--no-link] [--ref <tag|branch>] [--allow-downgrade]"
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      echo "Usage: install.sh [new|existing] [target-directory] [--no-link] [--ref <tag|branch>] [--allow-downgrade]" >&2
      exit 2
      ;;
    *)
      # One target, and it must be a directory. Anything else is a typo for a
      # mode, and silently installing somewhere unintended is the worst answer.
      if [ "$target_set" -eq 1 ]; then
        echo "Unexpected argument: $1" >&2
        echo "Usage: install.sh [new|existing] [target-directory] [--no-link] [--ref <tag|branch>] [--allow-downgrade]" >&2
        exit 2
      fi
      target=$1
      target_set=1
      ;;
  esac
  shift
done

if [ ! -d "$target" ]; then
  echo "Target directory does not exist: $target" >&2
  exit 2
fi

target=$(CDPATH= cd "$target" && pwd)
source_dir=
temp_dir=

for file in AGENTS.md CLAUDE.md; do
  if [ -L "$target/$file" ] || { [ -e "$target/$file" ] && [ ! -f "$target/$file" ]; }; then
    echo "Cannot install: $target/$file is not a regular file" >&2
    exit 1
  fi
done

# Run as `sh E:\path\install.sh` from Windows, $0 holds backslashes only; read
# them as slashes so a checkout is still recognised instead of downloading.
script=$(printf '%s' "$0" | tr '\\' '/')
case $script in
  */*)
    candidate=$(CDPATH= cd "$(dirname "$script")" && pwd)
    if [ -f "$candidate/templates/chronicle/README.md" ]; then
      source_dir=$candidate
    fi
    ;;
esac

cleanup() {
  if [ -n "$temp_dir" ]; then
    rm -rf "$temp_dir"
  fi
}
trap cleanup EXIT HUP INT TERM

if [ -n "$source_dir" ] && [ -n "$ref" ]; then
  echo "--ref applies only when the installer downloads Praukron; this one runs from $source_dir" >&2
  exit 2
fi

# The default is the latest published release, not `main`, so two people who
# install on different days get the same runtime (ADR-040).
use_gh=0
if [ -z "$source_dir" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  use_gh=1
fi
downloaded=0
if [ -z "$source_dir" ]; then
  temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/praukron.XXXXXX")
  command -v tar >/dev/null 2>&1 || { echo "tar is required" >&2; exit 1; }
  if [ "$use_gh" -eq 0 ]; then
    command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
  fi
  if [ -z "$ref" ]; then
    if [ "$use_gh" -eq 1 ]; then
      ref=$(gh api repos/qomero/praukron/releases/latest --jq .tag_name 2>/dev/null || true)
    else
      latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
        https://github.com/qomero/praukron/releases/latest 2>/dev/null || true)
      case $latest in */releases/tag/*) ref=${latest##*/} ;; esac
    fi
    if [ -z "$ref" ]; then
      echo "No published release was found; installing main." >&2
      ref=main
    fi
  fi
  if [ "$use_gh" -eq 1 ]; then
    gh api "repos/qomero/praukron/tarball/$ref" > "$temp_dir/praukron.tar.gz"
  else
    curl -fsSL "https://github.com/qomero/praukron/archive/$ref.tar.gz" \
      -o "$temp_dir/praukron.tar.gz"
  fi
  mkdir "$temp_dir/source"
  tar -xzf "$temp_dir/praukron.tar.gz" -C "$temp_dir/source"
  set -- "$temp_dir/source"/*
  source_dir=$1
  downloaded=1
fi

if [ ! -f "$source_dir/templates/chronicle/README.md" ]; then
  echo "Downloaded Praukron source is incomplete" >&2
  exit 1
fi
if [ ! -f "$source_dir/src/praukron/cli.py" ]; then
  echo "This revision predates the Praukron rename. Choose a Praukron revision with --ref; historical Prokron releases keep their original installer." >&2
  exit 1
fi

# An older runtime replacing a newer one silently loses whatever the newer one
# understood, so it takes an explicit flag (ADR-040).
version_older() {
  # True when $1 sorts strictly before $2 as a dotted version.
  [ "$1" != "$2" ] || return 1
  lowest=$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)
  [ "$lowest" = "$1" ]
}
incoming=$(cat "$source_dir/VERSION" 2>/dev/null || echo 0)
# Existing Prokron installations have one authority directory. Move it only
# after preflight, and retain an alias so edited guidance and old launchers work.
rename_install=0
if [ -L "$target/.prokron" ]; then
  # The alias is ours when it resolves to this installation: a relative symlink,
  # or on Windows a junction, whose link text is absolute.
  if [ ! -d "$target/.praukron" ] || [ -L "$target/.praukron" ] ||
    [ "$(CDPATH= cd "$target/.prokron" 2>/dev/null && pwd -P)" != "$(CDPATH= cd "$target/.praukron" && pwd -P)" ]; then
    echo "Cannot upgrade linked Prokron installation: $target/.prokron" >&2
    exit 1
  fi
elif [ -d "$target/.prokron/chronicle" ] || [ -d "$target/.prokron/runtime" ]; then
  if [ -e "$target/.praukron" ] || [ -L "$target/.praukron" ]; then
    echo "Cannot upgrade: both .prokron and .praukron exist; reconcile them first." >&2
    exit 1
  fi
  rename_install=1
fi
present=$(cat "$target/.praukron/runtime/VERSION" 2>/dev/null || true)
if [ "$rename_install" -eq 1 ]; then
  present=$(cat "$target/.prokron/runtime/VERSION" 2>/dev/null || true)
fi
if [ -n "$present" ] && version_older "$incoming" "$present" && [ "$allow_downgrade" != 1 ]; then
  echo "Refusing to install praukron $incoming over the newer $present already in $target." >&2
  echo "Pass --allow-downgrade if that is intended." >&2
  exit 1
fi

# A downloaded release installs itself, exactly as it shipped: its own
# installer knows its own files (ADR-040).
if [ "$downloaded" -eq 1 ]; then
  printf 'Installing praukron %s (%s)\n' "$incoming" "$ref"
  set -- "$mode" "$target"
  [ "$link" -eq 1 ] || set -- "$@" --no-link
  status=0
  PRAUKRON_ALLOW_DOWNGRADE=$allow_downgrade sh "$source_dir/install.sh" "$@" || status=$?
  exit "$status"
fi

copy_new() {
  source_file=$1
  target_file=$2
  if [ -L "$target_file" ] || { [ -e "$target_file" ] && [ ! -f "$target_file" ]; }; then
    echo "Cannot install: $target_file is not a regular file" >&2
    exit 1
  elif [ ! -e "$target_file" ]; then
    mkdir -p "$(dirname "$target_file")"
    cp "$source_file" "$target_file"
  fi
}

# Guidance is what Praukron tells people and agents to do. The runtime is
# replaced on every install, so guidance must keep up with it — without ever
# overwriting what a person edited (ADR-040). The previous install recorded a
# checksum of each guidance file it wrote; a file that still matches was never
# touched and is replaced, and one that differs is kept and the new version is
# staged beside it for review.
manifest="$target/$runtime/GUIDANCE"
previous=
previous_manifest=$manifest
if [ "$rename_install" -eq 1 ]; then
  previous_manifest="$target/.prokron/runtime/GUIDANCE"
fi
if [ -f "$previous_manifest" ] && [ ! -L "$previous_manifest" ]; then
  previous=$(cat "$previous_manifest")
fi
recorded=

sum_of() {
  cksum < "$1" | awk '{ print $1 "-" $2 }'
}

previous_sum() {
  old_key=$(printf '%s' "$1" | sed 's/praukron/prokron/g')
  printf '%s\n' "$previous" | awk -v key="$1" -v old="$old_key" '$2 == key || $2 == old { print $1; exit }'
}

record() {
  recorded="$recorded$1 $2
"
}

stage() {
  # $1 source, $2 key: the new version, at the key's path under upgrade/.
  mkdir -p "$(dirname "$target/$home/upgrade/$2")"
  cp "$1" "$target/$home/upgrade/$2"
  staged="$staged  $2
"
}

install_guidance() {
  source_file=$1
  key=$2
  target_file="$target/$key"
  if [ -L "$target_file" ] || { [ -e "$target_file" ] && [ ! -f "$target_file" ]; }; then
    echo "Cannot install: $target_file is not a regular file" >&2
    exit 1
  elif [ ! -e "$target_file" ]; then
    mkdir -p "$(dirname "$target_file")"
    cp "$source_file" "$target_file"
    record "$(sum_of "$source_file")" "$key"
  elif cmp -s "$source_file" "$target_file"; then
    record "$(sum_of "$target_file")" "$key"
  else
    was=$(previous_sum "$key")
    if [ -n "$was" ] && [ "$was" = "$(sum_of "$target_file")" ]; then
      cp "$source_file" "$target_file"
      record "$(sum_of "$source_file")" "$key"
      upgraded="$upgraded  $key
"
    else
      stage "$source_file" "$key"
      [ -z "$was" ] || record "$was" "$key"
    fi
  fi
}

for path in "$home" "$chronicle" "$chronicle/ADR" "$commands" "$runtime" \
  "$runtime/praukron" .claude .claude/commands .opencode .opencode/commands \
  .agents .agents/skills .agents/skills/praukron; do
  if [ -L "$target/$path" ] || { [ -e "$target/$path" ] && [ ! -d "$target/$path" ]; }; then
    echo "Cannot install into linked or non-directory path: $target/$path" >&2
    exit 1
  fi
done
if [ -L "$target/.prokron" ] && { [ -L "$target/$home/prokron" ] || { [ -e "$target/$home/prokron" ] && [ ! -f "$target/$home/prokron" ]; }; }; then
  echo "Cannot upgrade non-regular compatibility entry point." >&2
  exit 1
fi
if [ "$rename_install" -eq 1 ]; then
  # Check the source before moving it, including dangling links, and refuse
  # anything the ordinary installation preflight would refuse.
  for path in .prokron .prokron/chronicle .prokron/chronicle/ADR \
    .prokron/commands .prokron/runtime .prokron/runtime/praukron; do
    if [ -L "$target/$path" ] || { [ -e "$target/$path" ] && [ ! -d "$target/$path" ]; }; then
      echo "Cannot upgrade linked or non-directory path: $target/$path" >&2
      exit 1
    fi
  done
  for file in THESIS PHASES MODULES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL TRACE COORDINATION TECH_DEBT ASSUMPTIONS RESPONSES README; do
    path="$target/.prokron/chronicle/$file.md"
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
      echo "Cannot upgrade non-regular record: $path" >&2
      exit 1
    fi
  done
  if [ -L "$target/.prokron/prokron" ] || { [ -e "$target/.prokron/prokron" ] && [ ! -f "$target/.prokron/prokron" ]; }; then
    echo "Cannot upgrade non-regular entry point." >&2
    exit 1
  fi
  mv "$target/.prokron" "$target/.praukron"
  # Without the symlink privilege, Git Bash's `ln -s` copies the directory,
  # which would leave two chronicles. Make it fail instead, and fall back to a
  # junction, which Windows allows without privilege.
  if ! MSYS=winsymlinks:nativestrict ln -s .praukron "$target/.prokron" 2>/dev/null; then
    if ! { command -v cmd >/dev/null 2>&1 &&
      (CDPATH= cd "$target" && cmd //c "mklink /J .prokron .praukron" >/dev/null 2>&1); }; then
      echo "Could not create the .prokron alias; paths under .prokron/ no longer resolve." >&2
    fi
  fi
  printf 'Moved the Prokron installation to .praukron; historical paths remain available through an alias.\n'
fi
had_chronicle=0
[ ! -d "$target/$chronicle" ] || had_chronicle=1
# Records are the project's own: a template is only ever a starting point.
for file in THESIS PHASES MODULES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL TRACE COORDINATION TECH_DEBT ASSUMPTIONS RESPONSES; do
  copy_new "$source_dir/templates/chronicle/$file.md" "$target/$chronicle/$file.md"
done
copy_new "$source_dir/templates/chronicle/ADR/README.md" "$target/$chronicle/ADR/README.md"
install_guidance "$source_dir/templates/chronicle/README.md" "$chronicle/README.md"

for command in init work decide checkpoint resume baseline; do
  install_guidance "$source_dir/.praukron/commands/praukron-$command.md" \
    "$commands/praukron-$command.md"
  install_guidance "$source_dir/.claude/commands/praukron-$command.md" \
    ".claude/commands/praukron-$command.md"
  install_guidance "$source_dir/.opencode/commands/praukron-$command.md" \
    ".opencode/commands/praukron-$command.md"
done
install_guidance "$source_dir/.agents/skills/praukron/SKILL.md" \
  ".agents/skills/praukron/SKILL.md"

# The runtime is code, not a record: replace it on every install so a repository
# never runs a stale compiler against a current chronicle.
mkdir -p "$target/$runtime/praukron"
for module in __init__ layout model parse domain freeze validate analytics views index compile snapshot migrate \
  mermaid drawings mission review i18n dashboard retrieve codegraph respond mutate journal task task_update contract criterion acr batch archive archive_recovery serve cli; do
  cp "$source_dir/src/praukron/$module.py" "$target/$runtime/praukron/$module.py"
done
cp "$source_dir/VERSION" "$target/$runtime/VERSION"
cp "$source_dir/.praukron/praukron" "$target/$home/praukron"
chmod +x "$target/$home/praukron"
# Windows cannot run the POSIX entry above; PowerShell and cmd find this one
# beside it. It ships on every platform, so a repository shared between
# systems works on each.
cp "$source_dir/.praukron/praukron.cmd" "$target/$home/praukron.cmd"
if [ -L "$target/.prokron" ]; then
  # Compatibility entry: old launchers run the current runtime on the same
  # records, rather than continuing to run an obsolete copy.
  cat > "$target/$home/prokron" <<'COMPAT'
#!/bin/sh
exec "$(dirname "$0")/praukron" "$@"
COMPAT
  chmod +x "$target/$home/prokron"
fi

# A path is not a command. The launcher below goes on the reader's PATH so the
# command is `praukron`; it holds no logic of its own, walking up to the nearest
# project and running that project's runtime, so two repositories on different
# releases each keep their own (ADR-027).
#
# It creates no directory, edits no shell configuration, and never replaces a
# `praukron` it did not write. On Windows (Git Bash, MSYS, Cygwin) a
# `praukron.cmd` goes beside it, because PowerShell and cmd cannot run a POSIX
# script and pick the .cmd file instead.
windows=0
case $(uname -s 2>/dev/null || true) in MINGW*|MSYS*|CYGWIN*) windows=1 ;; esac
linked=
if [ "$link" -eq 1 ]; then
  for dir in "${HOME:-}/.local/bin" "${HOME:-}/bin" /usr/local/bin; do
    [ -n "$dir" ] && [ -d "$dir" ] && [ -w "$dir" ] || continue
    case ":${PATH:-}:" in *":$dir:"*) ;; *) continue ;; esac
    if [ -e "$dir/praukron" ] && ! grep -q 'praukron-launcher' "$dir/praukron" 2>/dev/null; then
      continue
    fi
    if [ "$windows" -eq 1 ] && [ -e "$dir/praukron.cmd" ] \
        && ! grep -q 'praukron-launcher' "$dir/praukron.cmd" 2>/dev/null; then
      continue
    fi
    cat > "$dir/praukron" <<'LAUNCHER'
#!/bin/sh
# praukron-launcher: run the nearest project's own copy of Praukron.
set -eu
dir=$(pwd -P)
while :; do
  if [ -x "$dir/.praukron/praukron" ]; then
    exec "$dir/.praukron/praukron" "$@"
  fi
  [ "$dir" != "/" ] || break
  dir=$(dirname "$dir")
done
echo "No .praukron/ in $(pwd) or any parent directory." >&2
echo "Install Praukron in this project with:" >&2
echo "  curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh | sh" >&2
exit 2
LAUNCHER
    chmod +x "$dir/praukron"
    if [ "$windows" -eq 1 ]; then
      # Batch files want CRLF line endings.
      awk '{ printf "%s\r\n", $0 }' > "$dir/praukron.cmd" <<'LAUNCHER'
@echo off
rem praukron-launcher: run the nearest project's own copy of Praukron.
setlocal
set "dir=%CD%"
:up
if exist "%dir%\.praukron\praukron.cmd" goto project
if exist "%dir%\.praukron\praukron" goto python
for %%D in ("%dir%\..") do set "parent=%%~fD"
if /i "%parent%"=="%dir%" goto none
set "dir=%parent%"
goto up
:project
call "%dir%\.praukron\praukron.cmd" %*
exit /b %errorlevel%
:python
rem Installations before 1.2.2 have no Windows entry of their own.
for %%P in ("py -3" python3 python) do (
  %%~P -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && (
    set "python=%%~P"
    goto run
  )
)
>&2 echo Praukron needs Python 3.9 or newer on PATH as py, python3 or python.
exit /b 127
:run
%python% "%dir%\.praukron\praukron" %*
exit /b %errorlevel%
:none
>&2 echo No .praukron\ in %CD% or any parent directory.
>&2 echo Install Praukron in this project from Git Bash with:
>&2 echo   curl -fsSL https://raw.githubusercontent.com/qomero/praukron/main/install.sh ^| sh
exit /b 2
LAUNCHER
    fi
    linked=$dir
    break
  done
fi

# Generated views are neither records nor guidance: they are reproducible from
# the chronicle, so an upgrade refreshes them rather than leaving a current
# runtime beside a page the previous release wrote (ADR-031). Only when the
# installation already has views and the chronicle holds work — never on a
# fresh install, where compiled state still appears only after compiling.
refreshed=
if [ -d "$target/$home/compiled" ] && grep -q '^## T-' "$target/$chronicle/TASKS.md" 2>/dev/null; then
  if (
    cd "$target" \
      && "$home/praukron" compile >/dev/null 2>&1 \
      && "$home/praukron" graph >/dev/null 2>&1 \
      && "$home/praukron" dashboard >/dev/null 2>&1
  ); then
    refreshed=done
  else
    refreshed=failed
  fi
fi

# A Praukron block shares a file with the project's own rules: AGENTS.md for
# every agent, CLAUDE.md for Claude Code. Only the text between the markers is
# ever compared or replaced; everything around it is left exactly as it was.
install_block() {
  # $1 file (relative), $2 source block, $3 start marker, $4 end marker,
  # $5 manifest key, $6 label for the output.
  file="$target/$1"
  # Retain the legacy marker and manifest key when upgrading an existing
  # block. Unmodified blocks can be replaced; edited blocks stay byte-exact.
  start_marker=$3
  end_marker=$4
  old_start=$(printf '%s' "$3" | sed 's/praukron/prokron/g')
  if [ -f "$file" ] && ! grep -Fq "$3" "$file" && grep -Fq "$old_start" "$file"; then
    start_marker=$old_start
    end_marker=$(printf '%s' "$4" | sed 's/praukron/prokron/g')
  fi
  set -- "$1" "$2" "$start_marker" "$end_marker" "$5" "$6"
  if [ ! -f "$file" ]; then
    cp "$2" "$file"
    record "$(sum_of "$2")" "$5"
  elif ! grep -Fq "$3" "$file"; then
    printf '\n' >> "$file"
    cat "$2" >> "$file"
    record "$(sum_of "$2")" "$5"
  else
    block="$target/$runtime/block.tmp"
    awk -v s="$3" -v e="$4" '
      index($0, s) && !seen { on = 1; seen = 1 }
      on { print }
      on && index($0, e) { on = 0; closed = 1 }
      END { if (!closed) exit 3 }
    ' "$file" > "$block" && complete=1 || complete=0
    if [ "$complete" -eq 1 ] && cmp -s "$2" "$block"; then
      record "$(sum_of "$block")" "$5"
    else
      was=$(previous_sum "$5")
      if [ "$complete" -eq 1 ] && [ -n "$was" ] && [ "$was" = "$(sum_of "$block")" ]; then
        awk -v s="$3" -v e="$4" -v src="$2" '
          index($0, s) && !done { while ((getline line < src) > 0) print line; skip = 1; done = 1; next }
          skip { if (index($0, e)) skip = 0; next }
          { print }
        ' "$file" > "$file.praukron-new"
        cat "$file.praukron-new" > "$file"
        rm -f "$file.praukron-new"
        record "$(sum_of "$2")" "$5"
        upgraded="$upgraded  $6
"
      else
        stage "$2" "$1"
        [ -z "$was" ] || record "$was" "$5"
      fi
    fi
    rm -f "$block"
  fi
}
install_block AGENTS.md "$source_dir/AGENTS.md" '<!-- project-praukron:start -->' \
  '<!-- project-praukron:end -->' 'AGENTS.md#praukron' 'AGENTS.md (Praukron block)'

# Claude Code reads CLAUDE.md, which imports AGENTS.md and carries the
# Claude-specific entry order (ADR-047).
if [ ! -f "$target/CLAUDE.md" ]; then
  printf '@AGENTS.md\n' > "$target/CLAUDE.md"
elif ! grep -Fxq '@AGENTS.md' "$target/CLAUDE.md"; then
  printf '\n@AGENTS.md\n' >> "$target/CLAUDE.md"
fi
if [ -f "$source_dir/templates/claude/CLAUDE.md" ]; then
  install_block CLAUDE.md "$source_dir/templates/claude/CLAUDE.md" \
    '<!-- project-praukron-claude:start -->' '<!-- project-praukron-claude:end -->' \
    'CLAUDE.md#praukron' 'CLAUDE.md (Praukron block)'
fi
printf '%s' "$recorded" > "$manifest"

# In a Git repository, append-only records merge by union, so two branches
# that each add a journal entry or an ADR do not conflict, and compiled output
# is marked generated (ADR-041). The block is Praukron's; the rest of the file
# is the project's and is left as it was.
if [ -e "$target/.git" ]; then
  attributes="$target/.gitattributes"
  if [ -L "$attributes" ] || { [ -e "$attributes" ] && [ ! -f "$attributes" ]; }; then
    echo "Cannot install: $attributes is not a regular file" >&2
    exit 1
  fi
  block_file="$target/$runtime/gitattributes.tmp"
  cat > "$block_file" <<'ATTRIBUTES'
# praukron:start — maintained by the Praukron installer (ADR-041)
.praukron/chronicle/JOURNAL.md merge=union
.praukron/chronicle/ADR/README.md merge=union
.praukron/compiled/** linguist-generated=true
.praukron/chronicle/INDEX.md linguist-generated=true
.praukron/praukron.cmd -text
# praukron:end
ATTRIBUTES
  if [ ! -f "$attributes" ]; then
    cp "$block_file" "$attributes"
  elif ! grep -Fq '# praukron:start' "$attributes" && ! grep -Fq '# prokron:start' "$attributes"; then
    [ ! -s "$attributes" ] || [ -z "$(tail -c 1 "$attributes")" ] || printf '\n' >> "$attributes"
    cat "$block_file" >> "$attributes"
  else
    awk -v src="$block_file" '
      /^# (praukron|prokron):start/ && !done { while ((getline line < src) > 0) print line; skip = 1; done = 1; next }
      skip { if (/^# (praukron|prokron):end/) skip = 0; next }
      { print }
    ' "$attributes" > "$attributes.praukron-new"
    cat "$attributes.praukron-new" > "$attributes"
    rm -f "$attributes.praukron-new"
  fi
  rm -f "$block_file"
fi


printf 'Praukron installed in %s\n' "$target"
if [ -n "$linked" ]; then
  printf 'The command is `praukron`, linked in %s\n' "$linked"
  if [ "$windows" -eq 1 ]; then
    printf 'PowerShell and cmd run %s\praukron.cmd; if they cannot find it,\n' \
      "$(cygpath -w "$linked" 2>/dev/null || printf '%s' "$linked")"
    printf 'add that folder to your Windows user PATH.\n'
  fi
  printf '\n'
elif [ "$link" -eq 1 ]; then
  printf 'Run it as `%s/praukron`, or put it on your PATH:\n' "$home"
  printf '  alias praukron="%s/praukron"\n\n' "$home"
else
  printf 'Run it as `%s/praukron`.\n\n' "$home"
fi
if grep -q '^## T-' "$target/prokron/TASKS.md" 2>/dev/null; then
  printf 'A v0.2 chronicle was found at prokron/ and the new layout reads %s/.\n' "$chronicle"
  printf 'Your records are intact. Move them with:\n'
  printf '  %s/praukron migrate           # shows what it would do\n' "$home"
  printf '  %s/praukron migrate --apply   # performs it\n\n' "$home"
elif grep -q '^## T-' "$target/.prokron/TASKS.md" 2>/dev/null; then
  printf 'A v0.1 chronicle was found in .prokron/ and the new layout cannot read it.\n'
  printf 'Your records are intact. Move them with:\n'
  printf '  %s/praukron migrate           # shows what it would do\n' "$home"
  printf '  %s/praukron migrate --apply   # performs it, archiving the originals\n\n' "$home"
fi
if [ "$had_chronicle" -eq 1 ]; then
  # Upgrades can introduce validation requirements even without compiled views.
  # Surface every finding (including the owner action for pre-VERIFY tasks),
  # without rewriting records or manufacturing verification on their behalf.
  if validation_output=$(cd "$target" && "$home/praukron" validate --quiet 2>&1); then
    :
  else
    printf 'Upgrade installed; the existing chronicle needs attention:\n%s\n\n' "$validation_output"
  fi
fi
if [ "$refreshed" = done ]; then
  printf 'Generated views were refreshed by the new runtime.\n\n'
elif [ "$refreshed" = failed ]; then
  printf 'Generated views could not be refreshed and are still the previous\n'
  printf 'version'"'"'s. Your records are untouched. Fix what `%s/praukron validate`\n' "$home"
  printf 'reports, then run:\n'
  printf '  %s/praukron compile && %s/praukron graph && %s/praukron dashboard\n\n' "$home" "$home" "$home"
fi
if [ -n "$upgraded" ]; then
  printf 'Guidance upgraded, unedited since the last install:\n%s\n' "$upgraded"
fi
if [ -n "$staged" ]; then
  printf 'Guidance kept because it was edited; the new version is beside it in %s/upgrade/:\n%s' "$home" "$staged"
  printf 'Merge what you want, then delete %s/upgrade/.\n\n' "$home"
fi
if [ "$had_chronicle" -eq 1 ]; then
  printf 'Existing chronicle preserved. Resume in your agent chat:\n'
  printf '  Codex:       $praukron resume\n'
  printf '  Claude Code / OpenCode: /praukron-resume\n'
  printf '  Other:       Read AGENTS.md, then follow %s/praukron-resume.md.\n' "$commands"
  exit 0
fi
printf 'Start in your agent chat:\n'
printf '  Codex:       $praukron init %s\n' "$mode"
printf '  Claude Code: /praukron-init %s\n' "$mode"
printf '  OpenCode:    /praukron-init %s\n' "$mode"
printf '  Other:       Read AGENTS.md, then follow %s/praukron-init.md in %s mode.\n' \
  "$commands" "$mode"
