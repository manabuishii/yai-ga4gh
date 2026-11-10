# yai-drs の設計(DRS 1.5.0)

- 作成: 2026-10-15。前提: `system.md`・`aai.md`。義務の ID は `docs/spec-obligations.md` 1 節
- DRS は入力だけ(D-003)。読み取り専用(DRS-RO-01)。登録は yai 独自のコマンド

## 1. 範囲

| | 最初の「動く最小版」 | あとで深める |
| --- | --- | --- |
| 操作 | service-info、GetObject・PostObject、OptionsObject、GetAccessURL・PostAccessURL、一括 3 つ(GetBulkObjects・GetBulkAccessURL・OptionsBulkObject) | 202 と `Retry-After`(使わない)、Basic(使わない) |
| 中身 | 手元のファイル置き場(内容アドレス)。`access_methods` は `https` 1 種 | `file` の方式、複数の方式、`cloud`・`region` |
| 方針 | オブジェクトごとに 3 種:`public`・`bearer`・`dataset`(Passport) | メタデータは公開・`/access` は要認証の組み合わせを全種類 |
| URI | ホスト名型を出す。compact 型は出さない(G-DRS-02) | — |
| バンドル | 作らない(DRS-BUN-01) | Compound Objects の付録の「目録ファイル」方式を試す |
| 登録 | `yai-drs register <file> --policy … --dataset …`(コマンド) | 一括登録、別名(`aliases`) |

## 2. 操作と義務

| 操作 | 道 | 最小版の中身 | 義務(ID) |
| --- | --- | --- | --- |
| GetServiceInfo | `GET /service-info` | `type={group: org.ga4gh, artifact: drs, version: 1.5.0}`。`maxBulkRequestLength` を根と `drs` の下に(既定 100)、`drs.objectCount`・`totalObjectSize` | DRS-SI-01〜03、G-DRS-06・14 |
| OptionsObject | `OPTIONS /objects/{id}` | `Authorizations`(**`metadata_policy` から作る**。`/access` の方式は `AccessMethod.authorizations` で、`access_policy` から作る)。`public` は `supported_types=[None]`、`bearer` は `[BearerAuth]`+`bearer_auth_issuers`、`dataset` は `[PassportAuth]`+`passport_auth_issuers`(Visa Issuer の `iss`)+**`yai_required_visas`**(要る Visa の type と value=dataset。yai の拡張、設定 `authorizations.expose_dataset` で切れる、D-072)。無い ID は 404 | DRS-AUTH-02・04・05、G-DRS-08・13・17 |
| GetObject | `GET /objects/{id}` | メタデータの方針で判定。`dataset` の方針で Passport が無ければ 401 | DRS-OBJ-01〜06、DRS-ID-01・02 |
| PostObject | `POST /objects/{id}` | 本文 `{passports: [...], expand?}`。`tokens` が来たら 400 | DRS-AUTH-03、G-DRS-03・04・11 |
| GetAccessURL | `GET /objects/{id}/access/{access_id}` | `public`・`bearer` の方針だけ。`dataset` は 401(Passport は POST で、と `msg` に) | DRS-ACC-01・02 |
| PostAccessURL | `POST /objects/{id}/access/{access_id}` | 本文の `passports` で判定 → 署名付き URL | DRS-ACC-01・02、AAI-CLI-01 |
| 一括 3 つ | `POST /objects`・`OPTIONS /objects`・`POST /objects/access` | 1 件ずつ判定。上限を超えたら 413。だめなものは `unresolved_drs_objects`(`error_code` は HTTP の状態コード)。**1 つも解決できなくても 200**(全部を `unresolved` に。D-021 をレビューで直した)。**段階 1 の中で、E2E-01 が通ったあとに作る**(最初の流れには要らない) | DRS-BULK-01・02、G-DRS-05・09 |
| バイト列の配信口(yai 独自) | `GET /yai/blobs/{object_id}?exp=…&aid=…&sig=…` | 署名と期限を確かめて中身を流す。`Range` に対応 | DRS-SEC-01、G-X-02 |

`access_methods` の作り方(DRS-AM-01〜03、C-03)。**表の「方針」は `access_policy`**(メタデータの方針 `metadata_policy` は使わない。レビューで直した)

| 方針 | `access_url` | `access_id` | `authorizations`(`/access` 用) |
| --- | --- | --- | --- |
| `public` | 署名なしの配信口の URL(ヘッダーなし) | なし | なし |
| `bearer` | なし | `a1` | `[BearerAuth]` |
| `dataset` | なし | `a1` | `[PassportAuth]`+`passport_auth_issuers`+`yai_required_visas`(D-072) |

- **メタデータの方針と `/access` の方針を別に持つ**(既定は同じ)。「メタデータは公開・`/access` は dataset」を試験に入れる(計画 第 2 版の段階 2)
- 署名付き URL: `HMAC-SHA256(秘密, object_id | access_id | exp)`、`exp` は既定で発行から 3600 秒。**ヘッダーは付けない**(TES に渡せるように、C-03)。期限切れ・別のオブジェクトの署名・改ざんは 403
- **`access_policy` が** `public` のオブジェクトの配信口は署名なしで通す(`metadata_policy` が `public` でも、`access_policy` が `dataset` なら署名が要る。NEG-DRS-MIXED-POLICY)。それ以外の方針のオブジェクトを署名なしで要求したら 403(裏の経路を作らない)

`self_uri` の形: 常に設定のホスト名(`drs.yai.test`)を使う。単体の試験(http・ポート付き)でも `drs://127.0.0.1:8402/…` のようなポート付きの URI は出さない(DRS-URI-02 で不正)。解決役は上書き表で `drs.yai.test` → `http://127.0.0.1:8402` に読み替える(自己レビュー R-03)

## 3. 認可の点(4 つの役との対応)

| 点 | R1 確かめる | R2 利用者 | R3 許すか | R4 下流 |
| --- | --- | --- | --- | --- |
| メタデータ(Get・Post・一括) | AT(`aud=yai-drs`)か Passport | AT の `sub` か Passport の `sub` | `permissions.yaml` の `metadata_policy` | — |
| `/access` | 同上 | 同上 | `access_policy`。dataset は `aai.md` 4 節の判定 | 署名付き URL を作る |
| 配信口 | 署名 | — | 署名と期限だけ | — |
| OPTIONS・service-info | なし | — | 常に許す | — |

## 4. 状態

DRS のオブジェクトは状態を持たない(登録したら変わらない、DRS-ID-02)。消すときは「消した ID は 404」(同じ ID を別の中身で使い直さない)。

## 5. データの持ち方

| 物 | 場所 | 中身 |
| --- | --- | --- |
| 中身 | `var/drs/blobs/sha256/<先頭 2 文字>/<sha256>` | 登録のとき**写す**(元のファイルを書き換えても変わらない)。同じ中身は 1 つだけ置き、ID は別でもよい(DRS-ID-02 と「1 つの中身に複数の ID」は許される) |
| メタデータ | SQLite `var/drs/drs.sqlite` | `objects(id, sha256, size, name, mime_type, created_time, created_time_source, registered_at, description, metadata_policy, access_policy, dataset, deleted)`、`checksums(object_id, type, checksum)`(`sha-256` と `md5`) |
| 表の定義 | `schema/drs.sql` | 言語に依らない形で持つ(`language.md`) |

- `created_time` は**中身の作成時刻**(DRS-OBJ-04)。登録のコマンドの `--created-time` で指定する。指定が無ければ元のファイルの更新時刻を使い、`created_time_source=file_mtime` と記録する(仕様の「中身の作成時刻」と同じとは限らないので、DRS-OBJ-04 は △)。登録の時刻は別の列 `registered_at` に持つ(D-022 をレビューで直した)

## 6. 誤りの返し方

- `Error {msg, status_code}`。状態コードは各操作の OpenAPI の一覧にあるものだけ
- 401: 資格情報が無い・壊れている・期限切れ。403: 確かめられたが権限が無い。404: オブジェクトが無い(方針に関係なく、無いものは 404)
- **確かめる順**(D-025、ユーザー 2026-10-16 01:00): **(1) 付いてきた資格情報の検査(壊れていれば 401)→ (2) オブジェクトが有るか(無ければ 404)→ (3) 方針(資格情報が要るのに無ければ 401、足りなければ 403)**。壊れた資格情報と無い ID が重なったときは 401 が先。DRS の仕様は順を決めていない(G-DRS-15)
- `/access` 系の応答には `Cache-Control: no-cache, no-store`・`Pragma: no-cache`(G-AAI-04)

## 7. 仮・未決

- 仮: 一括の全件失敗も 200(D-021)、`created_time` は指定か元のファイルの更新時刻(D-022)、署名付き URL 3600 秒(D-019)
- 試験で確かめる: 公式の試験道具(DRS 1.2.0 用)が 1.5.0 のサーバーのどこで落ちるか(`test-plan.md` CONF-DRS)
