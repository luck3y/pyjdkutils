#!/usr/bin/env python3
"""Offline tests for jdk_versions.py.

The network layer (jdk_versions.fetch) is replaced with canned responses, so these tests need no
network access and do not depend on whichever JDK versions happen to be current.

Run with:
    python3 -m unittest -v test_jdk_versions
"""
import contextlib
import csv
import hashlib
import http.client
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
import zipfile
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


def payload_for(url):
    """The fake file content served for a download URL."""
    return b"JDK:" + url.encode()


def fake_fetch(url, accept):
    """Stand-in for jdk_versions.fetch that serves canned responses."""
    if url.endswith((".sha256.txt", ".sha256")):
        data = payload_for(url.rsplit(".sha256", 1)[0])
        return hashlib.sha256(data).hexdigest() + "  " + url.rsplit("/", 1)[-1]
    # Both the Temurin and Semeru API bases serve the same canned data for tests.
    for _source_key, (_api_base, _jvm_impl, _label) in j.SOURCES.items():
        if url.startswith(_api_base):
            path = url[len(_api_base):]
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
        self.assertEqual([(r[0], r[6], r[7]) for r in rows], [("25", "linux", "x64")])

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
                                   "source", "os", "arch", "url", "sha256_url"])
        self.assertEqual(len(rows) - 1, 6 + 6 + 3 + 2)
        self.assertTrue(all(len(r) == 10 for r in rows))
        self.assertEqual(rows[1][:8], ["25", "25.0.1+1", "jdk-25.0.1+1", "true",
                                       "2026-01-25T00:00:00Z",
                                       j.SOURCES["temurin"][2], "alpine-linux", "x64"])
        self.assertTrue(rows[1][9].endswith(".sha256.txt"))

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
        self.assertEqual(rows[0], ["major", "version", "build", "released", "source", "os", "arch",
                                   "url", "sha256_url"])
        self.assertEqual([(r[5], r[6]) for r in rows[1:]], [("linux", "aarch64"), ("linux", "x64")])
        self.assertEqual(rows[1][:4], ["28", "28-ea+21", "21", "2026-10-22"])
        self.assertEqual(rows[1][4], "jdk.java.net (Oracle-led OpenJDK)")

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
        self.assertEqual([(r[5], r[6]) for r in rows[1:]], [("linux", "x64")])
        self.assertEqual(run("--auto", "--non-lts", "--version-only", detected=det)[1], "27\n")
        rows = run_csv("--auto", "--lts", "--count", "1", detected=det)
        self.assertEqual([(r[0], r[6], r[7]) for r in rows[1:]], [("25", "linux", "x64")])

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
        self.assertEqual(rows[1][:8], ["25", "25.0.1+1", "jdk-25.0.1+1", "true",
                                       "2026-01-25T00:00:00Z",
                                       j.SOURCES["temurin"][2], "alpine-linux", "x64"])
        self.assertTrue(rows[1][9].endswith(".sha256.txt"))

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
        self.assertIn("valid fields: major, version, release_name, lts, released, source", err)

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


def closed_http_error():
    """An HTTPError that owns no open file, so garbage collecting it doesn't warn."""
    error = j.urllib.request.HTTPError("u", 503, "unavailable", {}, io.BytesIO())
    error.close()
    return error


class NetworkFailureTests(unittest.TestCase):
    """A failing network must produce a JSON error on stderr and exit code 1, never a traceback."""

    ERRORS = [
        ("URLError", j.urllib.request.URLError("name resolution failed")),
        ("HTTPError", closed_http_error()),
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


class FilenameFromUrlTests(unittest.TestCase):
    def test_takes_the_last_path_segment_and_decodes_it(self):
        self.assertEqual(j.filename_from_url("https://h.test/a/b/OpenJDK_x64.tar.gz"),
                         "OpenJDK_x64.tar.gz")
        self.assertEqual(j.filename_from_url("https://h.test/a/openjdk-28-ea%2B21_bin.zip?x=1"),
                         "openjdk-28-ea+21_bin.zip")

    def test_rejects_unsafe_urls(self):
        for url in ("http://h.test/a.zip", "https://h.test/", "https://h.test/a/..",
                    "https://h.test/a/%2E%2E", "https://h.test/a/b%5Cc.zip",
                    "https://h.test/a/c%3Ad.zip", "ftp://h.test/a.zip"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    j.filename_from_url(url)

    def test_path_traversal_in_the_name_is_flattened(self):
        self.assertEqual(j.filename_from_url("https://h.test/a/..%2F..%2Fevil.zip"), "evil.zip")


class ParseSha256Tests(unittest.TestCase):
    DIGEST = "ab" * 32

    def test_accepts_the_common_layouts(self):
        for text in (self.DIGEST, self.DIGEST + "\n", f"{self.DIGEST}  file.tar.gz\n",
                     self.DIGEST.upper()):
            with self.subTest(text=text):
                self.assertEqual(j.parse_sha256(text), self.DIGEST)

    def test_rejects_anything_else(self):
        for text in ("", "   ", "nothex", "ab" * 31, "ab" * 33, "<html>" + self.DIGEST):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    j.parse_sha256(text)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def fake_urlopen(corrupt=(), fail=(), seen=None, announce=False):
    """Stand-in for urllib.request.urlopen that serves payload_for(url).

    URLs containing any string in `corrupt` return wrong bytes; those in `fail` raise OSError.
    With announce=True the response carries a Content-Length header, as real servers send.
    """
    def urlopen(req, timeout=None):
        url = req.full_url
        if seen is not None:
            seen.append((url, timeout))
        if any(f in url for f in fail):
            raise OSError("connection refused")
        data = payload_for(url)
        if any(c in url for c in corrupt):
            data = data + b"tampered"
        resp = FakeResponse(data)
        resp.headers = {"Content-Length": str(len(data))} if announce else {}
        return resp
    return urlopen


class DownloadTests(unittest.TestCase):
    BASE = ("--lts", "--os", "linux", "--arch", "x64", "--download")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "out")  # does not exist yet

    def go(self, *argv, urlopen=None, **kw):
        with mock.patch.object(j.urllib.request, "urlopen", urlopen or fake_urlopen()):
            return run(*argv, "--target-directory", self.dir, **kw)

    def files(self):
        return sorted(os.listdir(self.dir)) if os.path.isdir(self.dir) else []

    def test_downloads_every_listed_row_and_creates_the_directory(self):
        code, out, err = self.go(*self.BASE, "--csv")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.files(), [
            "OpenJDK17U-jdk_x64_linux_hotspot_17.0.1_1.tar.gz",
            "OpenJDK21U-jdk_x64_linux_hotspot_21.0.1_1.tar.gz",
            "OpenJDK25U-jdk_x64_linux_hotspot_25.0.1_1.tar.gz",
            "OpenJDK8U-jdk_x64_linux_hotspot_8.0.1_1.tar.gz",
        ])
        self.assertIn("downloaded 4 of 4 file(s)", err)

    def test_file_contents_are_what_the_server_sent(self):
        self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1", "--download")
        (name,) = self.files()
        with open(os.path.join(self.dir, name), "rb") as f:
            self.assertEqual(f.read(), payload_for(
                "https://example.test/jdk-25/OpenJDK25U-jdk_x64_linux_hotspot_25.0.1_1.tar.gz"))

    def test_the_normal_listing_still_goes_to_stdout(self):
        code, out, err = self.go(*self.BASE, "--count", "2")
        self.assertEqual(code, 0, err)
        self.assertIn("OpenJDK25U", out)
        self.assertNotIn("downloading", out)
        self.assertIn("downloading OpenJDK25U", err)

    def test_json_on_stdout_stays_valid_while_downloading(self):
        code, out, err = self.go(*self.BASE, "--count", "2", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["releases"]), 2)

    def test_count_limits_how_many_files_are_downloaded(self):
        self.go(*self.BASE, "--count", "2")
        self.assertEqual(len(self.files()), 2)

    def test_filters_apply_to_what_is_downloaded(self):
        self.go("--lts", "--os", "mac", "--arch", "x64", "--download")
        self.assertEqual(self.files(), ["OpenJDK17U-jdk_x64_mac_hotspot_17.0.1_1.tar.gz",
                                        "OpenJDK21U-jdk_x64_mac_hotspot_21.0.1_1.tar.gz",
                                        "OpenJDK25U-jdk_x64_mac_hotspot_25.0.1_1.tar.gz"])

    def test_works_with_early_access_builds(self):
        code, _, err = self.go("--ea", "--os", "linux", "--arch", "x64", "--download")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.files(), ["openjdk-28-ea+21_linux-x64_bin.tar.gz"])

    def test_sends_the_timeout_and_uses_https(self):
        seen = []
        self.go(*self.BASE, "--count", "1", urlopen=fake_urlopen(seen=seen))
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][0].startswith("https://"))
        self.assertEqual(seen[0][1], j.TIMEOUT)

    def test_a_checksum_mismatch_fails_and_leaves_no_file(self):
        code, out, err = self.go(*self.BASE, "--count", "1",
                                 urlopen=fake_urlopen(corrupt=["jdk-25"]))
        self.assertEqual(code, 1)
        self.assertEqual(self.files(), [])
        self.assertIn("checksum mismatch", err)
        self.assertIn("downloaded 0 of 1", err)

    def test_one_failure_does_not_stop_the_others(self):
        code, _, err = self.go(*self.BASE, "--count", "3",
                               urlopen=fake_urlopen(fail=["jdk-21"]))
        self.assertEqual(code, 1)
        self.assertEqual(self.files(), ["OpenJDK17U-jdk_x64_linux_hotspot_17.0.1_1.tar.gz",
                                        "OpenJDK25U-jdk_x64_linux_hotspot_25.0.1_1.tar.gz"])
        self.assertIn("failed to download", err)
        self.assertIn("downloaded 2 of 3", err)
        for line in err.splitlines():
            if line.startswith("{"):
                self.assertIn("error", json.loads(line))

    def test_a_bad_checksum_file_fails_cleanly(self):
        def bad_fetch(url, accept):
            return "not a digest" if url.endswith(".sha256.txt") else fake_fetch(url, accept)
        code, _, err = self.go(*self.BASE, "--count", "1", fetch=bad_fetch)
        self.assertEqual(code, 1)
        self.assertEqual(self.files(), [])
        self.assertIn("SHA-256", err)

    def test_an_unusable_target_directory_is_a_runtime_error(self):
        with tempfile.NamedTemporaryFile() as blocker:
            self.dir = os.path.join(blocker.name, "sub")  # a file is in the way
            code, out, err = self.go(*self.BASE, "--count", "1")
        self.assertEqual(code, 1)
        self.assertIn("cannot use target directory", err)
        self.assertIn(f"cannot use target directory '{self.dir}'",
                      json.loads(err.strip())["error"])

    def test_an_existing_directory_is_reused(self):
        os.makedirs(self.dir)
        code, _, err = self.go(*self.BASE, "--count", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.files()), 1)

    def test_no_part_files_are_left_behind(self):
        self.go(*self.BASE, "--count", "2")
        self.assertFalse([f for f in self.files() if f.endswith(".part")])

    def test_nothing_is_downloaded_without_the_flag(self):
        with mock.patch.object(j.urllib.request, "urlopen",
                               side_effect=AssertionError("no download expected")):
            code, _, _ = run("--lts", "--count", "1")
        self.assertEqual(code, 0)

    def test_no_match_means_no_download_and_exit_1(self):
        code, _, err = self.go("--os", "foo", "--download")
        self.assertEqual(code, 1)
        self.assertEqual(self.files(), [])

    def test_a_download_that_raises_unexpectedly_still_cleans_up(self):
        def boom(req, timeout=None):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.go(*self.BASE, "--count", "1", urlopen=boom)
        self.assertEqual(self.files(), [])


class DownloadArgumentTests(unittest.TestCase):
    def assert_rejected(self, *argv):
        code, out, err = run(*argv, fetch=raising(AssertionError("no network!")))
        self.assertEqual(code, 2, f"{argv} should be rejected (stderr: {err})")
        self.assertEqual(out, "")

    def test_target_directory_needs_download(self):
        self.assert_rejected("--lts", "--target-directory", "x")

    def test_not_allowed_with_list_or_version_only_modes(self):
        for mode in ("--list-arch", "--list-os", "--version-only"):
            with self.subTest(mode=mode):
                self.assert_rejected(mode, "--download", "--target-directory", "x")

    def test_target_directory_needs_a_value(self):
        self.assert_rejected("--download", "--target-directory")

    def test_both_flags_appear_in_the_help(self):
        _, out, _ = run("--h")
        self.assertIn("--download", out)
        self.assertIn("--target-directory", out)


class FakeStream(io.StringIO):
    """A text stream that claims to be (or not to be) a terminal."""

    def __init__(self, tty=True):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return ANSI.sub("", text)


class StyleTests(unittest.TestCase):
    def test_style_wraps_and_resets(self):
        self.assertEqual(j.style("x", j.BOLD, j.CYAN), "\x1b[1;36mx\x1b[0m")
        self.assertEqual(j.style("x"), "x")


class InteractiveTests(unittest.TestCase):
    def env(self, **values):
        env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "TERM")}
        env.update(values)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_a_terminal_is_interactive(self):
        with self.env(TERM="xterm"):
            self.assertTrue(j.is_interactive(FakeStream(True)))

    def test_pipes_files_and_dumb_terminals_are_not(self):
        with self.env(TERM="xterm"):
            self.assertFalse(j.is_interactive(FakeStream(False)))
            self.assertFalse(j.is_interactive(io.StringIO()))
            self.assertFalse(j.is_interactive(object()))
        with self.env(TERM="dumb"):
            self.assertFalse(j.is_interactive(FakeStream(True)))

    def test_color_needs_a_terminal(self):
        with self.env(TERM="xterm"), mock.patch.object(j.sys, "platform", "linux"):
            self.assertTrue(j.use_color(FakeStream(True)))
            self.assertFalse(j.use_color(FakeStream(False)))

    def test_no_color_turns_color_off_even_on_a_terminal(self):
        for value in ("1", "yes", "anything"):
            with self.subTest(value=value), self.env(TERM="xterm", NO_COLOR=value):
                self.assertFalse(j.use_color(FakeStream(True)))

    def test_an_empty_no_color_is_ignored(self):
        with self.env(TERM="xterm", NO_COLOR=""), mock.patch.object(j.sys, "platform", "linux"):
            self.assertTrue(j.use_color(FakeStream(True)))

    def test_windows_needs_a_terminal_that_understands_escapes(self):
        with mock.patch.object(j.sys, "platform", "win32"):
            with self.env(TERM="xterm"):
                self.assertFalse(j.use_color(FakeStream(True)))
            with self.env(TERM="xterm", WT_SESSION="abc"):
                self.assertTrue(j.use_color(FakeStream(True)))


class ColorTableTests(unittest.TestCase):
    COLUMNS = ["major", "version", "lts", "url"]
    ROWS = [
        {"major": 25, "version": "25.0.1+1", "lts": True, "url": "https://x.test/a"},
        {"major": 27, "version": "27", "lts": False, "url": "https://x.test/bb"},
    ]

    def test_plain_output_has_no_escape_codes(self):
        self.assertNotIn("\x1b", j.format_table(self.COLUMNS, self.ROWS))
        self.assertNotIn("\x1b", j.format_table(self.COLUMNS, self.ROWS, color=False))

    def test_color_only_adds_escape_codes(self):
        plain = j.format_table(self.COLUMNS, self.ROWS)
        colored = j.format_table(self.COLUMNS, self.ROWS, color=True)
        self.assertIn("\x1b[", colored)
        self.assertEqual(strip_ansi(colored), plain)

    def test_columns_stay_aligned_when_colored(self):
        colored = j.format_table(self.COLUMNS, self.ROWS, color=True)
        widths = {len(line) for line in strip_ansi(colored).splitlines()}
        self.assertEqual(len(widths), 1)

    def test_header_cells_are_bold_cyan(self):
        colored = j.format_table(self.COLUMNS, self.ROWS, color=True)
        self.assertIn("\x1b[1;36mmajor\x1b[0m", colored)

    def test_lts_is_green_and_non_lts_is_yellow(self):
        lines = j.format_table(self.COLUMNS, self.ROWS, color=True).splitlines()
        # The padding is inside the color so that the columns stay aligned.
        self.assertIn("\x1b[32mtrue \x1b[0m", [l for l in lines if "25.0.1" in l][0])
        self.assertIn("\x1b[33mfalse\x1b[0m", [l for l in lines if "27" in l and "false" in l][0])

    def test_version_and_url_columns_have_their_own_colors(self):
        colored = j.format_table(self.COLUMNS, self.ROWS, color=True)
        self.assertIn("\x1b[1m25.0.1+1\x1b[0m", colored)
        self.assertIn("\x1b[34mhttps://x.test/a \x1b[0m", colored)

    def test_cell_codes(self):
        self.assertEqual(j.cell_codes("lts", "true"), (j.GREEN,))
        self.assertEqual(j.cell_codes("lts", "false"), (j.YELLOW,))
        self.assertEqual(j.cell_codes("released", "2026-01-01"), ())
        self.assertEqual(j.cell_codes("release_name", "jdk-25"), ())

    def test_no_header_color_table(self):
        out = j.format_table(self.COLUMNS, self.ROWS, header=False, color=True)
        self.assertEqual(len(out.splitlines()), 4)
        self.assertNotIn("\x1b[1;36m", out)

    def test_emit_colors_only_the_table(self):
        payload = {"source": "s", "arch": None, "os": None}
        for fmt in ("csv", "json"):
            with self.subTest(fmt=fmt):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    j.emit(payload, fmt, self.COLUMNS, self.ROWS, color=True)
                self.assertNotIn("\x1b", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            j.emit(payload, "table", self.COLUMNS, self.ROWS, color=True)
        self.assertIn("\x1b[", out.getvalue())


class GroupColorTests(unittest.TestCase):
    COLUMNS = ["major", "os", "arch"]

    def rows(self, *triples):
        return [{"major": m, "os": o, "arch": a} for m, o, a in triples]

    def codes(self, text, word):
        """The escape code that colors `word`, found in the colored text."""
        match = re.search(r"\x1b\[([0-9;]+)m" + re.escape(word), text)
        return match.group(1) if match else None

    def test_every_known_os_and_arch_has_its_own_fixed_color(self):
        for column, colors in (("os", j.OS_COLORS), ("arch", j.ARCH_COLORS)):
            for name, code in colors.items():
                with self.subTest(column=column, name=name):
                    self.assertEqual(j.cell_codes(column, name), (code,))

    def test_colors_are_distinct_within_a_column(self):
        self.assertEqual(len(set(j.OS_COLORS.values())), len(j.OS_COLORS))
        self.assertEqual(len(set(j.ARCH_COLORS.values())), len(j.ARCH_COLORS))

    def test_os_and_arch_never_use_red_white_or_black(self):
        avoided = {"30", "31", "37", "90", "91"}
        self.assertFalse(avoided & set(j.OS_COLORS.values()))
        self.assertFalse(avoided & set(j.ARCH_COLORS.values()))

    def test_all_the_platforms_the_script_knows_about_have_a_color(self):
        self.assertTrue(set(j.SYSTEM_OSES.values()) <= set(j.OS_COLORS))
        self.assertTrue(set(j.MACHINE_ARCHES.values()) <= set(j.ARCH_COLORS))

    def test_an_unknown_name_gets_a_stable_color_from_the_palette(self):
        first = j.cell_codes("os", "haiku")
        self.assertEqual(first, j.cell_codes("os", "haiku"))
        self.assertIn(first[0], j.FALLBACK_COLORS)
        self.assertIn(j.cell_codes("arch", "mips")[0], j.FALLBACK_COLORS)

    def test_the_fallback_palette_has_no_red_and_names_spread_across_it(self):
        self.assertNotIn("91", j.FALLBACK_COLORS)
        used = {j.cell_codes("os", name)[0] for name in ("a", "b", "c", "d", "e", "haiku", "plan9")}
        self.assertGreater(len(used), 2)
        self.assertTrue(used <= set(j.FALLBACK_COLORS))

    def test_blank_os_and_arch_are_not_colored(self):
        self.assertEqual(j.cell_codes("os", ""), ())
        self.assertEqual(j.cell_codes("arch", ""), ())

    def test_the_same_os_has_the_same_color_in_every_row(self):
        rows = self.rows((25, "linux", "x64"), (25, "macos", "aarch64"), (21, "linux", "aarch64"))
        text = j.format_table(self.COLUMNS, rows, color=True)
        self.assertEqual(len(re.findall(r"\x1b\[35mlinux", text)), 2)
        self.assertEqual(len(re.findall(r"\x1b\[36mmacos", text)), 1)
        self.assertEqual(len(re.findall(r"\x1b\[96mx64", text)), 1)
        self.assertEqual(len(re.findall(r"\x1b\[92maarch64", text)), 2)

    def test_each_major_gets_the_next_color_in_order_of_appearance(self):
        rows = self.rows((27, "linux", "x64"), (27, "macos", "x64"), (25, "linux", "x64"),
                         (21, "linux", "x64"), (27, "windows", "x64"))
        lines = j.format_table(self.COLUMNS, rows, color=True, header=False).splitlines()[1:-1]
        seen = [re.search(r"\x1b\[1;(\d+)m", line).group(1) for line in lines]
        first, second, third = j.MAJOR_COLORS[:3]
        self.assertEqual(seen, [first, first, second, third, first])

    def test_major_colors_wrap_around_after_the_palette_is_used_up(self):
        count = len(j.MAJOR_COLORS)
        rows = [{"major": 100 + i} for i in range(count + 1)]
        lines = j.format_table(["major"], rows, color=True, header=False).splitlines()[1:-1]
        seen = [re.search(r"\x1b\[1;(\d+)m", line).group(1) for line in lines]
        self.assertEqual(seen[:count], j.MAJOR_COLORS)
        self.assertEqual(seen[count], j.MAJOR_COLORS[0])

    def test_neighbouring_majors_differ(self):
        rows = [{"major": m} for m in (27, 25, 21, 17, 11, 8)]
        lines = j.format_table(["major"], rows, color=True, header=False).splitlines()[1:-1]
        seen = [re.search(r"\x1b\[1;(\d+)m", line).group(1) for line in lines]
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])))

    def test_each_table_starts_over_with_the_first_color(self):
        rows = self.rows((25, "linux", "x64"))
        a = j.format_table(self.COLUMNS, rows, color=True)
        b = j.format_table(self.COLUMNS, rows, color=True)
        self.assertEqual(a, b)
        self.assertEqual(self.codes(a, "25"), "1;" + j.MAJOR_COLORS[0])

    def test_major_is_uncolored_without_a_shared_dict(self):
        self.assertEqual(j.cell_codes("major", "25"), ())
        self.assertEqual(j.cell_codes("major", ""), (), "a blank major is never colored")
        shared = {}
        self.assertEqual(j.cell_codes("major", "25", shared), ("1", j.MAJOR_COLORS[0]))
        self.assertEqual(shared, {"25": j.MAJOR_COLORS[0]})

    def test_only_the_columns_that_are_shown_are_colored(self):
        rows = self.rows((25, "linux", "x64"))
        text = j.format_table(["os"], rows, color=True)
        self.assertIn("\x1b[35mlinux", text)
        self.assertNotIn("\x1b[96m", text)
        self.assertNotIn("\x1b[1;", text.replace("\x1b[1;36m", ""))

    def test_grouping_adds_only_escape_codes(self):
        rows = self.rows((27, "linux", "x64"), (25, "windows", "aarch64"), (25, "macos", "x64"))
        plain = j.format_table(self.COLUMNS, rows)
        colored = j.format_table(self.COLUMNS, rows, color=True)
        self.assertEqual(strip_ansi(colored), plain)
        self.assertEqual(len({len(l) for l in strip_ansi(colored).splitlines()}), 1)

    def test_a_row_from_the_script_is_grouped_end_to_end(self):
        out = io.StringIO()
        data = PlatformFixture.rows()
        with contextlib.redirect_stdout(out):
            j.emit({"source": "s", "arch": None, "os": None}, "table", j.RELEASE_COLUMNS, data,
                   color=True)
        text = out.getvalue()
        self.assertIn("\x1b[35mlinux", text)
        self.assertIn("\x1b[36mmacos", text)
        self.assertIn("\x1b[94mwindows", text)
        self.assertIn("\x1b[96mx64", text)
        self.assertIn("\x1b[92maarch64", text)


class PlatformFixture:
    @staticmethod
    def rows():
        base = {"release_name": "r", "lts": True, "released": "d", "url": "u", "sha256_url": "s"}
        return [{**base, "major": m, "version": f"{m}.0.1", "os": o, "arch": a}
                for m, o, a in ((27, "linux", "x64"), (27, "macos", "aarch64"),
                                (25, "windows", "x64"), (25, "linux", "aarch64"))]


class FormatSizeTests(unittest.TestCase):
    def test_units(self):
        for num, text in [(0, "0 B"), (512, "512 B"), (1023, "1023 B"), (1024, "1.0 KiB"),
                          (1536, "1.5 KiB"), (1024 ** 2, "1.0 MiB"),
                          (int(141.3 * 1024 ** 2), "141.3 MiB"), (1024 ** 3, "1.0 GiB"),
                          (int(2.5 * 1024 ** 3), "2.5 GiB"), (5 * 1024 ** 4, "5120.0 GiB")]:
            with self.subTest(num=num):
                self.assertEqual(j.format_size(num), text)


class ContentLengthTests(unittest.TestCase):
    def resp(self, value):
        return mock.Mock(headers={} if value is None else {"Content-Length": value})

    def test_reads_a_valid_length(self):
        self.assertEqual(j.content_length(self.resp("12345")), 12345)

    def test_missing_zero_negative_or_bad_lengths_are_unknown(self):
        for value in (None, "0", "-5", "abc", ""):
            with self.subTest(value=value):
                self.assertIsNone(j.content_length(self.resp(value)))

    def test_a_response_without_headers_is_unknown(self):
        self.assertIsNone(j.content_length(object()))
        self.assertIsNone(j.content_length(mock.Mock(headers=None)))


class TerminalWidthTests(unittest.TestCase):
    def test_uses_the_terminal_size(self):
        stream = mock.Mock()
        with mock.patch.object(j.os, "get_terminal_size", return_value=os.terminal_size((132, 40))):
            self.assertEqual(j.terminal_width(stream), 132)

    def test_falls_back_when_it_cannot_be_found_or_is_zero(self):
        with mock.patch.object(j.os, "get_terminal_size", side_effect=OSError):
            self.assertEqual(j.terminal_width(mock.Mock()), 80)
        self.assertEqual(j.terminal_width(io.StringIO()), 80)
        with mock.patch.object(j.os, "get_terminal_size", return_value=os.terminal_size((0, 0))):
            self.assertEqual(j.terminal_width(mock.Mock()), 80)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class ProgressBarTests(unittest.TestCase):
    def make(self, total=1000, color=False, width=100, name="jdk.tar.gz"):
        self.clock = Clock()
        self.stream = FakeStream()
        return j.ProgressBar(name, total, stream=self.stream, color=color, width=width,
                             clock=self.clock)

    def test_line_shows_name_bar_percent_sizes_and_speed(self):
        bar = self.make(total=1000)
        bar.done = 500
        self.clock.now += 2
        text, length = bar.line()
        self.assertEqual(text, "jdk.tar.gz [##########----------]  50% 500 B/1000 B 250 B/s")
        self.assertEqual(length, len(text))

    def test_the_bar_fills_up_and_never_overflows(self):
        bar = self.make(total=1000)
        self.clock.now += 1
        bar.done = 0
        self.assertIn("[--------------------]   0%", bar.line()[0])
        bar.done = 1000
        self.assertIn("[####################] 100%", bar.line()[0])
        bar.done = 5000  # more data than announced
        self.assertIn("[####################] 100%", bar.line()[0])

    def test_unknown_total_has_no_bar_or_percentage(self):
        bar = self.make(total=None)
        bar.done = 2048
        self.clock.now += 1
        text, _ = bar.line()
        self.assertEqual(text, "jdk.tar.gz  2.0 KiB 2.0 KiB/s")
        self.assertNotIn("[", text)
        self.assertNotIn("%", text)

    def test_a_long_name_is_shortened_to_fit_the_width(self):
        bar = self.make(total=1000, width=60, name="OpenJDK25U-jdk_x64_linux_hotspot_25.0.5_8.tar.gz")
        bar.done = 100
        self.clock.now += 1
        text, length = bar.line()
        self.assertLessEqual(length, 59)
        self.assertIn("...", text)
        self.assertTrue(text.endswith("B/s"))

    def test_a_tiny_width_does_not_crash(self):
        bar = self.make(width=5, name="OpenJDK25U-jdk_x64_linux_hotspot_25.0.5_8.tar.gz")
        bar.done = 1
        self.clock.now += 1
        text, _ = bar.line()
        self.assertTrue(text.startswith("OpenJ..."))  # the name is cut to a minimum of 8 columns
        bar.draw()
        bar.finish()

    def test_color_adds_escapes_and_plain_does_not(self):
        bar = self.make(color=False)
        bar.done = 500
        self.assertNotIn("\x1b", bar.line()[0])
        colored = self.make(color=True)
        colored.done = 500
        text, length = colored.line()
        self.assertIn("\x1b[32m##########\x1b[0m", text)
        self.assertEqual(len(strip_ansi(text)), length)

    def test_updates_are_throttled(self):
        bar = self.make()
        bar.update(100)  # no time has passed
        self.assertEqual(self.stream.getvalue(), "")
        self.clock.now += 2 * j.BAR_INTERVAL
        bar.update(200)
        self.assertEqual(self.stream.getvalue().count("\r"), 1)
        bar.update(300)  # too soon again
        self.assertEqual(self.stream.getvalue().count("\r"), 1)

    def test_drawing_redraws_in_place_and_blanks_leftovers(self):
        bar = self.make()
        bar.drawn = 200  # a longer line was drawn before
        bar.draw()
        out = self.stream.getvalue()
        self.assertTrue(out.startswith("\r"))
        self.assertNotIn("\n", out)
        self.assertGreaterEqual(len(out) - 1, 200)

    def test_finish_draws_the_final_state_and_ends_the_line(self):
        bar = self.make()
        bar.done = 1000
        self.clock.now += 1
        bar.finish()
        self.assertTrue(self.stream.getvalue().endswith("\n"))
        self.assertIn("100%", self.stream.getvalue())

    def test_abort_erases_the_bar(self):
        bar = self.make()
        bar.abort()  # nothing drawn yet: nothing to erase
        self.assertEqual(self.stream.getvalue(), "")
        bar.drawn = 30
        bar.abort()
        self.assertEqual(self.stream.getvalue(), "\r" + " " * 30 + "\r")


class FakeBar:
    def __init__(self, total):
        self.total, self.updates, self.state = total, [], "open"

    def update(self, done):
        self.updates.append(done)

    def finish(self):
        self.state = "finished"

    def abort(self):
        self.state = "aborted"


class DownloadProgressTests(unittest.TestCase):
    URL = "https://example.test/jdk.tar.gz"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = os.path.join(tmp.name, "jdk.tar.gz")
        self.bars = []

    def make_bar(self, total):
        self.bars.append(FakeBar(total))
        return self.bars[-1]

    def download(self, urlopen):
        with mock.patch.object(j.urllib.request, "urlopen", urlopen), \
                mock.patch.object(j, "fetch", fake_fetch):
            j.download_verified(self.URL, self.URL + ".sha256.txt", self.dest, self.make_bar)

    def test_progress_is_reported_and_finished_on_success(self):
        with mock.patch.object(j, "CHUNK_SIZE", 10):
            self.download(fake_urlopen())
        (bar,) = self.bars
        self.assertEqual(bar.state, "finished")
        size = len(payload_for(self.URL))
        self.assertEqual(bar.updates[-1], size)
        self.assertEqual(bar.updates, sorted(bar.updates))
        self.assertGreater(len(bar.updates), 1)

    def test_the_total_comes_from_content_length(self):
        def urlopen(req, timeout=None):
            resp = FakeResponse(payload_for(req.full_url))
            resp.headers = {"Content-Length": str(len(payload_for(req.full_url)))}
            return resp
        self.download(urlopen)
        self.assertEqual(self.bars[0].total, len(payload_for(self.URL)))

    def test_an_unknown_total_is_passed_as_none(self):
        self.download(fake_urlopen())
        self.assertIsNone(self.bars[0].total)

    def test_the_bar_is_aborted_when_the_checksum_fails(self):
        with self.assertRaises(ValueError):
            self.download(fake_urlopen(corrupt=["jdk"]))
        self.assertEqual(self.bars[0].state, "aborted")
        self.assertFalse(os.path.exists(self.dest))

    def test_the_bar_is_aborted_when_the_connection_fails_midway(self):
        class Dying(FakeResponse):
            def read(self, n=-1):
                raise ConnectionResetError("reset")
        with self.assertRaises(ConnectionResetError):
            self.download(lambda req, timeout=None: Dying(b"x"))
        self.assertEqual(self.bars[0].state, "aborted")

    def test_no_bar_is_made_when_the_connection_cannot_be_opened(self):
        with self.assertRaises(OSError):
            self.download(fake_urlopen(fail=["jdk"]))
        self.assertEqual(self.bars, [])

    def test_no_progress_callback_works_as_before(self):
        with mock.patch.object(j.urllib.request, "urlopen", fake_urlopen()), \
                mock.patch.object(j, "fetch", fake_fetch):
            j.download_verified(self.URL, self.URL + ".sha256.txt", self.dest)
        self.assertTrue(os.path.exists(self.dest))


class PlainFlagTests(unittest.TestCase):
    """--plain, and how main() decides whether to color and whether to draw a progress bar."""

    BASE = ("--lts", "--os", "linux", "--arch", "x64", "--count", "1")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "out")
        env = mock.patch.dict(os.environ, {"TERM": "xterm"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NO_COLOR", None)
        platform_patch = mock.patch.object(j.sys, "platform", "linux")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def run_tty(self, *argv, stdout_tty=True, stderr_tty=True):
        out, err = FakeStream(stdout_tty), FakeStream(stderr_tty)
        with mock.patch.object(j, "fetch", fake_fetch), \
                mock.patch.object(j.urllib.request, "urlopen", fake_urlopen(announce=True)), \
                mock.patch.object(sys, "argv", ["jdk_versions.py", *argv]), \
                mock.patch.object(j, "terminal_width", return_value=100), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = j.main()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_the_table_is_colored_on_a_terminal(self):
        code, out, _ = self.run_tty(*self.BASE)
        self.assertEqual(code, 0)
        self.assertIn("\x1b[", out)

    def test_plain_turns_color_off(self):
        _, out, _ = self.run_tty(*self.BASE, "--plain")
        self.assertNotIn("\x1b", out)

    def test_plain_output_is_the_same_text_as_colored_output(self):
        _, colored, _ = self.run_tty(*self.BASE)
        _, plain, _ = self.run_tty(*self.BASE, "--plain")
        self.assertEqual(strip_ansi(colored), plain)

    def test_no_color_when_stdout_is_not_a_terminal(self):
        _, out, _ = self.run_tty(*self.BASE, stdout_tty=False)
        self.assertNotIn("\x1b", out)

    def test_no_color_when_no_color_is_set(self):
        os.environ["NO_COLOR"] = "1"
        _, out, _ = self.run_tty(*self.BASE)
        self.assertNotIn("\x1b", out)

    def test_csv_and_json_are_never_colored(self):
        for flag in ("--csv", "--json"):
            with self.subTest(flag=flag):
                _, out, _ = self.run_tty(*self.BASE, flag)
                self.assertNotIn("\x1b", out)

    def test_list_and_early_access_tables_are_colored_too(self):
        for argv in (("--list-os",), ("--list-arch",), ("--ea", "--os", "linux")):
            with self.subTest(argv=argv):
                _, out, _ = self.run_tty(*argv)
                self.assertIn("\x1b[", out)

    def test_run_helper_output_is_plain_because_it_is_not_a_terminal(self):
        _, out, _ = run(*self.BASE)
        self.assertNotIn("\x1b", out)

    def test_a_progress_bar_is_drawn_on_a_terminal(self):
        code, out, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir)
        self.assertEqual(code, 0, err)
        self.assertIn("\r", err)
        self.assertIn("100%", err)
        self.assertNotIn("downloading OpenJDK", err)
        self.assertIn("downloaded 1 of 1 file(s)", err)

    def test_the_bar_is_colored_on_a_terminal(self):
        _, _, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir)
        self.assertIn("\x1b[", err)

    def test_the_bar_shows_the_file_name(self):
        _, _, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir)
        self.assertIn("OpenJDK25U", strip_ansi(err))

    def test_plain_gives_simple_progress_lines_instead_of_a_bar(self):
        code, _, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir,
                                    "--plain")
        self.assertEqual(code, 0, err)
        self.assertNotIn("\r", err)
        self.assertNotIn("\x1b", err)
        self.assertIn("downloading OpenJDK25U", err)

    def test_no_bar_when_stderr_is_not_a_terminal(self):
        _, _, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir,
                                 stderr_tty=False)
        self.assertNotIn("\r", err)
        self.assertIn("downloading OpenJDK25U", err)

    def test_the_bar_can_be_on_while_stdout_is_piped(self):
        _, out, err = self.run_tty(*self.BASE, "--download", "--target-directory", self.dir,
                                   stdout_tty=False)
        self.assertNotIn("\x1b", out)
        self.assertIn("\r", err)

    def test_a_failed_download_erases_the_bar_before_the_error(self):
        out, err = FakeStream(True), FakeStream(True)
        with mock.patch.object(j, "fetch", fake_fetch), \
                mock.patch.object(j.urllib.request, "urlopen", fake_urlopen(corrupt=["jdk-25"])), \
                mock.patch.object(sys, "argv", ["jdk_versions.py", *self.BASE, "--download",
                                                "--target-directory", self.dir]), \
                mock.patch.object(j, "terminal_width", return_value=100), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = j.main()
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch", err.getvalue())
        self.assertEqual(os.listdir(self.dir), [])

    def test_plain_is_accepted_everywhere_and_in_the_help(self):
        for argv in (("--list-os", "--plain"), ("--ea", "--plain"), ("--plain", "--json"),
                     ("--plain", "--version-only")):
            with self.subTest(argv=argv):
                self.assertEqual(run(*argv)[0], 0)
        self.assertIn("--plain", run("--h")[1])


# ---------------------------------------------------------------------------------------------
# --install
# ---------------------------------------------------------------------------------------------

def build_tar(files, links=None, mode="w:gz"):
    """A .tar.gz held in memory. files maps name -> bytes; links maps name -> symlink target."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if "/bin/" in name or name.startswith("bin/") else 0o644
            tf.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return buf.getvalue()


def build_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def jdk_files(top, layout="plain"):
    """The files of a small JDK. layout: plain (top/bin), mac (top/Contents/Home/bin), windows."""
    home = f"{top}/Contents/Home" if layout == "mac" else top
    exe = "java.exe" if layout == "windows" else "java"
    files = {f"{home}/bin/{exe}": b"launcher", f"{home}/lib/modules": b"modules",
             f"{home}/release": b'JAVA_VERSION="1"\n', f"{home}/legal/java.base/LICENSE": b"gpl"}
    if layout == "mac":
        files[f"{top}/Contents/Info.plist"] = b"plist"
    return files


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def write(self, name, data):
        full = self.path(name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    def make_tree(self, root, files):
        for name, data in files.items():
            self.write(os.path.join(root, name), data)
        return self.path(root)

    def tree(self, root):
        """Every file and directory under root, as sorted relative paths (forward slashes)."""
        found = []
        for base, dirs, names in os.walk(root):
            for n in dirs + names:
                found.append(os.path.relpath(os.path.join(base, n), root).replace(os.sep, "/"))
        return sorted(found)


class InstallPrefixNameTests(unittest.TestCase):
    def test_accepts_names_that_are_safe_to_use_in_a_directory_name(self):
        for value in ("jdk", "java-", "OpenJDK_", "temurin.", "j+d", "jdk-", "", "..", "x" * 50):
            with self.subTest(value=value):
                self.assertEqual(j.install_prefix_name(value), value)

    def test_rejects_anything_that_could_be_a_path_or_surprising(self):
        for value in ("a/b", "/abs", "../a", "a\\b", "a b", "a:b", "jdk\n", "jdk\0", "~", "$HOME",
                      "a;b", "a*"):
            with self.subTest(value=value):
                with self.assertRaises(j.argparse.ArgumentTypeError):
                    j.install_prefix_name(value)

    def test_dots_alone_cannot_climb_out_because_the_major_is_always_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "jdk.tar.gz")
            with open(archive, "wb") as f:
                f.write(build_tar(jdk_files("jdk-25")))
            dest = j.install_jdk(archive, 25, os.path.join(tmp, "p"), "..")
            self.assertEqual(os.path.dirname(dest), os.path.join(tmp, "p"))
            self.assertEqual(os.path.basename(dest), "..25")


class InstallConflictTests(unittest.TestCase):
    def row(self, major, os_name="linux", arch="x64"):
        return {"major": major, "os": os_name, "arch": arch}

    def test_one_download_per_major_is_fine(self):
        self.assertIsNone(j.install_conflict([self.row(25), self.row(21), self.row(17)]))
        self.assertIsNone(j.install_conflict([]))

    def test_two_platforms_of_one_major_are_a_conflict(self):
        message = j.install_conflict([self.row(25), self.row(25, "macos", "aarch64"), self.row(21)])
        self.assertIn("JDK 25 has linux-x64, macos-aarch64", message)
        self.assertNotIn("JDK 21", message)
        self.assertIn("--os", message)
        self.assertIn("--auto", message)

    def test_every_crowded_major_is_named(self):
        message = j.install_conflict([self.row(25), self.row(25, "windows"), self.row(21),
                                      self.row(21, arch="aarch64")])
        self.assertIn("JDK 25", message)
        self.assertIn("JDK 21", message)


class FindJdkHomeTests(TempDirTestCase):
    def home(self, files):
        return j.find_jdk_home(self.make_tree("root", files))

    def test_a_versioned_wrapper_directory_is_looked_through(self):
        self.assertEqual(self.home(jdk_files("jdk-25.0.5+8")), self.path("root", "jdk-25.0.5+8"))

    def test_the_macos_layout_resolves_to_contents_home(self):
        self.assertEqual(self.home(jdk_files("jdk-25.0.5+8", "mac")),
                         self.path("root", "jdk-25.0.5+8", "Contents", "Home"))

    def test_the_macos_early_access_layout_resolves_to_contents_home(self):
        self.assertEqual(self.home(jdk_files("jdk-28.jdk", "mac")),
                         self.path("root", "jdk-28.jdk", "Contents", "Home"))

    def test_a_windows_jdk_is_found_by_java_exe(self):
        self.assertEqual(self.home(jdk_files("jdk-25", "windows")), self.path("root", "jdk-25"))

    def test_an_archive_with_no_wrapper_directory_is_accepted(self):
        self.assertEqual(self.home({"bin/java": b"x", "lib/modules": b"m"}), self.path("root"))

    def test_the_result_is_the_directory_that_holds_bin_not_something_deeper(self):
        files = {**jdk_files("jdk-25"), "jdk-25/jre/bin/java": b"inner"}
        self.assertEqual(self.home(files), self.path("root", "jdk-25"))

    def test_a_directory_with_no_launcher_is_not_a_jdk(self):
        for files in ({"jdk-25/readme.txt": b"x"}, {"jdk-25/bin/javac": b"x"},
                      {"jdk-25/java/bin/java": b"x"}, {"a/bin/java": b"x", "b/readme": b"y"}):
            with self.subTest(files=list(files)):
                with self.assertRaises(ValueError):
                    j.find_jdk_home(self.make_tree(f"r{len(files)}{list(files)[0][0]}", files))

    def test_an_empty_archive_is_not_a_jdk(self):
        os.makedirs(self.path("empty"))
        with self.assertRaises(ValueError):
            j.find_jdk_home(self.path("empty"))

    def test_a_launcher_that_is_a_directory_does_not_count(self):
        with self.assertRaises(ValueError):
            self.home({"jdk-25/bin/java/placeholder": b"x"})


class UnpackTests(TempDirTestCase):
    def archive(self, name, data):
        return self.write(name, data)

    def test_unpacks_a_tar_gz(self):
        a = self.archive("jdk.tar.gz", build_tar(jdk_files("jdk-25")))
        j.unpack(a, self.path("out"))
        self.assertTrue(os.path.isfile(self.path("out", "jdk-25", "bin", "java")))

    def test_unpacks_a_tgz_and_a_zip(self):
        j.unpack(self.archive("a.tgz", build_tar(jdk_files("jdk-25"))), self.path("o1"))
        self.assertTrue(os.path.isfile(self.path("o1", "jdk-25", "bin", "java")))
        j.unpack(self.archive("a.zip", build_zip(jdk_files("jdk-25", "windows"))), self.path("o2"))
        self.assertTrue(os.path.isfile(self.path("o2", "jdk-25", "bin", "java.exe")))

    @unittest.skipUnless(os.name == "posix", "needs POSIX permissions and symlinks")
    def test_keeps_symlinks_and_the_executable_bit(self):
        data = build_tar(jdk_files("jdk-25"), links={"jdk-25/legal/jdk.x/LICENSE": "../java.base/LICENSE"})
        j.unpack(self.archive("jdk.tar.gz", data), self.path("out"))
        link = self.path("out", "jdk-25", "legal", "jdk.x", "LICENSE")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(read_bytes(link), b"gpl")
        self.assertTrue(os.access(self.path("out", "jdk-25", "bin", "java"), os.X_OK))

    def test_unknown_archive_types_are_rejected(self):
        for name in ("jdk.rar", "jdk.tar.bz2", "jdk", "jdk.tar"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    j.unpack(self.archive(name, b"x"), self.path("out"))

    def test_a_tar_cannot_write_outside_the_target(self):
        data = build_tar({"../escaped.txt": b"x", "jdk-25/bin/java": b"x"})
        with self.assertRaises(tarfile.TarError):
            j.unpack(self.archive("evil.tar.gz", data), self.path("out"))
        self.assertFalse(os.path.exists(self.path("escaped.txt")))

    def test_a_tar_cannot_use_an_absolute_path(self):
        # Python 3.11 data_filter raises AbsolutePathError on absolute paths; later versions strip
        # them silently.  Either way the file must not be written to the absolute path.
        target = self.path("abs.txt")
        data = build_tar({target: b"x"})
        try:
            j.unpack(self.archive("abs.tar.gz", data), self.path("out"))
        except tarfile.TarError:
            pass
        self.assertFalse(os.path.exists(target), "an absolute path must not be honored")

    @unittest.skipUnless(os.name == "posix", "needs symlinks")
    def test_a_symlink_pointing_outside_is_rejected(self):
        data = build_tar({"jdk-25/bin/java": b"x"}, links={"jdk-25/evil": "../../outside"})
        with self.assertRaises(tarfile.TarError):
            j.unpack(self.archive("link.tar.gz", data), self.path("out"))

    def test_a_zip_cannot_write_outside_the_target(self):
        j.unpack(self.archive("evil.zip", build_zip({"../escaped.txt": b"x", "/abs.txt": b"y"})),
                 self.path("out"))
        self.assertFalse(os.path.exists(self.path("escaped.txt")))
        self.assertFalse(os.path.exists("/abs.txt"))

    def test_a_corrupt_archive_raises_one_of_the_install_errors(self):
        for name, data in (("c.tar.gz", b"not gzip at all"), ("c.zip", b"not a zip"),
                           ("t.tar.gz", build_tar(jdk_files("jdk-25"))[:40])):
            with self.subTest(name=name):
                with self.assertRaises(j.INSTALL_ERRORS):
                    j.unpack(self.archive(name, data), self.path("out-" + name))

    def test_refuses_to_unpack_tar_without_extraction_filters(self):
        a = self.archive("jdk.tar.gz", build_tar(jdk_files("jdk-25")))
        with mock.patch.dict(j.tarfile.__dict__):
            del j.tarfile.__dict__["data_filter"]
            with self.assertRaises(ValueError) as caught:
                j.unpack(a, self.path("out"))
        self.assertIn("safely", str(caught.exception))
        self.assertFalse(os.path.exists(self.path("out")))


class ReplaceDirectoryTests(TempDirTestCase):
    def source(self):
        return self.make_tree("src", {"bin/java": b"new", "release": b"new"})

    def test_moves_into_a_new_place(self):
        j.replace_directory(self.source(), self.path("dest"))
        self.assertEqual(read_bytes(self.path("dest", "bin", "java")), b"new")
        self.assertFalse(os.path.exists(self.path("src")))

    def test_replaces_an_existing_jdk_completely(self):
        self.make_tree("dest", {"bin/java": b"old", "stale-file": b"old"})
        j.replace_directory(self.source(), self.path("dest"))
        self.assertEqual(self.tree(self.path("dest")), ["bin", "bin/java", "release"])
        self.assertEqual(os.listdir(self.tmp), ["dest"], "no set-aside copy may be left behind")

    def test_replaces_a_directory_that_only_has_a_release_file(self):
        self.make_tree("dest", {"release": b"old"})
        j.replace_directory(self.source(), self.path("dest"))
        self.assertEqual(read_bytes(self.path("dest", "bin", "java")), b"new")

    def test_refuses_to_replace_a_directory_that_is_not_a_jdk(self):
        self.make_tree("dest", {"my-documents/thesis.txt": b"precious"})
        with self.assertRaises(ValueError) as caught:
            j.replace_directory(self.source(), self.path("dest"))
        self.assertIn("does not look like a JDK", str(caught.exception))
        self.assertEqual(read_bytes(self.path("dest", "my-documents", "thesis.txt")),
                         b"precious")

    def test_refuses_to_replace_a_file(self):
        self.write("dest", b"i am a file")
        with self.assertRaises(ValueError):
            j.replace_directory(self.source(), self.path("dest"))
        self.assertEqual(read_bytes(self.path("dest")), b"i am a file")

    @unittest.skipUnless(os.name == "posix", "needs symlinks")
    def test_refuses_to_replace_a_symlink_and_leaves_its_target_alone(self):
        self.make_tree("real", {"bin/java": b"real jdk"})
        os.symlink(self.path("real"), self.path("dest"))
        with self.assertRaises(ValueError):
            j.replace_directory(self.source(), self.path("dest"))
        self.assertTrue(os.path.islink(self.path("dest")))
        self.assertEqual(read_bytes(self.path("real", "bin", "java")), b"real jdk")

    def test_puts_the_old_jdk_back_if_the_move_fails(self):
        self.make_tree("dest", {"bin/java": b"old"})
        real_replace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:  # the move of the new JDK into place
                raise OSError("disk full")
            return real_replace(src, dst)

        with mock.patch.object(j.os, "replace", flaky), self.assertRaises(OSError):
            j.replace_directory(self.source(), self.path("dest"))
        self.assertEqual(read_bytes(self.path("dest", "bin", "java")), b"old")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["dest", "src"])


class InstallJdkTests(TempDirTestCase):
    def archive(self, files, name="jdk.tar.gz", **kw):
        return self.write(name, build_tar(files, **kw) if name.endswith("gz") else build_zip(files))

    def install(self, archive, major=25, prefix="jdk", where="java"):
        return j.install_jdk(archive, major, self.path(where), prefix)

    def test_installs_as_prefix_plus_major_directly_under_the_prefix_dir(self):
        dest = self.install(self.archive(jdk_files("jdk-25.0.5+8")), 25, "jdk", "java")
        self.assertEqual(dest, self.path("java", "jdk25"))
        self.assertEqual(os.listdir(self.path("java")), ["jdk25"])

    def test_the_prefix_is_used_as_given(self):
        for prefix, expected in (("jdk", "jdk27"), ("java-", "java-27"), ("temurin_", "temurin_27"),
                                 ("", "27"), ("OpenJDK", "OpenJDK27")):
            with self.subTest(prefix=prefix):
                dest = self.install(self.archive(jdk_files("jdk-27")), 27, prefix, "p-" + expected)
                self.assertEqual(os.path.basename(dest), expected)

    def test_the_installed_directory_holds_the_jdk_not_another_java_directory(self):
        dest = self.install(self.archive(jdk_files("jdk-25.0.5+8")))
        top = sorted(os.listdir(dest))
        self.assertEqual(top, ["bin", "legal", "lib", "release"])
        self.assertFalse([n for n in top if n.startswith(("jdk", "java", "Contents"))])
        self.assertTrue(os.path.isfile(os.path.join(dest, "bin", "java")))

    def test_macos_archives_install_the_contents_of_contents_home(self):
        dest = self.install(self.archive(jdk_files("jdk-25.0.5+8", "mac")))
        self.assertEqual(sorted(os.listdir(dest)), ["bin", "legal", "lib", "release"])
        self.assertFalse(os.path.exists(os.path.join(dest, "Contents")))

    def test_the_macos_early_access_wrapper_is_not_carried_over(self):
        dest = self.install(self.archive({"./" + k: v for k, v in jdk_files("jdk-28.jdk", "mac").items()}), 28)
        self.assertEqual(sorted(os.listdir(dest)), ["bin", "legal", "lib", "release"])

    def test_windows_zips_install_too(self):
        dest = self.install(self.archive(jdk_files("jdk-25", "windows"), "jdk.zip"))
        self.assertTrue(os.path.isfile(os.path.join(dest, "bin", "java.exe")))
        self.assertEqual(sorted(os.listdir(dest)), ["bin", "legal", "lib", "release"])

    @unittest.skipUnless(os.name == "posix", "needs POSIX permissions and symlinks")
    def test_symlinks_and_permissions_survive_the_install(self):
        a = self.archive(jdk_files("jdk-25"), links={"jdk-25/legal/jdk.x/LICENSE": "../java.base/LICENSE"})
        dest = self.install(a)
        self.assertTrue(os.access(os.path.join(dest, "bin", "java"), os.X_OK))
        self.assertTrue(os.path.islink(os.path.join(dest, "legal", "jdk.x", "LICENSE")))

    def test_creates_missing_prefix_dirs(self):
        dest = self.install(self.archive(jdk_files("jdk-25")), where="deep/er/java")
        self.assertTrue(os.path.isdir(dest))

    def test_no_scratch_directory_is_left_after_success(self):
        self.install(self.archive(jdk_files("jdk-25")))
        self.assertEqual(os.listdir(self.path("java")), ["jdk25"])

    def test_no_scratch_directory_is_left_after_a_failure(self):
        bad = self.archive({"readme.txt": b"not a jdk"})
        with self.assertRaises(ValueError):
            self.install(bad)
        self.assertEqual(os.listdir(self.path("java")), [])

    def test_a_failed_install_leaves_the_existing_jdk_untouched(self):
        good = self.install(self.archive(jdk_files("jdk-25")))
        with self.assertRaises(ValueError):
            self.install(self.archive({"readme.txt": b"x"}, name="bad.tar.gz"))
        self.assertTrue(os.path.isfile(os.path.join(good, "bin", "java")))
        self.assertEqual(os.listdir(self.path("java")), ["jdk25"])

    def test_installing_again_replaces_the_old_version(self):
        self.install(self.archive({**jdk_files("jdk-25"), "jdk-25/OLD": b"x"}))
        dest = self.install(self.archive(jdk_files("jdk-25"), name="new.tar.gz"))
        self.assertFalse(os.path.exists(os.path.join(dest, "OLD")))

    def test_two_majors_sit_side_by_side(self):
        self.install(self.archive(jdk_files("jdk-25")), 25)
        self.install(self.archive(jdk_files("jdk-21"), name="b.tar.gz"), 21)
        self.assertEqual(sorted(os.listdir(self.path("java"))), ["jdk21", "jdk25"])

    def test_it_will_not_overwrite_something_that_is_not_a_jdk(self):
        os.makedirs(self.path("java", "jdk25"))
        self.write("java/jdk25/notes.txt", b"mine")
        with self.assertRaises(ValueError):
            self.install(self.archive(jdk_files("jdk-25")))
        self.assertEqual(read_bytes(self.path("java", "jdk25", "notes.txt")), b"mine")

    def test_the_archive_itself_is_not_touched(self):
        a = self.archive(jdk_files("jdk-25"))
        before = read_bytes(a)
        self.install(a)
        self.assertEqual(read_bytes(a), before)


def archive_world(not_jdk=(), corrupt=(), fail=()):
    """A fetch and urlopen pair that serve real JDK-shaped archives with matching checksums.

    URLs containing a string in not_jdk serve an archive with no JDK in it; those in corrupt
    serve bytes that don't match the published checksum; those in fail raise OSError.
    """
    cache = {}

    def build(url):
        if url not in cache:
            match = re.search(r"/jdk-(\d+)/|jdk(\d+)/", url)
            major = match.group(1) or match.group(2)
            if any(n in url for n in not_jdk):
                cache[url] = build_tar({"readme.txt": b"not a jdk"})
            elif "windows" in url:
                files = jdk_files(f"jdk-{major}", "windows")
                cache[url] = build_zip(files) if url.endswith(".zip") else build_tar(files)
            else:
                cache[url] = build_tar(jdk_files(f"jdk-{major}.0.1+1"))
        return cache[url]

    def fetch(url, accept):
        if url.endswith((".sha256.txt", ".sha256")):
            return hashlib.sha256(build(url.rsplit(".sha256", 1)[0])).hexdigest() + "  file"
        return fake_fetch(url, accept)

    def urlopen(req, timeout=None):
        url = req.full_url
        if any(f in url for f in fail):
            raise OSError("connection refused")
        data = build(url)
        if any(c in url for c in corrupt):
            data += b"tampered"
        resp = FakeResponse(data)
        resp.headers = {"Content-Length": str(len(data))}
        return resp

    return fetch, urlopen


class InstallFlagTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.dl, self.prefix = self.path("dl"), self.path("java")

    def go(self, *argv, world=None, install=True):
        fetch, urlopen = world or archive_world()
        args = [*argv, "--plain", "--download", "--target-directory", self.dl]
        if install:
            args += ["--install", "--prefix-dir", self.prefix, "--install-prefix", "jdk"]
        with mock.patch.object(j.urllib.request, "urlopen", urlopen):
            return run(*args, fetch=fetch)

    LTS2 = ("--lts", "--os", "linux", "--arch", "x64", "--count", "2")

    def test_installs_every_downloaded_jdk_as_prefix_plus_major(self):
        code, _, err = self.go(*self.LTS2)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(os.listdir(self.prefix)), ["jdk21", "jdk25"])
        for major in (21, 25):
            self.assertTrue(os.path.isfile(os.path.join(self.prefix, f"jdk{major}", "bin", "java")))

    def test_the_example_from_the_docs_prefix_dir_java_prefix_jdk_puts_jdk_27_at_java_jdk27(self):
        self.prefix = self.path("java")
        code, _, err = self.go("--non-lts", "--os", "linux", "--arch", "x64")
        self.assertEqual(code, 0, err)
        self.assertEqual(os.listdir(self.prefix), ["jdk27"])
        self.assertEqual(sorted(os.listdir(self.path("java", "jdk27"))),
                         ["bin", "legal", "lib", "release"])

    def test_nothing_nested_inside_the_installed_directories(self):
        self.go(*self.LTS2)
        for major in (21, 25):
            top = os.listdir(os.path.join(self.prefix, f"jdk{major}"))
            self.assertFalse([n for n in top if n.startswith(("jdk", "java"))], top)

    def test_the_downloaded_archives_are_kept(self):
        self.go(*self.LTS2)
        self.assertEqual(len(os.listdir(self.dl)), 2)

    def test_progress_names_each_install_and_counts_them(self):
        _, _, err = self.go(*self.LTS2)
        self.assertIn("installed " + os.path.join(self.prefix, "jdk25"), err)
        self.assertIn("installed " + os.path.join(self.prefix, "jdk21"), err)
        self.assertIn("installed 2 of 2 JDK(s)", err)
        self.assertIn("downloaded 2 of 2 file(s)", err)

    def test_the_listing_still_goes_to_stdout_unchanged(self):
        _, out, _ = self.go(*self.LTS2, "--json")
        self.assertEqual(len(json.loads(out)["releases"]), 2)

    def test_a_download_that_is_not_a_jdk_fails_but_the_others_install(self):
        code, _, err = self.go(*self.LTS2, world=archive_world(not_jdk=["jdk-25"]))
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.prefix), ["jdk21"])
        self.assertIn("failed to install", err)
        self.assertIn("no bin/java", err)
        self.assertIn("installed 1 of 2 JDK(s)", err)
        error_lines = [l for l in err.splitlines() if l.startswith("{")]
        self.assertTrue(all("error" in json.loads(l) for l in error_lines))

    def test_a_corrupt_download_is_never_installed(self):
        code, _, err = self.go(*self.LTS2, world=archive_world(corrupt=["jdk-21"]))
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.prefix), ["jdk25"])
        self.assertIn("checksum mismatch", err)
        self.assertIn("downloaded 1 of 2", err)
        self.assertIn("installed 1 of 2", err)

    def test_a_failed_connection_is_never_installed(self):
        code, _, err = self.go(*self.LTS2, world=archive_world(fail=["jdk-25"]))
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.prefix), ["jdk21"])

    def test_two_platforms_of_one_major_are_refused_before_anything_is_downloaded(self):
        fetch, _ = archive_world()
        with mock.patch.object(j.urllib.request, "urlopen",
                               side_effect=AssertionError("no download expected")):
            code, out, err = run("--lts", "--plain", "--download", "--target-directory", self.dl,
                                 "--install", "--prefix-dir", self.prefix, "--install-prefix", "jdk",
                                 fetch=fetch)
        self.assertEqual(code, 1)
        self.assertIn("exactly one download for each major version", json.loads(err)["error"])
        self.assertFalse(os.path.exists(self.dl))
        self.assertFalse(os.path.exists(self.prefix))

    def test_an_existing_non_jdk_directory_is_not_overwritten(self):
        os.makedirs(os.path.join(self.prefix, "jdk25"))
        self.write("java/jdk25/mine.txt", b"keep me")
        code, _, err = self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1")
        self.assertEqual(code, 1)
        self.assertIn("does not look like a JDK", err)
        self.assertEqual(read_bytes(self.path("java", "jdk25", "mine.txt")), b"keep me")

    def test_installing_over_an_older_install_replaces_it(self):
        self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1")
        self.write("java/jdk25/OLD", b"x")
        code, _, err = self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1", "--force")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(self.path("java", "jdk25", "OLD")))

    def test_skips_install_if_already_installed(self):
        self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1")
        self.write("java/jdk25/OLD", b"x")
        code, _, err = self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1")
        self.assertEqual(code, 0, err)
        self.assertIn("skipping install of JDK 25", err)
        self.assertTrue(os.path.exists(self.path("java", "jdk25", "OLD")),
                        "existing install must be untouched when skipped")

    def test_works_for_early_access_builds(self):
        code, _, err = self.go("--ea", "--os", "linux", "--arch", "x64")
        self.assertEqual(code, 0, err)
        self.assertEqual(os.listdir(self.prefix), ["jdk28"])
        self.assertTrue(os.path.isfile(os.path.join(self.prefix, "jdk28", "bin", "java")))

    def test_windows_builds_install_with_java_exe(self):
        code, _, err = self.go("--lts", "--os", "windows", "--arch", "x64", "--count", "1")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.prefix, "jdk25", "bin", "java.exe")))

    def test_without_install_nothing_is_unpacked(self):
        code, _, err = self.go(*self.LTS2, install=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(os.listdir(self.dl)), 2)
        self.assertFalse(os.path.exists(self.prefix))

    def test_an_unusable_prefix_dir_is_reported_as_an_install_failure(self):
        self.write("java", b"a file where the directory should go")
        code, _, err = self.go("--lts", "--os", "linux", "--arch", "x64", "--count", "1")
        self.assertEqual(code, 1)
        self.assertIn("failed to install", err)

    def test_the_prefix_can_be_empty(self):
        fetch, urlopen = archive_world()
        with mock.patch.object(j.urllib.request, "urlopen", urlopen):
            code, _, err = run("--lts", "--os", "linux", "--arch", "x64", "--count", "1", "--plain",
                               "--download", "--target-directory", self.dl, "--install",
                               "--prefix-dir", self.prefix, "--install-prefix", "", fetch=fetch)
        self.assertEqual(code, 0, err)
        self.assertEqual(os.listdir(self.prefix), ["25"])


class InstallArgumentTests(unittest.TestCase):
    FULL = ("--download", "--target-directory", "dl", "--install", "--prefix-dir", "p",
            "--install-prefix", "jdk")

    def assert_rejected(self, *argv):
        code, out, err = run(*argv, fetch=raising(AssertionError("no network!")))
        self.assertEqual(code, 2, f"{argv} should be rejected (stderr: {err})")
        self.assertEqual(out, "")

    def test_install_needs_prefix_dir_and_install_prefix(self):
        self.assert_rejected("--lts", "--download", "--target-directory", "dl", "--install",
                             "--install-prefix", "jdk")
        self.assert_rejected("--lts", "--download", "--target-directory", "dl", "--install",
                             "--prefix-dir", "p")
        self.assert_rejected("--lts", "--download", "--target-directory", "dl", "--install")

    def test_the_prefix_flags_need_install(self):
        self.assert_rejected("--lts", "--download", "--target-directory", "dl", "--prefix-dir", "p")
        self.assert_rejected("--lts", "--download", "--target-directory", "dl",
                             "--install-prefix", "jdk")

    def test_a_prefix_that_is_a_path_is_rejected(self):
        for bad in ("a/b", "../x", "/abs", "a b"):
            with self.subTest(bad=bad):
                self.assert_rejected("--lts", "--download", "--target-directory", "dl", "--install",
                                     "--prefix-dir", "p", "--install-prefix", bad)

    def test_not_allowed_with_the_modes_that_cannot_download(self):
        for mode in ("--list-arch", "--list-os", "--version-only"):
            with self.subTest(mode=mode):
                self.assert_rejected(mode, *self.FULL)

    def test_the_flags_need_values(self):
        self.assert_rejected("--lts", *self.FULL[:-1])
        self.assert_rejected("--lts", *self.FULL[:-3])

    def test_the_flags_are_in_the_help(self):
        _, out, _ = run("--h")
        for flag in ("--install", "--prefix-dir", "--install-prefix"):
            self.assertIn(flag, out)


class SourceAllTests(unittest.TestCase):
    """Tests for --source all (merge GA releases from every source in SOURCES)."""

    def test_json_has_sources_list_not_source_string(self):
        data = run_json("--source", "all", "--lts")
        self.assertIn("sources", data)
        self.assertNotIn("source", data)
        self.assertIsInstance(data["sources"], list)
        self.assertEqual(len(data["sources"]), len(j.SOURCES))

    def test_each_source_label_appears_in_sources_list(self):
        data = run_json("--source", "all", "--lts")
        labels = {sl for _, _, sl in j.SOURCES.values()}
        self.assertEqual(set(data["sources"]), labels)

    def test_releases_have_source_field(self):
        data = run_json("--source", "all", "--lts")
        for r in data["releases"]:
            self.assertIn("source", r)
            self.assertIn(r["source"], {sl for _, _, sl in j.SOURCES.values()})

    def test_releases_sorted_by_major_descending(self):
        data = run_json("--source", "all", "--lts")
        majors = [r["major"] for r in data["releases"]]
        self.assertEqual(majors, sorted(majors, reverse=True))

    def test_same_major_appears_once_per_source(self):
        data = run_json("--source", "all", "--lts")
        pairs = [(r["major"], r["source"]) for r in data["releases"]]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_csv_source_column_is_populated(self):
        rows = run_csv("--source", "all", "--lts", "--count", "1")
        self.assertEqual(rows[0][5], "source")
        self.assertNotEqual(rows[1][5], "")  # non-empty source label

    def test_table_includes_source_label(self):
        _, out, _ = run("--source", "all", "--lts", "--count", "1")
        labels = [sl for _, _, sl in j.SOURCES.values()]
        self.assertTrue(any(lbl in out for lbl in labels))

    def test_version_only_deduplicates_across_sources(self):
        code, out, _ = run("--source", "all", "--lts", "--version-only")
        versions = [int(v) for v in out.strip().splitlines() if v]
        # No duplicate major versions
        self.assertEqual(len(versions), len(set(versions)))

    def test_version_only_json_has_all_sources_label(self):
        data = run_json("--source", "all", "--lts", "--version-only")
        self.assertEqual(data["source"], "all sources")

    def test_fields_with_source_all(self):
        rows = run_csv("--source", "all", "--lts", "--count", "1",
                       "--fields", "major", "source", "os")
        self.assertEqual(rows[0], ["major", "source", "os"])
        self.assertNotEqual(rows[1][1], "")  # source field is populated

    def test_source_all_not_allowed_with_ea(self):
        code, out, err = run("--source", "all", "--ea")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--ea", err)

    def test_source_all_not_allowed_with_list_arch(self):
        code, out, err = run("--source", "all", "--list-arch")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--list-arch", err)

    def test_source_all_not_allowed_with_list_os(self):
        code, out, err = run("--source", "all", "--list-os")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--list-os", err)

    def test_unknown_source_is_still_rejected(self):
        code, out, err = run("--source", "bogus")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("unknown source", err)

    def test_count_limits_total_rows_across_sources(self):
        data = run_json("--source", "all", "--lts", "--count", "2")
        total_rows = sum(len(r["downloads"]) for r in data["releases"])
        self.assertLessEqual(total_rows, 2)


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
        for name, (api_base, _, _) in j.SOURCES.items():
            self.assertTrue(api_base.startswith("https://"), f"{name} API base must use HTTPS")
        self.assertTrue(j.EA_SITE.startswith("https://"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
