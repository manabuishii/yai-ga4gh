# 仕様の考慮漏れ・食い違い(spec-gaps)

<!-- 生成物: docs/data/spec-gaps.yaml から tools/gen_diagram.py で作る。手で直さない -->

- 作成: 2026-10-15。出典の `file:line` は `specs/` の下(`docs/spec-pins.md` の版。`aai/`・`passport/` は写しを置かず、`specs/SOURCES.yaml` の固定した commit の原文)
- 列: 種類=**食い違い**(仕様の中で矛盾)/**漏れ**(決まっていない)/**古い**(参照先が無い・名前が古い)/**組み合わせ**(1 つずつは正しいが、4 つ組み合わせると合わない)
- 確かめ: **原文**=原文を読んで確かめた/**作る**=作らないと分からない(確かめる試験を `docs/test-plan.md` に入れた)
- yai の解釈で「仮」のものは `docs/decisions.md` に同じ D 番号
- 図の場所: 関係図(`docs/diagram.html`、原本 `docs/data/diagram.yaml`)のどの箱・矢印の問題か。矢印の ①〜⑯ は `docs/design/system.md` 4 節の番号


## 1. DRS 1.5.0

| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |
| --- | --- | --- | --- | --- | --- | --- |
| G-DRS-01 | 漏れ | ホスト名型 URI は https・443 固定で、ポートを含むのは不正。**手元の試験(http・別ポート)では仕様どおりに解決できない**。開発・試験の環境の扱いが無い | `drs/openapi/tags/DrsApiPrinciples.md:42` | 4 つまとめた試験は TLS の 443(手元の CA)で仕様どおりに。単体の試験だけ「ホスト名 → URL」の上書き表を解決役に渡す(D-017)。**試験の環境でも手元の CA で仕様どおりにできる。仕様の文に開発・試験の環境の扱いが書かれていないだけ** | 原文 | a56 |
| G-DRS-02 | 漏れ | compact identifier 型は identifiers.org・n2t.net への登録が前提(サーバーは MUST)。登録しない私的な運用での解決のしかたが無い(付録に「外のサイトに頼らない運用もありうる」とだけ) | `DrsApiPrinciples.md:66`、`CompactIdentifierBasedURIs.md:65` | yai は compact 型を**出さない**。解決役は手元の解決表(`prefix → URL パターン`)で解決する。試験用 prefix `yaitest.drs` は実在の prefix と重ならないことを identifiers.org で確かめてから使う(仮) | 原文 | a56 |
| G-DRS-03 | 食い違い | Auth 節と `PassportAuth` の説明は「`tokens[]` 配列」、操作の本文の定義は `passports` | `drs/openapi/data_repository_service.openapi.yaml:144`、`drs/openapi/tags/Auth.md:60`、`components/requestBodies/Passports.yaml:7` | 操作の定義(`passports`)を正とする(計画 第 2 版から)。`tokens` で来たら 400(黙って読まない) | 原文 | a56、a16 |
| G-DRS-04 | 食い違い | PostObject の 404 が `404NotFoundAccess`(「アクセスが無い」)を指す。GetObject は `404NotFoundDrsObject` | `paths/objects@{object_id}.yaml` の PostObject | 中身はどちらも `Error`。yai は「オブジェクトが無い」の文で返す | 原文 | drs |
| G-DRS-05 | 食い違い | 一括の操作の説明は「Passport か 1 つの Bearer」と言うが、`security` には `PassportAuth` だけ。`PassportAuth` は `x-in: body` という独自の拡張で、OpenAPI の道具は理解しない | `paths/bulkobjects@{object_id}.yaml`、`drs/openapi/data_repository_service.openapi.yaml:137-144` | 一括でも Bearer を受ける(説明文を正とする)。形の検査の道具には `PassportAuth` を「本文の `passports`」と読み替える設定を置く | 原文 | drs、a16 |
| G-DRS-06 | 食い違い | `maxBulkRequestLength` が根(非推奨と書きつつ `required`)と `drs` の下(これも `required`)の両方で必須 | `components/schemas/DrsService.yaml:2-4,22-24` | 両方に同じ値を出す | 原文 | drs |
| G-DRS-07 | 漏れ | 「blob では `access_methods` が要る」「`access_url` と `access_id` の少なくとも一方」は説明文だけで、schema では強制されない | `DrsObject.yaml`、`AccessMethod.yaml:20,26` | yai の試験(b 層)で確かめる。形の検査では見えないので義務の表に入れた(DRS-OBJ-02・DRS-AM-01) | 原文 | drs |
| G-DRS-08 | 漏れ | `PassportAuth` を含むときの `passport_auth_issuers` は「必須」と説明文だけ | `drs/openapi/components/schemas/Authorizations.yaml:22` | 必ず出す。値は「Visa の `iss`」で、Passport の Broker ではない点に注意(Visa Issuer の一覧を出す) | 原文 | drs、ch |
| G-DRS-09 | 漏れ | 一括で一部だけ解決できないときの `unresolved_drs_objects[].error_code` の意味(HTTP の状態コードか)が書かれていない。全部が無いときに 200 か 404 かも書かれていない | `components/schemas/unresolved.yaml`、`paths/bulkobjects@{object_id}.yaml` | `error_code` は HTTP の状態コード(404・403)。1 つも解決できなくても 200 で、全部を `unresolved_drs_objects` に入れる(仮 D-021。初めは「全件失敗は 404」としたが、失敗の理由が消えるのでレビューで直した)。**他の実装がどうしているかは作って比べる** | 作る | drs |
| G-DRS-10 | 漏れ | `AccessURL` に**期限の欄が無い**。クライアントは署名付き URL がいつまで使えるか分からない | `components/schemas/AccessURL.yaml` | 期限は yai の設定(既定 60 分、D-019)。URL ごとの期限は問い合わせ部分の `exp`(エポック秒、yai の URL の形で仕様の契約ではない)。service-info には出さない(D-063、D-040 と同じく相互運用を優先) | 原文 | a08、a11 |
| G-DRS-11 | 古い | PostObject・PostAccessURL の説明が「formData で Passport を送る」と言うが、本文は `application/json` | `paths/objects@{object_id}.yaml`、`paths/objects@{object_id}@access@{access_id}.yaml` | JSON だけを受ける | 原文 | drs |
| G-DRS-12 | 古い | `BulkObjectSingleAccessId.yaml` はどこからも参照されていない | `components/parameters/BulkObjectSingleAccessId.yaml` | 使わない | 原文 | drs |
| G-DRS-13 | 漏れ | OPTIONS は認証なし(`security: [{}]`)で、オブジェクトの有無が誰にでも分かる。「見せない」方針と両立しない | `paths/objects@{object_id}.yaml` OptionsObject | 受け入れる(学習用で隠すデータが無い)。他人の資源を 404 にする方針(D-013)は TES・WES だけ | 原文 | drs、a16 |
| G-DRS-14 | 漏れ | service-info の例は `"version": "1.5"`。`type.version` に何を書くか(`1.5`・`1.5.0`)が決まっていない | `drs/openapi/paths/service-info.yaml:26` の例 | `1.5.0`(タグの版)。相互運用の相手が `1.5` を期待するかは作って確かめる | 作る | drs |
| G-DRS-15 | 漏れ | **壊れた資格情報と無い ID が重なったとき、401 と 404 のどちらを返すか**(確かめる順)が決まっていない。GetObject・PostObject・GetAccessURL・PostAccessURL の応答の一覧には 401・403・404 が並ぶだけで、説明は「The request is unauthorized.」「The requested `DrsObject` wasn't found.」だけ。認可の方針は実装が決めて執行する、とあるだけ(コードのレビューで見つけた、2026-10-16) | `drs/openapi/paths/objects@{object_id}.yaml:42・46`(GetObject の 401・404)、`:70・74`(PostObject)、`drs/openapi/paths/objects@{object_id}@access@{access_id}.yaml:16・20・49・53`、`drs/openapi/components/responses/401Unauthorized.yaml:1`、`404NotFoundDrsObject.yaml:1`、`drs/openapi/tags/Auth.md:3` | **資格情報の検査(壊れていれば 401)→ 有るか(404)→ 方針(401・403)**(案 A、D-025)。壊れた資格情報を黙って無視しない(`permissions.yaml` の public の note)と合う。どちらの順でも、資格情報なしで ID の有無が分かるのは同じ(資格情報なしの無い ID は 404)。GA4GH への質問の下書き: `docs/questions/2026-10-16_drs-401-vs-404.md`(投稿していない) | 原文 | drs、ch |
| G-DRS-16 | 漏れ | `AccessMethod.type`(https など)と `AccessURL.url` のスキームが一致することを求める文が無い。ga4gh/passport-playground-drs(5eb7745)は `type: https` で `http://…` の URL を返す(INTEROP-DRS-1、2026-10-17) | `drs/openapi/components/schemas/AccessMethod.yaml`・`AccessURL.yaml` | yai-drs は type と URL のスキームを揃える(https)。yai-tes は取る URL のスキームを自分で見て https 以外は断る(試験の設定 `insecure_http_hosts` だけ http)。`type` は信用しない | 原文 | a56 |

## 2. TES 1.1

| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |
| --- | --- | --- | --- | --- | --- | --- |
| G-TES-01 | 漏れ | **誤りの応答が 1 つも定義されていない**(全操作が 200 だけ)。認証の失敗・無いタスク・不正な要求の返し方が無い | `tes/task_execution_service.openapi.yaml` の paths | DRS・WES と同じ `{msg, status_code}` で 400・401・403・404・500 を返す(D-011)。公式の試験道具(openapi-test-runner)が 4xx をどう扱うかは作って確かめる | 作る | tes、a10、a15 |
| G-TES-02 | 漏れ | 終わったタスク(COMPLETE など)への CancelTask の結果が決まっていない。CANCELING と CANCELED の境目(「片付け」が何か)も実装しだい | `tes/…` CancelTask、`tesState` の説明 | 終わったものへの取り消しは 200 で何もしない(D-020)。CANCELING → CANCELED は「コンテナを止めて消し、作業ディレクトリを消した」とき | 原文 | tes、a13 |
| G-TES-03 | 漏れ | `page_size` が 2048 以上・0・負のときの扱いが無い | `tes/…:161` | 400(仮)。他の実装は切り詰めるかもしれない → 相互運用の試験で比べる | 作る | tes |
| G-TES-04 | 漏れ | 読み取り専用の項目(`id`・`state`・`logs`・`creation_time`)を CreateTask に入れて送ったときの扱い(無視か 400 か) | `tes/…` `readOnly` | 無視して、サーバーが付けたもので上書き(OpenAPI の readOnly の意味どおり) | 原文 | tes、a10 |
| G-TES-05 | 漏れ | 入力・出力の取得に使う資格情報を渡す欄が無い(`tesInput` は `url` だけ、ヘッダーも無い) | `tes/…` `tesInput` | 署名付き URL で渡す(D-007、`system.md` 3 節)。TES 自身が DRS を解決するのは「あとで深める」の独自拡張 | 原文 | a10、a11 |
| G-TES-06 | 漏れ | 入力のチェックサムを確かめる欄が無い | `tes/…` `tesInput` | 最小版は確かめない。深める段階で検討(`system.md` C-05) | 原文 | a11 |
| G-TES-07 | 古い | `tesOutputFileLog.size_bytes` は文字列(「JSON が int64 を扱えないため」と注記) | `tes/…` `size_bytes` | 文字列で返す。読むときは整数に直す | 原文 | tes |
| G-TES-08 | 食い違い | `view` の説明が `tesTask.Id`・`tesTask.State`(大文字始まり)と書くが、項目名は `id`・`state` | `tes/task_execution_service.openapi.yaml:257` | 項目名(小文字)を正とする | 原文 | tes |
| G-TES-09 | 漏れ | サーバーの再起動で実行中のタスクがどうなるかが無い(`logs` は「再試行で追加される」とだけ) | `tes/…` `logs` | 再起動のとき、実行役の Docker のラベルからタスクを突き合わせて状態を戻す。突き合わせられないものは `SYSTEM_ERROR`(`tes.md` 4 節) | 作る | tes、runner |
| G-TES-10 | 漏れ | `PAUSED`・`PREEMPTED` に入る条件・出る条件が無い | `tesState` | yai は**使わない**(この 2 つの状態にはしない) | 原文 | tes |
| G-TES-11 | 古い | 応答コードが引用符なしの `200:`。YAML としては整数のキーになり、OpenAPI(文字列のキー)として厳しく読む道具は落ちる | `tes/task_execution_service.openapi.yaml:82` ほか | 形の検査・生成の前処理で文字列のキーに直す(1 か所、`language.md` 3 節) | 原文 | tes |
| G-TES-12 | 食い違い | `view=MINIMAL` は「`id` と `state` だけ」を返すと決めるが、`tesTask` の schema は `executors` を必須にし、ListTasks・GetTask の応答はその schema を指す。MINIMAL の応答は schema の検査に通らない(段階 1 の試験で 2026-10-16 に見つけた) | `tes/task_execution_service.openapi.yaml:256`(view)、`tesTask` の `required` | view の説明を正とし、MINIMAL では `id`・`state` だけを返す。形の検査は MINIMAL の応答だけ「鍵が `id`・`state` の 2 つ」で照らす(`tests/shape.py`)。BASIC・FULL は schema で照らす | 作る | tes、a13 |
| G-TES-13 | 漏れ | `tesTaskLog`・`tesExecutorLog` の `start_time`・`end_time` は「`type: string`、RFC 3339」とだけで、**まだ無いとき**の表し方(省く / null / 空文字)が無い。Funnel v0.11.14 は protobuf の JSON 化の既定で `""` を返す(INTEROP-TES-2 で yai-wes が落ちた、2026-10-17) | `tes/task_execution_service.openapi.yaml:395`・`:856` | WES の engine は、文字列でない・空・RFC 3339 として読めない時刻を「無い」(null)として run を落とさない(`yai/wes/engine.py`、試験 `test_INT_TIME_EMPTY`)。yai-tes は無い時刻を省く | 原文 | a13 |

## 3. WES 1.1.0

| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |
| --- | --- | --- | --- | --- | --- | --- |
| G-WES-01 | 漏れ | ジョブの入出力用の資格情報の受け渡しは**対象外**(「DOS の作業で扱う予定」とある) | `wes/workflow_execution_service.openapi.yaml:53` | multipart の yai 独自の項目 `yai_passports` で Passport を預かり(D-009)、DRS の解決は WES がする(D-007)。長い run での Passport の取り直しは「あとで深める」 | 原文 | a04、a56 |
| G-WES-02 | 食い違い | `RunRequest.workflow_params` は説明で REQUIRED、schema の `required` には無い。multipart では文字列(JSON を文字列で)、`RunRequest` では object。`requestBody` 自体が `required: false` | `wes/…:265,287,737-746` | `workflow_params` は必須(無ければ 400)。multipart の文字列を JSON として読み、`RunLog.request` では object で返す | 原文 | a04 |
| G-WES-03 | 組み合わせ | WES の `Log.stdout` は URL、TES の `ExecutorLog.stdout` は中身の文字列 | `wes/…:844`、`tes/…` `tesExecutorLog` | WES が中身を返す自分の URL を作る(`system.md` C-08) | 原文 | a13、wes |
| G-WES-04 | 漏れ | `TaskLog.tes_uri` は任意で、それを読むための資格情報が決まっていない | `wes/…:930` | `system.md` C-12 | 原文 | a15 |
| G-WES-05 | 漏れ | `workflow_type` は「CWL か WDL か、その WES が対応する別のもの」。別のものの名前の決まりが無い | `wes/…:247` | 最小版のエンジンの形は D-014(決定: yai 内蔵の CWL の部分集合。`workflow_type=CWL`) | 原文 | wes |
| G-WES-06 | 古い | `State` の説明が TES からの写しで、「Executor」「output files uploaded」などワークフローに合わない語を使う | `wes/…` `State` | 意味は `docs/data/wes-states.yaml` で yai が定める(run の状態は全タスクから決める) | 原文 | wes |
| G-WES-07 | 漏れ | 終わった run への CancelRun の結果が決まっていない | `wes/…` CancelRun | 200 で RunId を返し、何もしない(D-020) | 原文 | wes、a14 |
| G-WES-08 | 漏れ | 他人の run を指定されたとき 403 か 404 か(両方が一覧にある) | `wes/…` GetRunLog など | 404(D-013) | 原文 | a14 |
| G-WES-09 | 古い | 「Data Object Service(DOS)」は DRS の旧名 | `wes/…:53` | 読み替える | 原文 | wes |
| G-WES-10 | 漏れ | run の一覧の「安定した並び」と「最初のページの時点の一覧」を、どう実現するかが無い(新しい run が途中で増えたとき) | `wes/…:183` | ページの印に「最初のページを求めた時刻」と「最後に返した ID」を入れる(`docs/data/paging.yaml`)。G-X-04 も同じ | 原文 | a14 |
| G-WES-11 | 漏れ | ListTasks(`/runs/{run_id}/tasks`)は `page_size`・`page_token` を受けるのに、応答の一覧に 400 が無い(200・401・403・404・500 だけ)。壊れた・改ざんされた `page_token`、範囲の外の `page_size` を返す状態コードが無い(段階 1 の WES で 2026-10-16 に見つけた) | `wes/workflow_execution_service.openapi.yaml:417`(ListTasks の responses) | ListRuns と同じく **400** を返す(`paging.yaml` と test-plan の NEG-PAGING-TAMPER に合わせる)。「状態コードは一覧にあるものだけ」(`wes.md` 6 節)からはこの 1 か所だけ外れる(D-027、ユーザー 2026-10-16 08:52 決定) | 作る | a14 |
| G-WES-12 | 漏れ | GetRunLog の `request` は型が `RunRequest` とあるだけで、送られた要求をそのまま返すのか、秘密(`workflow_url`・File の `location` に入る署名付き URL の問い合わせ部分)を伏せてよいのかが無い。run が終わったあと、いつまで持つかも無い | `wes/workflow_execution_service.openapi.yaml:799`(RunLog の `request`) | run が終わったら問い合わせ部分を消し、伏せた形を返す(D-030 案 A、ユーザー 2026-10-16 決定)。終わる前は送られたまま返す。保存した文書・添付も run が終わったら消す(D-030 の拡張、2026-10-17) | 原文 | wes、a14 |
| G-WES-13 | 漏れ | `GET /runs/{run_id}` などの 404 は「The requested workflow run not found」とだけで、**形が違う run_id**(UUID でない等)の扱いが無い。Sapporo 2.4.0 は 400 `Invalid run_id format`、yai は 404(INTEROP-WES-1、2026-10-17) | `wes/workflow_execution_service.openapi.yaml:356` | 無い run は形にかかわらず 404(id の形を外に見せない)。相手が 400 を返しても「無い」と読めるよう、相互運用の報告に残す | 原文 | wes |

## 4. AAI 1.2・Passport 1.2

| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |
| --- | --- | --- | --- | --- | --- | --- |
| G-AAI-01 | 組み合わせ | AAI 1.2 では Passport-Scoped Access Token はクライアントと Broker の間だけで使う。一方 DRS は `BearerAuth` を認めるが、その Bearer が何であるべきかは決めていない | `aai/AAIConnectProfile.md:328`、DRS `Auth.md` | DRS の Bearer は yai の AT(`aud=yai-drs`)だけ。PSAT が来たら(`scope` に `ga4gh_passport_v1`)401 | 原文 | a16、a02 |
| G-AAI-02 | 古い | Visa Access Token は「将来消す提案」。1 時間の規則と Polling が要る | `aai/AAIConnectProfile.md:570,574,700-708` | 受けない(`scope` を持つ Visa は無視)。Visa Document Token だけ | 原文 | ch、m5 |
| G-AAI-03 | 漏れ | Passport の `aud` は任意。どのサービスあての Passport か分からないと、別のサービスに持ち込まれても区別できない | `aai/…` Passport Format の `aud` | 試験用 Broker は `aud` を付ける。yai-drs は有れば自分を含むことを要求。無いものを受けるかは設定(既定は受ける、仮)。省いたときは yai-drs の起動で WARNING(D-055) | 原文 | a56、ch |
| G-AAI-04 | 組み合わせ | 秘密を含む応答に `Cache-Control: no-store` などを求めるのは「クライアント・アプリ」の節。DRS の `/access`(署名付き URL を返す)は対象と書かれていない | `aai/…:443-448` | 署名付き URL も秘密として扱い、yai-drs の `/access` 系の応答に同じヘッダーを付ける | 原文 | a08 |
| G-AAI-05 | 古い | Visa の `typ` は AAIv1.2 タグの時点では必須、その後の Passport の文書(a4ee601)で任意に。版の番号は同じ 1.2 のまま | `passport/ga4gh_passport_v1.md` `typ` の節、`specs/SOURCES.yaml` | いまの文書(任意)に従う。有るなら `vnd.ga4gh.visa+jwt` であること | 原文 | ch、visa-issuer |
| G-AAI-06 | 漏れ | Passport の `sub` と Visa の `sub` は違いうる。複数の身元の Visa を組み合わせるなら LinkedIdentities が要る | `aai/…` Passport Format の `sub`、`passport/…:192-196` | 最小版は 1 つの判断に 1 つの Visa だけを使い、組み合わせない。複数の Visa が要る方針は LinkedIdentities を作るまで書かない | 原文 | ch |
| G-AAI-07 | 古い | Passport の文書が AAI の「Token Revocation」の節へ張っているが、AAI 1.2 にその節は無い | `passport/…:1050`、`aai/…` の見出し一覧 | Passport の文書の本文(`asserted`・`exp` を確かめる)だけに従う | 原文 | ch |
| G-AAI-08 | 漏れ | WES・TES の Bearer と AAI の関係が無い(どのトークンを受けるべきか) | WES `:49`、TES `:56` | yai の AT(D-008)。Passport を Bearer にしない | 原文 | a04、a14、a15、at-issuer |
| G-AAI-09 | 漏れ | Broker が OIDC の認可コードの流れを持つこと(MUST)と、試験用の最小 Broker との差 | `aai/…` Conformance for Brokers | 試験用 Broker は認可コードの流れを持たない(固定の利用者表)。**yai-aai の Broker は AAI に適合しない**と明記し、適合の主張は Clearinghouse の部品だけ | 原文 | broker、a01 |
| G-AAI-10 | 漏れ | Passport の大きさ・1 つの要求の Passport の個数・Passport の中の Visa の個数に上限の決まりが無い。PP-CH-01 の「必要な検査」(AAI の仕様が決める)にも無い。Clearinghouse は信頼した Broker の Passport でも、Visa の数だけ署名を確かめることになる | `passport/ga4gh_passport_v1.md:171-179`(Clearinghouse の処理)、`aai/…` Passport Format | 1 つの要求の Passport は 10 個まで・1 つ 64 KiB まで(超えたら 400)。Visa は 1 つの Passport に 100 個まで(超えたら Passport ごと拒否、D-058 仮)。PP-CH-02(要らない Visa は無視)の意図とずれることを承知で、資源の備え(AAI-CH-04)を優先する | 原文 | ch |

## 5. 4 つを組み合わせたとき(横断)

| 番号 | 種類 | 中身 | 出典 | yai の解釈 | 確かめ | 図の場所 |
| --- | --- | --- | --- | --- | --- | --- |
| G-X-01 | 組み合わせ | WES 1.1.0 は「入力の URL を WES が取りに行ける」ことを求めるが、DRS の資格情報(Passport)を WES に渡す方法は無い。DRS の URI を WES に渡すことは DRS 側が想定している(「WES に渡すときの便利のため URI を定義」) | `DrsApiPrinciples.md:13`、WES `:53` | `system.md` 3 節の (a) | 原文 | a04、a56 |
| G-X-02 | 組み合わせ | Passport は「期限までに(か少し後に)アクセスを止める」ことを求める。署名付き URL は出したら期限まで使える | `passport/…:1060-1066`、DRS の取り方の 4 番目 | Option B で、署名付き URL の期限 = 許容する遅れ(既定 60 分)。Visa の残りがそれより短ければ URL を出さない | 原文 | a08、a11、ch |
| G-X-03 | 組み合わせ | 時刻の形が 3 つ(RFC 3339・`%Y-%m-%dT%H:%M:%SZ`・エポック秒) | DRS `DrsObject.yaml`、WES `:722`、AAI | 内部は UTC の秒、外へ出すときに各形(D-015) | 原文 | a13、a56 |
| G-X-04 | 組み合わせ | ページの終わりの合図: WES は「空文字」、TES は書いていない | WES `:904,960`、TES `:163-170` | yai-tes は出さない、yai-wes は空文字を出す。読む側は両方を終わりと読む | 作る(相互運用の相手が何を返すか) | a13、a14 |
| G-X-05 | 組み合わせ | DRS の `AccessURL.headers`(ヘッダー付きで取る)を TES の入力に渡せない | DRS `AccessURL.yaml`、TES `tesInput` | ヘッダーなしの署名付き URL だけを TES に渡す(`system.md` C-03) | 原文 | a08、a10 |
| G-X-06 | 漏れ | 3 つの service-info の `type.version` の形(`1.5`・`1.5.0`・`1.1.0`)がそろっていない | DRS の例、TES・WES の `info.version` | 各 OpenAPI の `info.version`(`1.5.0`・`1.1.0`・`1.1.0`) | 作る | drs、tes、wes |
| G-X-07 | 食い違い | 同じ GA4GH の service-info 1.0.0 の拡張なのに、WES 1.1.0 の `ServiceInfo` は `tags`(string→string)を **required** とし、TES 1.1 の `tesServiceInfo` には `tags` が無い(`tags` は Task にだけ)。追加の項目は schema で禁じられていないが、TES の厳密なクライアント(py-tes 1.1.4 の attrs の型)は知らない項目を拒む(INTEROP-TES-1、2026-10-17) | `wes/workflow_execution_service.openapi.yaml:638`・`:651`(required)、`tes/task_execution_service.openapi.yaml:660`(tesServiceInfo)・`:801`(Task の tags) | WES の service-info には `tags` を入れ、TES の service-info には入れない(D-040、ユーザー 2026-10-17 15:51)。実行役の種類は TES の `description` に書く | 原文 | tes、wes |

## 6. 作らないと分からないもの(試験で確かめる)

| もの | 何を確かめるか | 試験 |
| --- | --- | --- |
| 公式の TES の試験道具が 4xx をどう見るか(G-TES-01) | yai の誤りの応答で落ちないか | CONF-TES |
| DRS の試験道具(1.2.0 用)が 1.5.0 のサーバーのどこで落ちるか | 落ちる項目の一覧と、版の違いによるものか | CONF-DRS |
| 他の TES が署名付き URL(クエリ付きの https)を取れるか | Funnel などに yai-drs の URL を渡す | INTEROP-TES-HTTPS |
| 他の WES が `drs://` の入力を扱えるか、資格情報をどう受けるか | 相手の実装を読む・動かす | INTEROP-WES-DRS |
| 一括の部分的な失敗・ページの終わり・`page_size` の上限の扱い | 相手と yai で同じ入力を比べる | INTEROP-EDGE |
| 再起動のときの突き合わせ(G-TES-09) | 実行中に yai-tes を止めて上げる | NEG-RESTART |
| 取り消しと完了が重なったとき | 終わる直前に取り消す | NEG-CANCEL-RACE |
| エンジンから WES のタスク情報が取れるか(D-014 の後) | 選んだエンジンで 1 ステップ・2 ステップ | SPIKE-ENGINE |

## 7. 数(道具が数えたもの)

| 仕様 | 行 | 食い違い | 漏れ | 古い | 組み合わせ |
| --- | --- | --- | --- | --- | --- |
| DRS 1.5.0 | 16 | 4 | 10 | 2 | 0 |
| TES 1.1 | 13 | 2 | 9 | 2 | 0 |
| WES 1.1.0 | 13 | 1 | 9 | 2 | 1 |
| AAI 1.2・Passport 1.2 | 10 | 0 | 5 | 3 | 2 |
| 4 つを組み合わせたとき(横断) | 7 | 1 | 1 | 0 | 5 |
| 計 | 59 | 8 | 34 | 9 | 8 |
