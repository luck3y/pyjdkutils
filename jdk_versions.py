#!/usr/bin/env python3
"""Print the most recent released JDK (Eclipse Temurin) versions as a table, JSON or CSV.
Author: Ken Wills <ken@zaptillion.net>

Data sources: Adoptium API (https://api.adoptium.net) for GA releases and
https://jdk.java.net for the latest Oracle-led OpenJDK early-access build.

Usage:
    python3 jdk_versions.py                    # LTS releases + latest feature release
    python3 jdk_versions.py --all              # every available major release
    python3 jdk_versions.py --lts              # only LTS releases
    python3 jdk_versions.py --non-lts          # only the latest non-LTS feature release
    python3 jdk_versions.py --all --non-lts    # every non-LTS major release
    python3 jdk_versions.py --list-arch        # list all available architectures
    python3 jdk_versions.py --list-os          # list all available operating systems
    python3 jdk_versions.py --arch x64         # only x64 downloads (works with all modes)
    python3 jdk_versions.py --os linux         # only linux downloads (works with all modes)
    python3 jdk_versions.py --lts --os macos --arch aarch64   # LTS releases, macOS/aarch64 only
    python3 jdk_versions.py --ea               # most recent early-access build
    python3 jdk_versions.py                    # formatted table (the default output)
    python3 jdk_versions.py --json             # JSON output
    python3 jdk_versions.py --csv              # CSV output (works with all modes)
    python3 jdk_versions.py --non-lts --version-only   # just the major version, e.g. 27
    python3 jdk_versions.py --lts --version-only       # LTS major versions, one per line
    python3 jdk_versions.py --count 3          # only the first 3 rows (works with all modes)
    python3 jdk_versions.py --no-header        # table or CSV without the header row
    python3 jdk_versions.py --auto             # only downloads for this machine's OS and architecture
    python3 jdk_versions.py --auto --lts --count 1   # newest LTS download for this machine
    python3 jdk_versions.py --h                # show help (also --help)
"""
import argparse
import csv
import http.client
import json
import os
import platform
import re
import sys
import urllib.request

API = "https://api.adoptium.net/v3"
EA_SITE = "https://jdk.java.net"
TIMEOUT = 20

# Everything that can go wrong while talking to the network. OSError covers URLError, HTTPError,
# timeouts, connection resets/refusals, broken pipes and TLS failures; HTTPException covers
# protocol-level failures such as IncompleteRead and a server closing the connection early.
NETWORK_ERRORS = (OSError, http.client.HTTPException)

# Common alternate spellings mapped to the architecture names used by the data sources.
ARCH_ALIASES = {"amd64": "x64", "x86_64": "x64", "x86-64": "x64", "arm64": "aarch64"}


# Alternate spellings mapped to the OS names used in this script's output
# (Adoptium calls macOS "mac"; this script reports it as "macos").
OS_ALIASES = {
    "mac": "macos", "osx": "macos", "darwin": "macos",
    "win": "windows", "win32": "windows",
    "alpine": "alpine-linux",
}


def normalize_arch(arch):
    arch = arch.strip().lower()
    return ARCH_ALIASES.get(arch, arch)


# platform.machine() values mapped to the architecture names used by the data sources.
MACHINE_ARCHES = {
    "x86_64": "x64", "amd64": "x64",
    "aarch64": "aarch64", "arm64": "aarch64", "armv8l": "aarch64",
    "armv7l": "arm", "armv6l": "arm", "armv7": "arm", "arm": "arm",
    "i386": "x32", "i486": "x32", "i586": "x32", "i686": "x32", "x86": "x32",
    "ppc64le": "ppc64le", "ppc64": "ppc64", "s390x": "s390x", "riscv64": "riscv64",
    "sparc64": "sparcv9",
}

# platform.system() values mapped to the OS names used in this script's output.
SYSTEM_OSES = {
    "linux": "linux", "darwin": "macos", "windows": "windows", "aix": "aix", "sunos": "solaris",
}


def detect_platform():
    """Return (os, arch) for the machine running this script, using this script's naming.

    Raises ValueError if the OS or architecture is not one this script knows about.
    """
    system = platform.system().lower()
    os_name = SYSTEM_OSES.get(system)
    if os_name is None:
        raise ValueError(f"unsupported operating system '{platform.system()}'")
    if os_name == "linux" and os.path.exists("/etc/alpine-release"):
        os_name = "alpine-linux"

    machine = platform.machine().lower()
    # AIX reports a hardware id instead of an architecture name, and is always 64-bit POWER.
    arch = "ppc64" if os_name == "aix" else MACHINE_ARCHES.get(machine)
    if arch is None:
        raise ValueError(f"unsupported architecture '{platform.machine()}'")
    return os_name, arch


def normalize_os(os_name):
    os_name = os_name.strip().lower()
    return OS_ALIASES.get(os_name, os_name)


def filter_downloads(downloads, arch=None, os_name=None):
    """Keep only downloads whose 'arch' and 'os' fields match the given filters."""
    return {
        k: v for k, v in downloads.items()
        if (arch is None or v["arch"] == arch) and (os_name is None or v["os"] == os_name)
    }


def positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: '{value}'") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def limit_downloads(downloads, count):
    """Keep only the first `count` downloads (a no-op when count is None)."""
    return downloads if count is None else dict(list(downloads.items())[:count])


def limit_releases(releases, count):
    """Keep only the first `count` rows, where a row is one release/platform download.

    This matches the rows shown in the table and CSV output, so every format is cut at the
    same point. Releases left with no downloads are dropped.
    """
    if count is None:
        return releases
    limited, remaining = [], count
    for release in releases:
        if remaining <= 0:
            break
        kept = limit_downloads(release["downloads"], remaining)
        limited.append({**release, "downloads": kept})
        remaining -= len(kept)
    return limited


def describe_filters(arch, os_name):
    return " and ".join(
        f"{label} '{value}'" for label, value in (("architecture", arch), ("os", os_name)) if value
    )


def format_table(columns, rows, header=True):
    """Render rows as a plain-text table. The sha256_url column is omitted to keep it readable.

    With header=False the column names and the rule beneath them are left out.
    """
    columns = [c for c in columns if c != "sha256_url"]
    cells = [["" if row.get(c) is None else str(row.get(c)) for c in columns] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    border = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    def line(values):
        return "| " + " | ".join(v.ljust(w) for v, w in zip(values, widths)) + " |"

    head = [line(columns), border] if header else []
    return "\n".join([border, *head, *(line(r) for r in cells), border])


def emit(payload, fmt, columns, rows, header=True):
    """Write the result to stdout as a table, indented JSON, or CSV.

    header=False drops the header row from the table and CSV output; JSON is unaffected.
    """
    if fmt == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        if header:
            writer.writerow(columns)
        writer.writerows([row.get(c) for c in columns] for row in rows)
    elif fmt == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(format_table(columns, rows, header))


def emit_versions(source, arch, os_name, majors, fmt, header=True):
    """Write only the major version numbers: bare values (default), a JSON object, or CSV."""
    if fmt == "json":
        print(json.dumps(
            {"source": source, "arch": arch, "os": os_name, "major_versions": majors}, indent=2
        ))
    else:
        if fmt == "csv" and header:
            print("major")
        print("\n".join(str(m) for m in majors))


def download_rows(downloads, **common):
    """Flatten a downloads dict into CSV rows, one per platform, repeating the common fields."""
    return [
        {**common, "os": d["os"], "arch": d["arch"], "url": d["url"], "sha256_url": d["sha256_url"]}
        for d in downloads.values()
    ]


def fetch(url, accept):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "jdk-versions/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8")


def get_json(path):
    return json.loads(fetch(f"{API}{path}", "application/json"))


def latest_early_access():
    """Scrape jdk.java.net for the most recent early-access JDK build."""
    home = fetch(f"{EA_SITE}/", "text/html")
    match = re.search(r'Early access:\s*<a href="/(\d+)/">JDK', home)
    if not match:
        raise ValueError("could not find the early-access JDK on jdk.java.net")
    major = int(match.group(1))

    page = fetch(f"{EA_SITE}/{major}/", "text/html")
    build = re.search(r"Build (\d+) \((\d{4})/(\d{2})/(\d{2})\)", page)
    if not build:
        raise ValueError(f"could not find the build number for JDK {major} early access")

    downloads = {}
    for url in re.findall(r'href="(https://download\.java\.net/[^"]+_bin\.(?:tar\.gz|zip))"', page):
        target = re.search(r"_([a-z]+-[a-z0-9]+)_bin\.", url)
        if target:
            os_name, arch = target.group(1).rsplit("-", 1)
            downloads[target.group(1)] = {
                "os": os_name,
                "arch": arch,
                "url": url,
                "sha256_url": f"{url}.sha256",
            }

    return {
        "major": major,
        "version": f"{major}-ea+{build.group(1)}",
        "build": int(build.group(1)),
        "released": f"{build.group(2)}-{build.group(3)}-{build.group(4)}",
        "downloads": downloads,
    }


def latest_for_major(major, lts_majors):
    """Return details and download links of the latest GA Temurin JDK for a major version."""
    assets = get_json(f"/assets/latest/{major}/hotspot?image_type=jdk")
    if not assets:
        return None

    # Prefer the linux/x64 asset as the reference for version and release date.
    asset = next(
        (a for a in assets if a["binary"]["os"] == "linux" and a["binary"]["architecture"] == "x64"),
        assets[0],
    )

    downloads = {}
    for a in assets:
        binary = a["binary"]
        os_name = "macos" if binary["os"] == "mac" else binary["os"]
        downloads[f"{os_name}-{binary['architecture']}"] = {
            "os": os_name,
            "arch": binary["architecture"],
            "url": binary["package"]["link"],
            "sha256_url": binary["package"]["checksum_link"],
        }

    return {
        "major": major,
        "version": asset["version"]["semver"],
        "release_name": asset["release_name"],
        "lts": major in lts_majors,
        "released": asset["binary"].get("updated_at"),
        "downloads": dict(sorted(downloads.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], add_help=False)
    parser.add_argument("--h", "--help", action="help", help="show this help message and exit")
    parser.add_argument("--all", action="store_true", help="include all available major releases")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--lts", action="store_true", help="return only LTS releases")
    group.add_argument("--non-lts", action="store_true", help="return only non-LTS releases")
    group.add_argument(
        "--ea", dest="early_access", action="store_true",
        help="return only the most recent early-access build (from jdk.java.net)",
    )
    group.add_argument(
        "--list-arch", action="store_true",
        help="list all architectures available from the Adoptium API and exit",
    )
    group.add_argument(
        "--list-os", action="store_true",
        help="list all operating systems available from the Adoptium API and exit",
    )
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="output JSON instead of a table")
    fmt.add_argument("--csv", action="store_true", help="output CSV instead of a table")
    parser.add_argument(
        "--count", type=positive_int, metavar="N",
        help="only return the first N rows (a row is one release/platform download, "
             "or one entry for --list-arch and --list-os; with --version-only, one version)",
    )
    parser.add_argument(
        "--version-only", action="store_true",
        help="only return the major version number(s), one per line (JSON: a list; CSV: a "
             "'major' column). Not available with --list-arch or --list-os",
    )
    parser.add_argument(
        "--arch", type=normalize_arch, metavar="ARCH",
        help="only include downloads for this architecture, e.g. x64, aarch64, arm, "
             "ppc64le, s390x, riscv64 (aliases: amd64, x86_64 -> x64; arm64 -> aarch64)",
    )
    parser.add_argument(
        "--os", type=normalize_os, metavar="OS", dest="os_name",
        help="only include downloads for this operating system, e.g. linux, macos, windows, "
             "alpine-linux, aix (aliases: mac, osx, darwin -> macos; win -> windows; "
             "alpine -> alpine-linux). 'linux' does not include 'alpine-linux'",
    )
    parser.add_argument(
        "--no-header", action="store_true",
        help="omit the header row from table and CSV output (JSON is unaffected)",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="detect this machine's OS and architecture and filter for them. An explicit "
             "--os or --arch takes precedence over the detected value",
    )
    args = parser.parse_args()
    if args.version_only and (args.list_arch or args.list_os):
        parser.error("argument --version-only: not allowed with --list-arch or --list-os")
    if args.auto and (args.list_arch or args.list_os):
        parser.error("argument --auto: not allowed with --list-arch or --list-os")
    if args.auto:
        try:
            detected_os, detected_arch = detect_platform()
        except ValueError as exc:
            print(json.dumps({"error": f"--auto could not detect the platform: {exc}"}),
                  file=sys.stderr)
            return 1
        args.os_name = args.os_name or detected_os
        args.arch = args.arch or detected_arch
    output_format = "csv" if args.csv else "json" if args.json else "table"
    header = not args.no_header

    if args.list_arch:
        try:
            archs = get_json("/types/architectures")[:args.count]
        except (*NETWORK_ERRORS, ValueError) as exc:
            print(json.dumps({"error": f"failed to fetch architectures: {exc}"}), file=sys.stderr)
            return 1
        emit({
            "source": "Eclipse Temurin (Adoptium API)",
            "arch": args.arch,
            "os": args.os_name,
            "architectures": archs,
        }, output_format, ["arch"], [{"arch": a} for a in archs], header)
        return 0

    if args.list_os:
        try:
            oses = [OS_ALIASES.get(o, o) for o in get_json("/types/operating_systems")][:args.count]
        except (*NETWORK_ERRORS, ValueError) as exc:
            print(json.dumps({"error": f"failed to fetch operating systems: {exc}"}), file=sys.stderr)
            return 1
        emit({
            "source": "Eclipse Temurin (Adoptium API)",
            "arch": args.arch,
            "os": args.os_name,
            "operating_systems": oses,
        }, output_format, ["os"], [{"os": o} for o in oses], header)
        return 0

    if args.early_access:
        try:
            ea = latest_early_access()
        except (*NETWORK_ERRORS, ValueError) as exc:
            print(json.dumps({"error": f"failed to fetch early-access build: {exc}"}), file=sys.stderr)
            return 1
        if args.arch or args.os_name:
            ea["downloads"] = filter_downloads(ea["downloads"], args.arch, args.os_name)
            if not ea["downloads"]:
                print(json.dumps({
                    "error": f"no early-access downloads for {describe_filters(args.arch, args.os_name)}"
                }), file=sys.stderr)
                return 1
        if args.version_only:
            emit_versions("jdk.java.net (Oracle-led OpenJDK)", args.arch, args.os_name,
                          [ea["major"]], output_format, header)
            return 0
        ea["downloads"] = limit_downloads(ea["downloads"], args.count)
        emit({
            "source": "jdk.java.net (Oracle-led OpenJDK)",
            "arch": args.arch,
            "os": args.os_name,
            "early_access": ea,
        }, output_format,
            ["major", "version", "build", "released", "os", "arch", "url", "sha256_url"],
            download_rows(ea["downloads"], major=ea["major"], version=ea["version"],
                          build=ea["build"], released=ea["released"]),
            header)
        return 0

    try:
        info = get_json("/info/available_releases")
        lts = set(info["available_lts_releases"])
        if args.all:
            majors = info["available_releases"]
        else:
            majors = sorted(lts | {info["most_recent_feature_release"]})
        if args.lts:
            majors = [m for m in majors if m in lts]
        elif args.non_lts:
            majors = [m for m in majors if m not in lts]

        releases = []
        for major in sorted(majors, reverse=True):
            entry = latest_for_major(major, lts)
            if entry and (args.arch or args.os_name):
                entry["downloads"] = filter_downloads(entry["downloads"], args.arch, args.os_name)
                if not entry["downloads"]:
                    continue
            if entry:
                releases.append(entry)
    except (*NETWORK_ERRORS, KeyError, ValueError) as exc:
        print(json.dumps({"error": f"failed to fetch JDK releases: {exc}"}), file=sys.stderr)
        return 1

    if (args.arch or args.os_name) and not releases:
        print(json.dumps({
            "error": f"no releases with downloads for {describe_filters(args.arch, args.os_name)}"
        }), file=sys.stderr)
        return 1

    if args.version_only:
        emit_versions("Eclipse Temurin (Adoptium API)", args.arch, args.os_name,
                      [r["major"] for r in releases][:args.count], output_format, header)
        return 0

    releases = limit_releases(releases, args.count)
    result = {
        "source": "Eclipse Temurin (Adoptium API)",
        "arch": args.arch,
        "os": args.os_name,
        "most_recent_feature_release": info["most_recent_feature_release"],
        "most_recent_lts": info["most_recent_lts"],
        "releases": releases,
    }
    rows = [
        row for r in releases
        for row in download_rows(
            r["downloads"], major=r["major"], version=r["version"], release_name=r["release_name"],
            lts=str(r["lts"]).lower(), released=r["released"],
        )
    ]
    emit(result, output_format,
         ["major", "version", "release_name", "lts", "released", "os", "arch", "url", "sha256_url"],
         rows, header)
    return 0


if __name__ == "__main__":
    sys.exit(main())
