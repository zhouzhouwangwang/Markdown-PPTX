# Vendored converter provenance

These files are copied verbatim from the PPTAgent project and are the only
upstream component this service depends on:

- Source: https://github.com/icip-cas/PPTAgent
- Path: `deeppresenter/html2pptx/`
- Tag: `v1.1.38` (byte-identical to `v1.1.37`; also shipped by the PyPI
  `pptagent==1.1.37` package that the upstream Skill uses)
- License: MIT (see the PPTAgent repository `LICENSE`)

Files:

| File | Role |
| --- | --- |
| `html2pptx.js` | HTML slide to pptxgenjs elements (Playwright measurement + validation) |
| `html2pptx_cli.js` | CLI wrapper: `--html_dir --layout --output --validate --soft` |
| `package.json` | Runtime dependencies (playwright, pptxgenjs, sharp, fast-glob, minimist) |
| `package-lock.json` | Pinned dependency graph used by `npm ci` |

Everything else in this service (`app/`) is original code and can be modified
freely. Do not edit the vendored files: re-vendor them from a pinned tag instead
so upgrades stay reviewable.
