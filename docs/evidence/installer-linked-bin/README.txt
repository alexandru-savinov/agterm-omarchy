Installer symlinked-bin proposal — task #98, review wave #92

Baseline: 8458e16d06b2432cd6895955b6be972b1edae93d (PR #6).
The current installer exits0 but creates dangling command links if PREFIX/bin
points at a different physical directory. Both CLI and desktop launches fail.

This inert patch calculates each relative link target from its physical parent.
It preserves ordinary prefix relocation and supports symlinked prefix/bin paths.
It does not modify the active installer. The #3 installer owner decides adoption.

Replay in a disposable checkout of the baseline:
  python3 /path/to/test_linked_bin.py /path/to/baseline-checkout
  git -C /path/to/baseline-checkout apply /path/to/proposal.patch
  python3 /path/to/test_linked_bin.py /path/to/baseline-checkout

Use Python 3.12 and put python3 on PATH (the installer invokes it).
The suite extends the existing PersonalInstallTests fixture without compiling
Swift or installing into a real home. Baseline:12 tests,2 failures.
Patched:12 tests pass (9 inherited checks and3 new cases).
The new cases cover symlinked bin plus reinstall, relocation of a normal prefix,
and a prefix which is itself a symlink with a separately symlinked bin directory.

All observations were reproduced on Linux in temporary fixtures. This is not
GUI/VM runtime or release acceptance. No unrelated installer behavior, rollback
semantics, library environment policy or ownership changes are included.
Before adoption, move the new cases into the repository's regular test suite.
