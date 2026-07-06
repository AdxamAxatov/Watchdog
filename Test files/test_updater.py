"""Unit tests for auto_updater SHA256 release-note parsing.

Covers the per-asset named format written by the CI release workflow, the
bare single-hash back-compat fallback, ambiguity handling, and — critically —
that the NEW named format does NOT match the OLD deployed regex
(`SHA256:\\s*([a-fA-F0-9]{64})`), so old exes skip verification instead of
verifying the wrong asset's hash and failing.

auto_updater is cross-platform importable (only stdlib at import time), so this
runs under a plain python3 on any OS:

    python3 "Test files/test_updater.py"
"""
import re
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from auto_updater import GitHubAutoUpdater  # noqa: E402

# The exact regex baked into OLD deployed exes.
OLD_REGEX = re.compile(r'SHA256:\s*([a-fA-F0-9]{64})', re.IGNORECASE)

H_WATCHDOG = "a" * 64
H_BOOT = "b" * 64
H_WINDOW = "c" * 64
H_DROP = "d" * 64
H_MEM = "e" * 64
H_BARE = "f" * 64


def _named_notes() -> str:
    """A realistic multi-asset release body as the workflow writes it."""
    return (
        "Automated release built from commit deadbeef.\n"
        "\n"
        "## Checksums\n"
        f"SHA256 (Watchdog.exe): {H_WATCHDOG}\n"
        f"SHA256 (Boot.exe): {H_BOOT}\n"
        f"SHA256 (WindowChecker.exe): {H_WINDOW}\n"
        f"SHA256 (DropStats.exe): {H_DROP}\n"
        f"SHA256 (MemReductLooped.exe): {H_MEM}\n"
        "\n"
        "## Changes\n"
        "- 1234abc some commit\n"
    )


def _updater() -> GitHubAutoUpdater:
    # Bypass __init__ (config load / dependency checks) — extract_sha256 is a
    # pure function of its arguments and needs no instance state.
    return GitHubAutoUpdater.__new__(GitHubAutoUpdater)


class TestSha256Parsing(unittest.TestCase):
    def setUp(self):
        self.u = _updater()

    def test_named_single_asset(self):
        body = f"SHA256 (Watchdog.exe): {H_WATCHDOG}\n"
        self.assertEqual(self.u.extract_sha256_from_release(body, "Watchdog.exe"), H_WATCHDOG)

    def test_named_picks_the_right_asset(self):
        body = _named_notes()
        self.assertEqual(self.u.extract_sha256_from_release(body, "Watchdog.exe"), H_WATCHDOG)
        self.assertEqual(self.u.extract_sha256_from_release(body, "Boot.exe"), H_BOOT)
        self.assertEqual(self.u.extract_sha256_from_release(body, "WindowChecker.exe"), H_WINDOW)
        self.assertEqual(self.u.extract_sha256_from_release(body, "DropStats.exe"), H_DROP)
        self.assertEqual(self.u.extract_sha256_from_release(body, "MemReductLooped.exe"), H_MEM)

    def test_named_case_insensitive_and_flexible_whitespace(self):
        body = f"sha256(  Watchdog.exe )  :   {H_WATCHDOG.upper()}\n"
        self.assertEqual(self.u.extract_sha256_from_release(body, "watchdog.exe"), H_WATCHDOG)

    def test_named_missing_asset_no_bare_returns_none(self):
        # Named block present but not for the asset we're updating, and no bare
        # line to fall back on.
        body = f"SHA256 (Boot.exe): {H_BOOT}\n"
        self.assertIsNone(self.u.extract_sha256_from_release(body, "Watchdog.exe"))

    def test_bare_single_fallback_with_asset_name(self):
        # Manual single-hash release; the asset we want has no named line.
        body = f"Release notes\nSHA256: {H_BARE}\n"
        self.assertEqual(self.u.extract_sha256_from_release(body, "Watchdog.exe"), H_BARE)

    def test_bare_single_fallback_without_asset_name(self):
        body = f"SHA256: {H_BARE}\n"
        self.assertEqual(self.u.extract_sha256_from_release(body, None), H_BARE)

    def test_ambiguous_multiple_bare_returns_none(self):
        body = f"SHA256: {H_BARE}\nSHA256: {H_BOOT}\n"
        self.assertIsNone(self.u.extract_sha256_from_release(body, "Watchdog.exe"))

    def test_named_present_does_not_count_as_bare(self):
        # The named multi-asset body must yield ZERO bare matches, so a
        # non-listed asset falls through to None (not a bogus bare pick).
        body = _named_notes()
        self.assertIsNone(self.u.extract_sha256_from_release(body, "Missing.exe"))

    def test_empty_body(self):
        self.assertIsNone(self.u.extract_sha256_from_release("", "Watchdog.exe"))
        self.assertIsNone(self.u.extract_sha256_from_release(None, "Watchdog.exe"))


class TestOldRegexCompat(unittest.TestCase):
    """The compat contract: OLD exes' regex must NOT match the NEW named form."""

    def test_old_regex_does_not_match_named_format(self):
        body = _named_notes()
        self.assertIsNone(
            OLD_REGEX.search(body),
            "OLD regex matched the NEW named format — old exes would verify the "
            "wrong hash and fail. The '(' after SHA256 must break the match.",
        )

    def test_old_regex_still_matches_bare_format(self):
        # Sanity: the old regex still works on the old single-hash format, so
        # manual releases stay backward compatible for old exes.
        body = f"SHA256: {H_BARE}\n"
        m = OLD_REGEX.search(body)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).lower(), H_BARE)


class TestTokenSanitization(unittest.TestCase):
    """A placeholder token must never reach the Authorization header — GitHub
    answers 401 to a bad token, killing the update check entirely (strictly
    worse than the anonymous 60 req/hr with no header)."""

    def test_placeholder_is_ignored(self):
        self.assertIsNone(GitHubAutoUpdater._sanitize_token("PASTE_NEW_GITHUB_TOKEN_HERE"))

    def test_empty_and_none_are_ignored(self):
        self.assertIsNone(GitHubAutoUpdater._sanitize_token(""))
        self.assertIsNone(GitHubAutoUpdater._sanitize_token("   "))
        self.assertIsNone(GitHubAutoUpdater._sanitize_token(None))

    def test_short_garbage_is_ignored(self):
        self.assertIsNone(GitHubAutoUpdater._sanitize_token("changeme"))

    def test_token_with_spaces_is_ignored(self):
        self.assertIsNone(GitHubAutoUpdater._sanitize_token("paste token here please"))

    def test_real_looking_tokens_pass(self):
        classic = "gho_" + "x" * 36
        fine_grained = "github_pat_" + "x" * 60
        hex40 = "0123456789abcdef0123456789abcdef01234567"
        self.assertEqual(GitHubAutoUpdater._sanitize_token(classic), classic)
        self.assertEqual(GitHubAutoUpdater._sanitize_token(fine_grained), fine_grained)
        self.assertEqual(GitHubAutoUpdater._sanitize_token(hex40), hex40)

    def test_surrounding_whitespace_is_stripped(self):
        classic = "ghp_" + "y" * 36
        self.assertEqual(GitHubAutoUpdater._sanitize_token(f"  {classic}\n"), classic)


if __name__ == "__main__":
    unittest.main(verbosity=2)
