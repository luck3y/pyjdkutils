#!/usr/bin/env python3
"""Print the most recent released JDK (Eclipse Temurin) versions as a table, JSON or CSV.
Author: Ken Wills <ken@zaptillion.net>

Data sources: Adoptium API (https://api.adoptium.net) for GA releases and
https://jdk.java.net for the latest Oracle-led OpenJDK early-access build.

Usage:
    python3 jdk_versions.py                                                    # LTS releases + latest feature release, as a table
    python3 jdk_versions.py --all                                              # every available major release
    python3 jdk_versions.py --all --non-lts                                    # every non-LTS major release
    python3 jdk_versions.py --arch x64                                         # only x64 downloads (works with all modes)
    python3 jdk_versions.py --auto                                             # only downloads for this machine's OS and architecture
    python3 jdk_versions.py --auto --lts --count 1                             # newest LTS download for this machine
    python3 jdk_versions.py --auto --lts --download --target-directory jdks    # download the listed files
    python3 jdk_versions.py --auto --lts --download --target-directory jdks --force   # re-download even if already up to date
    python3 jdk_versions.py --auto --lts --install --target-directory jdks --prefix-dir /java --install-prefix jdk   # download + install as /java/jdk25, /java/jdk21, ...
    python3 jdk_versions.py --count 3                                          # only the first 3 rows (works with all modes)
    python3 jdk_versions.py --csv                                              # CSV output (works with all modes)
    python3 jdk_versions.py --ea                                               # most recent early-access build
    python3 jdk_versions.py --fields version os url                            # only these fields (commas also work)
    python3 jdk_versions.py --h                                                # show help (also --help)
    python3 jdk_versions.py --json                                             # JSON output
    python3 jdk_versions.py --list-arch                                        # list all available architectures
    python3 jdk_versions.py --list-os                                          # list all available operating systems
    python3 jdk_versions.py --lts                                              # only LTS releases
    python3 jdk_versions.py --lts --os macos --arch aarch64                    # LTS releases, macOS/aarch64 only
    python3 jdk_versions.py --lts --version-only                               # LTS major versions, one per line
    python3 jdk_versions.py --no-header                                        # table or CSV without the header row
    python3 jdk_versions.py --non-lts                                          # only the latest non-LTS feature release
    python3 jdk_versions.py --non-lts --version-only                           # just the major version, e.g. 27
    python3 jdk_versions.py --os linux                                         # only linux downloads (works with all modes)
    python3 jdk_versions.py --plain                                            # no colors and no download progress bar
"""
import argparse
import csv
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
import zlib

API = "https://api.adoptium.net/v3"
EA_SITE = "https://jdk.java.net"
TIMEOUT = 20
CHUNK_SIZE = 1024 * 1024
BAR_WIDTH = 20
BAR_INTERVAL = 0.1  # seconds between redraws of the progress bar

# Everything that can go wrong while talking to the network. OSError covers URLError, HTTPError,
# timeouts, connection resets/refusals, broken pipes and TLS failures; HTTPException covers
# protocol-level failures such as IncompleteRead and a server closing the connection early.
NETWORK_ERRORS = (OSError, http.client.HTTPException)

# Everything that can go wrong while unpacking and installing a downloaded archive: a full disk or
# missing permissions (OSError), a corrupt gzip, tar or zip file, or an archive that is not a JDK.
INSTALL_ERRORS = (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, EOFError, zlib.error)

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


# The fields (columns) available for each kind of output, in their default order.
RELEASE_COLUMNS = ["major", "version", "release_name", "lts", "released", "os", "arch", "url",
                   "sha256_url"]
EA_COLUMNS = ["major", "version", "build", "released", "os", "arch", "url", "sha256_url"]


def parse_fields(raw, valid):
    """Turn the --fields arguments into an ordered list of unique, validated field names.

    Names may be separated by commas, spaces or both, so "version, os, url", "version,os,url" and
    "version os url" are equivalent. Raises ValueError for an empty or unknown selection.
    """
    names = []
    for token in raw:
        for name in re.split(r"[,\s]+", token.strip().lower()):
            if name and name not in names:
                names.append(name)
    if not names:
        raise ValueError("no field names given")
    unknown = [n for n in names if n not in valid]
    if unknown:
        label = "field" if len(unknown) == 1 else "fields"
        raise ValueError(f"unknown {label} {', '.join(repr(u) for u in unknown)} "
                         f"(valid fields: {', '.join(valid)})")
    return names


def cell(value):
    """Render a value for table and CSV output: None is blank, booleans are true/false."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


BOLD, DIM, GREEN, YELLOW, BLUE, CYAN = "1", "2", "32", "33", "34", "36"

# Colors that group rows in the table. Each OS and architecture has a fixed color, so "linux" or
# "aarch64" looks the same in every run. Majors get the next color from MAJOR_COLORS in the order
# they first appear, so the rows of one release share a color and neighbouring releases differ.
# Names that aren't listed get a color derived from the name. Red is not used for an OS or
# architecture, because it reads as an error, and plain white and black are avoided because one of
# them is unreadable on any terminal background.
OS_COLORS = {"linux": "35", "alpine-linux": "95", "macos": "36", "windows": "94", "aix": "34",
             "solaris": "33"}
ARCH_COLORS = {"x64": "96", "aarch64": "92", "arm": "93", "x32": "34", "ppc64": "95",
               "ppc64le": "35", "s390x": "94", "riscv64": "36", "sparcv9": "33"}
MAJOR_COLORS = ["91", "92", "93", "94", "95", "96"]
FALLBACK_COLORS = MAJOR_COLORS[1:]  # for OS and architecture names that aren't listed: no red


def style(text, *codes):
    """Wrap text in ANSI escape codes (no-op without codes)."""
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if codes else text


def is_interactive(stream):
    """True if stream is a real terminal that can redraw a line (not a pipe, file or dumb tty)."""
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty()) and os.environ.get("TERM") != "dumb"


def use_color(stream):
    """True if ANSI colors should be written to stream: a capable terminal and NO_COLOR unset.

    On Windows, where older consoles print escape codes literally, colors are used only in
    terminals known to support them (Windows Terminal, VS Code and similar).
    """
    if os.environ.get("NO_COLOR") or not is_interactive(stream):
        return False
    if sys.platform == "win32":
        return any(os.environ.get(v) for v in ("WT_SESSION", "TERM_PROGRAM", "ANSICON"))
    return True


def group_color(colors, value):
    """The fixed color for value, or one derived from its name if it isn't in colors."""
    return colors.get(value) or FALLBACK_COLORS[sum(map(ord, value)) % len(FALLBACK_COLORS)]


def cell_codes(column, value, majors=None):
    """The ANSI codes for one table cell, chosen by its column and value.

    majors is a dict shared by every cell of one table, which hands out a color to each major
    version the first time it is seen. Without it the major column is left uncolored.
    """
    if column == "lts":
        return (GREEN,) if value == "true" else (YELLOW,)
    if column == "version":
        return (BOLD,)
    if column in ("url", "sha256_url"):
        return (BLUE,)
    if column == "os" and value:
        return (group_color(OS_COLORS, value),)
    if column == "arch" and value:
        return (group_color(ARCH_COLORS, value),)
    if column == "major" and majors is not None and value:
        return (BOLD, majors.setdefault(value, MAJOR_COLORS[len(majors) % len(MAJOR_COLORS)]))
    return ()


def format_table(columns, rows, header=True, keep_sha256=False, color=False):
    """Render rows as a plain-text table.

    The sha256_url column is left out by default to keep the table readable; keep_sha256=True
    includes it. With header=False the column names and the rule beneath them are left out.
    color=True adds ANSI colors; padding is applied before coloring so columns stay aligned.
    """
    if not keep_sha256:
        columns = [c for c in columns if c != "sha256_url"]
    cells = [[cell(row.get(c)) for c in columns] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]

    def paint(text, *codes):
        return style(text, *codes) if color else text

    border = paint("+-" + "-+-".join("-" * w for w in widths) + "-+", DIM)
    bar = paint("|", DIM)

    def line(values, codes_for):
        parts = [paint(v.ljust(w), *codes_for(c, v)) for c, v, w in zip(columns, values, widths)]
        return f"{bar} " + f" {bar} ".join(parts) + f" {bar}"

    majors = {}
    head = [line(columns, lambda c, v: (BOLD, CYAN)), border] if header else []
    body = [line(r, lambda c, v: cell_codes(c, v, majors)) for r in cells]
    return "\n".join([border, *head, *body, border])


def emit(payload, fmt, columns, rows, header=True, fields=None, color=False):
    """Write the result to stdout as a table, indented JSON, or CSV.

    header=False drops the header row from the table and CSV output; JSON is unaffected.

    fields, if given, is the list of columns to output, in that order. Table and CSV show just
    those columns (the table includes sha256_url if it is asked for). JSON becomes the usual
    source/arch/os envelope plus a flat "rows" list of objects holding only those fields.

    color=True colors the table. CSV and JSON are never colored.
    """
    if fields:
        columns = fields
        rows = [{f: row.get(f) for f in fields} for row in rows]
        payload = {**{k: payload[k] for k in ("source", "arch", "os")}, "rows": rows}
    if fmt == "csv":
        writer = csv.writer(sys.stdout, lineterminator="\n")
        if header:
            writer.writerow(columns)
        writer.writerows([cell(row.get(c)) for c in columns] for row in rows)
    elif fmt == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(format_table(columns, rows, header, keep_sha256=bool(fields), color=color))


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


def filename_from_url(url):
    """Return the file name to save a download as, or raise ValueError if the URL is unsafe."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("refusing to download over anything but HTTPS")
    name = urllib.parse.unquote(parsed.path).rsplit("/", 1)[-1]
    if name in ("", ".", "..") or any(c in name for c in '\\:\0'):
        raise ValueError(f"cannot make a safe file name from '{name}'")
    return name


def parse_sha256(text):
    """Return the SHA-256 hex digest from a checksum file ('<digest>' or '<digest>  <name>')."""
    words = text.split()
    digest = words[0].lower() if words else ""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("the checksum file does not hold a SHA-256 digest")
    return digest


def format_size(num):
    """Format a byte count for people, in binary units: 512 B, 3.4 KiB, 141.3 MiB, 1.2 GiB."""
    num = float(num)
    for unit in ("B", "KiB", "MiB"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GiB"


def content_length(resp):
    """The size announced by a response's Content-Length header, or None if unknown."""
    headers = getattr(resp, "headers", None)
    try:
        value = int(headers.get("Content-Length")) if headers is not None else None
    except (TypeError, ValueError):
        return None
    return value if value and value > 0 else None


def terminal_width(stream, default=80):
    """The stream's terminal width, or default if it can't be found or is reported as 0."""
    try:
        return os.get_terminal_size(stream.fileno()).columns or default
    except (OSError, ValueError, AttributeError):
        return default


class ProgressBar:
    """A one-line download progress bar that redraws itself in place on a terminal.

    Shows the file name, a bar, the percentage, bytes done/total and the average speed. If the
    total size is unknown the bar and percentage are left out. Redraws are limited to one every
    BAR_INTERVAL seconds so a fast download doesn't flood the terminal.
    """

    def __init__(self, name, total, stream=None, color=False, width=None, clock=time.monotonic):
        self.name, self.total = name, total
        self.stream = stream if stream is not None else sys.stderr
        self.color = color
        self.width = width or terminal_width(self.stream)
        self.clock = clock
        self.start = self.last_draw = clock()
        self.done = 0
        self.drawn = 0  # width of the last line drawn, so a shorter one can blank out the rest

    def line(self):
        """Return (text, visible_length) for the current state; text has escapes only if color."""
        elapsed = max(self.clock() - self.start, 1e-9)
        rate = f"{format_size(self.done / elapsed)}/s"
        size = format_size(self.done)
        if self.total:
            frac = min(self.done / self.total, 1.0)
            filled = int(BAR_WIDTH * frac)
            size = f"{size}/{format_size(self.total)}"
            tail = [(" [", ()), ("#" * filled, (GREEN,)), ("-" * (BAR_WIDTH - filled), (DIM,)),
                    ("] ", ()), (f"{int(frac * 100):3d}%", (BOLD,)), (f" {size} {rate}", ())]
        else:
            tail = [(f"  {size} {rate}", ())]
        room = max(self.width - 1 - sum(len(t) for t, _ in tail), 8)
        name = self.name if len(self.name) <= room else self.name[:room - 3] + "..."
        parts = [(name, (BOLD,))] + tail
        plain = "".join(t for t, _ in parts)
        text = "".join(style(t, *c) if self.color else t for t, c in parts)
        return text, len(plain)

    def draw(self, end=""):
        text, length = self.line()
        padding = " " * max(self.drawn - length, 0)
        self.stream.write(f"\r{text}{padding}{end}")
        self.stream.flush()
        self.drawn = length

    def update(self, done):
        self.done = done
        now = self.clock()
        if now - self.last_draw >= BAR_INTERVAL:
            self.last_draw = now
            self.draw()

    def finish(self):
        """Draw the final state and move to the next line."""
        self.draw(end="\n")

    def abort(self):
        """Erase the bar so an error message can take its place."""
        if self.drawn:
            self.stream.write("\r" + " " * self.drawn + "\r")
            self.stream.flush()


def download_verified(url, sha256_url, dest, make_progress=None):
    """Download url to dest, keeping it only if it matches the published SHA-256 checksum.

    The data goes to dest + '.part' first, so a failed or corrupt download never leaves a file
    under the final name. make_progress, if given, is called with the total size (or None) once
    the download starts and must return an object with update(done), finish() and abort().
    """
    expected = parse_sha256(fetch(sha256_url, "text/plain"))
    part = dest + ".part"
    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "jdk-versions/1.0"})
    bar = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(part, "wb") as out:
            if make_progress:
                bar = make_progress(content_length(resp))
            done = 0
            while chunk := resp.read(CHUNK_SIZE):
                digest.update(chunk)
                out.write(chunk)
                done += len(chunk)
                if bar:
                    bar.update(done)
        if digest.hexdigest() != expected:
            raise ValueError("checksum mismatch")
        os.replace(part, dest)
        if bar:
            bar.finish()
    except BaseException:
        if bar:
            bar.abort()
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def install_prefix_name(value):
    """argparse type for --install-prefix: it names part of one directory, never a path."""
    if not re.fullmatch(r"[\w.+-]*", value):
        raise argparse.ArgumentTypeError(
            f"invalid prefix '{value}': use only letters, digits, '.', '_', '+' and '-' "
            "(it is the start of a directory name, not a path)")
    return value


def install_conflict(rows):
    """Return a message if rows hold more than one download for a major version, else None.

    Each major version is installed into one directory, so two platforms of the same release
    (say linux-x64 and macos-aarch64 JDK 25) would fight over it.
    """
    platforms = {}
    for row in rows:
        platforms.setdefault(row["major"], []).append(f"{row['os']}-{row['arch']}")
    crowded = [f"JDK {major} has {', '.join(names)}"
               for major, names in platforms.items() if len(names) > 1]
    if not crowded:
        return None
    return ("--install needs exactly one download for each major version, because each one is "
            "installed into a single directory: " + "; ".join(crowded)
            + ". Narrow the choice with --os and --arch, or use --auto")


def has_java(directory):
    """True if directory has a bin/java (bin/java.exe on Windows) launcher."""
    return any(os.path.isfile(os.path.join(directory, "bin", name)) for name in ("java", "java.exe"))


def find_jdk_home(root):
    """Return the directory under root that holds the JDK itself (bin/, lib/ and so on).

    Archives wrap the JDK in one versioned directory such as jdk-25.0.5+8, and macOS archives put
    the JDK a level further down, in Contents/Home. The result is the directory that has the
    bin/java launcher, so none of that wrapping is carried into the installed JDK.
    """
    candidates = [root]
    entries = os.listdir(root)
    if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
        candidates.append(os.path.join(root, entries[0]))
    candidates += [os.path.join(c, "Contents", "Home") for c in candidates]
    for candidate in candidates:
        if has_java(candidate):
            return candidate
    raise ValueError("the archive does not hold a JDK (there is no bin/java in it)")


def unpack(archive, directory):
    """Unpack a .tar.gz or .zip archive into directory, refusing anything that would escape it.

    tar files are extracted with the "data" filter, which rejects absolute paths, '..' paths and
    links that point outside, and drops special permission bits. zipfile removes absolute paths
    and '..' parts from names itself.
    """
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(directory)
    elif archive.endswith((".tar.gz", ".tgz")):
        if not hasattr(tarfile, "data_filter"):
            raise ValueError("this Python cannot safely unpack tar files; update it to a release "
                             "with tarfile extraction filters (3.9.17, 3.10.12, 3.11.4, 3.12+)")
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(directory, filter="data")
    else:
        raise ValueError(f"do not know how to unpack '{os.path.basename(archive)}'")


def replace_directory(source, dest):
    """Move source to dest. An existing JDK at dest is replaced; anything else is left alone.

    The old directory is set aside first and put back if the move fails, so a failure never
    leaves the installation half done.
    """
    if not os.path.lexists(dest):
        os.replace(source, dest)
        return
    if os.path.islink(dest) or not os.path.isdir(dest):
        raise ValueError(f"'{dest}' exists and is not a directory, so it was not replaced")
    if not (has_java(dest) or os.path.isfile(os.path.join(dest, "release"))):
        raise ValueError(f"'{dest}' exists but does not look like a JDK, so it was not replaced")
    old = f"{dest}.replaced-{os.getpid()}"
    os.replace(dest, old)
    try:
        os.replace(source, dest)
    except BaseException:
        os.replace(old, dest)
        raise
    shutil.rmtree(old, ignore_errors=True)


def install_jdk(archive, major, prefix_dir, install_prefix):
    """Unpack a downloaded JDK archive as prefix_dir/<install_prefix><major>; return that path.

    The directory holds the JDK directly (prefix_dir/jdk25/bin, .../lib and so on) with no
    version-named directory inside it. The archive is unpacked in a scratch directory next to the
    destination and moved into place only when it checks out, and the scratch directory is always
    removed.
    """
    os.makedirs(prefix_dir, exist_ok=True)
    dest = os.path.join(prefix_dir, f"{install_prefix}{major}")
    staging = tempfile.mkdtemp(prefix=".jdk-", dir=prefix_dir)
    try:
        unpack(archive, staging)
        replace_directory(find_jdk_home(staging), dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return dest


def file_matches_sha256(path, expected):
    """True if the file at path exists and its SHA-256 digest matches expected."""
    if not os.path.isfile(path):
        return False
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(CHUNK_SIZE):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected


def download_all(rows, directory, show_bar=False, installer=None, force=False):
    """Download every row's url into directory, reporting progress on stderr.

    With show_bar a progress bar is drawn for each file; otherwise one "downloading <name>" line
    is printed per file. installer, if given, is called with (archive_path, row) after each
    verified download and returns where the JDK was installed. When force is False (default) a
    file whose SHA-256 already matches the remote checksum is skipped. Returns the exit code: 0
    if every file downloaded, verified and installed, otherwise 1. A failed file does not stop
    the rest.
    """
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"error": f"cannot use target directory '{directory}': {exc}"}),
              file=sys.stderr)
        return 1
    download_failures = install_failures = installed = skipped = 0
    for row in rows:
        try:
            name = filename_from_url(row["url"])
            archive = os.path.join(directory, name)
            if not force:
                try:
                    expected = parse_sha256(fetch(row["sha256_url"], "text/plain"))
                except (*NETWORK_ERRORS, ValueError) as exc:
                    print(json.dumps({"error": f"failed to fetch checksum for {name}: {exc}"}),
                          file=sys.stderr)
                    download_failures += 1
                    continue
                if file_matches_sha256(archive, expected):
                    print(f"skipping {name} (already up to date)", file=sys.stderr)
                    skipped += 1
                    if installer:
                        try:
                            print(f"installed {installer(archive, row)}", file=sys.stderr)
                            installed += 1
                        except INSTALL_ERRORS as exc:
                            print(json.dumps({"error": f"failed to install {name}: {exc}"}),
                                  file=sys.stderr)
                            install_failures += 1
                    continue
            make_progress = None
            if show_bar:
                def make_progress(total, name=name):
                    return ProgressBar(name, total, color=use_color(sys.stderr))
            else:
                print(f"downloading {name}", file=sys.stderr)
            download_verified(row["url"], row["sha256_url"], archive, make_progress)
        except (*NETWORK_ERRORS, ValueError) as exc:
            print(json.dumps({"error": f"failed to download {row['url']}: {exc}"}), file=sys.stderr)
            download_failures += 1
            continue
        if installer:
            try:
                print(f"installed {installer(archive, row)}", file=sys.stderr)
                installed += 1
            except INSTALL_ERRORS as exc:
                print(json.dumps({"error": f"failed to install {name}: {exc}"}), file=sys.stderr)
                install_failures += 1
    downloaded = len(rows) - download_failures - skipped
    print(f"downloaded {downloaded} of {len(rows)} file(s) to {directory}"
          + (f" ({skipped} skipped, already up to date)" if skipped else ""),
          file=sys.stderr)
    if installer:
        print(f"installed {installed} of {len(rows)} JDK(s)", file=sys.stderr)
    return 1 if download_failures or install_failures else 0


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


def finish_downloads(rows, args, show_bar):
    """Run --download (and --install, if given) for the listed rows; return the exit code."""
    if not args.download:
        return 0
    installer = None
    if args.install:
        conflict = install_conflict(rows)
        if conflict:
            print(json.dumps({"error": conflict}), file=sys.stderr)
            return 1

        def installer(archive, row):
            return install_jdk(archive, row["major"], args.prefix_dir, args.install_prefix)
    return download_all(rows, args.target_directory, show_bar, installer, force=args.force)


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
        "--fields", nargs="+", metavar="FIELD",
        help="only output these fields, in this order, separated by commas, spaces or both, "
             "e.g. --fields version,os,url. Releases: " + ", ".join(RELEASE_COLUMNS)
             + ". With --ea: " + ", ".join(EA_COLUMNS) + ". --list-arch: arch. --list-os: os. "
             "JSON output becomes a flat 'rows' list. Not available with --version-only",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="download every listed result (after --os, --arch, --count and the other filters) "
             "into --target-directory, checking each against its SHA-256 checksum. Progress goes "
             "to stderr. Not available with --list-arch, --list-os or --version-only. Implied "
             "by --install",
    )
    parser.add_argument(
        "--target-directory", metavar="DIR",
        help="the directory --download saves files in (created if it does not exist)",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="unpack each downloaded JDK into --prefix-dir, as a directory named "
             "--install-prefix followed by the major version (--prefix-dir /java "
             "--install-prefix jdk puts JDK 27 in /java/jdk27). The directory holds the JDK "
             "itself (bin/, lib/ and so on) with no versioned directory inside it. Implies "
             "--download; one download per major version. An existing JDK there is replaced",
    )
    parser.add_argument(
        "--prefix-dir", metavar="DIR",
        help="the directory --install installs into (created if it does not exist)",
    )
    parser.add_argument(
        "--install-prefix", type=install_prefix_name, metavar="PREFIX",
        help="the start of each install directory's name; the major version is added to it "
             "(jdk -> jdk27). Letters, digits, '.', '_', '+' and '-' only",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="re-download a file even if the local copy already matches the remote SHA-256 "
             "checksum (default: skip files that are already up to date)",
    )
    parser.add_argument(
        "--plain", action="store_true",
        help="no colors in the table and no download progress bar (both are also off when the "
             "output is not a terminal; colors are off when NO_COLOR is set)",
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
    if args.install and not args.download:
        args.download = True
    if args.download and args.target_directory is None:
        parser.error("argument --download/--install: requires --target-directory")
    if args.target_directory is not None and not args.download:
        parser.error("argument --target-directory: requires --download")
    if args.download and (args.list_arch or args.list_os or args.version_only):
        parser.error("argument --download: not allowed with --list-arch, --list-os or "
                     "--version-only")
    install_args = {"--prefix-dir": args.prefix_dir, "--install-prefix": args.install_prefix}
    if args.install:
        for flag, value in install_args.items():
            if value is None:
                parser.error(f"argument --install: requires {flag}")
    else:
        for flag, value in install_args.items():
            if value is not None:
                parser.error(f"argument {flag}: requires --install")
    fields = None
    if args.fields:
        if args.version_only:
            parser.error("argument --fields: not allowed with --version-only")
        valid = (["arch"] if args.list_arch else ["os"] if args.list_os
                 else EA_COLUMNS if args.early_access else RELEASE_COLUMNS)
        try:
            fields = parse_fields(args.fields, valid)
        except ValueError as exc:
            parser.error(f"argument --fields: {exc}")
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
    color = not args.plain and use_color(sys.stdout)
    show_bar = not args.plain and is_interactive(sys.stderr)

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
        }, output_format, ["arch"], [{"arch": a} for a in archs], header, fields, color)
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
        }, output_format, ["os"], [{"os": o} for o in oses], header, fields, color)
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
        rows = download_rows(ea["downloads"], major=ea["major"], version=ea["version"],
                             build=ea["build"], released=ea["released"])
        emit({
            "source": "jdk.java.net (Oracle-led OpenJDK)",
            "arch": args.arch,
            "os": args.os_name,
            "early_access": ea,
        }, output_format, EA_COLUMNS, rows, header, fields, color)
        return finish_downloads(rows, args, show_bar)

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
            lts=r["lts"], released=r["released"],
        )
    ]
    emit(result, output_format, RELEASE_COLUMNS, rows, header, fields, color)
    return finish_downloads(rows, args, show_bar)


if __name__ == "__main__":
    sys.exit(main())
