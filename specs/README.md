# specs/

This repository does not contain copies of the specifications it implements. `specs/SOURCES.yaml`
pins each file to a commit of its GitHub repository (or to a fixed URL), and
`specs/EXTERNAL.SHA256SUMS` holds its sha256.

`python tools/ext_specs.py` (the first step of `make specs`) downloads the files into
`build/specs-ext/`, with the same layout as the paths below, and fails if a sha256 differs.
After that, no network is needed. References such as `aai/AAIConnectProfile.md:672` in the
documents are lines of these pinned originals.

| Name | Repository | Tag | Commit | Files |
| --- | --- | --- | --- | --- |
| drs | [ga4gh/data-repository-service-schemas](https://github.com/ga4gh/data-repository-service-schemas/tree/fe25c3953ae3398a31054d3f9f040d5e27aad517) | `drs-1.5.0` | `fe25c3953ae3398a31054d3f9f040d5e27aad517` | 52 |
| wes | [ga4gh/workflow-execution-service-schemas](https://github.com/ga4gh/workflow-execution-service-schemas/tree/226f43b1fec97ad3778897a27a9651ccfa631d77) | `1.1.0` | `226f43b1fec97ad3778897a27a9651ccfa631d77` | 2 |
| tes | [ga4gh/task-execution-schemas](https://github.com/ga4gh/task-execution-schemas/tree/117cd927aa119469ee80cedbd8940c968b81c5d6) | `v1.1` | `117cd927aa119469ee80cedbd8940c968b81c5d6` | 2 |
| aai | [ga4gh/data-security](https://github.com/ga4gh/data-security/tree/9d05792a87ab96bf46566369053c28deb63afb78) | `AAIv1.2` | `9d05792a87ab96bf46566369053c28deb63afb78` | 2 |
| passport | [ga4gh-duri/ga4gh-duri.github.io](https://github.com/ga4gh-duri/ga4gh-duri.github.io/tree/82546d502c78ca341035a75c69f36edc8c50927a) | — (no tag) | `82546d502c78ca341035a75c69f36edc8c50927a` | 1 |
| service-info | [ga4gh-discovery/ga4gh-service-info](https://github.com/ga4gh-discovery/ga4gh-service-info/tree/daa4014a27545376415882a1ef17cf9fc4ae6a05) | `v1.0.0` | `daa4014a27545376415882a1ef17cf9fc4ae6a05` | 2 |
| base | RFC Editor and OpenID Foundation (fixed URLs in `SOURCES.yaml`) | — | — | 18 |
| interop-sapporo | [sapporo-wes/sapporo-service](https://github.com/sapporo-wes/sapporo-service/tree/14e6b5c24dbe9fd1ed8165b7924808498eb4e7ea) | `2.4.0` | `14e6b5c24dbe9fd1ed8165b7924808498eb4e7ea` | 1 |

`interop-sapporo` is not a specification: it is `sapporo/run.sh` of the interoperability partner
Sapporo (Apache-2.0), which `deploy/interop/setup.sh` adapts. `make specs` downloads it too.
