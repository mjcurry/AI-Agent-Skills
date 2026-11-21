#!/usr/bin/env python3
"""Check that the packages a manifest names really exist - and look established.

Models invent plausible dependency names; attackers register them. This reads
requirements files, pyproject.toml and package.json (or names given directly),
asks the public registry about each one, and reports:

  not-found   no such package: a hallucinated name, or a typo. Do not install.
  suspicious  it exists, but is very new or barely used - which is exactly
              what a freshly squatted name looks like. Needs a human look.
  ok          exists and looks established. (Not a safety audit.)
  unknown     the registry could not be reached. Never treated as ok.

Exit code: 1 if anything is not-found (or suspicious, with --strict); 3 if
nothing was worse but some lookups came back unknown - a check that could not
run is not a pass; 0 otherwise.

Standard library only. Package NAMES are sent to pypi.org / registry.npmjs.org
(or the URLs you pass); use --skip for internal packages.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

USER_AGENT = "package-reality-check/1"
MAX_BYTES = 40 * 1024 * 1024
PEP508_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
NPM_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
NPM_DIRECT = ("file:", "link:", "workspace:", "git", "http:", "https:", "github:", "portal:", "patch:")


CA_FALLBACKS = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
                "/etc/pki/tls/certs/ca-bundle.crt",
                "/opt/homebrew/etc/ca-certificates/cert.pem",
                "/usr/local/etc/ca-certificates/cert.pem")


def tls_context(ca_file: str | None = None) -> tuple[ssl.SSLContext, str]:
    """A verifying TLS context, plus a note if a fallback bundle was needed.

    Some Python installs ship with no CA bundle at all (python.org builds on
    macOS until "Install Certificates" is run), which fails every lookup. Fall
    back to the system bundle instead. Verification is never switched off.
    """
    if ca_file:
        return ssl.create_default_context(cafile=ca_file), ""
    ctx = ssl.create_default_context()
    capath = ssl.get_default_verify_paths().capath
    has_dir = bool(capath and os.path.isdir(capath) and os.listdir(capath))
    if ctx.cert_store_stats().get("x509_ca") or has_dir:
        return ctx, ""
    candidates: list[str] = []
    try:
        import certifi
        candidates.append(certifi.where())
    except ImportError:
        pass
    for path in [*candidates, *CA_FALLBACKS]:
        if os.path.isfile(path):
            try:
                return (ssl.create_default_context(cafile=path),
                        f"this Python has no CA bundle of its own; TLS was verified against {path}")
            except (ssl.SSLError, OSError):
                continue
    return ctx, "this Python has no CA bundle and no system bundle was found - pass --ca-file"


def make_fetcher(context: ssl.SSLContext):
    def http_json(url: str, timeout: float):
        """-> (status | None, data | None, error text)."""
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
                raw = r.read(MAX_BYTES)
                try:
                    return r.status, json.loads(raw.decode("utf-8", "replace")), ""
                except json.JSONDecodeError:
                    return r.status, None, "response was not JSON"
        except urllib.error.HTTPError as e:
            return e.code, None, f"HTTP {e.code}"
        except (OSError, ValueError, http.client.HTTPException) as e:
            reason = str(getattr(e, "reason", e)) or e.__class__.__name__
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                reason = "TLS certificate verification failed"
            return None, None, reason
    return http_json


def parse_time(value) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


# --- manifest readers -----------------------------------------------------------

def read_requirements(path: str) -> tuple[list[str], list[str]]:
    names, notes = [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-"):
                if line.split()[0] in ("-r", "--requirement", "-c", "--constraint"):
                    notes.append(f"{path}: '{line}' is not followed - pass that file too")
                continue
            if "://" in line or line[0] in "./" or " @ " in line:
                continue                               # direct URL / path reference
            m = PEP508_NAME.match(line)
            if m:
                names.append(m.group(1))
    return names, notes


def read_pyproject(path: str) -> tuple[list[str], list[str]]:
    try:
        import tomllib
    except ImportError:
        return [], [f"{path}: needs Python 3.11+ (tomllib) to be read - skipped"]
    with open(path, "rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            return [], [f"{path}: invalid TOML ({exc})"]
    specs: list[str] = []
    project = data.get("project") or {}
    specs += [s for s in project.get("dependencies") or [] if isinstance(s, str)]
    for group in (project.get("optional-dependencies") or {}).values():
        specs += [s for s in group or [] if isinstance(s, str)]
    for group in (data.get("dependency-groups") or {}).values():
        specs += [s for s in group or [] if isinstance(s, str)]
    names = []
    for spec in specs:
        if "://" in spec or " @ " in spec:
            continue
        m = PEP508_NAME.match(spec)
        if m:
            names.append(m.group(1))
    poetry = ((data.get("tool") or {}).get("poetry") or {})
    tables = [poetry.get("dependencies") or {}]
    tables += [(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values()]
    for table in tables:
        for name, spec in table.items():
            if name.lower() == "python":
                continue
            if isinstance(spec, dict) and any(k in spec for k in ("path", "git", "url")):
                continue
            names.append(name)
    return names, []


def read_package_json(path: str) -> tuple[list[str], list[str]]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            return [], [f"{path}: invalid JSON ({exc})"]
    names = []
    for section in NPM_SECTIONS:
        for name, spec in (data.get(section) or {}).items():
            spec = str(spec)
            if spec.startswith("npm:"):                # alias: check the real package
                m = re.match(r"^npm:((?:@[^/@]+/)?[^@]+)", spec)
                if m:
                    names.append(m.group(1))
                continue
            if spec.startswith(NPM_DIRECT) or re.match(r"^[\w.-]+/[\w.-]+", spec):
                continue                               # local, git or tarball source
            names.append(name)
    return names, []


def collect(files: list[str], pypi: list[str], npm: list[str]):
    wanted: dict[tuple[str, str], dict] = {}
    notes: list[str] = []

    def add(eco: str, name: str, source: str) -> None:
        key = (eco, re.sub(r"[-_.]+", "-", name).lower() if eco == "pypi" else name)
        entry = wanted.setdefault(key, {"ecosystem": eco, "name": key[1], "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)

    for path in files:
        base = os.path.basename(path)
        try:
            if base == "package.json":
                names, more = read_package_json(path)
                eco = "npm"
            elif base == "pyproject.toml":
                names, more = read_pyproject(path)
                eco = "pypi"
            else:
                names, more = read_requirements(path)
                eco = "pypi"
        except OSError as exc:
            notes.append(f"{path}: cannot read ({exc.strerror})")
            continue
        notes += more
        for n in names:
            add(eco, n, path)
    for n in pypi:
        add("pypi", n, "--pypi")
    for n in npm:
        add("npm", n, "--npm")
    return list(wanted.values()), notes


# --- registry checks ---------------------------------------------------------------

def check_pypi(pkg: dict, cfg) -> dict:
    status, data, err = cfg.fetch(f"{cfg.pypi_url}/pypi/{quote(pkg['name'])}/json", cfg.timeout)
    if status == 404:
        return {**pkg, "status": "not-found", "flags": [],
                "detail": "no such project on the index - hallucinated name or typo"}
    if status != 200 or not isinstance(data, dict):
        return {**pkg, "status": "unknown", "flags": [],
                "detail": f"registry check failed ({err or 'unexpected response'})"}
    releases = data.get("releases") or {}
    uploads = [parse_time(f.get("upload_time_iso_8601") or f.get("upload_time"))
               for files in releases.values() for f in files or []]
    uploads = [u for u in uploads if u]
    out = {**pkg, "latest": (data.get("info") or {}).get("version"),
           "releases": sum(1 for files in releases.values() if files)}
    if uploads:
        first = min(uploads)
        out["first_published"] = first.date().isoformat()
        out["age_days"] = (cfg.now - first).days
    return grade(out, cfg)


def check_npm(pkg: dict, cfg) -> dict:
    enc = quote(pkg["name"], safe="@")
    status, data, err = cfg.fetch(f"{cfg.npm_url}/{enc}/latest", cfg.timeout)
    if status == 404:
        hint = " (a scoped name may simply be private)" if pkg["name"].startswith("@") else ""
        return {**pkg, "status": "not-found", "flags": [],
                "detail": f"not on the public registry - hallucinated name or typo{hint}"}
    if status != 200 or not isinstance(data, dict):
        return {**pkg, "status": "unknown", "flags": [],
                "detail": f"registry check failed ({err or 'unexpected response'})"}
    out = {**pkg, "latest": data.get("version")}
    dstatus, ddata, _ = cfg.fetch(f"{cfg.npm_downloads_url}/downloads/point/last-week/{pkg['name']}",
                                  cfg.timeout)
    if dstatus == 200 and isinstance(ddata, dict) and isinstance(ddata.get("downloads"), int):
        out["weekly_downloads"] = ddata["downloads"]
    # The full document is large for popular packages, and their age is not in
    # question - only fetch it when adoption is low or unknown.
    if out.get("weekly_downloads", 0) < cfg.min_downloads:
        fstatus, fdata, _ = cfg.fetch(f"{cfg.npm_url}/{enc}", cfg.timeout)
        if fstatus == 200 and isinstance(fdata, dict):
            created = parse_time((fdata.get("time") or {}).get("created"))
            if created:
                out["first_published"] = created.date().isoformat()
                out["age_days"] = (cfg.now - created).days
            out["releases"] = len(fdata.get("versions") or {})
    return grade(out, cfg)


def grade(out: dict, cfg) -> dict:
    flags = []
    age, downloads, releases = out.get("age_days"), out.get("weekly_downloads"), out.get("releases")
    if age is not None and age < cfg.min_age_days:
        flags.append(f"first published {age} day(s) ago")
    if downloads is not None and downloads < cfg.min_downloads:
        flags.append(f"{downloads:,} downloads last week")
    if releases is not None and releases <= 1:
        flags.append("a single release")
    # New, or barely used: what a freshly registered squat looks like.
    new = age is not None and age < cfg.min_age_days
    unused = downloads is not None and downloads < cfg.min_downloads
    out["flags"] = flags
    out["status"] = "suspicious" if (new or unused) else "ok"
    if out["status"] == "suspicious":
        out["detail"] = "exists, but looks unestablished - confirm it is the package you mean"
    elif age is None and downloads is None:
        out["detail"] = "exists; no age or adoption data available"
    else:
        out["detail"] = "exists and looks established"
    return out


STATUS_ORDER = {"not-found": 0, "suspicious": 1, "unknown": 2, "ok": 3, "skipped": 4}


def render(results: list[dict], notes: list[str]) -> str:
    counts = {s: sum(1 for r in results if r["status"] == s) for s in STATUS_ORDER}
    head = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    lines = [f"{len(results)} package(s): {head}", ""]
    lines.append(f"{'STATUS':<12}{'ECO':<6}{'PACKAGE':<34}{'AGE':>9}{'DL/WEEK':>12}  NOTES")
    lines.append("-" * 100)
    for r in sorted(results, key=lambda r: (STATUS_ORDER[r["status"]], r["ecosystem"], r["name"])):
        age = f"{r['age_days'] // 365}y" if r.get("age_days", 0) >= 365 else \
            (f"{r['age_days']}d" if "age_days" in r else "-")
        dl = f"{r['weekly_downloads']:,}" if "weekly_downloads" in r else "-"
        name = r["name"] if len(r["name"]) <= 32 else r["name"][:31] + "~"
        note = "; ".join(r.get("flags") or []) or r.get("detail", "")
        lines.append(f"{r['status']:<12}{r['ecosystem']:<6}{name:<34}{age:>9}{dl:>12}  {note}")
    if counts["not-found"]:
        lines += ["", "not-found: do NOT install or 'fix' by guessing a similar name. Find the real "
                  "package from the project's own docs, or remove the dependency."]
    if counts["unknown"]:
        lines += ["", "unknown: the registry could not be reached - these are unverified, not verified."]
    lines += [""] + [f"note: {n}" for n in notes]
    lines.append("Existing is not the same as safe: this checks that a name is real and established, "
                 "not what the code does.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Check that declared packages exist on the registry and look established.")
    p.add_argument("files", nargs="*",
                   help="requirements*.txt, pyproject.toml, or package.json files.")
    p.add_argument("--pypi", nargs="+", default=[], metavar="NAME", help="PyPI package names to check.")
    p.add_argument("--npm", nargs="+", default=[], metavar="NAME", help="npm package names to check.")
    p.add_argument("--skip", action="append", default=[], metavar="GLOB",
                   help="Name glob never sent to a registry, e.g. 'acme-*' or '@acme/*' (repeatable).")
    p.add_argument("--min-age-days", type=int, default=90,
                   help="Younger than this is suspicious (default 90).")
    p.add_argument("--min-weekly-downloads", type=int, default=1000, dest="min_downloads",
                   help="npm: fewer weekly downloads than this is suspicious (default 1000).")
    p.add_argument("--pypi-url", default="https://pypi.org",
                   help="Index base URL; must serve the /pypi/<name>/json API.")
    p.add_argument("--npm-url", default="https://registry.npmjs.org", help="npm registry base URL.")
    p.add_argument("--npm-downloads-url", default="https://api.npmjs.org",
                   help="npm downloads API base URL.")
    p.add_argument("--ca-file", metavar="FILE",
                   help="CA bundle to verify TLS against (default: Python's, else the system bundle).")
    p.add_argument("--timeout", type=float, default=15.0, help="Per-request timeout (default 15s).")
    p.add_argument("--workers", type=int, default=8, help="Concurrent lookups (default 8).")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 for suspicious packages too, not only not-found.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not (args.files or args.pypi or args.npm):
        p.error("give at least one manifest file, --pypi NAME or --npm NAME")
    if args.workers < 1 or args.timeout <= 0:
        p.error("--workers must be >= 1 and --timeout > 0")
    args.pypi_url, args.npm_url = args.pypi_url.rstrip("/"), args.npm_url.rstrip("/")
    args.npm_downloads_url = args.npm_downloads_url.rstrip("/")
    try:
        context, tls_note = tls_context(args.ca_file)
    except (ssl.SSLError, OSError) as exc:
        p.error(f"--ca-file: {exc}")
    args.fetch = make_fetcher(context)
    args.now = dt.datetime.now(dt.timezone.utc)

    packages, notes = collect(args.files, args.pypi, args.npm)
    if not packages:
        print("No registry packages found in the given input.", file=sys.stderr)
        for n in notes:
            print(f"note: {n}", file=sys.stderr)
        return 2

    def work(pkg: dict) -> dict:
        if any(fnmatch.fnmatch(pkg["name"], pat) for pat in args.skip):
            return {**pkg, "status": "skipped", "flags": [],
                    "detail": "matched --skip; not sent to any registry"}
        return check_pypi(pkg, args) if pkg["ecosystem"] == "pypi" else check_npm(pkg, args)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(work, packages))

    if tls_note:
        notes.append(tls_note)
    if any("TLS certificate verification failed" in r.get("detail", "") for r in results):
        notes.append("TLS verification failed - set SSL_CERT_FILE or pass --ca-file with your CA "
                     "bundle (a corporate proxy?). Verification is never disabled.")

    if args.json:
        print(json.dumps({"packages": results, "notes": notes}, indent=2))
    else:
        print(render(results, notes))

    statuses = {r["status"] for r in results}
    if "not-found" in statuses or (args.strict and "suspicious" in statuses):
        return 1
    return 3 if "unknown" in statuses else 0     # could not verify is not a pass


if __name__ == "__main__":
    raise SystemExit(main())
