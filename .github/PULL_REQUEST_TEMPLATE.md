## What this changes

<!-- One or two sentences. The commit message carries the detail. -->

## Contract

<!--
The acceptance criteria, written before the work: Given / When / Then, each
with an evidence class. A maintainer records them in the project chronicle.
-->

## Decisions

<!-- Any material choice, the options rejected, and why — or "none". -->

## Evidence

<!--
What you actually ran and what it showed, against the criterion it satisfies.
"Tests pass" is not evidence; name the check and what it would have caught.
-->

## Before requesting review

- [ ] The contract was written before the work, and was not reworded to match what got built
- [ ] `python3 -m unittest discover -s tests` and `sh tests/install.sh` pass
- [ ] Anything not yet built is described as not yet built
- [ ] The change is recorded in `changelog.d/<task id>.md`, and `CHANGELOG.md` is untouched
