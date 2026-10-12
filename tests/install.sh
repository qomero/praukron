#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
fixture=$(mktemp -d "${TMPDIR:-/tmp}/praukron-install.XXXXXX")
# A directory with no Praukron above it, for checking what happens outside a
# project. It cannot live under the fixture, which is itself an installation.
outside=$(mktemp -d "${TMPDIR:-/tmp}/praukron-outside.XXXXXX")
trap 'rm -rf "$fixture" "$outside"' EXIT HUP INT TERM

printf '# Keep agent rules\n' > "$fixture/AGENTS.md"
printf '# Keep Claude rules\n' > "$fixture/CLAUDE.md"
"$root/install.sh" existing "$fixture" --no-link > "$fixture/output"

for file in README THESIS PHASES MODULES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL TRACE TECH_DEBT; do
  test -f "$fixture/.praukron/chronicle/$file.md"
done
# Every installed surface describes the same hierarchy (ADR-035).
for surface in .praukron/chronicle/README.md .praukron/chronicle/PHASES.md \
    .praukron/chronicle/MODULES.md AGENTS.md .agents/skills/praukron/SKILL.md; do
  grep -qi 'module' "$fixture/$surface" || { echo "no module hierarchy in $surface" >&2; exit 1; }
done
test -f "$fixture/.praukron/chronicle/ADR/README.md"
# Authority never lands in the compiled directory.
for file in PHASES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL DECISIONS; do
  test ! -e "$fixture/.praukron/compiled/$file.md"
done
test ! -e "$fixture/.praukron/chronicle/DECISIONS.md"
test -f "$fixture/.agents/skills/praukron/SKILL.md"

# Everything Praukron chooses for itself lives in one directory. What is left
# outside it is only what an agent host reads by fixed address.
installed=$(cd "$fixture" && ls -A | LC_ALL=C sort | tr '\n' ' ')
expected='.agents .claude .opencode .praukron AGENTS.md CLAUDE.md output '
test "$installed" = "$expected" || {
  echo "Install placed unexpected entries at the root: $installed" >&2
  exit 1
}

# The runtime ships with the chronicle and runs in the installed repository.
test -x "$fixture/.praukron/praukron"
test -f "$fixture/.praukron/runtime/praukron/cli.py"
# Every runtime module must be installed, or the tool breaks in the target repo.
for module in "$root"/src/praukron/*.py; do
  test -f "$fixture/.praukron/runtime/praukron/$(basename "$module")"
done
"$fixture/.praukron/praukron" --version | grep -Fq "praukron $(cat "$root/VERSION")"
# The entry chooses a working interpreter instead of trusting `python3`: on
# Windows that name is often a Microsoft Store placeholder that only fails.
real_python=$(for py in python3 python; do "$py" -c 'import sys; print(sys.executable)' 2>/dev/null && break; done)
mkdir -p "$fixture/pybin" "$fixture/nopy"
printf '#!/bin/sh\nexit 49\n' > "$fixture/pybin/python3"
cp "$fixture/pybin/python3" "$fixture/nopy/python3"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$real_python" > "$fixture/pybin/python"
chmod +x "$fixture/pybin/python3" "$fixture/nopy/python3" "$fixture/pybin/python"
PATH="$fixture/pybin:/usr/bin:/bin" "$fixture/.praukron/praukron" --version \
  | grep -Fq "praukron $(cat "$root/VERSION")"
# With no usable Python it says so, rather than failing obscurely.
status=0
PATH="$fixture/nopy" "$fixture/.praukron/praukron" --version 2> "$fixture/nopy/err" || status=$?
test "$status" -eq 127
grep -Fq 'Python 3.9 or newer' "$fixture/nopy/err"
# PowerShell and cmd cannot run the POSIX entry; a batch file beside it, with
# the CRLF endings batch files need, kept so by the attribute block below.
test -f "$fixture/.praukron/praukron.cmd"
test "$(tr -d '\r' < "$fixture/.praukron/praukron.cmd" | wc -c)" -lt "$(wc -c < "$fixture/.praukron/praukron.cmd")"
grep -Fq '%~dp0praukron' "$fixture/.praukron/praukron.cmd"
# The runtime reports its own version, never the tracked project's.
printf '9.9.9\n' > "$fixture/VERSION"
"$fixture/.praukron/praukron" --version | grep -Fvq '9.9.9'
rm "$fixture/VERSION"
(cd "$fixture" && .praukron/praukron validate) | grep -Fq 'consistent'
(cd "$fixture" && .praukron/praukron compile) >/dev/null
test -f "$fixture/.praukron/compiled/project.json"
(cd "$fixture" && .praukron/praukron status) | grep -Fq 'tasks'
# Compiled state is disposable: deleting it loses nothing.
cp -R "$fixture/.praukron/compiled" "$fixture/first-compile"
rm -rf "$fixture/.praukron/compiled"
(cd "$fixture" && .praukron/praukron compile) >/dev/null
for file in project.json README.md STATE.md TASK_GRAPH.md; do
  cmp "$fixture/first-compile/$file" "$fixture/.praukron/compiled/$file"
done
rm -rf "$fixture/first-compile"

for command in init work decide checkpoint resume baseline; do
  test -f "$fixture/.praukron/commands/praukron-$command.md"
  test -f "$fixture/.claude/commands/praukron-$command.md"
  test -f "$fixture/.opencode/commands/praukron-$command.md"
done
# Initialization records nothing about the past on its own; the baseline is a
# separate workflow the owner has to ask for (ADR-037).
if ls "$fixture/.praukron/chronicle/ADR/"ADR-*.md >/dev/null 2>&1; then
  echo "a fresh install must contain no ADR" >&2
  exit 1
fi
grep -Fq 'only when the project owner explicitly asks' \
  "$fixture/.praukron/commands/praukron-baseline.md"
grep -Fq 'initialization never runs it' "$fixture/.praukron/commands/praukron-init.md"
grep -Fq '/praukron-baseline' "$fixture/AGENTS.md"
grep -Fq 'baseline' "$fixture/.agents/skills/praukron/SKILL.md"
grep -Fq '.praukron/commands/praukron-baseline.md' "$fixture/.claude/commands/praukron-baseline.md"
grep -Fq 'Origin: RECONSTRUCTED' "$fixture/.praukron/chronicle/ADR/README.md"
# A host command file must name a document that was actually installed.
grep -Fq '.praukron/commands/praukron-work.md' "$fixture/.claude/commands/praukron-work.md"
grep -Fq '# Keep agent rules' "$fixture/AGENTS.md"
grep -Fq '# Keep Claude rules' "$fixture/CLAUDE.md"
# Both entry files make the index the first project-context read (ADR-047).
grep -Fq '1. Read `.praukron/chronicle/INDEX.md`.' "$fixture/AGENTS.md"
grep -Fq 'Read `.praukron/chronicle/INDEX.md` before anything else' "$fixture/CLAUDE.md"
grep -Fxq '@AGENTS.md' "$fixture/CLAUDE.md"
test "$(grep -Fc '<!-- project-praukron-claude:start -->' "$fixture/CLAUDE.md")" -eq 1
grep -Fq 'do not wait for a Praukron command' "$fixture/AGENTS.md"
grep -Fq 'context, token, time, session, rate, or quota limit' "$fixture/AGENTS.md"
grep -Fq 'Record every new work request as a task before implementation' \
  "$fixture/.praukron/chronicle/README.md"
grep -Fq 'Append an ADR as soon as a material choice is made' \
  "$fixture/.praukron/chronicle/README.md"
grep -Fq 'Do not edit' "$fixture/.praukron/compiled/README.md"
grep -Fq 'Given <precondition>' "$fixture/.praukron/chronicle/ACCEPTANCE.md"
grep -Fq 'ACCEPTANCE_FAILURE' "$fixture/.praukron/chronicle/ACCEPTANCE.md"
grep -Fq 'Exit authority' "$fixture/.praukron/chronicle/PHASES.md"
# A project can name itself; the field ships present and empty so the next
# project is shown it exists without being made to fill it (ADR-030).
grep -Eq '^Project:' "$fixture/.praukron/chronicle/PHASES.md"
grep -Fq '$praukron init existing' "$fixture/output"
grep -Fq '/praukron-init existing' "$fixture/output"
grep -Fq '.praukron/commands/praukron-init.md in existing mode' "$fixture/output"

grep -Fq '$ARGUMENTS' "$fixture/.opencode/commands/praukron-decide.md"

# Both entry modes must preserve every record and customized instruction.
for file in README PHASES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL; do
  printf '\nKEEP %s\n' "$file" >> "$fixture/.praukron/chronicle/$file.md"
done
printf '\nKEEP ADR\n' >> "$fixture/.praukron/chronicle/ADR/README.md"
printf '\nCUSTOM\n' >> "$fixture/.praukron/commands/praukron-work.md"
cp -R "$fixture/.praukron/chronicle" "$fixture/saved"
cp "$fixture/.praukron/commands/praukron-work.md" "$fixture/saved-work"
cp "$fixture/AGENTS.md" "$fixture/saved-agents"
cp "$fixture/CLAUDE.md" "$fixture/saved-claude"
for mode in existing new; do
  "$root/install.sh" "$mode" "$fixture" --no-link > "$fixture/output"
  for file in README PHASES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL; do
    cmp "$fixture/saved/$file.md" "$fixture/.praukron/chronicle/$file.md"
  done
  cmp "$fixture/saved/ADR/README.md" "$fixture/.praukron/chronicle/ADR/README.md"
  cmp "$fixture/saved-work" "$fixture/.praukron/commands/praukron-work.md"
  cmp "$fixture/saved-agents" "$fixture/AGENTS.md"
  cmp "$fixture/saved-claude" "$fixture/CLAUDE.md"
  grep -Fq 'Guidance kept because it was edited' "$fixture/output"
  grep -Fq '.praukron/commands/praukron-work.md' "$fixture/output"
  test -f "$fixture/.praukron/upgrade/.praukron/commands/praukron-work.md"
  grep -Fq '$praukron resume' "$fixture/output"
done
test "$(grep -Fc '<!-- project-praukron:start -->' "$fixture/AGENTS.md")" -eq 1
test "$(grep -Fxc '@AGENTS.md' "$fixture/CLAUDE.md")" -eq 1

# Guidance keeps up with the runtime (ADR-040). A release that changes its
# guidance replaces what nobody edited and stages what somebody did.
make_source() {
  # $1: a copy of this source whose guidance says something new.
  mkdir -p "$1"
  (cd "$root" && tar -cf - --exclude ./.git .) | (cd "$1" && tar -xf -)
  for file in "$1"/.praukron/commands/praukron-*.md "$1"/.claude/commands/praukron-*.md \
    "$1"/.opencode/commands/praukron-*.md "$1/.agents/skills/praukron/SKILL.md" \
    "$1/templates/chronicle/README.md"; do
    printf '\nNEW GUIDANCE %s\n' "$2" >> "$file"
  done
  awk -v tag="$2" '/<!-- project-praukron:end -->/ { print "NEW BLOCK " tag } { print }' \
    "$1/AGENTS.md" > "$1/AGENTS.tmp" && mv "$1/AGENTS.tmp" "$1/AGENTS.md"
  awk -v tag="$2" '/<!-- project-praukron-claude:end -->/ { print "NEW CLAUDE " tag } { print }' \
    "$1/templates/claude/CLAUDE.md" > "$1/CLAUDE.tmp" && mv "$1/CLAUDE.tmp" "$1/templates/claude/CLAUDE.md"
}
outside_block() {
  awk '/<!-- project-praukron:start -->/ { on = 1 } !on { print } /<!-- project-praukron:end -->/ { on = 0 }' "$1"
}
records_of() {
  for file in PHASES TASKS ACCEPTANCE INTENT HANDOFF JOURNAL; do
    cksum < "$1/.praukron/chronicle/$file.md"
  done
  cksum < "$1/.praukron/chronicle/ADR/README.md"
}
guide="$fixture/guidance"
mkdir -p "$guide"
printf '# Before\n' > "$guide/AGENTS.md"
"$root/install.sh" "$guide" --no-link >/dev/null
test -f "$guide/.praukron/runtime/GUIDANCE"
printf '\n# After\n' >> "$guide/AGENTS.md"
printf '\n## T-001: Real work\n- Status: TODO\n- Phase: P-NONE\n- Validation: UNTESTED\n- Dependencies: none\n- AC: AC-T-001\n- Evidence:\n- Governed by: none\n' >> "$guide/.praukron/chronicle/TASKS.md"
printf '\nCUSTOM WORK\n' >> "$guide/.praukron/commands/praukron-work.md"
cp "$guide/.praukron/commands/praukron-work.md" "$fixture/custom-work"
outside_block "$guide/AGENTS.md" > "$fixture/outside-before"
records_of "$guide" > "$fixture/records-before"
make_source "$fixture/source-two" two
"$fixture/source-two/install.sh" "$guide" --no-link > "$guide/output"
# Unedited guidance is replaced, and named.
for command in init decide checkpoint resume baseline; do
  cmp "$fixture/source-two/.praukron/commands/praukron-$command.md" "$guide/.praukron/commands/praukron-$command.md"
done
for command in init work decide checkpoint resume baseline; do
  cmp "$fixture/source-two/.claude/commands/praukron-$command.md" "$guide/.claude/commands/praukron-$command.md"
  cmp "$fixture/source-two/.opencode/commands/praukron-$command.md" "$guide/.opencode/commands/praukron-$command.md"
done
cmp "$fixture/source-two/.agents/skills/praukron/SKILL.md" "$guide/.agents/skills/praukron/SKILL.md"
cmp "$fixture/source-two/templates/chronicle/README.md" "$guide/.praukron/chronicle/README.md"
grep -Fq 'NEW BLOCK two' "$guide/AGENTS.md"
grep -Fq 'NEW CLAUDE two' "$guide/CLAUDE.md"
grep -Fq 'CLAUDE.md (Praukron block)' "$guide/output"
test "$(grep -Fc '<!-- project-praukron:start -->' "$guide/AGENTS.md")" -eq 1
grep -Fq 'Guidance upgraded' "$guide/output"
grep -Fq '.claude/commands/praukron-init.md' "$guide/output"
grep -Fq 'AGENTS.md (Praukron block)' "$guide/output"
# Only the block moved; the project's own rules around it did not.
outside_block "$guide/AGENTS.md" | cmp "$fixture/outside-before" -
# Edited guidance stays exactly as it was, with the new version beside it.
cmp "$fixture/custom-work" "$guide/.praukron/commands/praukron-work.md"
cmp "$fixture/source-two/.praukron/commands/praukron-work.md" \
  "$guide/.praukron/upgrade/.praukron/commands/praukron-work.md"
grep -Fq 'Guidance kept because it was edited' "$guide/output"
records_of "$guide" | cmp "$fixture/records-before" -

# An edited Praukron block is kept and staged like any other edited guidance.
awk '/<!-- project-praukron:end -->/ { print "MY OWN RULE" } { print }' "$guide/AGENTS.md" > "$guide/AGENTS.tmp"
mv "$guide/AGENTS.tmp" "$guide/AGENTS.md"
cp "$guide/AGENTS.md" "$fixture/custom-agents"
make_source "$fixture/source-three" three
"$fixture/source-three/install.sh" "$guide" --no-link > "$guide/output"
cmp "$fixture/custom-agents" "$guide/AGENTS.md"
cmp "$fixture/source-three/AGENTS.md" "$guide/.praukron/upgrade/AGENTS.md"
grep -Fq 'NEW GUIDANCE three' "$guide/.praukron/commands/praukron-init.md"
records_of "$guide" | cmp "$fixture/records-before" -

# With no record of what was installed, a difference can only mean an edit.
rm "$guide/.praukron/runtime/GUIDANCE"
cp "$guide/.praukron/commands/praukron-init.md" "$fixture/unrecorded-init"
make_source "$fixture/source-four" four
"$fixture/source-four/install.sh" "$guide" --no-link > "$guide/output"
cmp "$fixture/unrecorded-init" "$guide/.praukron/commands/praukron-init.md"
cmp "$fixture/source-four/.praukron/commands/praukron-init.md" \
  "$guide/.praukron/upgrade/.praukron/commands/praukron-init.md"
records_of "$guide" | cmp "$fixture/records-before" -

# An older runtime does not replace a newer one unless asked to.
printf '999.0.0\n' > "$guide/.praukron/runtime/VERSION"
snapshot() { (cd "$1" && find . -type f ! -name output -exec cksum {} + | LC_ALL=C sort); }
snapshot "$guide" > "$fixture/before-downgrade"
if "$root/install.sh" "$guide" --no-link > "$guide/output" 2>&1; then
  echo "an older runtime replaced a newer one" >&2
  exit 1
fi
grep -Fq 'Refusing to install' "$guide/output"
snapshot "$guide" | cmp "$fixture/before-downgrade" -
"$root/install.sh" "$guide" --no-link --allow-downgrade >/dev/null
cmp "$root/VERSION" "$guide/.praukron/runtime/VERSION"
records_of "$guide" | cmp "$fixture/records-before" -
# `--ref` names what to download; it means nothing to a local source.
if "$root/install.sh" "$guide" --no-link --ref v0.4.6 >/dev/null 2>&1; then
  exit 1
fi

# A Git repository gets one marked .gitattributes block, and parallel appends
# to the journal and the ADR index merge without conflict (ADR-041).
test ! -e "$fixture/.gitattributes"
repo="$fixture/gitrepo"
mkdir -p "$repo"
( cd "$repo" && git init -q -b main )
printf '*.png binary\n' > "$repo/.gitattributes"
"$root/install.sh" "$repo" --no-link >/dev/null
"$root/install.sh" "$repo" --no-link >/dev/null
test "$(grep -c '^# praukron:start' "$repo/.gitattributes")" -eq 1
grep -Fxq '*.png binary' "$repo/.gitattributes"
grep -Fxq '.praukron/chronicle/JOURNAL.md merge=union' "$repo/.gitattributes"
grep -Fxq '.praukron/chronicle/ADR/README.md merge=union' "$repo/.gitattributes"
grep -Fxq '.praukron/compiled/** linguist-generated=true' "$repo/.gitattributes"
grep -Fxq '.praukron/praukron.cmd -text' "$repo/.gitattributes"
g() { git -C "$repo" -c user.name=test -c user.email=test@example.com "$@"; }
g add -A && g commit -qm base
g checkout -qb one
printf '\n## 2026-01-01 — T-ONE\n\n- Did: one.\n' >> "$repo/.praukron/chronicle/JOURNAL.md"
printf -- '- [ADR-001](ADR-001.md) — One (ACCEPTED)\n' >> "$repo/.praukron/chronicle/ADR/README.md"
g commit -qam one
g checkout -q main
g checkout -qb two
printf '\n## 2026-01-01 — T-TWO\n\n- Did: two.\n' >> "$repo/.praukron/chronicle/JOURNAL.md"
printf -- '- [ADR-002](ADR-002.md) — Two (ACCEPTED)\n' >> "$repo/.praukron/chronicle/ADR/README.md"
g commit -qam two
g merge -q --no-edit one
grep -Fq 'T-ONE' "$repo/.praukron/chronicle/JOURNAL.md"
grep -Fq 'T-TWO' "$repo/.praukron/chronicle/JOURNAL.md"
grep -Fq 'ADR-001' "$repo/.praukron/chronicle/ADR/README.md"
grep -Fq 'ADR-002' "$repo/.praukron/chronicle/ADR/README.md"
if grep -q '^<<<<<<<' "$repo/.praukron/chronicle/JOURNAL.md" "$repo/.praukron/chronicle/ADR/README.md"; then
  exit 1
fi

mkdir "$fixture/new project"
"$root/install.sh" new "$fixture/new project" --no-link > "$fixture/output-new"
grep -Fq '$praukron init new' "$fixture/output-new"

# An interrupted installation can be repaired without resetting existing tasks.
mkdir -p "$fixture/partial/.praukron/chronicle"
cp "$fixture/saved/TASKS.md" "$fixture/partial/.praukron/chronicle/TASKS.md"
"$root/install.sh" existing "$fixture/partial" --no-link >/dev/null
cmp "$fixture/saved/TASKS.md" "$fixture/partial/.praukron/chronicle/TASKS.md"
for file in README PHASES ACCEPTANCE INTENT HANDOFF JOURNAL; do
  cmp "$root/templates/chronicle/$file.md" "$fixture/partial/.praukron/chronicle/$file.md"
done
cmp "$root/templates/chronicle/ADR/README.md" \
  "$fixture/partial/.praukron/chronicle/ADR/README.md"
# compiled state appears only after compiling
test ! -d "$fixture/partial/.praukron/compiled"

# Upgrading over existing generated views must refresh them. Every other case
# in this suite installs into a directory that has none, which is exactly why
# this defect shipped: the upgrade path had never been exercised (ADR-031).
upgrade="$fixture/upgrade"
mkdir -p "$upgrade"
"$root/install.sh" "$upgrade" --no-link >/dev/null
printf '\n## T-001: Real work\n- Status: DONE\n- Phase: P-NONE\n- Validation: SYNTHETIC\n- Dependencies: none\n- AC: AC-T-001\n- Evidence: recorded\n- Governed by: none\n' >> "$upgrade/.praukron/chronicle/TASKS.md"
printf '\n## AC-T-001 — Real work\n\n- `AC-T-001-01` — It exists. `INSPECTION` · `PASS`\n  - Evidence: it does.\n' >> "$upgrade/.praukron/chronicle/ACCEPTANCE.md"
( cd "$upgrade" && .praukron/praukron compile >/dev/null && .praukron/praukron dashboard >/dev/null )
# Stand in for output written by an older release.
printf 'STALE\n' > "$upgrade/.praukron/compiled/dashboard.html"
printf 'STALE\n' > "$upgrade/.praukron/compiled/STATE.md"
"$root/install.sh" "$upgrade" --no-link > "$upgrade/output"
grep -Fq 'Generated views were refreshed' "$upgrade/output"
grep -Fvq 'STALE' "$upgrade/.praukron/compiled/dashboard.html"
grep -Fvq 'STALE' "$upgrade/.praukron/compiled/STATE.md"
grep -Fq 'generatorVersion' "$upgrade/.praukron/compiled/project.json"
# The refreshed views must come from the version that just installed.
grep -Fq "\"generatorVersion\": \"$(cat "$root/VERSION")\"" "$upgrade/.praukron/compiled/project.json"

# Authority that does not validate must not fail the installation, and must
# not leave the reader guessing what to do.
printf '\n## T-BROKEN: Bad\n- Status: DONE\n- Phase: P-NONE\n- Validation: SYNTHETIC\n- Dependencies: T-GHOST\n- AC: AC-T-NOPE\n- Evidence: x\n- Governed by: none\n' >> "$upgrade/.praukron/chronicle/TASKS.md"
"$root/install.sh" "$upgrade" --no-link > "$upgrade/output-broken"
grep -Fq 'could not be refreshed' "$upgrade/output-broken"
grep -Fq 'praukron compile' "$upgrade/output-broken"
grep -Fq 'T-BROKEN' "$upgrade/.praukron/chronicle/TASKS.md"

# A stale installation is visible to someone who never reinstalls.
stale="$fixture/stale"
mkdir -p "$stale"
"$root/install.sh" "$stale" --no-link >/dev/null
printf '\n## T-001: Real work\n- Status: DONE\n- Phase: P-NONE\n- Validation: SYNTHETIC\n- Dependencies: none\n- AC: AC-T-001\n- Evidence: recorded\n- Governed by: none\n' >> "$stale/.praukron/chronicle/TASKS.md"
printf '\n## AC-T-001 — Real work\n\n- `AC-T-001-01` — It exists. `INSPECTION` · `PASS`\n  - Evidence: it does.\n' >> "$stale/.praukron/chronicle/ACCEPTANCE.md"
( cd "$stale" && .praukron/praukron compile >/dev/null )
sed 's/"generatorVersion": "[^"]*"/"generatorVersion": "0.0.1"/' \
  "$stale/.praukron/compiled/project.json" > "$stale/patched.json"
mv "$stale/patched.json" "$stale/.praukron/compiled/project.json"
( cd "$stale" && .praukron/praukron status ) | grep -Fq 'written by praukron 0.0.1'

# Upgrading a v0.2 project must relocate its chronicle without rewriting it.
legacy2="$fixture/v02"
mkdir -p "$legacy2/prokron/ADR" "$legacy2/.prokron" "$legacy2/commands" "$legacy2/bin"
mkdir -p "$legacy2/.prokron-runtime/prokron" "$legacy2/.claude/commands"
cp "$root"/templates/chronicle/*.md "$legacy2/prokron/"
cp "$root/templates/chronicle/ADR/README.md" "$legacy2/prokron/ADR/README.md"
printf '\n## T-V02-01: Carry a record across the move\n- Status: TODO\n- Phase: P-NONE\n- Validation: UNTESTED\n- Dependencies: none\n- AC: AC-T-V02-01\n- Evidence: —\n- Governed by: ADR-001\n' >> "$legacy2/prokron/TASKS.md"
printf '\n## AC-T-V02-01 — Carry a record across the move\n\n- `AC-T-V02-01-01` — It survived. `INSPECTION` · `NOT_RUN`\n  - Evidence: —\n' >> "$legacy2/prokron/ACCEPTANCE.md"
printf '# ADR-001: Keep it\n- Date: 2026-01-01\n- Status: ACCEPTED\n- Decision: Keep it.\n' > "$legacy2/prokron/ADR/ADR-001.md"
printf 'stale\n' > "$legacy2/.prokron/STATE.md"
printf 'stale\n' > "$legacy2/.prokron/task-graph.mmd"
printf 'Follow `commands/prokron-work.md`.\n' > "$legacy2/.claude/commands/prokron-work.md"
printf 'CUSTOM WORK\n' > "$legacy2/commands/prokron-work.md"
printf 'old\n' > "$legacy2/.prokron-runtime/prokron/cli.py"
printf 'old\n' > "$legacy2/bin/prokron"
cp "$legacy2/prokron/TASKS.md" "$fixture/saved-v02-tasks"
"$root/install.sh" existing "$legacy2" --no-link > "$legacy2/output"
grep -Fq 'A v0.2 chronicle was found' "$legacy2/output"
( cd "$legacy2" && .praukron/praukron status ) | grep -Fq 'Run `praukron migrate`'
( cd "$legacy2" && .praukron/praukron migrate ) | grep -Fq 'Nothing was changed'
test -f "$legacy2/prokron/TASKS.md"
( cd "$legacy2" && .praukron/praukron migrate --apply ) | grep -Fq 'Relocated'
# A relocation moves records; it never rewrites them.
cmp "$fixture/saved-v02-tasks" "$legacy2/.praukron/chronicle/TASKS.md"
test -f "$legacy2/.praukron/chronicle/ADR/ADR-001.md"
grep -Fq 'CUSTOM WORK' "$legacy2/.praukron/commands/praukron-work.md"
grep -Fq '.praukron/commands/praukron-work.md' "$legacy2/.claude/commands/prokron-work.md"
test ! -e "$legacy2/prokron"
test ! -e "$legacy2/commands"
test ! -e "$legacy2/bin"
test ! -e "$legacy2/.prokron-runtime"
test ! -e "$legacy2/.prokron/STATE.md"
test ! -e "$legacy2/.prokron/task-graph.mmd"
( cd "$legacy2" && .praukron/praukron validate ) | grep -Fq 'consistent'
( cd "$legacy2" && .praukron/praukron explain T-V02-01 ) | grep -Fq 'Carry a record'

# Upgrading a v0.1 project must not strand its chronicle.
legacy="$fixture/legacy"
mkdir -p "$legacy/.prokron"
( cd "$legacy" && git init -q )
printf '# Tasks\n\n## T-OLD-01: Ship it\n- Status: DONE\n- Validation: SYNTHETIC\n- Dependencies: none\n- Acceptance: It ships.\n- Evidence: It shipped.\n- Governed by: ADR-001\n' > "$legacy/.prokron/TASKS.md"
printf '# Decisions\n\n## ADR-001: Do it\n- Date: 2026-01-01\n- Status: ACCEPTED\n- Decision: Do it.\n' > "$legacy/.prokron/DECISIONS.md"
printf '# State\n\n## Next\n- Call the vendor.\n' > "$legacy/.prokron/STATE.md"
"$root/install.sh" existing "$legacy" --no-link > "$legacy/output"
grep -Fq 'praukron migrate' "$legacy/output"
( cd "$legacy" && .praukron/praukron status ) | grep -Fq 'praukron migrate'
( cd "$legacy" && .praukron/praukron migrate ) | grep -Fq 'Nothing was changed'
test -f "$legacy/.prokron/TASKS.md"
( cd "$legacy" && .praukron/praukron migrate --apply ) | grep -Fq 'Authority validates'
grep -Fq 'T-OLD-01' "$legacy/.praukron/chronicle/TASKS.md"
grep -Fq 'It ships.' "$legacy/.praukron/chronicle/ACCEPTANCE.md"
grep -Fq 'Call the vendor' "$legacy/.praukron/chronicle/HANDOFF.md"
test -f "$legacy/.praukron/chronicle/ADR/ADR-001.md"
ls -a "$legacy" | grep -q '^\.prokron-v0\.1-backup-'
( cd "$legacy" && .praukron/praukron validate ) | grep -Fq 'consistent'

# Do not follow links into other projects, including dangling links.
for path in AGENTS.md CLAUDE.md .praukron .agents .claude .opencode; do
  mkdir "$fixture/link-test"
  ln -s "$fixture/missing" "$fixture/link-test/$path"
  if "$root/install.sh" existing "$fixture/link-test" --no-link >/dev/null 2>&1; then
    echo "Unexpected success for symlink: $path" >&2
    exit 1
  fi
  test ! -e "$fixture/missing"
  rm "$fixture/link-test/$path"
  rmdir "$fixture/link-test"
done

# A misspelled mode is not silently accepted as a target directory.
if "$root/install.sh" invalid "$fixture" --no-link >/dev/null 2>&1; then
  exit 1
fi
if "$root/install.sh" invalid --no-link >/dev/null 2>&1; then
  exit 1
fi
if "$root/install.sh" existing "$fixture/nonexistent" --no-link >/dev/null 2>&1; then
  exit 1
fi
if "$root/install.sh" --nonsense "$fixture" >/dev/null 2>&1; then
  exit 1
fi

# Installing takes no arguments: the mode almost everyone wants is the default.
plain="$fixture/plain"
mkdir "$plain"
( cd "$plain" && "$root/install.sh" --no-link > output )
grep -Fq '$praukron init existing' "$plain/output"
test -f "$plain/.praukron/chronicle/TASKS.md"
test -x "$plain/.praukron/praukron"

# The command goes on PATH, in a directory that already exists and is already
# on it. Nothing is created, and no shell configuration is touched.
machine="$fixture/machine"
mkdir -p "$machine/.local/bin" "$machine/project/nested/deeper"
printf 'KEEP\n' > "$machine/.zshrc"
(
  export HOME="$machine"
  export PATH="$machine/.local/bin:/usr/bin:/bin"
  "$root/install.sh" "$machine/project" > "$machine/output"
)
grep -Fq 'The command is `praukron`' "$machine/output"
test -x "$machine/.local/bin/praukron"
grep -Fq 'praukron-launcher' "$machine/.local/bin/praukron"
cmp "$machine/.zshrc" - <<'RC'
KEEP
RC
# Off Windows there is no batch launcher to leave behind.
test ! -e "$machine/.local/bin/praukron.cmd"
# It runs from anywhere inside the project, not only its root.
(
  export PATH="$machine/.local/bin:$PATH"
  cd "$machine/project/nested/deeper" && praukron validate
) | grep -Fq 'consistent'
# And it runs that project's own runtime, not a global copy.
printf 'echo PROJECT-COPY\n' > "$machine/project/.praukron/praukron"
chmod +x "$machine/project/.praukron/praukron"
(
  export PATH="$machine/.local/bin:$PATH"
  cd "$machine/project/nested" && praukron anything
) | grep -Fq 'PROJECT-COPY'
# Outside any project it explains itself rather than failing obscurely.
(
  export PATH="$machine/.local/bin:$PATH"
  cd "$outside" && praukron status 2>&1 || true
) | grep -Fq 'No .praukron/'
# A `praukron` someone else put on PATH is never replaced, and with nowhere
# else to link, the installer says how to run it instead.
other="$fixture/other"
mkdir -p "$other/.local/bin" "$other/project"
printf '#!/bin/sh\necho NOT OURS\n' > "$other/.local/bin/praukron"
chmod +x "$other/.local/bin/praukron"
(
  export HOME="$other"
  export PATH="$other/.local/bin:/usr/bin:/bin"
  "$root/install.sh" "$other/project" > "$other/output"
)
grep -Fq 'NOT OURS' "$other/.local/bin/praukron"
grep -Fq 'alias praukron=' "$other/output"
# On Windows (Git Bash, MSYS, Cygwin) PowerShell and cmd need a batch launcher
# beside the POSIX one, and one somebody else wrote is never replaced.
win="$fixture/win"
mkdir -p "$win/.local/bin" "$win/fake" "$win/project" "$win/taken/.local/bin"
printf '#!/bin/sh\necho MINGW64_NT-10.0-26200\n' > "$win/fake/uname"
chmod +x "$win/fake/uname"
(
  export HOME="$win"
  export PATH="$win/fake:$win/.local/bin:/usr/bin:/bin"
  "$root/install.sh" "$win/project" > "$win/output"
)
grep -Fq 'praukron-launcher' "$win/.local/bin/praukron"
grep -Fq 'praukron-launcher' "$win/.local/bin/praukron.cmd"
grep -Fq 'praukron.cmd' "$win/.local/bin/praukron.cmd"
test "$(tr -d '\r' < "$win/.local/bin/praukron.cmd" | wc -c)" -lt "$(wc -c < "$win/.local/bin/praukron.cmd")"
grep -Fq 'Windows user PATH' "$win/output"
printf '@echo NOT OURS\r\n' > "$win/taken/.local/bin/praukron.cmd"
(
  export HOME="$win/taken"
  export PATH="$win/fake:$win/taken/.local/bin:/usr/bin:/bin"
  "$root/install.sh" "$win/project" > "$win/taken/output"
)
grep -Fq 'NOT OURS' "$win/taken/.local/bin/praukron.cmd"
test ! -e "$win/taken/.local/bin/praukron"
grep -Fq 'alias praukron=' "$win/taken/output"
# --no-link leaves the machine alone entirely.
optout="$fixture/optout"
mkdir -p "$optout/.local/bin" "$optout/project"
(
  export HOME="$optout"
  export PATH="$optout/.local/bin:/usr/bin:/bin"
  "$root/install.sh" "$optout/project" --no-link > "$optout/output"
)
test ! -e "$optout/.local/bin/praukron"
grep -Fq '.praukron/praukron' "$optout/output"

echo 'installer smoke: pass'
