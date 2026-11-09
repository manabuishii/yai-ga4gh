# yai-wes の設計(WES 1.1.0)

- 作成: 2026-10-15。前提: `system.md`・`aai.md`・`tes.md`。義務の ID は `docs/spec-obligations.md` 3 節
- WES は「エンジンを動かす口」。yai-wes は 3 つの部品からなる:WES の API、エンジン、DRS の解決役(`system.md` 3 節の (a))

## 1. 範囲

| | 最初の「動く最小版」 | あとで深める |
| --- | --- | --- |
| 操作 | service-info、RunWorkflow、ListRuns、GetRunLog、GetRunStatus、ListTasks、GetTask、CancelRun、stdout・stderr の口(yai 独自、C-08) | — |
| エンジン | **D-014(決定、ユーザー 2026-10-16 00:02)**: yai 内蔵の小さなエンジン。`workflow_type=CWL`・`workflow_type_version=v1.2` の **CommandLineTool 1 つ**を TES タスク 1 つへ写す。対応する CWL は小さな部分集合(下)。**直列の `Workflow` は、CommandLineTool 1 つで E2E-01 が通ったあと**に足す(段階 2 の中) | 既存のエンジン(cwltool・Toil・Nextflow の TES 対応のどれか)。選ぶ前に「エンジンから WES のタスク情報が取れるか」の小さな試し(SPIKE-ENGINE) |
| 入力 | `workflow_params` の `File` の `location` に `drs://`・`https://`。添付(`workflow_attachment`)のファイル | `File` の配列、ディレクトリ |
| 資格情報 | multipart の yai 独自の項目 `yai_passports` で Passport を預かる(D-009)。run が終わったら消す | 長い run のための取り直しの口 |
| TES | yai-tes だけ(設定の 1 つの URL) | 相互運用の相手の TES(Funnel など)に流す |
| 出力の受け取り | `outputs` の `location` は TES の出力の `file://` の URL。**利用者は同じ機械の上でそのパスを読む**(D-002 の「自分だけ」が前提、自己レビュー R-02) | WES の口から出力を取れるようにする(yai 独自)か、出力を置く先を https にする |

**最小版の CWL の部分集合**(エンジンの作りを小さくするため。名前は CWL の正式なもの。レビューで具体にした)

- 受ける文書: `cwlVersion: v1.2`、`class: CommandLineTool` の 1 つの文書か、下の直列の `Workflow`(`$graph`・`$import`・`$include`・`run` の参照は無し)
- 受ける項目: `id`・`label`・`doc`(無視)、`baseCommand`(文字列か文字列の配列)、`arguments`(文字列だけ)、`inputs`(型は `File`・`string`・`int`。`default` は可。`inputBinding` は `position`(整数)と `prefix` だけ)、`outputs`(型は `File`。`outputBinding.glob` はワイルドカードなしのファイル名 1 つ)、`stdout`・`stderr`(ファイル名)、`requirements` の `DockerRequirement.dockerPull`(必須)
- コマンドの作り方: argv = `baseCommand` + (`arguments` と `inputBinding` を持つ入力を `position` の昇順、同じ `position` は `arguments` が先・入力は名前の順)。`prefix` は別の要素(`separate: true` の既定どおり)。`File` は TES の入力の `path`(`/yai/inputs/<入力名>/<basename>`)に置き、その path を argv に入れる。シェルを通さない
- 出力の判定: 作業ディレクトリ(`/yai/work`)の `glob` のファイル。無ければ EXECUTOR_ERROR(executor は 0 で終わったが、CWL の出力が無い。D-026)。**見分け方**: WES から TES の作業ディレクトリは見えないので、TES の報告で判定する。TES のタスクは出力の転送の失敗として SYSTEM_ERROR のまま(TES-ST-02)。yai-tes は「写す元のファイルが無い」ときだけ `tesTaskLog.metadata` の `yai.missing_outputs` に出力の `path` を(「,」区切りで)書く。WES は「TES が SYSTEM_ERROR・executor が全部 0 で終わった・宣言した出力の `path` が `yai.missing_outputs` にある」ときに run を EXECUTOR_ERROR にする。書けない置き場・リンクなどの転送の失敗、起動・入力の取得の失敗、この印を書かない他の TES は SYSTEM_ERROR のまま
- 受けないもの(400): 上に無い項目・型・`requirements`(`hints` は無視して受ける)、`$namespaces` の拡張、`InlineJavascriptRequirement`・`ShellCommandRequirement` など
- **直列の `Workflow`**(段階 2 の下準備で足した、D-033): `class: Workflow` の 1 つの文書。受ける項目は `inputs`(型は `File`・`string`・`int`、`default` 可。`inputBinding` は無し)、`steps`、`outputs`(型は `File`、`outputSource` は `<段>/<出力>`)、`id`・`label`・`doc`・`hints`(無視)。`requirements` は空だけ(各段のツールに書く)
  - `steps` は対応表か id 付きの配列。**書いた順に 1 段ずつ**動かす(段 1 つ = TES タスク 1 つ、`TaskLog.id=<run_id>.<n>`、`name`=段の名前)
  - 各段の `run` は**埋め込みの CommandLineTool だけ**(上の部分集合。`cwlVersion` は省いてよい)。別の文書の参照・子の `Workflow`・`scatter`・`when` は 400
  - `in` は `{ツールの入力: 出どころ}`(か `{source: …}`)。出どころはワークフローの入力の名前か、**それより前の段**の `<段>/<出力>`(前を向く参照・循環は 400)。型が合わなければ 400。出どころの無いツールの入力は `default` が要る。`out` はツールの出力の名前の配列
  - 段の間の受け渡し: 前の段の TES の出力の `file://` の URL を、次の段の `tesInput.url` にそのまま入れる(同じ `yai.run_id`・`yai.on_behalf_of` なので C-25 で通る)。TES の出力の置き場は `wes/<run_id>/<n>/<出力>/<glob>`(段の番号を入れて、同じ出力の名前でもぶつからない)
  - 受け付けでは、前の段の出力の代わりに印(`yai-step:<n>/<glob>`)を入れて全段の argv が組めることまで確かめる。`drs://` は**その段を出す直前**に解決する。再起動で Passport が消えたときに SYSTEM_ERROR になるのは、**これから出す段に `drs://` の入力があるとき**だけ(E2E-09 の読み)
  - どれかの段が終わりの状態(COMPLETE 以外)になったら、そこで run を終える(次の段を出さない)。取り消しは出した全段に CancelTask
- **確かめ方**: 同じ例(`tests/cases/cwl/`)を cwltool でも動かし、argv と出力の中身を比べる(CWL-PARITY)。「CWL に適合」とは言わず、service-info の tags に部分集合と書く

## 2. 操作と義務

| 操作 | 最小版の中身 | 義務(ID) |
| --- | --- | --- |
| GetServiceInfo | `workflow_type_versions={"CWL": {"workflow_type_version": ["v1.2"]}}`、`supported_wes_versions=["1.1.0"]`、`supported_filesystem_protocols=["drs","https"]`、`workflow_engine_versions={"yai-mini": {...}}`、`default_workflow_engine_parameters=[]`、`system_state_counts`(本物の数)、`auth_instructions_url`(リポジトリの `docs/` を指す)、`tags={"yai.cwl_subset": "docs/design/wes.md 1 節の部分集合だけ"}`(CWL の全部に対応すると誤解されないように) | WES-SI-01、C-14 |
| RunWorkflow | multipart を読む → `workflow_params` を JSON として読む(必須)→ 種類・版・エンジンの検査 → 添付を run のディレクトリへ(名前の検査)→ **ワークフローの文書をその場で取って(`workflow_url` が絶対 URL なら共通の取り役で、10 秒・1 MiB まで)run のディレクトリに保存し、部分集合の検査をする(対応外は 400)。実行には保存したものだけを使う**→ `yai_passports` を取り出して形と期限だけ検査し、別の表へ(`RunRequest` には入れない) → QUEUED で保存 → `run_id` | WES-RUN-01〜07、WES-AUTH-01〜03、G-WES-02 |
| ListRuns | 本人の run だけ、`RunSummary` で、並びは `(created_at 降順, id 降順)`、ページの印は `paging.yaml` | WES-LST-01〜05 |
| GetRunLog | `request`(Passport は入らない)、`state`、`run_log`、`task_logs_url`(絶対 URL)、`outputs`。`task_logs` は出さない | WES-LOG-01〜03、C-18 |
| GetRunStatus | `run_id`・`state` | |
| ListTasks・GetTask | ステップごとの `TaskLog`(`id=<run_id>.<n>`、`name`=ステップ名、`cmd`、時刻、`exit_code`、`stdout`・`stderr` は yai の URL、`tes_uri`、`system_logs` の先頭に `state=<TES の状態>`) | WES-TSK-01・02、C-07〜10・12 |
| CancelRun | 本人だけ。終わったものは 200 で何もしない。それ以外は CANCELING → 全タスクに CancelTask → 全部が終わったら CANCELED | WES-CAN-01、C-20 |

- 添付のファイル名: `..`・絶対パス・空の名前・NUL を拒否(400)。展開先は `var/wes/runs/<run_id>/attachments/` の下に限る(WES-RUN-02)
- `workflow_url` が絶対 URL なら `https` だけ、許すホストは TES と同じ設定(SSRF の予防)

## 3. 認可の点(4 つの役との対応)

| 点 | R1 | R2 | R3 | R4 |
| --- | --- | --- | --- | --- |
| すべての操作 | AT(`aud=yai-wes`) | 利用者 | `permissions.yaml`。他人の run は 404 | — |
| RunWorkflow の Passport | Passport の形と期限だけ(早めに失敗させるため)。**判定は DRS がする** | — | — | 預かる |
| DRS の解決 | — | — | — | Passport を DRS の本文 `passports` に入れる(利用者の代わり)。**送ってよい先の検査**(`system.md` 4 節 ⑤⑥、DRS-AUTH-07) |
| TES へ出す・見張る・取り消す | — | — | — | WES のサービスの AT(client_credentials、scope は `tes.submit tes.read tes.cancel tes.delegate` の 4 つ)+tags `yai.run_id`・`yai.on_behalf_of`・`yai.submit_nonce` |

## 4. 状態

- 移り方の表: `docs/data/wes-states.yaml`。TES の状態からの写し方: `docs/data/state-map-tes-to-wes.yaml`
- エンジンの進め方(最小版):
  1. QUEUED → INITIALIZING: 受け付けのときに保存した文書と入力を読む(文書の誤りは受け付けで 400 になっているので、ここでは起きない)
  2. 入力の `drs://` を解決する(`/access` → 署名付き URL)。**TES に出す直前に解決する**(署名付き URL の期限を短く保つため、`system.md` 5 節)
  3. **TES に出す前に意図を保存する**: `steps` に `phase=submitting` と、その場で作った `submit_nonce` を書き、CreateTask の tags に `yai.submit_nonce` を付ける。成功したら `tes_task_id` を保存して `phase=submitted`。応答が分からないまま失敗したら、出し直す前に ListTasks(**`tag_key=yai.submit_nonce` と `tag_value=<nonce>` の両方**。値を省くと別の run のタスクにも一致する。全ページを辿る)で探す。見つからなくても、最初の要求がまだ処理中かもしれないので、**60 秒の間 10 秒ごとに探し直してから**出し直す。yai-tes は同じ主体・同じ `yai.submit_nonce` の CreateTask を 2 度目は新しく作らず、前の `id` を返す(一意の制約)ので二重にならない。**外の TES ではこの保証は無い**(TES 1.1 に冪等の決まりが無い)ので、探し直しの範囲でだけ防ぐ
  4. RUNNING。2 秒ごとに GetTask(`view=FULL`)で見張る(仮)
  5. タスクが COMPLETE → `outputs` に CWL の出力の形(`location` は TES の出力の URL)を書いて COMPLETE
- **取り消し**(`wes-states.yaml`): CANCELING にしたら新しいタスクを出さない。出している途中の CreateTask は結果(か `submit_nonce` での探し)を待ってから取り消す。CancelTask が届かなければ 10 秒ごとに出し直し、再起動のあとも出し直す。全タスク(出している途中のものを含む)が終わった状態になってから CANCELED。2 度目の CancelRun は 200 で何もしない
- **エラーで終わるとき**も、まだ動いているタスクに CancelTask を出す(WES の外で動き続けないように)。TES に届かない間は run を終わらせず、30 分続いたら SYSTEM_ERROR にして CancelTask を出し続ける
- 再起動のとき: 終わっていない run について、`steps` の意図から続きを決める。`submitted` は GetTask で見張り直す。`submitting` は `submit_nonce` で探し、見つかればそれを使い、無ければ出し直す。Passport の表は再起動で消える(メモリだけ、下) → これから DRS の解決が要るものは SYSTEM_ERROR(「再起動で資格情報が失われた」)

**プロセスの形(最小版)**: API とエンジンは**同じプロセス**(エンジンは FastAPI の中の非同期の仕事)。**`--workers 1`・自動の再読み込みなしを強制**し、起動時にロックのファイルを取る(2 つ目が起動したら止まる)。Passport をメモリだけに持つので、受け取ったプロセスがそのまま使う必要がある。別のプロセスに分けるのは「深める」(Passport の保存のしかたと一緒に決める)。

## 5. データの持ち方

| 物 | 場所 | 中身 |
| --- | --- | --- |
| run | SQLite `var/wes/wes.sqlite` | `runs(id, owner_iss, owner_sub, state, request_json, created_at, start_time, end_time, outputs_json, version)` |
| ステップ | 同上 | `steps(run_id, n, name, phase, submit_nonce, tes_task_id, state, cmd_json, exit_code, start_time, end_time, cancel_sent_at, unreachable_since, cancel_pending)`。`unreachable_since` は TES に届かない・UNKNOWN が続き始めた時刻(30 分を再起動をまたいで数える)、`cancel_pending` は run が終わったあとも CancelTask を出し続ける印(再起動で拾う) |
| ワークフローの文書 | `var/wes/runs/<run_id>/workflow/` | 受け付けのときに取って保存したもの(実行はこれだけを使う) |
| 添付 | `var/wes/runs/<run_id>/attachments/` | |
| Passport | **メモリだけ**(`run_id → Passport`)。DB・ログ・`request_json` に入れない。run が終わったら消す | C-18・C-19 |

## 6. 誤りの返し方

- `ErrorResponse {msg, status_code}`。状態コードは各操作の OpenAPI の一覧にあるものだけ(例: RunWorkflow で無い資源を指しても 404 は使わず 400)
- **例外**: ListTasks の壊れた・改ざんされた `page_token`、範囲の外の `page_size` は、一覧に無い **400** を返す(ListRuns と同じ。G-WES-11、D-027、ユーザー 2026-10-16 08:52)
- 部分集合の外の CWL・対応外の `workflow_type`・版は 400

## 7. 仮・未決

- **決定(D-014、ユーザー 2026-10-16 00:02)**: 最小版のエンジンは上の「yai 内蔵・CWL の小さな部分集合」。理由: 4 つまとめた試験に最短で入れる/`workflow_type=CWL` なので、相互運用の試験で同じ CWL を他の WES(Sapporo・Toil など)にも出せる/既存のエンジンの TES 対応を調べて動かす手間(SPIKE-ENGINE)を「深める」段階に回せる。代わりの案: 最初から cwltool などの既存のエンジンを使う(作る量は減るが、TES に流す経路とタスク情報の取り方を先に調べる必要がある)
- 仮: 見張りの間隔 2 秒、`page_size` の既定 100・上限 1000、Passport はメモリだけ、RunWorkflow の上限(本文全体 40 MiB・添付 1 つ 1 MiB・添付 32 個。添付は文書か `content` の入力にしか使わず、どちらも 1 MiB まで。添付の文書も URL と同じ 1 MiB。検査が全部通ってから保存する)
- 参考(Galaxy v26.1.1 を読んだ結果、`docs/research/2026-10-15_galaxy.md`): Galaxy の WES は `workflow_type` が Galaxy 独自の 2 種類だけで CWL を受けない。つまり **yai-wes のクライアントから Galaxy の WES に同じワークフローは出せない**(相互運用の試験は「yai のクライアント → Galaxy の WES」で、Galaxy のワークフローを使う形になる)
