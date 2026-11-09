# 仕様の版の固定(spec-pins)

- 作成: 2026-10-15(JST)。取得は `git clone --depth 1 --branch <tag>`、ID は `git rev-parse HEAD` で確認
- 機械向けの同じ内容: `specs/SOURCES.yaml`。**このリポジトリには仕様の写しを置かない**(2026-11-06、D-065): `python tools/ext_specs.py`(`make specs` の最初)が固定したコミット・URL から `build/specs-ext/` に取り、`specs/EXTERNAL.SHA256SUMS` と合わなければ失敗する
- **契約の正は下の表の固定したコミットの原文**。本文中の `file:line` はすべてその原文(`build/specs-ext/` に同じ並びで取ったもの)の行番号を指す

## 1. 一覧

| 仕様 | リポジトリ | タグ | コミット(タグが指すもの) | 取り込んだファイル | 置き場所 |
| --- | --- | --- | --- | --- | --- |
| DRS 1.5.0 | ga4gh/data-repository-service-schemas | `drs-1.5.0`(注釈付き、タグ物 `a0e65629…`) | `fe25c3953ae3398a31054d3f9f040d5e27aad517`(2025-03-24) | `openapi/` 一式(分割された YAML 43 ファイルと本文の Markdown 9 ファイル)、LICENSE | 写しを置かない(`build/specs-ext/drs/` に取る) |
| WES 1.1.0 | ga4gh/workflow-execution-service-schemas | `1.1.0` | `226f43b1fec97ad3778897a27a9651ccfa631d77`(2023-09-14) | `openapi/workflow_execution_service.openapi.yaml`、LICENSE | 写しを置かない(`build/specs-ext/wes/` に取る) |
| TES 1.1 | ga4gh/task-execution-schemas | `v1.1` | `117cd927aa119469ee80cedbd8940c968b81c5d6`(2022-11-14) | `openapi/task_execution_service.openapi.yaml`、LICENSE | 写しを置かない(`build/specs-ext/tes/` に取る) |
| AAI 1.2 | ga4gh/data-security | `AAIv1.2` | `9d05792a87ab96bf46566369053c28deb63afb78`(2023-01-17) | `AAI/AAIConnectProfile.md`、`AAI/CHANGES_1.2.md` | 写しを置かない(`build/specs-ext/aai/` に取る) |
| Passport 1.2 | ga4gh-duri/ga4gh-duri.github.io | **タグなし** | 既定ブランチの先頭 `82546d502c78ca341035a75c69f36edc8c50927a`(2023-03-30)。ファイルを最後に変えたのは `a4ee601`(2023-03-30「Visa の typ を任意に」) | `researcher_ids/ga4gh_passport_v1.md`(本文に「Version: 1.2」) | 写しを置かない(`build/specs-ext/passport/` に取る) |
| service-info 1.0.0 | ga4gh-discovery/ga4gh-service-info | `v1.0.0` | `daa4014a27545376415882a1ef17cf9fc4ae6a05` | `service-info.yaml`、LICENSE | 写しを置かない(`build/specs-ext/service-info/` に取る) |

## 2. 外部 `$ref` の先

DRS・WES・TES の OpenAPI が外へ向ける `$ref` は、**すべて service-info v1.0.0 の 1 ファイル**(raw.githubusercontent.com のタグ固定の URL)。

| 参照元 | 参照先 |
| --- | --- |
| `drs/openapi/components/responses/200ServiceInfo.yaml:6` | `service-info.yaml#/components/schemas/Service` |
| `wes/workflow_execution_service.openapi.yaml:604` | 同上 `Service` |
| `tes/task_execution_service.openapi.yaml:651` | 同上 `ServiceType` |
| `tes/task_execution_service.openapi.yaml:662` | 同上 `Service` |

- 形の検査では、この URL を取ってきた `service-info/service-info.yaml` へ置き換えて読む(固定した原文を使う)。置き換えの表は検査の道具の設定に 1 か所だけ持つ
- DRS の内部 `$ref` は相対パス(`./paths/…`、`../components/…`)。分割のまま読める道具を使うか、取り込み時に 1 ファイルへ束ねる(束ねたものは生成物として `build/` に置く)
- 本文から張られているが `$ref` ではない先(identifiers.org・n2t.net の API、Passport 本文からの AAI の節へのリンクなど)は取り込まない。食い違いは `docs/spec-gaps.md`

## 3. 版について分かっていること

- **Passport はタグが無い**ので、コミットで固定する。本文の「Version: 1.2」は、AAIv1.2 タグ(2023-01-17)より後の `a4ee601`(2023-03-30、Visa の `typ` を任意に)を含む。つまり「AAI 1.2 と同時に出た Passport 1.2」と「いまの Passport 1.2」は 1 か所違う。yai は**いまの方(`typ` は任意)**を使う(`docs/spec-gaps.md` G-AAI-05)
- Passport・AAI 1.2.1 は ga4gh/aai で公開(`AAI/VERSIONS.md` で latest、`AAI/ga4gh_passport_v1.md` は「Version: 1.2.1」)。1.2 との差は `AAI/CHANGES_1.2.1.md` の編集上の 2 点(DURI の Passport の文書を AAI のリポジトリに移した・版の表を足した)だけで、義務の表はそのまま(2026-10-31 確認、出典は <https://github.com/ga4gh/aai>。ga4gh/aai に 1.2.1 のタグは無く、見たのは既定ブランチの先頭 `194bcfa`)。固定する版は 1.2 のまま(上げるかは未決定)
- 旧 data-security の公開ページ(ga4gh.github.io/data-security/…)は 404 で、AAI の文書は ga4gh/aai に移った。1.2 の写しはタグ `AAIv1.2`(ga4gh/aai にも同じ `9d05792` のタグがある)で読む
- Passport のリポジトリ(ga4gh-duri/ga4gh-duri.github.io)にタグ `1.2` ができていて、固定のコミット `82546d5` と同じものを指す(2026-10-31 に ls-remote で確認。2 節の表の「タグなし」は 10-15 時点)
- AAI と Passport のリポジトリには LICENSE ファイルが無い(このコミットで確認)。10-15 には学習用で外に公開しないので写しを置いたが(`docs/decisions.md` D-012)、公開の準備で 2026-11-05 に写しを外し、固定したコミットから取る形にした(`tools/ext_specs.py`)。2026-11-06 には GA4GH の他の仕様・RFC・OpenID Connect も同じ形にした(D-065)。原文が無く取れないときは失敗する

## 4. 更新のしかた

1. `specs/SOURCES.yaml` のコミット(または URL)を新しい版に替え、`specs/EXTERNAL.SHA256SUMS` を作り直す
2. `python tools/ext_specs.py` で取り直す
3. 元のリポジトリで差分を読んで、`docs/spec-obligations.md`・`docs/spec-gaps.md` の `file:line` を直す(行番号がずれる)
4. 決定記録に版を上げた理由を書く
