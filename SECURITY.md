# Security policy

## Supported versions

pyxaf is in its `0.x` series. Security fixes are made for the **latest released version** only;
please upgrade before reporting.

| Version          | Supported |
| ---------------- | --------- |
| latest `0.x`     | yes       |
| older releases   | no        |

## Reporting a vulnerability

**Please do not report security problems in public issues, discussions or pull requests.**

Report privately through either channel:

1. **GitHub private vulnerability reporting** (preferred):
   [Report a vulnerability](https://github.com/SpireflyHQ/pyxaf/security/advisories/new)
   (repository → *Security* → *Advisories* → *Report a vulnerability*).
2. **E-mail** to [ben@spirefly.com](mailto:ben@spirefly.com) with the subject
   `pyxaf security`.

Include the pyxaf version, Python version and operating system, a description of the impact, and
a minimal reproducer. A reproducer must be a **synthetic** file or snippet. Never send a real
client or company auditfile, not even privately.

pyxaf is maintained by one person in their spare time, so these are targets, not guarantees:

- acknowledgement within **5 working days**;
- an assessment and a plan within **14 days**;
- a fix released and a GitHub Security Advisory (with CVE where appropriate) published in
  coordination with you. You will be credited unless you prefer otherwise.

## Threat model and hardening

Auditfiles are untrusted input: they arrive from clients, third-party accounting software and
e-mail. pyxaf is designed to read hostile files safely:

- **No DTDs or entities.** The core parser (the standard library's `pyexpat`) refuses any
  `DOCTYPE` or `ENTITY` declaration, so billion-laughs, quadratic-blowup and external-entity (XXE)
  attacks are rejected (`ForbiddenConstructError`, finding `XAF2002`). Parameter-entity parsing is
  disabled and external entity references are never resolved.
- **No network and no file access** beyond the paths and streams you pass in.
- **Resource limits.** Configurable caps on element nesting depth and text-node size, and on the
  decompressed size of gzip/zip input (a 100× ratio guard by default, replaced by an explicit
  `max_decompressed_size`) (decompression-bomb guard). A limit
  that is hit raises `LimitExceededError` (finding `XAF2003`) instead of exhausting memory.
- **Streaming.** Transactions and lines are streamed, so memory does not grow with file size.
- **No "recover" modes.** Malformed XML is reported (`XAF2001`), never silently truncated or
  repaired. The opt-in repairs (`repair=`) are narrow, documented and each reported as a finding.
- **Optional XSD validation** (`pyxaf[xsd]`) uses lxml **>= 6.1.3** and always creates its parser
  with `resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False`
  (lxml before 6.1 resolved external entities in `iterparse` by default, CVE-2026-41066).
- Python can be linked against a system Expat. Expat versions before 2.7.2 have known
  denial-of-service weaknesses (see the
  [Python XML security notes](https://docs.python.org/3/library/xml.html#xml-security)); keep your
  Python and Expat up to date. pyxaf's refusal of DTDs does not depend on the Expat version.

Findings about the contents of a file (unbalanced transactions, unknown accounts, …) are data
quality problems, not security issues. Report them as normal issues, without real data.

## Supply chain

Releases are built in GitHub Actions and published to PyPI with Trusted Publishing; each
distribution carries a PEP 740 attestation linking it to the workflow run that built it. All
workflow actions are pinned to full commit SHAs and checked with zizmor.
