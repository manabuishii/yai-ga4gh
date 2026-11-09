# 仕様の義務の表(spec-obligations)

<!-- 生成物: docs/data/obligations.yaml から tools/gen_obligations.py で作る。手で直さない -->

- 出典の `file:line` は `specs/` の下(`docs/spec-pins.md` で固定した版。`aai/`・`passport/` は写しを置かず、`specs/SOURCES.yaml` の固定した commit の原文)。行番号は原文の引用文字列から道具が求めたもの
- 1 行 = 1 つの義務。**ID は試験の名前と 1 対 1**(`docs/test-plan.md` 3 節)
- 強さ: 原文の語(MUST・should・shall・Must be・schema の required など)。語が無いものは「定義」「責任」など
- 場所: **本文**=OpenAPI の形の検査では見えない(説明文・Markdown にだけある)/**形**=schema で検査できる
- 最小版: ◯=最初の「動く最小版」で満たして試す/△=一部だけ(何を省くかは各設計書)/後=あとで深める/外=対象外/決=仕様に定めが無く yai が決める
- 解釈: `docs/spec-gaps.md` の番号。yai が仕様の上に足した決まりはここに書かず、設計書と `docs/decisions.md` に書く


## 1. DRS 1.5.0

| ID | 対象 | 義務 | 強さ | 出典 | 場所 | 最小版 | 解釈 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DRS-ID-01 | ID | ID は [A-Za-z0-9.-_~]。ほかの文字は API で使うとき percent-encode | MUST | `drs/openapi/tags/DrsApiPrinciples.md:6` | 本文 | ◯ |  |
| DRS-ID-02 | ID | 1 つの DRS ID は常に同じデータを返す | MUST | `drs/openapi/tags/DrsApiPrinciples.md:7` | 本文 | ◯ |  |
| DRS-ID-03 | ID | 版の概念(最新版の取得・版の一覧)は無い | 定義 | `drs/openapi/tags/DrsApiPrinciples.md:9` | 本文 | ◯ |  |
| DRS-URI-01 | クライアント | ホスト名型と compact identifier 型の両方を解決できる | MUST | `drs/openapi/tags/DrsApiPrinciples.md:15` | 本文 | △ | G-DRS-01, G-DRS-02 |
| DRS-URI-02 | クライアント | ホスト名型は https・443 固定。ポートを含むのは不正 | 定義 | `drs/openapi/tags/DrsApiPrinciples.md:42` | 本文 | △ | G-DRS-01 |
| DRS-URI-03 | クライアント | ホスト名型の ID は percent-encode 済みで「:」を含まない | 定義 | `drs/openapi/tags/HostnameBasedURIs.md:3` | 本文 | ◯ |  |
| DRS-URI-04 | クライアント | compact 型は最初の「:」で prefix と accession に分け、accession は percent-encode して DRS ID に使う | must | `drs/openapi/tags/DrsApiPrinciples.md:76`<br>`drs/openapi/tags/CompactIdentifierBasedURIs.md:69` | 本文 | ◯ |  |
| DRS-URI-05 | クライアント | URL パターンを 24 時間めやすでキャッシュする | SHOULD | `drs/openapi/tags/DrsApiPrinciples.md:92` | 本文 | 後 |  |
| DRS-URI-06 | サーバー | 自前の compact identifier を出すなら identifiers.org に prefix を登録する | MUST | `drs/openapi/tags/DrsApiPrinciples.md:66` | 本文 | 外 | G-DRS-02 |
| DRS-SI-01 | service-info | type.group=org.ga4gh、type.artifact=drs | MUST | `drs/openapi/paths/service-info.yaml:13` | 本文 | ◯ |  |
| DRS-SI-02 | service-info | maxBulkRequestLength を根(非推奨)と drs の下の両方に出す | required | `drs/openapi/components/schemas/DrsService.yaml:4,21`<br>`drs/openapi/components/schemas/DrsService.yaml:8` | 形 | ◯ | G-DRS-06 |
| DRS-SI-03 | service-info | service-info 1.0.0 の必須(id・name・type・organization・version) | required | `service-info/service-info.yaml:42` | 形 | ◯ |  |
| DRS-OBJ-01 | GetObject | id・self_uri・size・created_time・checksums が必須。checksums は 1 つ以上 | required | `drs/openapi/components/schemas/DrsObject.yaml:4`<br>`drs/openapi/components/schemas/DrsObject.yaml:60` | 形 | ◯ |  |
| DRS-OBJ-02 | GetObject | blob では access_methods が要る | 本文の Required | `drs/openapi/components/schemas/DrsObject.yaml:93` | 本文 | ◯ | G-DRS-07 |
| DRS-OBJ-03 | GetObject | self_uri はホスト名型の drs:// | 定義 | `drs/openapi/components/schemas/DrsObject.yaml:20` | 本文 | ◯ |  |
| DRS-OBJ-04 | GetObject | created_time・updated_time は RFC 3339 で、中身の時刻(JSON の作成時刻ではない) | 定義 | `drs/openapi/components/schemas/DrsObject.yaml:34` | 本文 | △ | G-X-03 |
| DRS-OBJ-05 | GetObject | チェックサムの type は IANA の Hash Name String(例 sha-256) | SHOULD | `drs/openapi/components/schemas/Checksum.yaml:14` | 本文 | ◯ |  |
| DRS-OBJ-06 | GetObject | 無い ID は 404(Error の形) | 応答の定義 | `drs/openapi/paths/objects@{object_id}.yaml:18,47` | 形 | ◯ |  |
| DRS-AM-01 | AccessMethod | access_url と access_id の少なくとも一方 | must | `drs/openapi/components/schemas/AccessMethod.yaml:20,26` | 本文 | ◯ | G-DRS-07 |
| DRS-AM-02 | AccessMethod | access_id は 1 つのオブジェクトの中で一意 | must | `drs/openapi/components/schemas/AccessMethod.yaml:25` | 本文 | ◯ |  |
| DRS-AM-03 | AccessMethod | access_id があるとき /access の認証のしかたを authorizations で示す | 定義 | `drs/openapi/components/schemas/AccessMethod.yaml:47` | 本文 | ◯ |  |
| DRS-ACC-01 | GetAccessURL | 返す AccessURL は url 必須。headers は任意 | required | `drs/openapi/components/schemas/AccessURL.yaml:3` | 形 | ◯ | G-X-05 |
| DRS-ACC-02 | GetAccessURL | access_id はオブジェクトの中でだけ一意なので、別のオブジェクトの access_id は 404(仕様の帰結) | 帰結 | `drs/openapi/components/schemas/AccessMethod.yaml:25` | 本文 | ◯ |  |
| DRS-AUTH-01 | 全体 | 誰が何をできるかの方針は実装が決めて執行する | 責任 | `drs/openapi/tags/Auth.md:3` | 本文 | ◯ |  |
| DRS-AUTH-02 | 全体 | オブジェクトごと、またメタデータと /access で認証方式が違ってよい。OPTIONS で示す | should | `drs/openapi/tags/Auth.md:34` | 本文 | ◯ |  |
| DRS-AUTH-03 | PostObject・PostAccessURL・一括 | Passport は POST の本文 passports(配列)で渡す | 形 | `drs/openapi/components/requestBodies/Passports.yaml:7`<br>`drs/openapi/components/requestBodies/PostObjectBody.yaml:22`<br>`drs/openapi/tags/Auth.md:60` | 形 | ◯ | G-DRS-03 |
| DRS-AUTH-04 | OptionsObject | Authorizations を返す。対応しないなら 204 か 405 | 応答の定義 | `drs/openapi/components/responses/AuthorizationsNotSupported.yaml:1` | 形 | ◯ |  |
| DRS-AUTH-05 | Authorizations | PassportAuth を含むなら passport_auth_issuers は必須(本文だけ) | required(本文) | `drs/openapi/components/schemas/Authorizations.yaml:22` | 本文 | ◯ | G-DRS-08 |
| DRS-AUTH-07 | クライアント(yai-wes の解決役) | Passport を出すなら、passport_auth_issuers の一覧にある Visa を含む Passport だけを出す。送ってよい DRS かを確かめる | must / strongly recommended | `drs/openapi/components/schemas/Authorizations.yaml:24` | 本文 | ◯ |  |
| DRS-AUTH-06 | 全体 | 認証なし・Basic・Bearer・Passport の 4 方式 | 定義 | `drs/openapi/data_repository_service.openapi.yaml:24`<br>`drs/openapi/data_repository_service.openapi.yaml:138` | 形 | △ |  |
| DRS-BULK-01 | 一括 | 配列が maxBulkRequestLength を超えたら 413 | 本文 | `drs/openapi/components/schemas/DrsService.yaml:8,25` | 本文 | ◯ |  |
| DRS-BULK-02 | 一括 | 解決できない ID は unresolved_drs_objects に入れ summary で数える | 応答の定義 | `drs/openapi/components/responses/200OkDrsObjects.yaml:9` | 形 | ◯ | G-DRS-09 |
| DRS-202-01 | 全体 | 202 を返すなら Retry-After は整数の秒 | must | `drs/openapi/components/responses/202Accepted.yaml:8` | 本文 | 外 |  |
| DRS-RO-01 | 全体 | DRS v1 は読み取り専用。登録・更新は実装が独自に持つ | 定義 | `drs/openapi/tags/DrsApiPrinciples.md:120` | 本文 | ◯ |  |
| DRS-BUN-01 | 全体 | バンドルは 1.3 から非推奨 | 定義 | `drs/openapi/tags/DrsApiPrinciples.md:116` | 本文 | 外 |  |
| DRS-SEC-01 | 全体 | 取りに行ける先を絞り、悪用をログで見つけられるようにする | should | `drs/openapi/tags/Auth.md:28` | 本文 | △ |  |

## 2. TES 1.1

| ID | 対象 | 義務 | 強さ | 出典 | 場所 | 最小版 | 解釈 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TES-AUTH-01 | 全体 | 認証の要否は実装が決める。使うなら OAuth2 Bearer を推奨 | recommend | `tes/task_execution_service.openapi.yaml:56` | 本文 | ◯ |  |
| TES-AUTH-02 | 全体 | 利用者がタスクを出してよいかを確かめるのは実装の責任 | 責任 | `tes/task_execution_service.openapi.yaml:59` | 本文 | ◯ |  |
| TES-CORS-01 | 全体 | 別のサイトから使わせるなら CORS | must | `tes/task_execution_service.openapi.yaml:63` | 本文 | 外 |  |
| TES-SI-01 | service-info | type.artifact=tes。storage・tesResources_backend_parameters を返す | required(artifact) | `tes/task_execution_service.openapi.yaml:658`<br>`tes/task_execution_service.openapi.yaml:675` | 形 | ◯ |  |
| TES-CRT-01 | CreateTask | executors 必須。各 executor は image・command 必須 | required | `tes/task_execution_service.openapi.yaml:733`<br>`tes/task_execution_service.openapi.yaml:292` | 形 | ◯ |  |
| TES-CRT-02 | CreateTask | 応答は id(サーバーが付ける) | required | `tes/task_execution_service.openapi.yaml:279` | 形 | ◯ |  |
| TES-CRT-03 | CreateTask | stdin・stdout・stderr・入出力の path は絶対パス | must | `tes/task_execution_service.openapi.yaml:330`<br>`tes/task_execution_service.openapi.yaml:346,357,466` | 本文 | ◯ |  |
| TES-CRT-04 | CreateTask | tesInput.url は content が無いとき必須。content があれば url は無視 | REQUIRED / must | `tes/task_execution_service.openapi.yaml:454`<br>`tes/task_execution_service.openapi.yaml:480` | 本文 | ◯ |  |
| TES-CRT-05 | CreateTask | content は少なくとも 128 KiB を受ける | should | `tes/task_execution_service.openapi.yaml:475` | 本文 | ◯ |  |
| TES-CRT-06 | CreateTask | 出力の path に POSIX のワイルドカード。使うなら path_prefix 必須 | Required if | `tes/task_execution_service.openapi.yaml:548` | 本文 | 後 |  |
| TES-CRT-07 | CreateTask | 対応しない backend_parameters のキーは警告し、保存も返却もしない。strict なら失敗 | shall / should | `tes/task_execution_service.openapi.yaml:625`<br>`tes/task_execution_service.openapi.yaml:643` | 本文 | ◯ |  |
| TES-CRT-08 | CreateTask | 読み取り専用の項目(id・state・logs・creation_time)は送られても使わない | readOnly | `tes/task_execution_service.openapi.yaml:687` | 形 | ◯ | G-TES-04 |
| TES-RUN-01 | 実行 | executor は順に 1 つずつ。最初の誤りで止まる(ignore_error なら続ける) | 定義 | `tes/task_execution_service.openapi.yaml:780`<br>`tes/task_execution_service.openapi.yaml:386` | 本文 | ◯ |  |
| TES-RUN-02 | 実行 | volumes は空のディレクトリとして始まり、全 executor に同じパスで見える | 定義 | `tes/task_execution_service.openapi.yaml:789` | 本文 | ◯ |  |
| TES-RUN-04 | 実行 | 全 executor が、宣言した同じ入力と volumes を同じパスで見る | 定義 | `tes/task_execution_service.openapi.yaml:777` | 本文 | ◯ |  |
| TES-RUN-03 | 実行 | tesFileType はサーバーが実行時に埋める | required(本文) | `tes/task_execution_service.openapi.yaml:436` | 本文 | ◯ |  |
| TES-ST-01 | 状態 | COMPLETE は executor が誤りなく終わり、出力のアップロードも成功したこと | 定義 | `tes/task_execution_service.openapi.yaml:705` | 本文 | ◯ |  |
| TES-ST-02 | 状態 | 出力の転送の失敗などは SYSTEM_ERROR、executor の非 0 終了は EXECUTOR_ERROR | 定義 | `tes/task_execution_service.openapi.yaml:709` | 本文 | ◯ |  |
| TES-ST-03 | 状態 | 取り消しは CANCELING(片付け待ち)を経て CANCELED(片付け済み) | 定義 | `tes/task_execution_service.openapi.yaml:713` | 本文 | ◯ | G-TES-02 |
| TES-ST-04 | 状態 | 項目が無いときに QUEUED と取り違えないよう既定は UNKNOWN | 定義 | `tes/task_execution_service.openapi.yaml:694` | 本文 | ◯ |  |
| TES-LST-01 | ListTasks | page_size は 2048 未満、既定 256 | Must | `tes/task_execution_service.openapi.yaml:161` | 本文 | ◯ | G-TES-03 |
| TES-LST-02 | ListTasks | name_prefix・state・tag_key/tag_value で絞る(本文の表の一致規則) | OPTIONAL(引数) | `tes/task_execution_service.openapi.yaml:102`<br>`tes/task_execution_service.openapi.yaml:129` | 本文 | ◯ |  |
| TES-LST-03 | ListTasks | next_page_token を page_token に渡して次のページ | 定義 | `tes/task_execution_service.openapi.yaml:170` | 本文 | ◯ | G-X-04 |
| TES-VIEW-01 | GetTask・ListTasks | view は既定 MINIMAL(id と state だけ)。BASIC は stdout・stderr・content・system_logs を除く | 定義 | `tes/task_execution_service.openapi.yaml:256`<br>`tes/task_execution_service.openapi.yaml:269` | 本文 | ◯ |  |
| TES-CAN-01 | CancelTask | 取り消しの応答は空のオブジェクト。終わったタスクへの取り消しは未定義 | 形 | `tes/task_execution_service.openapi.yaml:276` | 形 | 決 | G-TES-02 |
| TES-ERR-01 | 全体 | 誤りの応答(4xx・5xx)が 1 つも定義されていない | なし | `tes/task_execution_service.openapi.yaml:211` | 形 | 決 | G-TES-01 |

## 3. WES 1.1.0

| ID | 対象 | 義務 | 強さ | 出典 | 場所 | 最小版 | 解釈 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| WES-AUTH-01 | 全体 | 利用者は身元と権限を示す資格情報を出す。Bearer 推奨。auth_instructions_url で案内 | must(users) | `wes/workflow_execution_service.openapi.yaml:49` | 本文 | ◯ |  |
| WES-AUTH-02 | 全体 | run を出してよいかの確かめは実装の責任 | 責任 | `wes/workflow_execution_service.openapi.yaml:51` | 本文 | ◯ |  |
| WES-AUTH-03 | 全体 | ジョブの入出力用の資格情報の受け渡しは対象外 | 範囲外 | `wes/workflow_execution_service.openapi.yaml:53` | 本文 | 決 | G-WES-01 |
| WES-SI-01 | service-info | workflow_type_versions・supported_wes_versions・supported_filesystem_protocols・workflow_engine_versions・default_workflow_engine_parameters・system_state_counts・auth_instructions_url・tags が必須 | required | `wes/workflow_execution_service.openapi.yaml:650` | 形 | ◯ |  |
| WES-LST-01 | ListRuns | 並びは安定。最初のページを求めた時点の一覧を返す | should | `wes/workflow_execution_service.openapi.yaml:183` | 本文 | ◯ | G-X-04 |
| WES-LST-02 | ListRuns・ListTasks | page_size より多く返してはならない | must not | `wes/workflow_execution_service.openapi.yaml:188`<br>`wes/workflow_execution_service.openapi.yaml:188,441` | 本文 | ◯ |  |
| WES-LST-03 | ListRuns | 呼んだ人が見てよい run だけに絞る | 定義 | `wes/workflow_execution_service.openapi.yaml:957` | 本文 | ◯ |  |
| WES-LST-04 | ListRuns・ListTasks | next_page_token が空文字なら終わり | 定義 | `wes/workflow_execution_service.openapi.yaml:904,960` | 本文 | ◯ | G-X-04 |
| WES-LST-05 | ListRuns | RunStatus でなく RunSummary を返す(2.0 で RunStatus は不可) | encourage | `wes/workflow_execution_service.openapi.yaml:965` | 本文 | ◯ |  |
| WES-RUN-01 | RunWorkflow | multipart/form-data。workflow_attachment の各部分は Content-Disposition の filename 必須 | must | `wes/workflow_execution_service.openapi.yaml:241` | 本文 | ◯ |  |
| WES-RUN-02 | RunWorkflow | 添付のファイル名に「..」を含めてはならず、実装は悪意ある名前から守る | must not / should | `wes/workflow_execution_service.openapi.yaml:241` | 本文 | ◯ |  |
| WES-RUN-03 | RunWorkflow | workflow_url は WES から届く絶対 URL か、添付への相対 URL | 定義 | `wes/workflow_execution_service.openapi.yaml:243` | 本文 | ◯ |  |
| WES-RUN-04 | RunWorkflow | 入力の URL を WES が理解して取りに行ける | must | `wes/workflow_execution_service.openapi.yaml:245` | 本文 | ◯ | G-WES-01 |
| WES-RUN-05 | RunWorkflow | workflow_type は CWL・WDL か、その WES が対応する別のもの。版・エンジン・エンジンの版も対応するものに限る | must | `wes/workflow_execution_service.openapi.yaml:247` | 本文 | ◯ | G-WES-05 |
| WES-RUN-06 | RunWorkflow | workflow_engine_version を出すなら workflow_engine も要る | Required if | `wes/workflow_execution_service.openapi.yaml:771` | 本文 | ◯ |  |
| WES-RUN-07 | RunRequest | workflow_params は本文で REQUIRED だが schema の required に無い。multipart では文字列 | 食い違い | `wes/workflow_execution_service.openapi.yaml:735`<br>`wes/workflow_execution_service.openapi.yaml:287` | 本文 | ◯ | G-WES-02 |
| WES-LOG-01 | GetRunLog | stdout・stderr は URL。WES と同じ資格情報で取れるべき | should | `wes/workflow_execution_service.openapi.yaml:844,847` | 本文 | ◯ | G-WES-03 |
| WES-LOG-02 | Log・RunSummary | 時刻は ISO 8601 "%Y-%m-%dT%H:%M:%SZ" | 定義 | `wes/workflow_execution_service.openapi.yaml:722,725,838,841` | 本文 | ◯ | G-X-03 |
| WES-LOG-03 | GetRunLog | task_logs は非推奨。task_logs_url で一覧を指す | deprecated | `wes/workflow_execution_service.openapi.yaml:805`<br>`wes/workflow_execution_service.openapi.yaml:818` | 形 | ◯ |  |
| WES-TSK-01 | ListTasks | タスクの並びは RunLog と同じ | should | `wes/workflow_execution_service.openapi.yaml:424` | 本文 | ◯ |  |
| WES-TSK-02 | TaskLog | id・name 必須。tes_uri は任意 | required | `wes/workflow_execution_service.openapi.yaml:930` | 形 | ◯ | G-WES-04 |
| WES-ST-01 | 状態 | 状態の集合は TES と同じ 11 個。説明は TES のものを流用 | 定義 | `wes/workflow_execution_service.openapi.yaml:660` | 本文 | ◯ | G-WES-06 |
| WES-ERR-01 | 全体 | 誤りは ErrorResponse(msg・status_code)。操作ごとに使える状態コードが決まっている | 応答の定義 | `wes/workflow_execution_service.openapi.yaml:966` | 形 | ◯ |  |
| WES-CAN-01 | CancelRun | 応答は RunId。終わった run への取り消しは未定義 | なし | `wes/workflow_execution_service.openapi.yaml:557` | 本文 | 決 | G-WES-07 |

## 4. AAI 1.2・Passport 1.2(yai は Clearinghouse が本命)

| ID | 対象 | 義務 | 強さ | 出典 | 場所 | 最小版 | 解釈 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AAI-CH-01 | Clearinghouse | 少なくとも 1 つの Broker を信頼する(設定で持つ) | MUST | `aai/AAIConnectProfile.md:672` | 本文 | ◯ |  |
| AAI-CH-02 | Clearinghouse | Passport を直接受けるなら Passport の有効性を確かめる | MUST | `aai/AAIConnectProfile.md:684` | 本文 | ◯ |  |
| AAI-CH-03 | Clearinghouse | アクセストークンを受けるなら有効性を確かめるか不透明として扱う | MUST | `aai/AAIConnectProfile.md:680` | 本文 | 外 | G-AAI-01 |
| AAI-CH-04 | Clearinghouse | RFC 6819 の攻撃への備え | MUST | `aai/AAIConnectProfile.md:689` | 本文 | △ |  |
| AAI-CH-05 | Visa | JWT の検査(期限など)がすべて通ること | MUST | `aai/AAIConnectProfile.md:696` | 本文 | ◯ |  |
| AAI-CH-06 | Visa Access Token | iat から 1 時間を超え、Polling で有効と確かめられなければ無効 | MUST(invalid) | `aai/AAIConnectProfile.md:706` | 本文 | 外 | G-AAI-02 |
| AAI-CH-07 | Visa Document Token | jku から鍵を取るなら、取りに行く前に jku がその iss の信頼先かを確かめる | MUST | `aai/AAIConnectProfile.md:717`<br>`aai/AAIConnectProfile.md:719` | 本文 | ◯ |  |
| AAI-CH-08 | Polling | 問い合わせは 1 時間に 1 回まで。再試行は指数的に待つ | MUST NOT | `aai/AAIConnectProfile.md:727` | 本文 | 外 |  |
| AAI-FMT-01 | Passport | typ は vnd.ga4gh.passport+jwt | REQUIRED | `aai/AAIConnectProfile.md:872` | 本文 | ◯ |  |
| AAI-FMT-02 | Passport | iss・sub・iat・exp・ga4gh_passport_v1 必須(空配列可)。aud は任意 | REQUIRED | `aai/AAIConnectProfile.md:915` | 本文 | ◯ | G-AAI-03 |
| AAI-FMT-03 | 全 JWT | 署名は ES256 か RS256 | MUST | `aai/AAIConnectProfile.md:923` | 本文 | ◯ |  |
| AAI-FMT-04 | Passport-Scoped Access Token | scope に openid と ga4gh_passport_v1。GA4GH Claim を含めない | MUST | `aai/AAIConnectProfile.md:148`<br>`aai/AAIConnectProfile.md:838` | 本文 | ◯ |  |
| AAI-FMT-05 | Visa Document Token | ヘッダーに jku。scope に openid を含めない | MUST | `aai/AAIConnectProfile.md:557`<br>`aai/AAIConnectProfile.md:564` | 本文 | ◯ |  |
| AAI-CLI-01 | 秘密を含む応答 | Cache-Control: no-cache, no-store と Pragma: no-cache | MUST | `aai/AAIConnectProfile.md:443` | 本文 | ◯ | G-AAI-04 |
| AAI-ISS-01 | Passport Issuer(試験用) | Token Exchange の requested_token_type などの決まり | MUST | `aai/AAIConnectProfile.md:600` | 本文 | ◯ |  |
| PP-CH-01 | Clearinghouse | 検査に通らない Passport を含む要求は拒否する | MUST | `passport/ga4gh_passport_v1.md:173` | 本文 | ◯ |  |
| PP-CH-02 | Clearinghouse | その要求に要らない Visa は無視する | MUST | `passport/ga4gh_passport_v1.md:176` | 本文 | △ | G-AAI-10 |
| PP-CH-03 | Clearinghouse | Broker・Visa Assertion Source・Visa Issuer を信頼できなければ無視する | MUST | `passport/ga4gh_passport_v1.md:179` | 本文 | ◯ |  |
| PP-CH-04 | Clearinghouse | 複数の Visa Identity を組み合わせるなら LinkedIdentities で同一人物と確かめる | MUST | `passport/ga4gh_passport_v1.md:192` | 本文 | △ | G-AAI-06 |
| PP-VISA-01 | Visa | iss・sub・iat・exp 必須。scope か jku のどちらかが要る | REQUIRED / MUST | `passport/ga4gh_passport_v1.md:274`<br>`passport/ga4gh_passport_v1.md:276` | 本文 | ◯ |  |
| PP-VISA-02 | Visa | conditions が有れば満たされたと確かめられるときだけ受ける。評価しないなら空でない conditions の Visa は拒否 | MUST | `passport/ga4gh_passport_v1.md:429`<br>`passport/ga4gh_passport_v1.md:439` | 本文 | △ |  |
| PP-VISA-05 | Visa | ga4gh_visa_v1 の asserted は必須(エポック秒)。Visa Expiry の計算に使ってよい | REQUIRED | `passport/ga4gh_passport_v1.md:339`<br>`passport/ga4gh_passport_v1.md:348` | 本文 | ◯ |  |
| PP-VISA-06 | Visa | typ は任意。Visa Document Token なら vnd.ga4gh.visa+jwt を推奨、JWT も可 | OPTIONAL / RECOMMENDED / MAY | `passport/ga4gh_passport_v1.md:287` | 本文 | ◯ | G-AAI-05 |
| AAI-FMT-06 | Visa Document Token | 失効の手続きなしに出してよい(Clearinghouse は失効を知る手段を持たない) | MAY | `aai/AAIConnectProfile.md:553` | 本文 | ◯ |  |
| PP-VISA-03 | Visa | 対応しない独自 type の Visa は無視する | MUST | `passport/ga4gh_passport_v1.md:926` | 本文 | ◯ |  |
| PP-VISA-04 | ControlledAccessGrants | by を含む。value は URL Claim | MUST | `passport/ga4gh_passport_v1.md:786` | 本文 | ◯ |  |
| PP-EXP-01 | Clearinghouse | 求められた期間のアクセスを与えるなら Option A か B で Visa の期限を考える。複数の Visa なら最小の期限 | MUST | `passport/ga4gh_passport_v1.md:998`<br>`passport/ga4gh_passport_v1.md:1043` | 本文 | ◯ | G-X-02 |
| PP-REV-01 | Clearinghouse | asserted・exp を必ず確かめ、期限までに(か少し後に)アクセスを止める仕組みを持つ | MUST | `passport/ga4gh_passport_v1.md:1060` | 本文 | ◯ | G-X-02 |

## 5. 数(道具が数えたもの)

| 仕様 | 行 | ◯ | △ | 後 | 外 | 決 |
| --- | --- | --- | --- | --- | --- | --- |
| DRS 1.5.0 | 36 | 27 | 5 | 1 | 3 | 0 |
| TES 1.1 | 26 | 22 | 0 | 1 | 1 | 2 |
| WES 1.1.0 | 24 | 22 | 0 | 0 | 0 | 2 |
| AAI 1.2・Passport 1.2 | 28 | 21 | 4 | 0 | 3 | 0 |
