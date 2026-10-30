#!/usr/bin/env python3
"""Offline tests for jdk_versions.py.

The network layer (jdk_versions.fetch) is replaced with canned responses, so these tests need no
network access and do not depend on whichever JDK versions happen to be current.

Run with:
    python3 -m unittest -v test_jdk_versions
"""
import contextlib
import csv
import http.client
import io
import json
import sys
import unittest
from unittest import mock

import jdk_versions as j

# ---------------------------------------------------------------------------------------------
# Canned data
# ---------------------------------------------------------------------------------------------

ALL = [("linux", "x64"), ("linux", "aarch64"), ("mac", "x64"), ("mac", "aarch64"),
       ("windows", "x64"), ("alpine-linux", "x64")]

# Which (adoptium-os, arch) pairs each major version is built for.
PLATFORMS = {
    27: [("linux", "x64"), ("linux", "aarch64"), ("mac", "aarch64"), ("windows", "x64")],
    26: [("linux", "x64")],
    25: ALL,
    21: ALL,
    17: [("linux", "x64"), ("linux", "aarch64"), ("mac", "x64")],
    8: [("linux", "x64"), ("linux", "arm")],
}

AVAILABLE = {
    "available_lts_releases": [8, 17, 21, 25],
    "available_releases": [8, 17, 21, 25, 26, 27],
    "most_recent_feature_release": 27,
    "most_recent_lts": 25,
}

EA_HOME = '<h1>Early access: <a href="/28/">JDK 28</a>, <a href="/jmc/">JMC</a></h1>'
EA_PAGE = """
<h2>Build 21 (2026/10/22)</h2>
<a href="https://download.java.net/java/early_access/jdk28/21/GPL/openjdk-28-ea+21_linux-aarch64_bin.tar.gz">tar.gz</a>
<a href="https://download.java.net/java/early_access/jdk28/21/GPL/openjdk-28-ea+21_linux-aarch64_bin.tar.gz.sha256">sha256</a>
<a href="https://download.java.net/java/early_access/jdk28/21/GPL/openjdk-28-ea+21_linux-x64_bin.tar.gz">tar.gz</a>
<a href="https://download.java.net/java/early_access/jdk28/21/GPL/openjdk-28-ea+21_macos-aarch64_bin.tar.gz">tar.gz</a>
<a href="https://download.java.net/java/early_access/jdk28/21/GPL/openjdk-28-ea+21_windows-x64_bin.zip">zip</a>
<a href="https://download.java.net/openjdk/testresults/28/testresults.html">Test results</a>
<a href="https://download.java.net/java/early_access/jdk28/docs/api/">Javadoc</a>
"""


def asset(major, os_name, arch):
    name = f"OpenJDK{major}U-jdk_{arch}_{os_name}_hotspot_{major}.0.1_1.tar.gz"
    link = f"https://example.test/jdk-{major}/{name}"
    return {
        "release_name": f"jdk-{major}.0.1+1",
        "version": {"semver": f"{major}.0.1+1"},
        "binary": {
            "os": os_name, "architecture": arch, "updated_at": f"2026-01-{major:02d}T00:00:00Z",
            "package": {"link": link, "checksum_link": f"{link}.sha256.txt"},
        },
    }


def fake_fetch(url, accept):
    """Stand-in for jdk_versions.fetch that serves canned responses."""
    if url.startswith(j.API):
        path = url[len(j.API):]
        if path == "/info/available_releases":
            return json.dumps(AVAILABLE)
        if path == "/types/architectures":
            return json.dumps(["x64", "x32", "aarch64", "arm"])
        if path == "/types/operating_systems":
            return json.dumps(["linux", "windows", "mac", "alpine-linux"])
        if path.startswith("/assets/latest/"):
            major = int(path.split("/")[3])
            return json.dumps([asset(major, o, a) for o, a in PLATFORMS[major]])
    if url == f"{j.EA_SITE}/":
        return EA_HOME
    if url == f"{j.EA_SITE}/28/":
        return EA_PAGE
    raise AssertionError(f"unexpected request: {url}")


def raising(exc):
    def fetch(url, accept):
        raise exc
    return fetch


def run(*argv, fetch=fake_fetch, detected=None):
    """Run main() with the given argv. Returns (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    patches = [
        mock.patch.object(j, "fetch", fetch),
        mock.patch.object(sys, "argv", ["jdk_versions.py", *argv]),
    ]
    if detected is not None:
        patches.append(mock.patch.object(j, "detect_platform", detected))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        try:
            code = j.main()
        except SystemExit as exc:  # argparse exits directly on bad usage and on --help
            code = exc.code
    return code, out.getvalue(), err.getvalue()


def run_json(*argv, **kw):
    code, out, err = run(*argv, "--json", **kw)
    assert code == 0, (code, err)
    return json.loads(out)


def run_csv(*argv, **kw):
    code, out, err = run(*argv, "--csv", **kw)
    assert code == 0, (code, err)
    return list(csv.reader(io.StringIO(out)))


def majors(data):
    return [r["major"] for r in data["releases"]]


def download_keys(data):
    return {r["major"]: list(r["downloads"]) for r in data["releases"]}


# ---------------------------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------------------------

class NormalizeTests(unittest.TestCase):
    def test_arch_aliases(self):
        for given, want in [("amd64", "x64"), ("X86_64", "x64"), ("x86-64", "x64"),
                            ("arm64", "aarch64"), (" AArch64 ", "aarch64"), ("arm", "arm"),
                            ("riscv64", "riscv64")]:
            with self.subTest(given=given):
                self.assertEqual(j.normalize_arch(given), want)

    def test_os_aliases(self):
        for given, want in [("mac", "macos"), ("OSX", "macos"), ("Darwin", "macos"),
                            ("win", "windows"), ("win32", "windows"), ("alpine", "alpine-linux"),
                            ("linux", "linux"), ("aix", "aix")]:
            with self.subTest(given=given):
                self.assertEqual(j.normalize_os(given), want)


class PositiveIntTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(j.positive_int("1"), 1)
        self.assertEqual(j.positive_int("250"), 250)

    def test_invalid(self):
        for bad in ["0", "-3", "abc", "1.5", ""]:
            with self.subTest(bad=bad), self.assertRaises(j.argparse.ArgumentTypeError):
                j.positive_int(bad)


class FilterAndLimitTests(unittest.TestCase):
    DOWNLOADS = {
        "linux-x64": {"os": "linux", "arch": "x64"},
        "linux-aarch64": {"os": "linux", "arch": "aarch64"},
        "macos-x64": {"os": "macos", "arch": "x64"},
    }

    def test_filter_by_arch_os_and_both(self):
        self.assertEqual(list(j.filter_downloads(self.DOWNLOADS, arch="x64")),
                         ["linux-x64", "macos-x64"])
        self.assertEqual(list(j.filter_downloads(self.DOWNLOADS, os_name="linux")),
                         ["linux-x64", "linux-aarch64"])
        self.assertEqual(list(j.filter_downloads(self.DOWNLOADS, "x64", "macos")), ["macos-x64"])
        self.assertEqual(j.filter_downloads(self.DOWNLOADS, "arm", "linux"), {})

    def test_limit_downloads(self):
        self.assertIs(j.limit_downloads(self.DOWNLOADS, None), self.DOWNLOADS)
        self.assertEqual(list(j.limit_downloads(self.DOWNLOADS, 2)),
                         ["linux-x64", "linux-aarch64"])
        self.assertEqual(len(j.limit_downloads(self.DOWNLOADS, 99)), 3)

    def test_limit_releases_counts_download_rows_across_releases(self):
        releases = [{"major": 27, "downloads": {"a": 1, "b": 2, "c": 3}},
                    {"major": 25, "downloads": {"d": 4, "e": 5}}]
        limited = j.limit_releases(releases, 4)
        self.assertEqual([(r["major"], list(r["downloads"])) for r in limited],
                         [(27, ["a", "b", "c"]), (25, ["d"])])
        self.assertEqual([r["major"] for r in j.limit_releases(releases, 2)], [27])
        self.assertIs(j.limit_releases(releases, None), releases)
        self.assertEqual(len(releases[0]["downloads"]), 3, "input must not be mutated")


class DescribeFiltersTests(unittest.TestCase):
    def test_messages(self):
        self.assertEqual(j.describe_filters("x64", None), "architecture 'x64'")
        self.assertEqual(j.describe_filters(None, "aix"), "os 'aix'")
        self.assertEqual(j.describe_filters("x64", "aix"), "architecture 'x64' and os 'aix'")


class FormatTests(unittest.TestCase):
    COLUMNS = ["major", "os", "sha256_url"]
    ROWS = [{"major": 25, "os": "linux", "sha256_url": "u1"},
            {"major": 8, "os": "macos", "sha256_url": "u2"}]

    def test_table_with_header_omits_sha256_column(self):
        lines = j.format_table(self.COLUMNS, self.ROWS).splitlines()
        self.assertEqual(lines[0], lines[2])
        self.assertEqual(lines[0], lines[-1])
        self.assertIn("major", lines[1])
        self.assertNotIn("sha256", "\n".join(lines))
        self.assertEqual(len(lines), 6)

    def test_table_without_header(self):
        lines = j.format_table(self.COLUMNS, self.ROWS, header=False).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertNotIn("major", "\n".join(lines))
        self.assertTrue(lines[0].startswith("+") and lines[-1].startswith("+"))

    def test_table_columns_are_aligned(self):
        lines = j.format_table(self.COLUMNS, self.ROWS).splitlines()
        self.assertEqual(len({len(line) for line in lines}), 1)

    def test_table_renders_missing_values_as_blank(self):
        lines = j.format_table(["a", "b"], [{"a": 1, "b": None}]).splitlines()
        self.assertEqual(lines[3], "| 1 |   |")

    def _emit(self, fmt, header=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            j.emit({"k": 1}, fmt, self.COLUMNS, self.ROWS, header)
        return out.getvalue()

    def test_emit_csv(self):
        self.assertEqual(self._emit("csv"), "major,os,sha256_url\n25,linux,u1\n8,macos,u2\n")
        self.assertEqual(self._emit("csv", header=False), "25,linux,u1\n8,macos,u2\n")

    def test_emit_json_ignores_header_flag(self):
        self.assertEqual(json.loads(self._emit("json", header=False)), {"k": 1})
        self.assertEqual(self._emit("json"), self._emit("json", header=False))


class EmitVersionsTests(unittest.TestCase):
    def _emit(self, fmt, header=True):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            j.emit_versions("src", "x64", None, [27, 25], fmt, header)
        return out.getvalue()

    def test_table_is_bare_numbers(self):
        self.assertEqual(self._emit("table"), "27\n25\n")

    def test_csv_header_and_no_header(self):
        self.assertEqual(self._emit("csv"), "major\n27\n25\n")
        self.assertEqual(self._emit("csv", header=False), "27\n25\n")

    def test_json(self):
        self.assertEqual(json.loads(self._emit("json")),
                         {"source": "src", "arch": "x64", "os": None, "major_versions": [27, 25]})


class DownloadRowsTests(unittest.TestCase):
    def test_flattens_and_repeats_common_fields(self):
        downloads = {"linux-x64": {"os": "linux", "arch": "x64", "url": "u", "sha256_url": "s"}}
        self.assertEqual(j.download_rows(downloads, major=21, version="21.0.1"), [
            {"major": 21, "version": "21.0.1", "os": "linux", "arch": "x64",
             "url": "u", "sha256_url": "s"}])


class DetectPlatformTests(unittest.TestCase):
    def detect(self, system, machine, alpine=False):
        with mock.patch("platform.system", return_value=system), \
             mock.patch("platform.machine", return_value=machine), \
             mock.patch.object(j.os.path, "exists", return_value=alpine):
            return j.detect_platform()

    def test_supported_platforms(self):
        cases = [
            ("Linux", "x86_64", False, ("linux", "x64")),
            ("Linux", "aarch64", False, ("linux", "aarch64")),
            ("Linux", "armv7l", False, ("linux", "arm")),
            ("Linux", "ppc64le", False, ("linux", "ppc64le")),
            ("Linux", "s390x", False, ("linux", "s390x")),
            ("Linux", "riscv64", False, ("linux", "riscv64")),
            ("Linux", "x86_64", True, ("alpine-linux", "x64")),
            ("Linux", "aarch64", True, ("alpine-linux", "aarch64")),
            ("Darwin", "arm64", False, ("macos", "aarch64")),
            ("Darwin", "x86_64", False, ("macos", "x64")),
            ("Windows", "AMD64", False, ("windows", "x64")),
            ("Windows", "ARM64", False, ("windows", "aarch64")),
            ("AIX", "00F84C0C4C00", False, ("aix", "ppc64")),
        ]
        for system, machine, alpine, want in cases:
            with self.subTest(system=system, machine=machine, alpine=alpine):
                self.assertEqual(self.detect(system, machine, alpine), want)

    def test_alpine_marker_ignored_on_other_systems(self):
        self.assertEqual(self.detect("Darwin", "arm64", alpine=True), ("macos", "aarch64"))

    def test_unsupported_os(self):
        with self.assertRaisesRegex(ValueError, "unsupported operating system 'FreeBSD'"):
            self.detect("FreeBSD", "amd64")

    def test_unsupported_architecture(self):
        with self.assertRaisesRegex(ValueError, "unsupported architecture 'mips'"):
            self.detect("Linux", "mips")


# ---------------------------------------------------------------------------------------------
# Command line behaviour (network mocked)
# ---------------------------------------------------------------------------------------------

class ReleaseSelectionTests(unittest.TestCase):
    def test_default_is_lts_plus_latest_feature_release_newest_first(self):
        self.assertEqual(majors(run_json()), [27, 25, 21, 17, 8])

    def test_all(self):
        self.assertEqual(majors(run_json("--all")), [27, 26, 25, 21, 17, 8])

    def test_lts(self):
        self.assertEqual(majors(run_json("--lts")), [25, 21, 17, 8])

    def test_non_lts(self):
        self.assertEqual(majors(run_json("--non-lts")), [27])

    def test_all_non_lts(self):
        self.assertEqual(majors(run_json("--all", "--non-lts")), [27, 26])

    def test_all_lts_matches_lts(self):
        self.assertEqual(majors(run_json("--all", "--lts")), [25, 21, 17, 8])

    def test_release_fields(self):
        data = run_json("--lts")
        self.assertEqual(data["source"], "Eclipse Temurin (Adoptium API)")
        self.assertEqual(data["most_recent_feature_release"], 27)
        self.assertEqual(data["most_recent_lts"], 25)
        self.assertIsNone(data["arch"])
        self.assertIsNone(data["os"])
        r = data["releases"][0]
        self.assertEqual((r["major"], r["version"], r["release_name"], r["lts"]),
                         (25, "25.0.1+1", "jdk-25.0.1+1", True))
        self.assertEqual(r["released"], "2026-01-25T00:00:00Z")
        self.assertFalse(run_json("--non-lts")["releases"][0]["lts"])

    def test_downloads_have_os_arch_and_links(self):
        dl = run_json("--lts")["releases"][0]["downloads"]
        self.assertEqual(list(dl), sorted(dl), "downloads are sorted by platform key")
        entry = dl["macos-aarch64"]
        self.assertEqual((entry["os"], entry["arch"]), ("macos", "aarch64"))
        self.assertTrue(entry["url"].startswith("https://example.test/jdk-25/"))
        self.assertEqual(entry["sha256_url"], entry["url"] + ".sha256.txt")

    def test_adoptium_mac_is_reported_as_macos(self):
        keys = {k for r in run_json()["releases"] for k in r["downloads"]}
        self.assertIn("macos-x64", keys)
        self.assertFalse(any(k.startswith("mac-") for k in keys))


class ArchOsFilterTests(unittest.TestCase):
    def test_arch_filter(self):
        data = run_json("--lts", "--arch", "aarch64")
        self.assertEqual(data["arch"], "aarch64")
        self.assertEqual(download_keys(data),
                         {25: ["linux-aarch64", "macos-aarch64"],
                          21: ["linux-aarch64", "macos-aarch64"],
                          17: ["linux-aarch64"]})

    def test_arch_alias(self):
        data = run_json("--lts", "--arch", "AMD64")
        self.assertEqual(data["arch"], "x64")
        self.assertTrue(all(d["arch"] == "x64" for r in data["releases"]
                            for d in r["downloads"].values()))
        self.assertEqual(run_json("--lts", "--arch", "arm64")["arch"], "aarch64")

    def test_32_bit_arm_is_not_aarch64(self):
        self.assertEqual(download_keys(run_json("--all", "--arch", "arm")), {8: ["linux-arm"]})

    def test_os_filter(self):
        data = run_json("--lts", "--os", "macos")
        self.assertEqual(data["os"], "macos")
        self.assertEqual(download_keys(data),
                         {25: ["macos-aarch64", "macos-x64"],
                          21: ["macos-aarch64", "macos-x64"],
                          17: ["macos-x64"]})

    def test_os_aliases(self):
        for alias in ("mac", "darwin", "OSX"):
            with self.subTest(alias=alias):
                self.assertEqual(run_json("--lts", "--os", alias)["os"], "macos")
        self.assertEqual(run_json("--lts", "--os", "win")["os"], "windows")
        self.assertEqual(run_json("--lts", "--os", "alpine")["os"], "alpine-linux")

    def test_linux_excludes_alpine(self):
        keys = {k for r in run_json("--os", "linux")["releases"] for k in r["downloads"]}
        self.assertNotIn("alpine-linux-x64", keys)
        self.assertIn("linux-x64", keys)

    def test_os_and_arch_combined(self):
        data = run_json("--os", "alpine-linux", "--arch", "x64")
        self.assertEqual(download_keys(data), {25: ["alpine-linux-x64"], 21: ["alpine-linux-x64"]})
        self.assertEqual((data["os"], data["arch"]), ("alpine-linux", "x64"))

    def test_releases_without_a_match_are_dropped(self):
        self.assertNotIn(27, majors(run_json("--os", "alpine-linux")))

    def test_no_match_is_an_error(self):
        for args, message in [(["--os", "solaris"], "os 'solaris'"),
                              (["--arch", "sparcv9"], "architecture 'sparcv9'"),
                              (["--os", "aix", "--arch", "x64"],
                               "architecture 'x64' and os 'aix'")]:
            with self.subTest(args=args):
                code, out, err = run(*args)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")
                self.assertIn(message, json.loads(err)["error"])


class CountTests(unittest.TestCase):
    TOTAL = 4 + 6 + 6 + 3 + 2  # downloads across the default releases

    def test_count_limits_rows_across_releases(self):
        data = run_json("--count", "5")
        self.assertEqual(download_keys(data)[27], ["linux-aarch64", "linux-x64",
                                                   "macos-aarch64", "windows-x64"])
        self.assertEqual(download_keys(data)[25], ["alpine-linux-x64"])
        self.assertEqual(majors(data), [27, 25])

    def test_count_is_the_same_in_every_format(self):
        rows = run_csv("--count", "7")[1:]
        self.assertEqual(len(rows), 7)
        data = run_json("--count", "7")
        self.assertEqual(sum(len(r["downloads"]) for r in data["releases"]), 7)
        code, out, _ = run("--count", "7")
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("| ")]), 8)

    def test_count_larger_than_available_returns_everything(self):
        self.assertEqual(len(run_csv("--count", "1000")) - 1, self.TOTAL)

    def test_no_count_returns_everything(self):
        self.assertEqual(len(run_csv()) - 1, self.TOTAL)

    def test_count_applies_after_filters(self):
        rows = run_csv("--lts", "--os", "linux", "--arch", "x64", "--count", "1")[1:]
        self.assertEqual([(r[0], r[5], r[6]) for r in rows], [("25", "linux", "x64")])

    def test_count_with_version_only_limits_versions(self):
        code, out, _ = run("--lts", "--version-only", "--count", "2")
        self.assertEqual(out, "25\n21\n")

    def test_count_with_lists_and_ea(self):
        self.assertEqual(run_csv("--list-arch", "--count", "2"), [["arch"], ["x64"], ["x32"]])
        self.assertEqual(run_csv("--list-os", "--count", "1"), [["os"], ["linux"]])
        self.assertEqual(len(run_csv("--ea", "--count", "2")) - 1, 2)

    def test_invalid_counts_are_rejected(self):
        for bad in ("0", "-1", "abc", "1.5"):
            with self.subTest(bad=bad):
                code, _, err = run("--count", bad)
                self.assertEqual(code, 2)
                self.assertIn("--count", err)


class VersionOnlyTests(unittest.TestCase):
    def test_non_lts_returns_just_the_major_version(self):
        self.assertEqual(run("--non-lts", "--version-only"), (0, "27\n", ""))

    def test_lts(self):
        self.assertEqual(run("--lts", "--version-only")[1], "25\n21\n17\n8\n")

    def test_default_and_all(self):
        self.assertEqual(run("--version-only")[1], "27\n25\n21\n17\n8\n")
        self.assertEqual(run("--all", "--version-only")[1], "27\n26\n25\n21\n17\n8\n")

    def test_csv_and_no_header(self):
        self.assertEqual(run("--non-lts", "--version-only", "--csv")[1], "major\n27\n")
        self.assertEqual(run("--non-lts", "--version-only", "--csv", "--no-header")[1], "27\n")

    def test_json(self):
        data = json.loads(run("--lts", "--version-only", "--json")[1])
        self.assertEqual(data["major_versions"], [25, 21, 17, 8])
        self.assertEqual(data["source"], "Eclipse Temurin (Adoptium API)")

    def test_respects_filters(self):
        self.assertEqual(run("--version-only", "--arch", "arm")[1], "8\n")
        self.assertEqual(run("--version-only", "--os", "alpine-linux")[1], "25\n21\n")

    def test_ea(self):
        self.assertEqual(run("--ea", "--version-only")[1], "28\n")

    def test_no_match_is_an_error(self):
        code, out, err = run("--version-only", "--os", "solaris")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("solaris", err)


class OutputFormatTests(unittest.TestCase):
    def test_default_is_a_table(self):
        code, out, _ = run("--lts", "--count", "2")
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertTrue(lines[0].startswith("+-") and lines[0] == lines[2] == lines[-1])
        self.assertIn("| major", lines[1])
        self.assertNotIn("sha256", out)
        self.assertNotIn('"source"', out)

    def test_explicit_json_flag_is_the_json_output(self):
        self.assertEqual(json.loads(run("--lts", "--json")[1])["most_recent_lts"], 25)

    def test_csv_columns_and_row_count(self):
        rows = run_csv("--lts")
        self.assertEqual(rows[0], ["major", "version", "release_name", "lts", "released",
                                   "os", "arch", "url", "sha256_url"])
        self.assertEqual(len(rows) - 1, 6 + 6 + 3 + 2)
        self.assertTrue(all(len(r) == 9 for r in rows))
        self.assertEqual(rows[1][:7], ["25", "25.0.1+1", "jdk-25.0.1+1", "true",
                                       "2026-01-25T00:00:00Z", "alpine-linux", "x64"])
        self.assertTrue(rows[1][8].endswith(".sha256.txt"))

    def test_no_header_csv(self):
        with_header = run_csv("--lts")
        without = run_csv("--lts", "--no-header")
        self.assertEqual(without, with_header[1:])

    def test_no_header_table(self):
        _, with_header, _ = run("--lts")
        _, without, _ = run("--lts", "--no-header")
        self.assertIn("| major", with_header)
        self.assertNotIn("major", without)
        self.assertEqual(len(without.splitlines()), len(with_header.splitlines()) - 2)

    def test_no_header_does_not_change_json(self):
        self.assertEqual(run("--lts", "--json")[1], run("--lts", "--json", "--no-header")[1])

    def test_no_header_table_has_no_blank_or_missing_frame(self):
        lines = run("--non-lts", "--no-header")[1].splitlines()
        self.assertEqual(lines[0], lines[-1])
        self.assertTrue(lines[0].startswith("+-"))


class EarlyAccessTests(unittest.TestCase):
    def test_json(self):
        data = run_json("--ea")
        self.assertEqual(data["source"], "jdk.java.net (Oracle-led OpenJDK)")
        ea = data["early_access"]
        self.assertEqual((ea["major"], ea["version"], ea["build"], ea["released"]),
                         (28, "28-ea+21", 21, "2026-10-22"))
        self.assertEqual(list(ea["downloads"]),
                         ["linux-aarch64", "linux-x64", "macos-aarch64", "windows-x64"])

    def test_only_binaries_are_listed_not_sha_test_results_or_javadoc(self):
        for d in run_json("--ea")["early_access"]["downloads"].values():
            self.assertRegex(d["url"], r"_bin\.(tar\.gz|zip)$")
            self.assertEqual(d["sha256_url"], d["url"] + ".sha256")

    def test_download_os_and_arch_fields(self):
        dl = run_json("--ea")["early_access"]["downloads"]
        self.assertEqual((dl["windows-x64"]["os"], dl["windows-x64"]["arch"]), ("windows", "x64"))
        self.assertEqual((dl["macos-aarch64"]["os"], dl["macos-aarch64"]["arch"]),
                         ("macos", "aarch64"))

    def test_filters(self):
        data = run_json("--ea", "--arch", "aarch64")
        self.assertEqual(list(data["early_access"]["downloads"]), ["linux-aarch64", "macos-aarch64"])
        self.assertEqual(data["arch"], "aarch64")
        data = run_json("--ea", "--os", "darwin", "--arch", "arm64")
        self.assertEqual(list(data["early_access"]["downloads"]), ["macos-aarch64"])
        self.assertEqual((data["os"], data["arch"]), ("macos", "aarch64"))

    def test_csv(self):
        rows = run_csv("--ea", "--os", "linux")
        self.assertEqual(rows[0], ["major", "version", "build", "released", "os", "arch",
                                   "url", "sha256_url"])
        self.assertEqual([(r[4], r[5]) for r in rows[1:]], [("linux", "aarch64"), ("linux", "x64")])
        self.assertEqual(rows[1][:4], ["28", "28-ea+21", "21", "2026-10-22"])

    def test_no_match_is_an_error(self):
        code, out, err = run("--ea", "--os", "aix")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no early-access downloads for os 'aix'", json.loads(err)["error"])

    def test_page_layout_changes_are_reported_not_crashed(self):
        def no_marker(url, accept):
            return "<html>nothing here</html>" if url == f"{j.EA_SITE}/" else fake_fetch(url, accept)

        def no_build(url, accept):
            return "<html>no build</html>" if url == f"{j.EA_SITE}/28/" else fake_fetch(url, accept)

        for name, fetch in [("home", no_marker), ("build", no_build)]:
            with self.subTest(name):
                code, out, err = run("--ea", fetch=fetch)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("early-access", json.loads(err)["error"])


class ListTests(unittest.TestCase):
    def test_list_arch(self):
        data = json.loads(run("--list-arch", "--json")[1])
        self.assertEqual(data["architectures"], ["x64", "x32", "aarch64", "arm"])
        self.assertEqual(run_csv("--list-arch"), [["arch"], ["x64"], ["x32"], ["aarch64"], ["arm"]])

    def test_list_os_renames_mac_to_macos(self):
        data = json.loads(run("--list-os", "--json")[1])
        self.assertEqual(data["operating_systems"], ["linux", "windows", "macos", "alpine-linux"])

    def test_list_table_and_no_header(self):
        _, out, _ = run("--list-os")
        self.assertIn("| os", out)
        _, out, _ = run("--list-os", "--no-header")
        self.assertNotIn("| os", out)
        self.assertEqual(run_csv("--list-os", "--no-header")[0], ["linux"])

    def test_list_includes_filter_fields(self):
        data = json.loads(run("--list-arch", "--json", "--os", "linux", "--arch", "x64")[1])
        self.assertEqual((data["os"], data["arch"]), ("linux", "x64"))


class AutoTests(unittest.TestCase):
    @staticmethod
    def detected(os_name, arch):
        return lambda: (os_name, arch)

    def test_filters_to_detected_platform(self):
        data = run_json("--auto", detected=self.detected("macos", "aarch64"))
        self.assertEqual((data["os"], data["arch"]), ("macos", "aarch64"))
        self.assertEqual(download_keys(data), {27: ["macos-aarch64"], 25: ["macos-aarch64"],
                                               21: ["macos-aarch64"]})

    def test_explicit_flags_take_precedence(self):
        data = run_json("--auto", "--os", "windows", detected=self.detected("linux", "x64"))
        self.assertEqual((data["os"], data["arch"]), ("windows", "x64"))
        data = run_json("--auto", "--arch", "aarch64", detected=self.detected("linux", "x64"))
        self.assertEqual((data["os"], data["arch"]), ("linux", "aarch64"))

    def test_works_with_ea_version_only_count_and_csv(self):
        det = self.detected("linux", "x64")
        rows = run_csv("--auto", "--ea", detected=det)
        self.assertEqual([(r[4], r[5]) for r in rows[1:]], [("linux", "x64")])
        self.assertEqual(run("--auto", "--non-lts", "--version-only", detected=det)[1], "27\n")
        rows = run_csv("--auto", "--lts", "--count", "1", detected=det)
        self.assertEqual([(r[0], r[5], r[6]) for r in rows[1:]], [("25", "linux", "x64")])

    def test_no_match_for_detected_platform_is_an_error(self):
        code, out, err = run("--auto", detected=self.detected("aix", "ppc64"))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("os 'aix'", json.loads(err)["error"])

    def test_detection_failure_is_reported(self):
        def fail():
            raise ValueError("unsupported operating system 'FreeBSD'")
        code, out, err = run("--auto", detected=fail)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("--auto could not detect the platform", json.loads(err)["error"])
        self.assertIn("FreeBSD", json.loads(err)["error"])

    def test_detection_is_not_run_without_the_flag(self):
        def explode():
            raise AssertionError("detect_platform should not be called")
        self.assertEqual(run("--lts", "--version-only", detected=explode)[0], 0)


class ArgumentValidationTests(unittest.TestCase):
    def assert_rejected(self, *argv):
        code, out, err = run(*argv)
        self.assertEqual(code, 2, f"{argv} should be rejected (stderr: {err})")
        self.assertEqual(out, "")
        self.assertIn("error", err)

    def test_mutually_exclusive_flags(self):
        for argv in [("--lts", "--non-lts"), ("--json", "--csv"), ("--ea", "--lts"),
                     ("--ea", "--non-lts"), ("--list-arch", "--list-os"),
                     ("--list-os", "--lts"), ("--list-arch", "--ea")]:
            with self.subTest(argv=argv):
                self.assert_rejected(*argv)

    def test_list_flags_reject_auto_and_version_only(self):
        for argv in [("--auto", "--list-arch"), ("--auto", "--list-os"),
                     ("--version-only", "--list-arch"), ("--version-only", "--list-os")]:
            with self.subTest(argv=argv):
                self.assert_rejected(*argv)

    def test_removed_and_unknown_flags(self):
        for argv in [("-h",), ("-ea",), ("--early-access",), ("--bogus",), ("--arch",),
                     ("--os",), ("--count",), ("--fields",)]:
            with self.subTest(argv=argv):
                self.assert_rejected(*argv)

    def test_help(self):
        for flag in ("--h", "--help"):
            with self.subTest(flag=flag):
                code, out, _ = run(flag)
                self.assertEqual(code, 0)
                for option in ("--auto", "--no-header", "--count", "--version-only", "--os",
                               "--arch", "--list-os", "--list-arch", "--ea", "--csv", "--json",
                               "--fields"):
                    self.assertIn(option, out)

    def test_validation_happens_before_any_network_access(self):
        code, _, _ = run("--lts", "--non-lts", fetch=raising(AssertionError("no network!")))
        self.assertEqual(code, 2)


class ParseFieldsTests(unittest.TestCase):
    VALID = ["major", "version", "os", "url"]

    def test_separators_are_equivalent(self):
        want = ["version", "os", "url"]
        for raw in (["version,", "os,", "url"],       # --fields version, os, url
                    ["version,os,url"],               # --fields version,os,url
                    ["version", "os", "url"],         # --fields version os url
                    ["version, os, url"],             # --fields "version, os, url"
                    ["version,os", "url"],            # mixed
                    ["  version ,  os  ,url  "]):
            with self.subTest(raw=raw):
                self.assertEqual(j.parse_fields(raw, self.VALID), want)

    def test_order_is_kept_and_duplicates_dropped(self):
        self.assertEqual(j.parse_fields(["url,os,url,major,os"], self.VALID), ["url", "os", "major"])

    def test_case_insensitive(self):
        self.assertEqual(j.parse_fields(["VERSION,Os"], self.VALID), ["version", "os"])

    def test_unknown_fields_are_named_with_the_valid_list(self):
        with self.assertRaisesRegex(ValueError, r"unknown field 'bogus' \(valid fields: major, version"):
            j.parse_fields(["version,bogus"], self.VALID)
        with self.assertRaisesRegex(ValueError, r"unknown fields 'a', 'b'"):
            j.parse_fields(["a,b"], self.VALID)

    def test_empty_selection(self):
        for raw in ([","], [" "], [",,"], [""], []):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "no field names"):
                j.parse_fields(raw, self.VALID)


class CellTests(unittest.TestCase):
    def test_rendering(self):
        self.assertEqual(j.cell(None), "")
        self.assertEqual(j.cell(True), "true")
        self.assertEqual(j.cell(False), "false")
        self.assertEqual(j.cell(0), "0")
        self.assertEqual(j.cell(25), "25")
        self.assertEqual(j.cell("x"), "x")


class FieldsTests(unittest.TestCase):
    BASE = ["--lts", "--os", "linux", "--arch", "x64"]

    def test_the_documented_example_in_every_spelling(self):
        want = [["version", "os", "url"]] + [
            [v, "linux", u] for v, u in [("25.0.1+1", "https://example.test/jdk-25/OpenJDK25U-jdk_x64_linux_hotspot_25.0.1_1.tar.gz"),
                                          ("21.0.1+1", "https://example.test/jdk-21/OpenJDK21U-jdk_x64_linux_hotspot_21.0.1_1.tar.gz"),
                                          ("17.0.1+1", "https://example.test/jdk-17/OpenJDK17U-jdk_x64_linux_hotspot_17.0.1_1.tar.gz"),
                                          ("8.0.1+1", "https://example.test/jdk-8/OpenJDK8U-jdk_x64_linux_hotspot_8.0.1_1.tar.gz")]]
        for spelling in (["version,", "os,", "url"], ["version,os,url"], ["version", "os", "url"],
                         ["version, os, url"]):
            with self.subTest(spelling=spelling):
                self.assertEqual(run_csv(*self.BASE, "--fields", *spelling), want)

    def test_only_the_requested_columns_in_the_requested_order(self):
        rows = run_csv(*self.BASE, "--count", "1", "--fields", "url,major,os")
        self.assertEqual(rows[0], ["url", "major", "os"])
        self.assertEqual(len(rows), 2)
        self.assertEqual([len(r) for r in rows], [3, 3])
        self.assertEqual(rows[1][1:], ["25", "linux"])

    def test_single_field_without_header_is_a_plain_list(self):
        code, out, _ = run(*self.BASE, "--fields", "major", "--csv", "--no-header")
        self.assertEqual(out, "25\n21\n17\n8\n")

    def test_every_release_field_can_be_selected(self):
        rows = run_csv("--lts", "--count", "1", "--fields", *j.RELEASE_COLUMNS)
        self.assertEqual(rows[0], j.RELEASE_COLUMNS)
        self.assertEqual(rows[1][:7], ["25", "25.0.1+1", "jdk-25.0.1+1", "true",
                                       "2026-01-25T00:00:00Z", "alpine-linux", "x64"])
        self.assertTrue(rows[1][8].endswith(".sha256.txt"))

    def test_table_output(self):
        _, out, _ = run(*self.BASE, "--count", "2", "--fields", "version,os")
        lines = out.splitlines()
        self.assertEqual(lines[0], lines[2])
        self.assertEqual(lines[0], lines[-1])
        self.assertRegex(lines[1], r"^\| version\s+\| os\s+\|$")
        self.assertEqual(len(lines), 6)
        self.assertNotIn("url", out)
        _, out, _ = run(*self.BASE, "--count", "2", "--fields", "version,os", "--no-header")
        self.assertEqual(len(out.splitlines()), 4)
        self.assertNotRegex(out.splitlines()[1], r"^\| version\s+\|")

    def test_sha256_url_is_shown_in_the_table_only_when_asked_for(self):
        self.assertNotIn("sha256", run("--lts", "--count", "1")[1])
        _, out, _ = run("--lts", "--count", "1", "--fields", "major,sha256_url")
        self.assertIn("sha256_url", out)
        self.assertIn(".sha256.txt", out)

    def test_booleans_render_as_true_false_in_table_and_csv(self):
        self.assertEqual(run_csv("--lts", "--count", "1", "--fields", "lts")[1], ["true"])
        self.assertIn("| true", run("--lts", "--count", "1", "--fields", "lts")[1])
        self.assertEqual(run_csv("--non-lts", "--fields", "lts")[1], ["false"])

    def test_json_is_a_flat_rows_list_with_the_usual_envelope(self):
        data = run_json(*self.BASE, "--count", "2", "--fields", "version,os,arch")
        self.assertEqual(list(data), ["source", "arch", "os", "rows"])
        self.assertEqual((data["source"], data["arch"], data["os"]),
                         ("Eclipse Temurin (Adoptium API)", "x64", "linux"))
        self.assertEqual(data["rows"], [
            {"version": "25.0.1+1", "os": "linux", "arch": "x64"},
            {"version": "21.0.1+1", "os": "linux", "arch": "x64"}])

    def test_json_keeps_native_types(self):
        row = run_json("--lts", "--count", "1", "--fields", "major,lts")["rows"][0]
        self.assertIs(row["lts"], True)
        self.assertEqual(row["major"], 25)

    def test_json_without_fields_is_unchanged(self):
        data = run_json("--lts", "--count", "1")
        self.assertIn("releases", data)
        self.assertNotIn("rows", data)

    def test_fields_not_selected_can_still_be_used_to_filter(self):
        rows = run_csv("--lts", "--os", "macos", "--arch", "aarch64", "--fields", "version")
        self.assertEqual(rows, [["version"], ["25.0.1+1"], ["21.0.1+1"]])

    def test_count_counts_rows_not_fields(self):
        self.assertEqual(len(run_csv("--fields", "major", "--count", "9")) - 1, 9)
        self.assertEqual(len(run_csv("--fields", "major,version,os", "--count", "9")) - 1, 9)

    def test_works_with_auto(self):
        rows = run_csv("--auto", "--lts", "--fields", "major,os,arch", detected=lambda: ("macos", "aarch64"))
        self.assertEqual(rows, [["major", "os", "arch"], ["25", "macos", "aarch64"],
                                ["21", "macos", "aarch64"]])

    def test_fields_can_come_before_other_flags(self):
        self.assertEqual(run_csv("--fields", "version", "os", "--lts", "--os", "linux", "--arch",
                                 "x64", "--count", "1"),
                         [["version", "os"], ["25.0.1+1", "linux"]])

    def test_early_access_fields(self):
        rows = run_csv("--ea", "--os", "linux", "--arch", "x64", "--fields", "build,released,url")
        self.assertEqual(rows, [["build", "released", "url"],
                                ["21", "2026-10-22", "https://download.java.net/java/early_access/"
                                 "jdk28/21/GPL/openjdk-28-ea+21_linux-x64_bin.tar.gz"]])
        data = run_json("--ea", "--fields", "version,os", "--arch", "aarch64")
        self.assertEqual(data["rows"], [{"version": "28-ea+21", "os": "linux"},
                                        {"version": "28-ea+21", "os": "macos"}])

    def test_list_commands(self):
        self.assertEqual(run_csv("--list-arch", "--fields", "arch", "--count", "2"),
                         [["arch"], ["x64"], ["x32"]])
        self.assertEqual(run_json("--list-os", "--fields", "os", "--count", "1")["rows"],
                         [{"os": "linux"}])

    def test_fields_valid_for_one_mode_are_rejected_in_another(self):
        for argv in [("--lts", "--fields", "build"),            # build is early-access only
                     ("--ea", "--fields", "release_name"),      # release_name is GA only
                     ("--ea", "--fields", "lts"),
                     ("--list-os", "--fields", "arch"),
                     ("--list-arch", "--fields", "os"),
                     ("--list-arch", "--fields", "version")]:
            with self.subTest(argv=argv):
                code, out, err = run(*argv)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("unknown field", err)

    def test_unknown_field_error_lists_the_valid_fields(self):
        code, _, err = run("--fields", "version,bogus")
        self.assertEqual(code, 2)
        self.assertIn("unknown field 'bogus'", err)
        self.assertIn("valid fields: major, version, release_name, lts", err)

    def test_empty_selection_is_rejected(self):
        for argv in [("--fields", ","), ("--fields", " ")]:
            with self.subTest(argv=argv):
                code, _, err = run(*argv)
                self.assertEqual(code, 2)
                self.assertIn("no field names given", err)

    def test_not_allowed_with_version_only(self):
        code, out, err = run("--fields", "os", "--version-only")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--fields: not allowed with --version-only", err)

    def test_validation_happens_before_any_network_access(self):
        code, _, _ = run("--fields", "bogus", fetch=raising(AssertionError("no network!")))
        self.assertEqual(code, 2)

    def test_no_match_is_still_an_error_with_fields(self):
        code, out, err = run("--os", "solaris", "--fields", "version")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("solaris", err)

    def test_default_output_is_unchanged_when_fields_is_not_used(self):
        self.assertEqual(run_csv("--lts", "--count", "1")[0], j.RELEASE_COLUMNS)
        self.assertEqual(run_csv("--ea", "--count", "1")[0], j.EA_COLUMNS)


class NetworkFailureTests(unittest.TestCase):
    """A failing network must produce a JSON error on stderr and exit code 1, never a traceback."""

    ERRORS = [
        ("URLError", j.urllib.request.URLError("name resolution failed")),
        ("HTTPError", j.urllib.request.HTTPError("u", 503, "unavailable", {}, None)),
        ("timeout", TimeoutError("timed out")),
        ("ConnectionResetError", ConnectionResetError(104, "Connection reset by peer")),
        ("ConnectionRefusedError", ConnectionRefusedError(111, "Connection refused")),
        ("BrokenPipeError", BrokenPipeError(32, "Broken pipe")),
        ("RemoteDisconnected", http.client.RemoteDisconnected("closed without response")),
        ("IncompleteRead", http.client.IncompleteRead(b"abc", 100)),
        ("BadStatusLine", http.client.BadStatusLine("garbage")),
        ("TLS failure", __import__("ssl").SSLError("handshake failure")),
        ("generic OSError", OSError("network is unreachable")),
    ]
    MODES = [("--lts",), ("--all",), ("--ea",), ("--list-arch",), ("--list-os",),
             ("--lts", "--version-only"), ("--lts", "--csv"), ("--auto", "--lts")]

    def assert_clean_failure(self, argv, fetch):
        code, out, err = run(*argv, fetch=fetch, detected=lambda: ("linux", "x64"))
        self.assertEqual(code, 1, f"{argv}: expected exit code 1")
        self.assertEqual(out, "", f"{argv}: nothing should reach stdout")
        self.assertIn("error", json.loads(err), f"{argv}: stderr should be a JSON error")

    def test_every_failure_in_every_mode(self):
        for error_name, exc in self.ERRORS:
            for argv in self.MODES:
                with self.subTest(error=error_name, argv=argv):
                    self.assert_clean_failure(argv, raising(exc))

    def test_failure_partway_through(self):
        """The first request succeeds and a later one drops, e.g. while fetching a release."""
        def drops_on_assets(url, accept):
            if "/assets/latest/" in url:
                raise ConnectionResetError(104, "Connection reset by peer")
            return fake_fetch(url, accept)

        def drops_on_ea_page(url, accept):
            if url.endswith("/28/"):
                raise http.client.IncompleteRead(b"<html", 5000)
            return fake_fetch(url, accept)

        self.assert_clean_failure(("--lts",), drops_on_assets)
        self.assert_clean_failure(("--ea",), drops_on_ea_page)

    def test_error_message_mentions_what_failed(self):
        exc = ConnectionResetError(104, "Connection reset by peer")
        expected = {("--lts",): "JDK releases", ("--ea",): "early-access",
                    ("--list-arch",): "architectures", ("--list-os",): "operating systems"}
        for argv, text in expected.items():
            with self.subTest(argv=argv):
                _, _, err = run(*argv, fetch=raising(exc))
                self.assertIn(text, json.loads(err)["error"])
                self.assertIn("Connection reset", json.loads(err)["error"])

    def test_malformed_json_is_reported(self):
        for argv in [("--lts",), ("--list-arch",), ("--list-os",)]:
            with self.subTest(argv=argv):
                self.assert_clean_failure(argv, lambda url, accept: "<html>not json</html>")

    def test_unexpected_response_shape_is_reported(self):
        def wrong_shape(url, accept):
            return json.dumps({"unexpected": True}) if "available_releases" in url \
                else fake_fetch(url, accept)
        self.assert_clean_failure(("--lts",), wrong_shape)

    def test_retry_free_success_still_works_after_failures(self):
        self.assert_clean_failure(("--lts",), raising(BrokenPipeError()))
        self.assertEqual(run("--lts", "--version-only")[0], 0)


class NetworkErrorsConstantTests(unittest.TestCase):
    def test_covers_connection_and_protocol_errors(self):
        for exc_type in (ConnectionResetError, BrokenPipeError, TimeoutError,
                         j.urllib.request.URLError, http.client.IncompleteRead,
                         http.client.RemoteDisconnected, http.client.BadStatusLine):
            with self.subTest(exc=exc_type.__name__):
                self.assertTrue(issubclass(exc_type, j.NETWORK_ERRORS))

    def test_does_not_swallow_programming_errors(self):
        for exc_type in (TypeError, AttributeError, NameError, AssertionError):
            with self.subTest(exc=exc_type.__name__):
                self.assertFalse(issubclass(exc_type, j.NETWORK_ERRORS))


class FetchTests(unittest.TestCase):
    def test_fetch_decodes_the_body_and_sends_headers(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = "héllo".encode("utf-8")
        with mock.patch.object(j.urllib.request, "urlopen", return_value=response) as urlopen:
            body = j.fetch("https://example.test/x", "application/json")
        self.assertEqual(body, "héllo")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/x")
        self.assertEqual(request.get_header("Accept"), "application/json")
        self.assertEqual(request.get_header("User-agent"), "jdk-versions/1.0")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], j.TIMEOUT)

    def test_urls_use_https(self):
        self.assertTrue(j.API.startswith("https://"))
        self.assertTrue(j.EA_SITE.startswith("https://"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
