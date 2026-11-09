# TES・WES のコードへの外部レビュー:写し・確かめ・仕分け・予想の評価

- 依頼: ユーザー依頼 10/16 08:54。レビューの担当。**読むだけで yai のコードは直していない**(直しは実装の担当に渡す)
- 対象: `623f2f3` の TES(`yai/tes/` の 7 ファイル、`schema/tes.sql`)、WES(`yai/wes/` の 7 ファイル、`schema/wes.sql`)、共通部品(`yai/common/` の 13 ファイル、`yai/aai/service_auth.py`、`yai/cli.py`)、設定例(`config/tes.example.yaml`・`wes.example.yaml`)、試験(`tests/test_tes.py`・`test_tes_restart.py`・`test_wes.py`・`stack.py`・`servers.py`・`conftest.py`・`shape.py`・`tokens.py`・`keyserver.py`・`tests/cases/cwl/` の 5 ファイル)。基準: `docs/design/tes.md`・`wes.md`・`system.md`、`docs/decisions.md`、`docs/data/` の状態の表 3 つと `paging.yaml`、`docs/spec-gaps.md`・`spec-obligations.md` の TES・WES の行
- 予想(送る前にコミット): `2026-10-16_review-prediction-tes-wes.md`(ef01587)

## 0. 呼び出しの記録

| 回 | 時刻(JST) | 結果 |
| --- | --- | --- |
| ① | 10/16 09:05〜09:07 | 22 件。前の回と同じく、上の 58 ファイル(`git show 623f2f3:<file>` に行番号)を依頼文に入れた。依頼文は約 374 KB(前の回の約 1.8 倍)。**「本文だけを使って」と書いた**。レビューは本文だけで見たと書いている。起動の失敗は無し |

注: 依頼文は再現できる(上のファイルを `623f2f3` から行番号つきで並べ、前置きに前提・見る観点・返し方を書いたもの)。大きいので入れていない。レビューは `jwtutil.py`・`clearinghouse.py` を本文に入れなかったので、そこでの型の確かめは見ていないと書いている(前の回で見たもの)。

## 1. レビューの返事(写し)

本文の静的レビューによる指摘です。ファイル参照・変更・試験実行はしていません。`jwtutil.py`・`clearinghouse.py` は本文にないため、そこでの型検査や `TokenError` への変換は確認できません。

1. **高 | yai/wes/resolver.py:34–56 | 【コード】| DRS の解決だけ取得先の許可表を通らず、SSRF が可能**
   
   利用者が指定した `drs://<host>/<id>` から直接 URL を作り、OPTIONS・GET を送っています。`passport_hosts` は Passport の送信だけを制限し、接続自体は制限しません。内部ホストや IP アドレスへの接続が可能で、設計の「外から取る道はすべて許可ホスト・ポートで制限する」を満たしません。`oid` も URL に直接連結されるため、`../`・問い合わせ・断片を使って要求先のパスを変えられます。
   
   **| 直し方:** DRS の解決にも共通の接続先検査を適用し、URI の構文を検査して ID をパス要素として符号化する。許可外ホスト・内部 IP・userinfo・パス操作を含む URI で、接続が一度も発生しない試験を追加する。

2. **高 | yai/tes/runner.py:109–119 | 【コード】| プロセス起動と PID 保存の間に落ちると executor が二重実行される**
   
   `Popen` 成功後、`record_start` が DB に PID を保存するまでに障害窓があります。この間に worker が落ちると、保存済みの phase は `starting` のままです。再起動した worker は既に動いているプロセスを発見できず、同じ executor を起動します。D-024 はプロセス方式を認めていますが、二重実行まで許容した決定ではありません。既存の `after_executor_start` は PID 保存後なので、この窓を試していません。
   
   **| 直し方:** 起動ごとの永続的な識別子と、wrapper 自身による起動記録・排他を用意し、`starting` から既存実行を照合できるようにする。`Popen` 直後・PID 保存前で停止し、作業領域外の追記回数が一回であることを確かめる試験を追加する。

3. **高 | yai/wes/engine.py:106–120、168–172 | 【コード】| 再起動後の再投入で nonce を作り直し、二重投入防止が失われる**
   
   `submitting` の復旧では保存済み nonce を一度探すだけで、見つからなければ `submit()` が新しい nonce を生成します。最初の CreateTask が処理中だった場合や、検索だけ通信に失敗した場合、旧 nonce と新 nonce の二つのタスクができます。yai-tes の一意制約でも異なる nonce は防げません。
   
   **| 直し方:** 復旧時も保存済み nonce を再利用し、「検索不能」と「検索成功・該当なし」を区別する。応答喪失後の再起動、検索失敗、遅延した最初の CreateTask を組み合わせ、TES タスクが一件になる試験を追加する。

4. **高 | yai/wes/engine.py:301–319 | 【コード】| 投入結果を確認できなくても CANCELED にし、タスクを取り残す**
   
   `tes_task_id` が未保存の取り消しでは、nonce 検索が一回 `None` を返すだけでループを抜けて CANCELED にします。`_find()` は通信失敗も `None` にするため、TES に作成済みのタスクや、まだ処理中の CreateTask が残っていても「全タスクが終わった」と扱います。
   
   **| 直し方:** 未確定の投入を永続的に追跡し、検索失敗や処理中の可能性が残る間は CANCELING を維持する。CreateTask の応答喪失中に CancelRun を送り、検索を一時失敗させても、後から見つかったタスクが終了するまで CANCELED にならない試験を追加する。

5. **高 | yai/wes/engine.py:325–332 | 【コード】| 完了と取り消しが競合すると、不正な状態遷移で取り消し処理が止まる**
   
   `watch()` の完了判定後に CancelRun が CANCELING を保存すると、`finish("COMPLETE")` は現在の CANCELING から COMPLETE へ遷移しようとします。DB は `False` を返す前に `ValueError` を投げるため、329 行以降の競合処理に到達しません。`_guard` の `fail("SYSTEM_ERROR")` も同じ理由で失敗します。CancelRun 側は既存スレッドが生きていると新しいスレッドを起動しないため、再起動まで CANCELING に残る競合があります。入力解決失敗と取り消しでも同型です。
   
   **| 直し方:** `finish()` の最初に CANCELING を扱い、期待した遷移元による条件付き更新に失敗したら状態を読み直す。完了・入力解決失敗の直前で停止し、その間に取り消して、必ず CANCELED に収束する試験を追加する。

6. **高 | yai/tes/worker.py:267–274 | 【コード】| 出力のコピーと DB 記録の間の障害から復旧できない**
   
   コピー成功後、`record_output()` 前に落ちると、置き場にはファイルがあるのに転送は未完了です。再起動後は同じ宛先への `O_EXCL` が失敗し、正常に実行できたタスクが SYSTEM_ERROR になります。コピー途中の障害では部分ファイルも残ります。既存の `after_output_copy` は DB 記録後なので、名前に反してこの障害窓を検証していません。
   
   **| 直し方:** 転送専用の一時ファイル・所有識別子・公開処理を使い、復旧時に自分の転送だけを照合して完了できるようにする。コピー途中とコピー完了後・DB 記録前の停止を追加し、部分出力を公開せず COMPLETE に復旧できる試験を追加する。

7. **高 | yai/tes/runner.py:36–40 | 【コード】| パスの逐次置換が、置換済みのホストパスを再び壊す**
   
   例えば `/d/out` と `/d` が宣言されていると、まず `/d/out` を `<fs_root>/d/out` にし、その中の `/d` を再度置換します。親子の宣言は `workdir` と出力で普通に発生します。また、ホスト側の `work_dir` 自体に宣言パスの文字列があれば、そこも書き換えます。これは D-024 の「実行中に組み立てたパスは対象外」とは別の、宣言済みパスが壊れる問題です。
   
   **| 直し方:** 元の文字列に対して最長一致の置換を一度だけ行い、挿入したホストパスは再走査しない。親子の宣言パスと、宣言パスを含む `work_dir` で argv が期待どおりになる試験を追加する。

8. **中 | yai/tes/worker.py:207–212、238–247 | 【コード】| `finished` の保存後に落ちると、実行エラーを忘れて COMPLETE にできる**
   
   `finished` を保存してから `result.error` を判定しています。時間超過や起動失敗の直後に落ちると、復旧処理は終了コードファイルが無い `None` を成功扱いして次へ進みます。また、終了コードファイルの公開は `os.replace` だけで永続化を保証していないため、ファイルが失われた場合も同様です。
   
   **| 直し方:** executor の終了結果を、エラー理由を含めて永続化し、復旧ではその結果を読む。終了コード不明を成功にしない。時間超過・起動失敗について、`finished` 保存直後に停止し、復旧後も SYSTEM_ERROR になる試験を追加する。

9. **中 | yai/tes/worker.py:297–308 | 【コード】| 保存した PID の生存だけで kill するため、別のプロセス群を止めうる**
   
   `executions` には終了済み executor の PID も残り、`finish_cancel()` は phase に関係なく処理します。PID が再利用されると、別のプロセス群に SIGTERM・SIGKILL を送る可能性があります。復旧時の `wait()` も PID の存在だけを見ているため、無関係なプロセスを実行中と誤認します。
   
   **| 直し方:** PID に加えてプロセス開始時刻や実行識別子を保存・照合し、終了結果を優先する。終了済み実行の PID が別プロセスを指す状況で、そのプロセスを待たず、停止もしない試験を追加する。

10. **中 | yai/tes/worker.py:277–278、301–320 | 【コード】| 終端状態を書いた後の障害で、秘密の消去と作業領域の削除が永久に抜ける**
    
    COMPLETE・CANCELED・エラー状態の保存後に `finish()` が動く構造です。その間に落ちると、復旧対象は非終端状態だけなので、署名付き URL の問い合わせ部分と作業ディレクトリが残ります。さらに終端状態を先に公開するため、通常時にも「終わったのに URL が未消去」の応答窓があります。
    
    **| 直し方:** 後処理の未完了を永続化し、終端タスクについても起動時に再開する。終端状態の保存直後に停止し、復旧後に URL が消去され、作業領域も削除される試験を追加する。

11. **中 | yai/wes/engine.py:78–87、224–230 | 【コード】| エラー終了後の CancelTask が永続化されず、再起動すると停止要求が消える**
    
    TES 到達不能の期限を超えると、先に run を SYSTEM_ERROR にしてからメモリ上で取り消しを続けます。ここで WES を再起動すると、終端 run は `recover()` の対象外なので CancelTask を再送しません。また `_guard` で捕捉した例外では、既に TES タスクが動いていても `fail()` だけで終わります。
    
    **| 直し方:** run の終端状態と独立した「停止確認待ち」を保存して復旧対象にする。TES 到達不能による SYSTEM_ERROR の後に WES を再起動し、TES 復帰後にタスクが停止する試験と、監視処理の例外時にも停止する試験を追加する。

12. **中 | yai/wes/engine.py:174–203、221–250 | 【コード】| 投入失敗と UNKNOWN の連続に終了条件がない**
    
    曖昧な CreateTask 失敗は、検索待ちを挟んで無期限に再送されます。30 分の到達不能上限は監視開始後だけです。また、HTTP 200 で `state=UNKNOWN` が続くと毎回 `unreachable_since` を消し、永久に RUNNING に残ります。UNKNOWN 連続は 30 分で同等に扱う設計にも反します。
    
    **| 直し方:** 投入・検索を含む到達不能期間と、UNKNOWN の連続期間を管理し、再起動でも継続できるよう保存する。常時 503 の CreateTask と、常時 UNKNOWN の GetTask で、期限後に所定のエラー・停止処理へ進む試験を追加する。

13. **中 | yai/wes/app.py:241、283–305 | 【コード】| 添付の容量上限がなく、添付ワークフローは 1 MiB 制限も迂回する**
    
    添付は全量を `read()` して保存し、個々のサイズ・合計サイズを検査しません。`MAX_ATTACHMENT_INPUT` は参照された File 入力だけの事後検査で、未使用添付には効きません。添付ワークフローは `MAX_DOC` を一切検査せず YAML として解析します。400 になった要求の保存済み添付も削除されません。multipart の解析段階で Passport が一時ファイルへ書かれる可能性については、依存版が本文にないため**推定**ですが、「メモリだけ」の保証も確認できません。
    
    **| 直し方:** 本文全体・添付一件・合計・文書の上限を読み取り中に適用し、失敗時に一時領域を削除する。文書を URL と添付で同じ制限にする。巨大・未使用・複数添付、拒否後の残骸、Passport のディスク非保存を確かめる試験を追加する。

14. **中 | yai/wes/app.py:288–329 | 【コード】| RunWorkflow に同期ファイル I/O と SQLite が残り、イベントループを止める**
    
    鍵取得と URL 取得はスレッドへ移されていますが、添付の `mkdir`・`write_bytes`、文書の読み書き、`stat`、`db.create_run()` は async 関数内で直接実行しています。SQLite のロック待ちは最大 30 秒なので、その間、同じプロセスの他の要求も進めなくなります。前回の修正型は WES に完全には適用されていません。
    
    **| 直し方:** multipart の非同期読み取りと同期の保存・検査・DB 処理を分け、後者をスレッドへ移す。DB ロック待ちや遅いファイル保存の間も status 要求が応答する試験を追加する。

15. **中 | yai/common/fetch.py:62–75、91–98、103–121 | 【コード】| 「全体の期限」が再試行ごとにリセットされ、待機中には効かない**
    
    `download()` は `_once()` ごとに期限を作り直すため、一回の取得全体が設定期限の約四倍とバックオフまで延びます。期限検査も chunk 受信後だけなので、10 秒制限の文書取得でも応答ヘッダーや次のデータを読み取り上限の 30 秒まで待ちます。executor の復旧待ちも開始時刻を現在に置き換えるため、再起動で実行期限が延びます（`worker.py:214–217`）。
    
    **| 直し方:** 操作開始時の締切を一度だけ作り、各接続・読み取り・再試行待ちへ残り時間を渡す。executor の開始時刻も保存して使う。遅いヘッダー、少量ずつの配信、複数回の失敗、実行途中の再起動で総所要時間が上限内になる試験を追加する。

16. **中 | yai/tes/model.py:126、172–191 | 【コード】| 型違い・壊れた URL が 400 でなく未処理例外になる**
    
    例えば `resources.backend_parameters=1` は `sorted(1)` で `TypeError`、`name={}` は SQLite の値束縛で例外になります。`https://host:bad/x` は `parts.port`、壊れた IPv6 URL は `urlsplit()` が `ValueError` を投げますが、受け口は `FetchError` しか捕捉しません。また `inputs=""`・`resources=[]` などの偽値は `or []`・`or {}` によって型検査を通過します。
    
    **| 直し方:** 省略・null・偽値を区別し、保存する既知項目の型を検査する。URL 解析例外も入力エラーへ変換する。上記の値と、配列・辞書・数値を各項目へ入れる試験で、500 や不正な成功応答にならないことを確かめる。

17. **中 | yai/wes/cwl.py:87–128 | 【コード】| CWL の対応外構文を拒否せず、型違いでは 500 になる**
    
    `inputs.x.type: [string, "null"]` は集合への所属判定で `TypeError`、`requirements: null` は反復で `TypeError`、辞書形式 requirements の値が文字列の場合も展開で例外になります。一方、入力定義の `valueFrom` など、DockerRequirement の `dockerFile` などは検査されず無視されます。対応外を 400 にする設計なので、無視した結果の別コマンド実行は範囲外です。
    
    **| 直し方:** 各階層の型と許可キーを検査してから処理し、対応外はすべて `CWLError` にする。nullable 型・null requirements・不正な辞書値と、未対応の入力定義・DockerRequirement 項目が 400 になる試験を追加する。

18. **中 | yai/wes/app.py:91–104、307–309 | 【コード】| CWL の default にある添付 File を解決できず、受け付け後に失敗する**
    
    添付への相対 location を変換する対象は `workflow_params` だけです。CWL の File 入力の `default` に `location: in.txt` があって params で省略すると、受付の `bind()` は成功しますが、実行時には `attachment:` に変換されておらず「unsupported location」で SYSTEM_ERROR になります。`default` と添付入力はどちらも対応範囲です。バイナリ添付も受付は成功し、後から `read_text()` で失敗します。
    
    **| 直し方:** default 適用後の入力に対して添付を解決する。バイナリを扱う方式を用意するか、対応外なら受付で明示的に拒否する。default の相対 File と非 UTF-8 添付で、受付と実行の判断が一致する試験を追加する。

19. **中 | config/wes.example.yaml:25、yai/wes/engine.py:153 | 【コード】| 設定例どおりに起動すると出力 URL が不正になる**
    
    設定例は `output_root: var/tes/files` ですが、エンジンは絶対化せず `file://var/tes/files/...` を生成します。これは netloc が `var` の URL になり、TES の `file_url_path()` に拒否されます。既存試験は絶対パスを設定するため、この設定例の失敗を検出しません。
    
    **| 直し方:** `output_root` を起動時に絶対パスへ正規化するか、絶対パス必須として設定例も修正する。例と同じ相対設定から出力付き run を投入する試験を追加する。

20. **中 | yai/tes/model.py:149–159、schema/tes.sql:28–33 | 【コード】| 同じ出力元から複数宛先への転送が一件に潰れる**
    
    検査は宛先の重複だけを拒否するため、同じ `path` を異なる URL へ出力する指定を受け付けます。しかし転送表の主キーは `(task_id, path)` で、二件目は `INSERT OR IGNORE` により消えます。結果として一部の宣言出力が置かれていなくても COMPLETE になります。同じ glob を二つの出力名で公開する CWL でも発生します。
    
    **| 直し方:** 出力の識別を path と URL の組などで行い、全宣言を追跡する。対応しないなら受付で重複元を拒否する。同一元・異なる二宛先について、両方が生成されるか受付で拒否される試験を追加する。

21. **中 | yai/wes/engine.py:140、yai/wes/app.py:316–320 | 【コード】| WES 側には署名付き URL の問い合わせ部分が永続的に残る**
    
    `workflow_url` と HTTPS File の location はそのまま `request_json` に保存され、終端後も消去されません。DRS 解決ログも元の `loc` は無加工なので、問い合わせを含む DRS URI を渡すと秘密が DB の system_logs に残ります。TES 側の消去処理だけでは、system.md の秘密の扱いを満たせません。
    
    **| 直し方:** 取得済み文書の URL は保存時に、実行に必要な入力 URL は終端時に問い合わせ部分を消去し、ログには元の location も伏せて書く。目印付き URL を workflow と入力へ渡し、終了後の RunLog・DB・ログに問い合わせ部分が残らない試験を追加する。

22. **低 | tests/test_tes_restart.py:58–65 | 【コード】| 作業領域内への追記だけでは、二重実行を検出できない**
    
    復旧時に INITIALIZING の作業ディレクトリを削除するため、一回目の追記が消えてから再実行されると、最終結果は一行になります。「最終ファイルが一行」は、executor が一回しか動いていない証拠になりません。また障害点は保存処理の後に偏り、起動・転送と記録の間を覆っていません。
    
    **| 直し方:** 削除されない観測先で実行回数を数え、外部操作の直前・直後と DB 記録の直前・直後を分けて障害を差し込む。再起動前後の総実行回数が一回であることを確認する試験を追加する。
## 2. 確かめと仕分け

仕分け: (a) 直す / (b) ユーザーに決めてほしい / (c) 直さない。「確かめ」はレビューの担当がコード(`623f2f3`)・設計で見たこと。手元の Python で試したものはそう書いた。

| # | 重さ | 要点 | 確かめ | 仕分け |
| --- | --- | --- | --- | --- |
| 1 | 高 | DRS の解決だけ許すホストの表を通らない(SSRF)。`oid` をそのままつなぐ | 正しい。`resolver.py:34-56` は `drs://<host>/` の host へ、表なしで OPTIONS・GET・POST を送る(`passport_hosts` は Passport を送る先だけ)。`tes.md:38` は「外から取る道はすべて共通の取り役を通す(…**DRS の解決**…)」。`oid` は `quote` せずに道へつなぐ(compact 型は `quote` 済み)。https だけ・応答は利用者に直接は返らないが、内側の https のホストへ要求を出させられる | (a) |
| 2 | 高 | `Popen` と PID の保存の間に落ちると executor が 2 度動く | 正しい。`run_executors` は `phase=starting` を書いてから `runner.run`(`worker.py:219-234`)、`record_start` は `Popen` のあと(`runner.py:119`)。その間に落ちると、上げ直しは `starting` を「まだ」と見て起動し直す。子は `start_new_session` で残る。差し込み点 `after_executor_start` は保存の**あと** | (a) |
| 3 | 高 | 再起動で nonce を作り直すので、二重の投入を防げない | 正しい。`drive` は保存した nonce で 1 回だけ `_find`(`engine.py:106-111`)、無ければ `submit` が新しい nonce(`:170`)。`_find` は TES に届かないときも `None`(`:205-209`)。`wes.md:61` の「無ければ出し直す」でも、**同じ nonce で出し直せば** yai-tes の一意の制約で二重にならない | (a) |
| 4 | 高 | 投入の結果が分からないのに CANCELED にする | 正しい。`cancel_all` は `tes_task_id` が無いと `_find` 1 回だけ、`None`(届かないときも)なら輪に入らず CANCELED(`engine.py:303-319`)。`wes.md:59` の「出している途中のものを含む全タスクが終わってから CANCELED」に反する | (a) |
| 5 | 高 | 完了と取り消しが重なると、ありえない移り方の `ValueError` で止まる | 正しい。`WesDb.transition` は表に無い移り方で `ValueError`(`wes/db.py:150-151`)。`finish` は `run.state`(CANCELING)から COMPLETE・SYSTEM_ERROR へ移ろうとして例外、`_guard` の `fail` も同じ例外でスレッドが死ぬ。CancelRun の `engine.start` は生きているスレッドを見て何もしないので、再起動まで CANCELING に残る | (a) |
| 6 | 高 | 出力の写しと記録の間で落ちると戻れない | 正しい(予想 P-04)。`copy_beneath` → `record_output`(`worker.py:267-274`)。上げ直しは同じ写し先に `O_EXCL` で失敗 → SYSTEM_ERROR。途中まで書いた写しも残る。`after_output_copy` は記録の**あと** | (a) |
| 7 | 高 | パスの置き換えを順にするので、置き換えたホストのパスをもう一度置き換える | **正しい(手元で再現)**。`PathMap(/w/t1/fs)` に `/yai/work`・`/yai/work/out.txt` を宣言すると、`rewrite("/yai/work/out.txt")` が `/w/t1/fs/w/t1/fs/yai/work/out.txt`。WES は workdir `/yai/work` と出力 `/yai/work/<glob>` を必ず宣言するので、**CWL の引数に出力のパスが来ると壊れる**。今の試験は入力のパスだけなので通っている | (a) |
| 8 | 中 | `finished` を書いたあとに落ちると、実行の失敗を忘れて COMPLETE に進む | 正しい。`set_execution(..., "finished")`(`worker.py:242`)のあとで `result.error` を見る。上げ直しは `phase=finished` で終了コードのファイルが無い(`None`)と「成功」として次へ(`:206-212` の `code not in (0, None)`)。時間切れ・起動の失敗でこうなる | (a) |
| 9 | 中 | 保存した PID が生きているかだけで止める(使い回しの別のプロセスを止めうる) | 正しい(予想 P-03)。`finish_cancel` は終わった executor も含め全部の PID に `kill_group`(`worker.py:297-300`)。上げ直しの `wait` も `pid_alive` だけ | (a) |
| 10 | 中 | 終わりの状態を書いたあとに落ちると、URL の消し・作業ディレクトリの削除が永久に抜ける | 正しい。`transition` → `finish`(URL の問い合わせ部分を消す・`rmtree`)の順。上げ直しは INITIALIZING・RUNNING だけを拾う(`worker.py:120`)。`system.md:153` の「タスクが終わったら消す」が落ちたときに守られない | (a) |
| 11 | 中 | エラーで終えたあとの CancelTask が保存されず、再起動で消える | 正しい。`watch` の届かない 30 分で SYSTEM_ERROR → メモリの中で `cancel_all(final=False)`(`engine.py:228-229`)。終わった run は `recover` の外。`_guard` の例外のときは `fail` だけで CancelTask を出さない。`wes.md:60`「CancelTask を出し続ける」に反する | (a) |
| 12 | 中 | 投入の失敗・UNKNOWN が続いても終わらない | 正しい。`submit` の「届いたか分からない」の輪に期限が無い(`engine.py:174-192`)。`TES_TO_RUN` に UNKNOWN が無く、200 で UNKNOWN が続くと RUNNING のまま。`state-map-tes-to-wes.yaml:15` は「UNKNOWN が 30 分続いたら tes_unreachable_timeout と同じ」 | (a) |
| 13 | 中 | 添付に上限が無い。添付のワークフローは 1 MiB の上限も通らない | 正しい(予想 P-12)。`write_bytes(await part.read())`(`wes/app.py:289`)、添付の文書は `names[rel].read_bytes()` で `MAX_DOC` を見ない(`:305`)。400 のときも `runs/<run_id>/` が残る。Passport が一時ファイルに書かれるかはレビューの**推定**で、`yai_passports` はファイルでない欄なので Starlette はメモリに置く(確かめていない) | (a) |
| 14 | 中 | RunWorkflow に同期のファイル I/O と SQLite が残っている | 正しい(予想の 41815c3 の表に書いたもの)。`mkdir`・`write_bytes`・`stat`・`cwl.parse`・`db.create_run` が async の中(`wes/app.py:283-330`) | (a) |
| 15 | 中 | 「全体の期限」が再試行ごとに数え直し、待ちの間は効かない | 正しい(予想 P-05・P-06)。`download` は `_once` ごとに `deadline`(`fetch.py:62-75`)、期限は塊が届いたときだけ見る。executor の時計も上げ直しで 0 から(`worker.py:214-217`) | (a) |
| 16 | 中 | CreateTask の型の違う値・壊れた URL で 500 | **正しい(手元で再現)**。`backend_parameters=1` は `sorted(1)` で `TypeError`、`https://h:bad/x` は `Fetcher.check` で `ValueError`(`FetchError` でない)。`name={}` は SQLite に dict を渡す。`inputs=""` は `or []` で検査を通り、保存した形は `""` のまま | (a) |
| 17 | 中 | CWL の部分集合の外を拒まない・型の違いで 500 | **正しい(手元で再現)**。`requirements: null` → `TypeError`、`type: [string, "null"]` → `TypeError`(unhashable)、`requirements: {DockerRequirement: x}` → `TypeError`。`run_workflow` は `CWLError` しか 400 にしない。入力の定義の知らない鍵・DockerRequirement の `dockerPull` 以外の鍵は黙って無視 | (a) |
| 18 | 中 | CWL の `default` の添付 File を解決できず、受け付けたあとに失敗 | 正しい。`attach_locations` は `workflow_params` だけを見る(`wes/app.py:91-104`)。`bind` は location の種類を見ないので受け付けは通り、`build_task` で「unsupported location」→ SYSTEM_ERROR。UTF-8 でない添付も受け付け後に `read_text` で失敗 | (a) |
| 19 | 中 | 設定例のままだと出力の URL が壊れる | 正しい。`config/wes.example.yaml:25` は `output_root: var/tes/files`(相対)。`engine.py:153` は `file://var/tes/files/...` を作り、`var` がホスト名になって TES が 400。試験は絶対パスを渡す(`tests/test_wes.py:98`) | (a) |
| 20 | 中 | 同じ path から 2 つの URL への出力が 1 件に潰れる | 正しい。検査は写し先の重複だけ(`model.py:157`)、`output_transfers` の主キーは `(task_id, path)`(`schema/tes.sql:33`)で 2 件目は `INSERT OR IGNORE` で消える。CWL で同じ glob を 2 つの出力にしても起きる | (a) |
| 21 | 中 | WES には署名付き URL の問い合わせ部分が残り続ける | 正しい。`request_json` に `workflow_url`・https の File の location をそのまま保存し、GetRunLog の `request` で返す(本人だけに見える)。DRS の解決の記録は元の `loc` をそのまま書く(`engine.py:140`)。`system.md:149-153` の決まりは TES の DB の分だけで、WES の DB・`RunLog.request` は書いていない | (b) |
| 22 | 低 | 作業ディレクトリへの追記だけでは二重の実行を見つけられない | **一部正しい**。今の 2 つの差し込み点では、`before_executor_start` は executor が動く前、`after_executor_start` は RUNNING で作業ディレクトリを消さないので、追記は残り検出できる(レビューの「消える」は今の点には当てはまらない)。ただし 2・6・8 の窓に差し込み点が無いのは正しい | (a)(試験だけ) |

数: 指摘 22 件。(a) 21 件(うち試験だけ 1 件)、(b) 1 件、(c) 0 件。誤り(外れ)は 0 件、一部外れ 1 件(22 の「消える」)。13 の Passport の一時ファイルは レビューも推定と書いており、確かめていない。

## 3. (a) の直し方の案(実装の担当向け)

同じ型が多いので、まとめて直す単位を書く。

| まとまり | # | ファイル(623f2f3) | 直し方の案 | 足す試験 |
| --- | --- | --- | --- | --- |
| A. WES の「分からない」と「無い」を分ける | 3・4・11・12 | `yai/wes/engine.py`(`drive`・`submit`・`_find`・`cancel_all`・`watch`)、`tes_client.py` | `_find` は届かないとき例外(または「分からない」)を返す。再起動の出し直しは**保存した nonce をそのまま使う**。取り消しは nonce の答えが「無い」と確かになるまで CANCELING のまま。`submit`・UNKNOWN の連続に `unreachable_limit_seconds` の期限。エラーで終えた run の「CancelTask を出し続ける」は `steps` に印を保存して `recover` で拾う | TES が止まった状態で再起動 → TES のタスクは 1 つ。投入の応答が消えた間に CancelRun → 後で見つかったタスクが終わるまで CANCELED にならない。常に 503・常に UNKNOWN で期限後に SYSTEM_ERROR |
| B. WES の終わり方の競合 | 5 | `yai/wes/engine.py:325-333`(`finish`) | 条件付き更新に失敗したら読み直し、CANCELING なら `cancel_all` に回す(`ValueError` を出す前に CANCELING を見る) | 完了の直前で止めて CancelRun → 必ず CANCELED |
| C. TES の実行役の再起動の窓 | 2・8・9・10・22 | `yai/tes/worker.py`・`runner.py`・`wrap.py` | `wrap` が起動のときに自分の PID と開始時刻を `log_dir` に書く(上げ直しは `starting` でもそれを見る)。結果(終了コード・`error`)を `finished` と同時に保存し、上げ直しは保存した結果で判定(終了コードが無いのを成功にしない)。止めるのは `phase=started` で、PID と開始時刻が合うものだけ。終わりの状態のあとの後片づけ(URL を消す・`rmtree`)は「済んだ」印を保存して、上げ直しで終わったタスクも拾う | 差し込み点を足す: `Popen` の直後・`finished` の直後・終わりの状態の直後。実行の回数は作業ディレクトリの外の観測先で数える |
| D. 出力の写し | 6・20 | `yai/common/safefs.py`(`copy_beneath`)、`yai/tes/worker.py`、`model.py`、`schema/tes.sql` | 一時の名前(タスクの ID 入り)に写して `rename`(出来上がったものだけ見える)。上げ直しは自分の一時ファイルを消してやり直す。同じ `path` の 2 つ目の出力は受け付けで 400(または主キーに URL を入れる) | 写しの途中・写しのあと記録の前で落とす → COMPLETE、途中のファイルが見えない。同じ path・2 つの URL が 400 |
| E. パスの置き換え | 7 | `yai/tes/runner.py:36-40`(`PathMap.rewrite`) | 元の文字列を 1 回だけ走査し、各位置で一番長い宣言に合えば置き換える(正規表現の選択を長い順に並べて `re.sub` 1 回) | workdir と出力が親子のとき、出力のパスを引数に入れた CWL が動く |
| F. 共通の取り役を DRS の解決にも | 1 | `yai/wes/resolver.py`、`yai/common/fetch.py` | DRS の解決の要求も `Fetcher.check`(許すホストとポート)を通す。`oid` は `quote(oid, safe="")`。応答の大きさにも上限 | 表に無いホスト・`drs://h/../x` で接続が 1 度も起きない |
| G. 期限は全体で 1 つ | 15 | `yai/common/fetch.py`、`yai/tes/worker.py` | `download` の初めに締切を 1 つ作り、各回の接続・読み取り・待ちに残り時間を渡す。executor の開始時刻を保存して上げ直しで使う | 少しずつ送る・失敗が続く取得元で全体が期限内。実行中に再起動しても時間切れの時刻が変わらない |
| H. 受け付けで型と上限を確かめる | 13・14・16・17・18・19 | `yai/wes/app.py`・`cwl.py`、`yai/tes/model.py`・`app.py`、`config/wes.example.yaml`、`yai/wes/engine.py` | WES: 本文全体・添付 1 つ・合計・数に上限、文書は URL・添付とも `MAX_DOC`、検査が通ってから保存(失敗なら消す)、同期の部分はスレッドへ。CWL: 各階層の型と許す鍵を確かめ、外は `CWLError`。`default` を当てたあとの File に添付の解決と location の種類の検査をし、UTF-8 でない添付は 400。TES: 省略・null・空を分けて型を確かめ、URL の解析の例外を 400 に。`output_root` は起動時に絶対パスに | 各値で 500 にならず 400。`default` の添付・バイナリの添付で受け付けと実行の判断が合う。設定例と同じ相対の `output_root` で出力つきの run が COMPLETE |

順番の提案: 安全・重複の実行に関わる A・C・F・E を先に、次に B・D、最後に G・H。

## 4. ユーザーに決めてほしいこと

- **(b) 21**: WES に残る署名付き URL の問い合わせ部分をどうするか。
  - 案 A(おすすめ): **TES と同じく、run が終わったら WES の DB の `request` の URL(`workflow_url`・File の `location`)の問い合わせ部分を消す**。記録(`system_logs`)には最初から問い合わせ部分を書かない。`system.md` 8 節に WES の行を足す。終わった run の GetRunLog の `request` は送った形と少し変わる(WES の仕様は `request` を「送られた要求」としているので、spec-gaps に 1 行足す)
  - 案 B: 記録(`system_logs`)だけ消し、`request` は送ったまま残す(本人だけが見える)。仕様の字面どおりだが、DB に秘密が残る
  - **答え: 案 A**(ユーザー 2026-10-16 11:43・13:27、D-030)。直した結果は 6 節の最後の行

## 5. 予想の評価

| 予想 | 結果 | 対応する指摘 |
| --- | --- | --- |
| P-01 stdout・stdin がリンクを辿る | 外れ | — |
| P-02 パスの部分文字列の置き換え | 一部当たり(同じ所を「置き換えたものをもう一度置き換える」として) | 7 |
| P-03 PID の使い回し・`Popen` と保存の間 | 当たり | 2・9 |
| P-04 出力の写しと記録の間 | 当たり | 6 |
| P-05 実行の時計が上げ直しで 0 から | 当たり | 15 |
| P-06 取得の期限が 1 回ごと | 当たり | 15 |
| P-07 `_find` が「届かない」を「無い」にする | 当たり | 3・4 |
| P-08 `cancel_all`・`submit` の輪に終わりが無い | 一部当たり(`submit` と UNKNOWN、エラー後の CancelTask) | 11・12 |
| P-09 GetTask の 404 を「届かない」扱い | 外れ | — |
| P-10 解決役が例外を `ResolveError` にしない | 外れ(解決役は SSRF の別の角度で 1) | — |
| P-11 受け付けで確かめず後で SYSTEM_ERROR | 一部当たり(`default` の添付・バイナリ) | 18 |
| P-12 multipart の上限・残骸 | 当たり | 13 |
| P-13 型の違う値(label・doc_name・exp) | 一部当たり(別の項目で) | 16・17 |
| P-14 CreateTask の配列の上限・パスの重複 | 一部当たり(同じ path の 2 つの出力) | 20 |
| P-15 run ごとのスレッドに上限なし | 外れ | — |
| P-16 YAML の別名の爆発 | 外れ | — |
| P-17 ListTasks が全部読む | 外れ | — |
| P-18 試験の不足 | 当たり | 22(と各指摘の試験の案) |
| (41815c3 の表)RunWorkflow の async の中の同期 I/O | 当たり | 14 |

- 予想 18 件: 当たり 7、一部当たり 5、外れ 6(前の回は 16 件で当たり 4・一部 2・外れ 10)
- 指摘 22 件のうち、予想に有ったもの 15 件(2・3・4・6・7・9・12・13・14・15・16・17・18・20・22)、**予想に無かったもの 7 件**(1・5・8・10・11・19・21)
- 外した型: **設計の一文と実装の突き合わせ**(1 は `tes.md:38` の「DRS の解決も共通の取り役」、11 は `wes.md:60`、12 の UNKNOWN は `state-map-tes-to-wes.yaml:15`、21 は `system.md` 8 節)、**状態の表の写し(`TRANSITIONS`)が例外を投げる道**(5)、**「記録したあと・判定する前」の窓**(8・10)、**設定例そのものを動かしていない**(19)。前の回で決めた 4 つの型は今回ほぼ当たった。次のレビューの予想では、設計の「〜はすべて」「〜し続ける」「30 分」のような文を grep して、コードの対応する所を 1 つずつ探す

## 6. 直した結果(実装の担当、2026-10-16)

順番は指示どおり A・C・F・E → B・D → G・H。B は A の取り消しの試験で必ず当たるので A と同じコミットにした。**同じ型を探して直した所**は表の 3 列目。(b) 21 はユーザーの答え待ちのため触っていない。

| まとまり(コミット) | # | 直したこと | 同じ型で一緒に直した所・直さなかった所 | 足した試験 |
| --- | --- | --- | --- | --- |
| A・B(`9e68fdd`) | 3・4・5・11・12 | nonce の探しは届かないとき None にしない(「無い」が `nonce_search_seconds` 続いて初めて無い。数え始めは初めて無いと分かった時)。再起動の出し直しは保存した nonce で。取り消しは「無い」と確かになるまで CANCELING。届かない・UNKNOWN の続く間を `steps.unreachable_since` に保存して 30 分で打ち切る。エラーで終えたあとの CancelTask は `steps.cancel_pending` に保存して `recover` で拾う(エンジンの例外でも)。`finish` は CANCELING を先に見る | 取り消しの輪の「TES の終わり」に PREEMPTED が無かった → `TES_TERMINAL` | `tests/test_wes_engine.py` 13 件(TES は偽物、DB は本物)。直す前のエンジンでは 9 件が落ちるか止まる |
| C(`592af7d`) | 2・8・9・10・22 | wrap が子を動かす前に起動の印 `logs/<i>/started`(PID・`/proc` の開始時刻・起動の時刻)を O_EXCL で書き、印があれば何も動かさない。上げ直しは `starting` でも印を見て見張る。executor の結果(`exit_code`・`error`)を `finished` と同時に保存し、終了コードが分からないのを成功にしない。終了コードのファイルは fsync。止める・待つのは印と同じプロセスだけ。入力の URL の問い合わせ部分は終わりの状態と同じ書き込みで消す。後片づけの前に落ちたら上げ直しで作業ディレクトリを消す | 上げ直しの見張りが stdout・stderr の末尾を宣言と違う場所から読んでいた。時間の上限を上げ直しから数えていた(15 の一部)。`kill_group` の SIGTERM と SIGKILL の間(0.3 秒)の PID の使い回しは現実的でないので直さない | 差し込み点 `before_executor_record`・`after_executor_finished`・`before_cleanup`。実行の回数は作業ディレクトリの外で数える。時間切れ・起動できないの上げ直し、後片づけ、URL の消し、PID の使い回し。印の確認と O_EXCL の両方を外すと二重の実行(2 行)を試験が見つけることを確かめた |
| F(`73f335e`) | 1 | DRS の API の URL(上書き表を当てたあと)を `Fetcher.check` で確かめてから接続。ID は `quote(oid, safe="")`。userinfo は不正。応答は 1 MiB まで | 取り役の外の通信は鍵の取得(信頼の表の URL)と TES(設定の URL)だけで、利用者が先を選べないので直さない | `tests/test_wes_resolver.py`(表に無いホスト・内側の IP に 1 度も接続しない、`../` が道を変えない、大きな応答) |
| E(`f9dce05`) | 7 | `PathMap.rewrite` は長い順の正規表現で元の文字列を 1 回だけ走査 | timeutil の `Z`→`+00:00`、resolver の `{id}` は置き換え後を見直さないので直さない | workdir と出力が親子のタスクが COMPLETE、`PathMap` の単体 |
| D(`349ac5f`) | 6・20 | 出力は `.<名前>.<task_id>.part` に写して fsync し link で公開(途中は見えない、上書きしない)。呼び直してよい。`output_transfers` の主キーを `(task_id, path, url)` に(古い DB は表を作り直す) | 入力の写し・取得は作業ディレクトリへの書き込みで、INITIALIZING の上げ直しで消して最初から取るので直さない | 差し込み点 `before_output_record`、途中の一時ファイル・公開のあとの呼び直し、同じ path から 2 つの宛先 |
| G(`aceee14`) | 15 | `download` の締切を 1 つにして各回・待ちに残りを渡す。読み取りの途中でも締切でソケットを shutdown。読み終わりにも締切を確かめる(切った接続は途中で終わって見える) | DRS の解決役も 1 要求ごとに全体の期限 | `tests/test_fetch.py`(少しずつ・ヘッダーが遅い・503 が続く、download・get_small)、DRS の少しずつ、executor の時間の上限が上げ直しで延びない |
| H(`3aff327`) | 13・14・16・17・18・19 | WES: 本文 40 MiB・添付 1 MiB・32 個(仮、wes.md 7 節の「仮」の行)、添付の文書も 1 MiB、検査が通ってから保存し失敗したら消す、残りの処理をスレッドへ。CWL の各階層の型と許す鍵。`default` の添付 File も受け付けで解決、解決できない location・UTF-8 でない添付は 400。`output_root` を絶対パスに。TES: 省略・null と偽値を分けて型を確かめ、壊れた URL は 400 | `urlsplit` の ValueError を `host_port` で FetchError に、`redact` も落ちない、AAI の `url_claim_ok`・鍵の URL の検査も 500 にしない。13 の Passport の一時ファイルは、Starlette がファイル名の無い欄をメモリにだけ置くことを `formparsers.py` で確かめた | TES の型違い 27 通り、CWL の型違い 6 通り、添付の上限と残骸、`default` の添付・バイナリ・ftp・壊れた IPv6、DB のロック中も status が 1 秒以内に応答、設定例の相対の `output_root` |

- 試験: 185 件 → 256 件(+2 件飛ばしは前から)。各まとまりで `make check`(ruff・pyright・specs・試験・義務 90/90)を通した
- 設計・決定は変えていない。実装に合わせて書き足したのは `wes.md` 5 節(`steps` の列)・7 節「仮・未決」の「仮」の行(受け付けの上限。コミット 3aff327 の文の「4 節」は 7 節の誤り)、`tes.md` 5 節(`executions` の列と起動の印)、設定例の注
- (b) 21(案 A、D-030): 終わりの状態と同じ書き込みで `request` の `workflow_url`・File の `location`・`path`(drs:// も)の問い合わせ部分を消す(`WesDb.transition`)。起動のときに前の版で終わった run も消す。`system_logs` は書く前に共通の消し役を通し、DRS の解決の記録は元の drs:// も伏せる。同じ型: TES の DB は前から同じ形(`TesDb` の終わりの書き込み)。WES の Python のログは前から共通の消し役を通る。spec-gaps に G-WES-12。試験は `test_wes_engine.py` の D-030 の節(終わり方 5 通り・負けた条件付き更新・記録・DRS の解決・古い DB)と `test_wes.py::test_finished_run_keeps_no_signed_url_query`
- 指摘を読んで「直さない」と判断したものは無い(22 の「消える」は一部外れだが、試験の差し込み点の不足は正しいので C で直した)
