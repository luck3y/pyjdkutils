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

Get the newest LTS download link for a script. `--no-header` leaves out the CSV header, and
`cut -d, -f8` picks the `url` column:

```
python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --csv --no-header | cut -d, -f8
```

Download the newest LTS tarball and check it against its published checksum:

```
ROW=$(python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --csv --no-header)
URL=$(echo "$ROW" | cut -d, -f8)
SHA_URL=$(echo "$ROW" | cut -d, -f9)

curl -L -o jdk.tar.gz "$URL"
echo "$(curl -sL "$SHA_URL" | cut -d' ' -f1)  jdk.tar.gz" | sha256sum -c -
```

On ARM64 Linux (for example a Raspberry Pi 4/5 or AWS Graviton), use `--arch aarch64`. On Alpine
Linux use `--os alpine-linux`, which needs its own musl builds. `--os linux` does not include them.

Read values in a shell script, here with `jq`:

```
python3 jdk_versions.py --lts --json | jq -r '.releases[].major'
python3 jdk_versions.py --lts --os linux --arch x64 --count 1 --json \
  | jq -r '.releases[0].downloads["linux-x64"].url'
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

Download the newest LTS for this Mac and verify it:

```
ROW=$(python3 jdk_versions.py --auto --lts --count 1 --csv --no-header)
URL=$(echo "$ROW" | cut -d, -f8)
SHA_URL=$(echo "$ROW" | cut -d, -f9)

curl -L -o jdk.tar.gz "$URL"
echo "$(curl -sL "$SHA_URL" | cut -d' ' -f1)  jdk.tar.gz" | shasum -a 256 -c -
```

### Windows x64

```
python jdk_versions.py --lts --os windows --arch x64
```

`--os` accepts `windows`, `win` and `win32`. Windows builds are `.zip` files. For ARM64 Windows use
`--arch aarch64`. Note that Windows on ARM builds are not available for every release. Check with
`--all --os windows --arch aarch64 --version-only`.

Download the newest LTS zip and verify it in PowerShell:

```powershell
$row = python jdk_versions.py --lts --os windows --arch x64 --count 1 --json |
       ConvertFrom-Json
$dl  = $row.releases[0].downloads.'windows-x64'

Invoke-WebRequest -Uri $dl.url -OutFile jdk.zip
$expected = (Invoke-WebRequest -Uri $dl.sha256_url).Content.Split(' ')[0].Trim()
$actual   = (Get-FileHash jdk.zip -Algorithm SHA256).Hash
if ($actual -ieq $expected) { 'checksum OK' } else { throw 'checksum mismatch' }
```

The same thing from `cmd.exe`, using the CSV output:

```
python jdk_versions.py --lts --os windows --arch x64 --count 1 --csv --no-header
```

### Early-access builds

`--ea` returns the newest early-access build from jdk.java.net. Early-access builds exist for
Linux, macOS and Windows on `x64` and `aarch64`. They do not exist for every combination.

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
| `--h`, `--help` | Shows the help text. Note that `-h` is not accepted. |

Rules for combining options:

- `--lts`, `--non-lts`, `--ea`, `--list-arch` and `--list-os` are mutually exclusive.
- `--json` and `--csv` are mutually exclusive.
- `--auto` and `--version-only` cannot be used with `--list-arch` or `--list-os`.
- `--count` must be a whole number of 1 or more.
- Everything else can be combined. `--arch`, `--os`, `--auto`, `--count`, `--no-header` and the
  output formats work with `--ea` too.
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
| `sha256_url` | Link to the SHA-256 checksum file. CSV only. The table leaves it out. |

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
| `1` | A runtime failure. Network problems, no release matches the filters, `--auto` could not detect the platform, or the jdk.java.net page layout changed. |
| `2` | Bad usage, such as an unknown flag, a conflicting combination or an invalid `--count`. |

Runtime failures print a JSON object on **stderr**, and nothing on stdout:

```
$ python3 jdk_versions.py --os foo
{"error": "no releases with downloads for os 'foo'"}
```

That keeps stdout clean for pipes. In a script:

```
if ! URL=$(python3 jdk_versions.py --auto --lts --count 1 --csv --no-header | cut -d, -f8); then
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
platform detection, the early-access scraper, invalid combinations and network failures.

## Author

Ken Wills &lt;ken@zaptillion.net&gt;
