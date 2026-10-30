# jdk_versions.py

A command-line tool that reports the most recent released JDK versions, with download links, as a
table, JSON or CSV.

- **GA releases** come from the [Adoptium API](https://api.adoptium.net) (Eclipse Temurin builds).
- **Early-access builds** come from [jdk.java.net](https://jdk.java.net), the OpenJDK site that
  Oracle leads.

It uses only the Python standard library, so there is nothing to install. It was developed and
tested on Python 3.14.

## Contents

- [Quick start](#quick-start)
- [Usage examples](#usage-examples)
  - [Linux x64](#linux-x64)
  - [macOS](#macos)
  - [Windows x64](#windows-x64)
  - [Early-access builds](#early-access-builds)
- [Options](#options)
- [Output formats](#output-formats)
  - [Choosing fields](#choosing-fields)
- [Downloading files](#downloading-files)
- [Installing JDKs](#installing-jdks)
- [Colors and the progress bar](#colors-and-the-progress-bar)
- [How filtering and `--count` work](#how-filtering-and---count-work)
- [Platform names](#platform-names)
- [Exit codes and errors](#exit-codes-and-errors)
- [Things to know](#things-to-know)
- [Tests](#tests)

## Quick start

```
python3 jdk_versions.py                  # LTS releases plus the latest feature release, as a table
python3 jdk_versions.py --auto           # the same, limited to downloads for this machine
python3 jdk_versions.py --h              # list every option
```

On Windows, use `python` or the `py` launcher instead of `python3`.

## Usage examples

Version numbers and dates in the output below are examples. They change as new JDKs are released.

### Linux x64

List the downloads for every LTS release:

```
python3 jdk_versions.py --lts --os linux --arch x64
```

```
+-------+-----------------+-----------------+------+----------------------+-------+------+-----------------------+
| major | version         | release_name    | lts  | released             | os    | arch | url                   |
+-------+-----------------+-----------------+------+----------------------+-------+------+-----------------------+
| 25    | 25.0.5+8.0.LTS  | jdk-25.0.5+8    | true | 2026-10-22T12:28:52Z | linux | x64  | https://github.com/…  |
| 21    | 21.0.13+9.0.LTS | jdk-21.0.13+9   | true | 2026-10-22T12:07:52Z | linux | x64  | https://github.com/…  |
| 17    | 17.0.21+9       | jdk-17.0.21+9   | true | 2026-10-22T17:28:37Z | linux | x64  | https://github.com/…  |
| 11    | 11.0.32+101     | jdk-11.0.32.1+1 | true | 2026-08-24T08:24:03Z | linux | x64  | https://github.com/…  |
| 8     | 8.0.504+1       | jdk8u504-b01    | true | 2026-08-25T08:14:51Z | linux | x64  | https://github.com/…  |
+-------+-----------------+-----------------+------+----------------------+-------+------+-----------------------+
```

The table cuts the URL short here for the page. The real output shows the full link.

Let the script detect the machine instead of naming the platform:

```
python3 jdk_versions.py --auto --lts
```

Get the newest LTS download link for a script. `--fields url` keeps only that column, and
`--no-header` drops the CSV header so the URL is the only output:

```
python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --csv --no-header --fields url
```

Download the newest LTS tarball, verify its SHA-256 checksum, and save it to `jdks/`:

```
python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --download --target-directory jdks
```

On ARM64 Linux (for example a Raspberry Pi 4/5 or AWS Graviton), use `--arch aarch64`. On Alpine
Linux use `--os alpine-linux`, which needs its own musl builds. `--os linux` does not include them.

Read individual values in a shell script:

```
python3 jdk_versions.py --lts --version-only
python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --csv --no-header --fields url
```

Capture a single number:

```
LATEST=$(python3 jdk_versions.py --non-lts --version-only)   # for example: 27
```

### macOS

Apple Silicon Macs (M1 and later) are `aarch64`. Intel Macs are `x64`. `--os` accepts `macos`,
`mac`, `osx` and `darwin`.

```
python3 jdk_versions.py --lts --os macos --arch aarch64     # Apple Silicon
python3 jdk_versions.py --lts --os macos --arch x64         # Intel
python3 jdk_versions.py --lts --os macos                    # both
```

Which major versions have an Apple Silicon build:

```
python3 jdk_versions.py --all --os macos --arch aarch64 --version-only
```

Download the newest LTS for this Mac, verify its SHA-256 checksum, and save it to `jdks/`:

```
python3 jdk_versions.py --auto --lts --count 1 --download --target-directory jdks
```

### Windows x64

```
python jdk_versions.py --lts --os windows --arch x64
```

`--os` accepts `windows`, `win` and `win32`. Windows builds are `.zip` files. For ARM64 Windows use
`--arch aarch64`. Windows on ARM builds exist for only a few releases (at the time of writing, 21
and 23). Check with `--all --os windows --arch aarch64 --version-only`.

Download the newest LTS zip and verify its SHA-256 checksum:

```
python jdk_versions.py --lts --os windows --arch x64 --count 1 --download --target-directory jdks
```

`cmd.exe` has no good way to parse the output. Use the script directly with `--download`, or run it
with `--csv` and read the result with whatever tool you have.

### Early-access builds

`--ea` returns the newest early-access build from jdk.java.net. Early-access builds exist for
Linux, macOS and Windows, but not for every architecture. At the time of writing the platforms are
linux/x64, linux/aarch64, macos/aarch64 and windows/x64. Asking for a combination with no build is
an error.

```
python3 jdk_versions.py --ea --os linux --arch x64 --csv
python3 jdk_versions.py --ea --os macos --arch aarch64
python3 jdk_versions.py --ea --os windows --arch x64
python3 jdk_versions.py --ea --version-only          # 28
```

## Options

| Option | What it does |
|---|---|
| *(none)* | Prints the LTS releases plus the latest feature release, as a table. |
| `--all` | Includes every major release Adoptium publishes, not only the LTS releases and the latest feature release. |
| `--lts` | Returns only LTS releases. |
| `--non-lts` | Returns only non-LTS releases. Without `--all` that is just the latest feature release. |
| `--ea` | Returns only the most recent early-access build, from jdk.java.net. |
| `--list-arch` | Lists the architectures Adoptium supports, then exits. |
| `--list-os` | Lists the operating systems Adoptium supports, then exits. |
| `--arch ARCH` | Keeps only downloads for this architecture. See [Platform names](#platform-names). |
| `--os OS` | Keeps only downloads for this operating system. See [Platform names](#platform-names). |
| `--auto` | Detects this machine's OS and architecture and filters for them. An explicit `--os` or `--arch` takes precedence over the detected value. |
| `--count N` | Returns only the first `N` rows. See [How filtering and `--count` work](#how-filtering-and---count-work). |
| `--version-only` | Returns only the major version numbers. |
| `--json` | Prints JSON instead of a table. |
| `--csv` | Prints CSV instead of a table. |
| `--no-header` | Leaves out the header row in table and CSV output. JSON is unaffected. |
| `--fields FIELD ...` | Outputs only these fields, in this order. See [Choosing fields](#choosing-fields). |
| `--download` | Downloads every listed result into `--target-directory`. See [Downloading files](#downloading-files). |
| `--target-directory DIR` | The directory `--download` saves files in. It is created if it does not exist. |
| `--install` | Downloads (implies `--download`) and unpacks each JDK into `--prefix-dir`. See [Installing JDKs](#installing-jdks). |
| `--prefix-dir DIR` | The directory `--install` installs into. It is created if it does not exist. |
| `--install-prefix PREFIX` | The start of each install directory's name. The major version is added to it, so `jdk` gives `jdk27`. |
| `--force` | Re-downloads a file even if the local copy already matches the remote SHA-256 checksum. By default already-up-to-date files are skipped. |
| `--plain` | Turns off the colors and the download progress bar. See [Colors and the progress bar](#colors-and-the-progress-bar). |
| `--h`, `--help` | Shows the help text. Note that `-h` is not accepted. |

Rules for combining options:

- `--lts`, `--non-lts`, `--ea`, `--list-arch` and `--list-os` are mutually exclusive.
- `--json` and `--csv` are mutually exclusive.
- `--auto` and `--version-only` cannot be used with `--list-arch` or `--list-os`.
- `--fields` cannot be used with `--version-only`.
- `--download` and `--target-directory` must be used together. `--download` cannot be used with
  `--version-only`, `--list-arch` or `--list-os`.
- `--install` implies `--download`. It requires `--target-directory`, `--prefix-dir` and
  `--install-prefix`. `--prefix-dir` and `--install-prefix` cannot be used without `--install`.
- `--force` only has an effect when `--download` or `--install` is also given.
- `--count` must be a whole number of 1 or more.
- Everything else can be combined. `--arch`, `--os`, `--auto`, `--count`, `--no-header`, `--download`, `--install`,
  `--plain` and the output formats work with `--ea` too.
- The long flags can be shortened as long as the prefix is unambiguous (argparse behavior).

## Output formats

The default is a table. `--json` and `--csv` replace it.

### Table and CSV

For GA releases, each row is one release and one platform download:

| Column | Meaning |
|---|---|
| `major` | Major version, for example `25`. |
| `version` | Full version of the release (see [Things to know](#things-to-know)). |
| `release_name` | Adoptium's name for the release. |
| `lts` | `true` or `false`. |
| `released` | Timestamp of the linux/x64 binary. |
| `os`, `arch` | The platform of this download. |
| `url` | Download link. |
| `sha256_url` | Link to the SHA-256 checksum file. CSV only. The table leaves it out unless you ask for it with `--fields`. |

For `--ea`, the columns are `major`, `version`, `build`, `released`, `os`, `arch`, `url` and
`sha256_url`.

`--list-arch` and `--list-os` produce a single column, `arch` or `os`.

`--version-only` prints bare numbers, one per line, in the default table mode. CSV adds a `major`
header unless `--no-header` is given.

### JSON

```
python3 jdk_versions.py --non-lts --os windows --arch x64 --json
```

```json
{
  "source": "Eclipse Temurin (Adoptium API)",
  "arch": "x64",
  "os": "windows",
  "most_recent_feature_release": 27,
  "most_recent_lts": 25,
  "releases": [
    {
      "major": 27,
      "version": "27.0.1+0",
      "release_name": "jdk-27.0.1.0.2+1",
      "lts": false,
      "released": "2026-10-24T11:39:16Z",
      "downloads": {
        "windows-x64": {
          "os": "windows",
          "arch": "x64",
          "url": "https://github.com/adoptium/temurin27-binaries/releases/download/…",
          "sha256_url": "https://github.com/adoptium/temurin27-binaries/releases/download/…"
        }
      }
    }
  ]
}
```

- The top-level `arch` and `os` hold the filters that were applied (after aliases are resolved,
  and including `--auto`). They are `null` when no filter was used. Every command's JSON output
  has them.
- `downloads` is keyed by platform, such as `linux-x64` or `macos-aarch64`, and sorted by key.
- `--ea` uses `"early_access"` in place of `"releases"`, and `--version-only` uses
  `"major_versions"`. The list commands use `"architectures"` and `"operating_systems"`.

### Choosing fields

`--fields` outputs only the fields you name, in the order you name them. Separate names with
commas, spaces or both. Names are case-insensitive and duplicates are dropped.

```
python3 jdk_versions.py --lts --os linux --arch x64 --fields version,os,url
python3 jdk_versions.py --lts --os linux --arch x64 --csv --no-header --fields url
python3 jdk_versions.py --ea --fields version build url --json
```

- The valid fields are the column names listed above. For `--ea`, `release_name` is replaced by
  `build`. `--list-arch` accepts only `arch`, and `--list-os` accepts only `os`.
- An unknown field, an empty selection, or `--fields` together with `--version-only` exits with
  code 2 before any network request is made.
- Table and CSV output show just the chosen columns. `sha256_url` appears in the table only when
  requested.
- With `--json`, the output is a flat list instead of the nested `releases` and `downloads`
  structure:

  ```json
  {
    "source": "Eclipse Temurin (Adoptium API)",
    "arch": "x64",
    "os": "linux",
    "rows": [
      {"version": "25.0.1+8", "os": "linux", "url": "https://github.com/adoptium/…"}
    ]
  }
  ```

  Values keep their native types, so `lts` is a boolean.
- Filters and `--count` still apply to fields that are not displayed.

## Downloading files

`--download` saves every result in the listing to the directory named by `--target-directory`.
What gets downloaded is exactly what would be listed, so `--lts`, `--os`, `--arch`, `--auto` and
`--count` choose the files.

```
python3 jdk_versions.py --auto --lts --count 1 --download --target-directory ~/jdks
python3 jdk_versions.py --lts --os linux --arch x64 --download --target-directory jdks
python3 jdk_versions.py --ea --os windows --arch x64 --download --target-directory ea
```

- The listing still prints on stdout in the format you chose, so `--json` and `--csv` output stays
  valid. Progress and the final count go to **stderr**. On a terminal each file gets a progress
  bar (see [Colors and the progress bar](#colors-and-the-progress-bar)); otherwise you get one
  `downloading <name>` line per file.
- Each file is checked against its published SHA-256 checksum. The data is written to a `.part`
  file first and renamed only when the checksum matches, so a failed download never leaves a file
  under the real name.
- **Already-up-to-date files are skipped.** Before downloading, the remote checksum is fetched and
  compared with the local file. If they match, the file is skipped and `skipping <name> (already up
  to date)` is printed to stderr. Use `--force` to download regardless.
- Files keep the name from their download link. A file that is not up to date is replaced.
- A failed file does not stop the others. The script then exits with code 1 and prints one JSON
  error per failed file on stderr.
- Downloads use HTTPS only, with a 20 second timeout on each connection and no retries. The files
  are large, often 100 MB or more, so check how many rows you are asking for. `--count` limits
  them.
- `--download` requires `--target-directory`, and the reverse. Without `--download`, nothing is
  written to disk.

## Installing JDKs

`--install` downloads (implies `--download`) and unpacks each JDK. Each one goes into its own
directory under `--prefix-dir`, named `--install-prefix` followed by the major version.

```
python3 jdk_versions.py --auto --non-lts --count 1 \
    --install --target-directory ~/dl --prefix-dir /java --install-prefix jdk
```

If the newest feature release is JDK 27, that installs it as `/java/jdk27`. With `--lts --count 2`
you would get `/java/jdk25` and `/java/jdk21`. Running the same command again skips the download if
the archive is already up to date, then re-installs from the cached archive.

- **The JDK sits directly in that directory.** `/java/jdk27/bin/java`, `/java/jdk27/lib` and so on.
  There is no `jdk-27.0.1+1` directory inside it, and on macOS the `Contents/Home` wrapper is
  removed, so `JAVA_HOME=/java/jdk27` works on every platform.
- **Each major version needs exactly one download.** Two downloads for the same major (for
  example Linux and macOS builds of JDK 25) would need the same directory. The script refuses that
  before downloading anything, exits with code 1, and tells you to narrow the selection with
  `--os`, `--arch` or `--auto`.
- **An existing JDK is replaced.** The old copy is set aside while the new one moves into place
  and is restored if that fails. A directory that does not look like a JDK (no `bin/java` and no
  `release` file), a plain file or a symbolic link is never replaced. That install fails instead
  and nothing is deleted.
- **The prefix may contain letters, digits, `.`, `_`, `+` and `-`.** It cannot contain a path
  separator, so installs always land directly under `--prefix-dir`. An empty prefix is allowed and
  gives `/java/27`.
- **The archives stay in `--target-directory`.** Delete them when you no longer need them.
- **Archives are unpacked safely.** Files that would land outside the install directory, and links
  pointing outside it, are rejected. Links inside the JDK are kept. The JDK is unpacked in a
  temporary directory inside `--prefix-dir` first, so a failed install leaves no partial JDK.
- **Failures are per JDK.** A JDK that fails to install does not stop the others. The script prints
  one JSON error for it on stderr and exits with code 1.
- Each success prints `installed <path>` on stderr, and the summary line reports
  `installed N of M JDK(s)`. The listing on stdout is unchanged.
- Unpacking uses the tar extraction filters in Python's `tarfile` module, which need Python 3.9.17,
  3.10.12, 3.11.4 or later. The script supports 3.11 and newer. A Python without them reports an
  install error instead of unpacking without protection.

## Colors and the progress bar

On a terminal, the table is colored and downloads show a progress bar:

```
OpenJDK25U-jdk_x64_linux_hotspot_25.0.5_8.tar.gz [##########----------]  51% 69.0 MiB/134.8 MiB 7.6 MiB/s
```

The bar shows the file name, the share done, the bytes done and total, and the average speed. It
redraws on one line and is replaced by the final state when the file finishes. A long file name is
shortened to fit the window. If the server does not announce the file size, the bar and percentage
are left out and you see the bytes done and the speed.

**Colors.** The table borders are dimmed, the header is bold cyan, `version` is bold, `lts` is green
for `true` and yellow for `false`, and the `url` and `sha256_url` columns are blue. Only the
default table is colored. CSV and JSON never are.

Three columns are colored by group, so you can scan down a long table and see the pattern:

- **`major`** is bold, and each major version gets the next color from red, green, yellow, blue,
  magenta and cyan, in the order the versions appear. Every row of one release shares a color and
  neighbouring releases differ. The colors start over for each table, so a given release may be a
  different color in another run. After six versions they repeat.
- **`os`** has one fixed color per operating system: `linux` magenta, `alpine-linux` bright
  magenta, `macos` cyan, `windows` bright blue, `aix` blue and `solaris` yellow.
- **`arch`** has one fixed color per architecture: `x64` bright cyan, `aarch64` bright green, `arm`
  bright yellow, `x32` blue, `ppc64` bright magenta, `ppc64le` magenta, `s390x` bright blue,
  `riscv64` cyan and `sparcv9` yellow.

Red is not used for an OS or architecture, because it reads as an error. A name the script does not
know gets a color worked out from the name, so it is the same every time. Your terminal's color
scheme decides the exact shades.

**Turning them off.** Use `--plain` to turn off both the colors and the progress bar. With
`--plain` the output is the same text without the escape codes, and a download prints one
`downloading <name>` line per file instead of a bar. They also turn off by themselves:

| Situation | Colors | Progress bar |
|---|---|---|
| Output goes to a terminal | On | On |
| `--plain` | Off | Off |
| stdout is redirected or piped | Off | Unaffected (it follows stderr) |
| stderr is redirected or piped | Unaffected (they follow stdout) | Off |
| The `NO_COLOR` environment variable is set to anything but empty | Off | Unaffected |
| `TERM=dumb` | Off | Off |

The colors follow stdout and the progress bar follows stderr, so this writes a plain list to the
file and still shows the bar on the terminal:

```
python3 jdk_versions.py --lts --download --target-directory jdks > list.txt
```

`NO_COLOR` is the [no-color.org](https://no-color.org) convention.

On Windows, colors are used only in terminals that understand escape codes (Windows Terminal, VS
Code and similar). In older consoles use `--plain`.

## How filtering and `--count` work

1. **Release selection** (`--all`, `--lts`, `--non-lts`) picks the major versions.
2. **Platform filters** (`--os`, `--arch`, `--auto`) keep only matching downloads. A release with no
   matching download is dropped. If nothing is left, the script exits with an error.
3. **`--count N`** keeps the first `N` rows. A row is one release and platform download, the same
   unit as a line in the table or CSV. JSON is cut at the same place, so the format never changes
   what you get.

Rows are ordered newest release first, and within a release alphabetically by platform. That means
`--count 1` on its own returns the alphabetically first platform of the newest release, which is
usually not the one you want. Combine it with `--os` and `--arch`, or use `--auto`.

With `--version-only`, `--count` limits the number of versions. With `--list-arch` and
`--list-os`, it limits the entries.

## Platform names

Names are matched case-insensitively.

**Operating systems** (`--os`, `--list-os`):

| Name | Aliases | Notes |
|---|---|---|
| `linux` | | Does not include `alpine-linux`. |
| `alpine-linux` | `alpine` | Linux builds for musl libc. |
| `macos` | `mac`, `osx`, `darwin` | Adoptium calls this `mac`. The script reports `macos`. |
| `windows` | `win`, `win32` | |
| `aix` | | |
| `solaris` | | Only old releases. |

**Architectures** (`--arch`, `--list-arch`):

| Name | Aliases | Notes |
|---|---|---|
| `x64` | `amd64`, `x86_64`, `x86-64` | 64-bit Intel and AMD. |
| `aarch64` | `arm64` | 64-bit ARM, including Apple Silicon. |
| `arm` | | 32-bit ARM. Not the same as `aarch64`. |
| `x32` | | 32-bit x86. |
| `ppc64`, `ppc64le`, `s390x`, `riscv64`, `sparcv9` | | |

`--list-arch` and `--list-os` show everything Adoptium supports. That is not the same as everything
that has a current download. Asking for a combination with no download is an error.

`--auto` recognizes Linux, macOS, Windows, AIX and Solaris. On Linux it reports `alpine-linux` when
`/etc/alpine-release` exists. On other platforms, or architectures it doesn't know, it exits with an
error. Pass `--os` and `--arch` yourself in that case.

## Exit codes and errors

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | A runtime failure. Network problems, no release matches the filters, `--auto` could not detect the platform, the jdk.java.net page layout changed, a `--download` failed (an unusable target directory, a network error or a checksum mismatch), or an `--install` failed (an archive that is not a JDK, an unusable `--prefix-dir`, or two downloads for one major version). |
| `2` | Bad usage, such as an unknown flag, a conflicting combination, an invalid `--count`, an unknown `--fields` name, `--download` without `--target-directory`, or `--install` without `--target-directory`, `--prefix-dir` or `--install-prefix`. |

Runtime failures print a JSON object on **stderr**, and nothing on stdout:

```
$ python3 jdk_versions.py --os foo
{"error": "no releases with downloads for os 'foo'"}
```

That keeps stdout clean for pipes. In a script:

```
if ! URL=$(python3 jdk_versions.py --auto --lts --count 1 --csv --no-header --fields url); then
  echo "lookup failed" >&2
fi
```

Network failures of every kind (timeouts, resets, refused connections, TLS errors, truncated
responses) are reported this way. Each request has a 20 second timeout. The script does not retry.

## Things to know

- **The version column comes from the linux/x64 build.** Adoptium does not always ship every
  platform at the same patch level. For example, an AIX or Windows ARM64 build can be one patch
  behind. The `url` in each row is the reliable record of which build you would actually get.
- **`--ea` reads a web page.** jdk.java.net has no API for early-access builds, so the script reads
  the site's HTML. If Oracle changes the page layout, `--ea` exits with an error until the script is
  updated. These builds come from the OpenJDK project, not from oracle.com.
- **Only Temurin builds are reported** for GA releases. This is not Oracle JDK or any other vendor's
  distribution.
- **`--auto` reports what Python reports.** A 32-bit Python on a 64-bit Windows machine may report
  `x32`, which matches nothing. Pass `--arch x64` in that case.
- **Early-access downloads** are for gathering feedback and may be removed or changed at any time.

## Tests

```
python3 -m unittest -v test_jdk_versions
```

The tests are in [`test_jdk_versions.py`](test_jdk_versions.py). They use no network access: the
network layer is replaced with canned responses. They cover every option, the output formats,
platform detection, downloads, installing from real tar and zip layouts, the early-access scraper, invalid combinations and network failures.

GitHub Actions runs them on every push to `main` and on every pull request, on Python 3.11 to 3.15
on each of Linux, macOS (Apple Silicon) and Windows. See
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). Dependabot keeps the pinned action versions
up to date.

A second workflow, [`.github/workflows/install-jdk.yml`](.github/workflows/install-jdk.yml), uses
the script to install the latest non-LTS JDK on the runner. `--auto` picks the runner's OS and
architecture, so it works unchanged on Linux x64, Linux ARM64, macOS (Apple Silicon) and Windows
x64. `--install` implies `--download`, so a single command fetches, verifies and unpacks the JDK:

```
python jdk_versions.py --auto --non-lts --count 1 \
    --install --target-directory "$DOWNLOAD_DIR" \
    --prefix-dir "$PREFIX_DIR" --install-prefix jdk
```

The workflow then sets `JAVA_HOME` and `PATH` from the known install path and runs `java -version`.
It is also a worked example of using the script in a pipeline.

## Author

Ken Wills &lt;ken@zaptillion.net&gt;
