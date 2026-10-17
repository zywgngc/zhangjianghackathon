# Material Portal 0.2: real reference catalog

`materials.json` contains **13 real, source-inspected discovery candidates** for the
`MaterialPortalPort` contract. It is separate from the image/icon catalog under
`catalogs/demo`. `isMock: false` means the referenced upstream sources exist and
were inspected; it does **not** mean the games or mechanics were run, verified,
approved, or found compatible with the target project.

The bootstrap script fetches only 24 small public text files from immutable
`raw.githubusercontent.com` URLs. It does not clone repositories, install
dependencies, run upstream code, download example art/media, read credentials,
or use synthetic contract fixtures as production evidence.

| Repository | Pinned revision | Candidates | Source-code license evidence |
| --- | --- | --- | --- |
| [nusr/wukong-survivors](https://github.com/nusr/wukong-survivors/tree/bb28e7091ca6072731d74a71a36c5d38877c761c) | `bb28e7091ca6072731d74a71a36c5d38877c761c` | Complete survivor archetype; cooldown combat/targeting; waves; XP and coin pickups; in-run upgrades; permanent gold economy; unlock progression | MIT in pinned `LICENSE` |
| [phaserjs/examples](https://github.com/phaserjs/examples/tree/68140eca72525d5034b783a262270715ddc17b78) | `68140eca72525d5034b783a262270715ddc17b78` | Spline route follower; closest-body targeting; top-down combat; projectile pool | Pinned README explicitly says source code is MIT and assets are not; `package.json` says ISC, so the discrepancy is recorded in every Phaser candidate |
| [BabylonJS/Babylon.js](https://github.com/BabylonJS/Babylon.js/tree/1431d93dbd9dd6051575fafcc43db9806d0483c4) | `1431d93dbd9dd6051575fafcc43db9806d0483c4` | Recast navmesh path queries and crowd destinations | Apache-2.0 in pinned `license.md`; separately supplied Recast runtime is outside this evidence |
| [qiao/PathFinding.js](https://github.com/qiao/PathFinding.js/tree/2904a9afba483c02b5a61eb60fefd63e9b2db78c) | `2904a9afba483c02b5a61eb60fefd63e9b2db78c` | Grid A* documentation/source, path smoothing and compression | Full MIT license text in pinned README |

The four cached Phaser JavaScript files are accompanied by a [source notice and standard MIT terms](PHASER-EXAMPLES-LICENSE.md), preserving the upstream metadata discrepancy without inventing a copyright holder or year.

There are three distinct navigation references: predefined Phaser spline
following, Babylon navmesh/crowd navigation, and engine-independent grid A*.
They expose different building blocks and are not interchangeable implementations
of one verified mechanic contract.

Wukong is actually implemented in TypeScript with Phaser and React. Its pinned
`package.json` declares Phaser `^4.2.1`; this is not a claim of Phaser 3
compatibility. The pinned Phaser example README describes Phaser 4 development
builds, so exact target release compatibility also needs review.

## Reproduce and inspect

From the repository root, fetch the immutable small files and rebuild metadata:

```text
python tools/bootstrap_reference_catalog.py
```

Verify the cached SHA-256 values and rebuild without network access:

```text
python tools/bootstrap_reference_catalog.py --offline
```

Every cached file has its original bytes and path under
`sources/<owner>/<repository>/<commit>/<upstream-path>`.
`sources.json` records upstream URI, fixed revision, local relative cache path,
byte length and SHA-256. Hashes are pinned in the script, rather than trusted only
from the adjacent manifest. A missing/modified file or stale source marker fails
the rebuild; it does not silently switch to a mock candidate.

The script checks source paths, symbol identifiers and individual evidence
markers. Candidate evidence links point to the real upstream file at the fixed
commit and, where applicable, a relevant line. These checks are static source
evidence, not an AST-based program verifier or a gameplay test.

`materials.json` records only the agreed candidate and metadata fields:
`candidate`, `searchText`, `tags`, `engines`, `languages`, `inputModes`, and
`purposes`. Empty engine/input metadata for an algorithm does not establish any
particular runtime/input compatibility. Search providers can read this metadata
without reading cached upstream code. Reviewers may inspect cached code or the
immutable evidence URLs separately.

## Reuse boundary

All candidate license scopes are `source_code`. No record establishes an asset
license, authorizes copying game artwork, or claims that media and trademarks
share a repository's software license. The referenced Phaser examples load
images at runtime, but those images were intentionally not fetched. Upstream
code is untrusted reference material and is never executed by this bootstrap.

Static inspection identifies useful candidate mechanisms and their limits. The
next reviewer must inspect integration context, dependency versions, licensing
and required behavior before treating a candidate as an implementation choice.
