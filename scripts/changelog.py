#!/usr/bin/env python3
"""Assemble changelog.d/ fragments into CHANGELOG.md at release time.

A repository tool, not part of the installed product. Standard library only.

    python3 scripts/changelog.py assemble <version> <date>

Every fragment's bullets go, in task-id order, to the end of the bullet text of
the `## <version>` section, which is made from `## Unreleased` (renamed) or, if
there is none, created above the newest release. Existing text is kept byte for
byte, the file's line endings are kept, and merged fragments are deleted. A
malformed fragment refuses the whole run before anything is written.
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H2 = re.compile(r"^## ")
H3_PLUS = re.compile(r"^#{3,6}\s")
ANY_HEADING = re.compile(r"^\s*#{1,6}(\s|$)")
BULLET = re.compile(r"^[-*] \S")


class Refusal(Exception):
    pass


def read_fragment(path):
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal("%s: not UTF-8" % path)
    lines = text.replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    name = os.path.basename(path)
    if not lines or not BULLET.match(lines[0]):
        raise Refusal("%s: a fragment must start with a '- ' bullet" % name)
    for line in lines:
        if ANY_HEADING.match(line):
            raise Refusal("%s: a fragment holds bullets, not headings" % name)
    return lines


def fragments(directory):
    names = sorted(
        n for n in os.listdir(directory)
        if n.endswith(".md") and n.lower() != "readme.md"
    )
    return [(n, read_fragment(os.path.join(directory, n))) for n in names]


def assemble_text(text, version, date, bullets):
    """Return CHANGELOG text with `bullets` (a list of lines) added."""
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    title = "## %s — %s" % (version, date)
    start = None
    for i, line in enumerate(lines):
        if H2.match(line) and line[3:].split(" ")[0] == version:
            start = i
            break
    if start is None:
        for i, line in enumerate(lines):
            if H2.match(line) and line.strip() == "## Unreleased":
                start = i
                lines[i] = title
                break
    if start is None:
        first = next((i for i, l in enumerate(lines) if H2.match(l)), len(lines))
        lines[first:first] = [title, ""]
        start = first
        end = start + 2
        lines[end:end] = bullets + [""]
        return eol.join(lines)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if H2.match(lines[j]) or H3_PLUS.match(lines[j]):
            end = j
            break
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    if at == start + 1:
        lines[at:at] = [""] + bullets
    else:
        lines[at:at] = bullets
    return eol.join(lines)


def assemble(version, date, root=ROOT):
    directory = os.path.join(root, "changelog.d")
    path = os.path.join(root, "CHANGELOG.md")
    frags = fragments(directory) if os.path.isdir(directory) else []
    if not frags:
        return []
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    bullets = [line for _, lines in frags for line in lines]
    out = assemble_text(text, version, date, bullets).encode("utf-8")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(out)
    os.replace(tmp, path)
    for name, _ in frags:
        os.remove(os.path.join(directory, name))
    return [name for name, _ in frags]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("assemble", help="merge fragments into CHANGELOG.md")
    a.add_argument("version")
    a.add_argument("date")
    a.add_argument("--root", default=ROOT)
    args = parser.parse_args(argv)
    try:
        done = assemble(args.version, args.date, args.root)
    except Refusal as err:
        print("refused: %s" % err, file=sys.stderr)
        return 1
    print("assembled %d fragment(s) into %s" % (len(done), args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
