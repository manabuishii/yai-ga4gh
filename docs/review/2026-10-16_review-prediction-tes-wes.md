# TES・WES のコードへの外部レビューの前の予想(2026-10-16、レビューに送る前に書いた)

- 書いた日時: 2026-10-16 09:0x JST(レビューの担当)。**このファイルのコミットがレビューに送るより前であることが、予想を後から書き換えていない証拠**
- 依頼: ユーザー依頼 10/16 08:54。レビューの担当は読むだけで、yai のコードは直さない
- 対象: `623f2f3` の `yai/tes/`(app・db・model・runner・worker・wrap)、`yai/wes/`(app・cwl・db・engine・resolver・tes_client)、`schema/tes.sql`・`wes.sql`、共通部品(`yai/common/` の fetch・safefs・paging・config・fakes ほか)、試験 `tests/test_tes.py`・`test_tes_restart.py`・`test_wes.py` と試験の道具。基準は `docs/design/tes.md`・`wes.md`・`system.md`、`docs/decisions.md`(D-014・D-024・D-026・D-027 ほか)、`docs/spec-gaps.md`
- 回数: 1 回だけ。本文を依頼文に入れ、依頼文の中の本文だけを使うように書く
- 評価のしかた: 返事のあとで各予想を「当たり / 一部当たり / 外れ」に分け、予想に無かった指摘も数える(`2026-10-16_external-review-tes-wes.md`)
- 材料: 前の回(AAI・DRS)の 5 節で「次は 4 つの型を先に自分で探す」と決めた。**型の違う値で出る未処理の例外 / 期限・上限の「1 回ごと」と「全体」/ 設定・資源の上限 / 試験が決まりを本当に通っているか**。それに TES・WES に特有の「安全(リンク・パス)」と「再起動」を足した

## 41815c3 の直し(同じ型)が TES・WES で本当に直っているか — 送る前にレビューの担当が見たこと

| 前の回の # | 型 | 623f2f3 での様子(レビューの担当が読んだだけ) |
| --- | --- | --- |
| 1 | 知らない `auth.mode` は起動しない | TES は `auth_mode(config, ("aai","anonymous","static-bearer"))`(`tes/app.py:67`)、WES は `("aai","anonymous")`(`wes/app.py:164`)。直っている |
| 2 | async の中の同期 I/O | TES は受け口が全部 `def`。WES は RunWorkflow だけ `async` で、認証と workflow_url の取得はスレッドへ。**ただし添付の書き込み・`cwl.parse`(YAML)・`db.create_run`(SQLite)は async の中のまま**(`wes/app.py:283-330`)。`task_output` の TES 呼び出しは `def` の中なので良い |
| 6 | SQLite の接続を閉じる | `TesDb.connect`・`WesDb.connect` は `finally: db.close()`。直っている |
| 9 | 資格情報の数・長さ | WES の `yai_passports` に 10 個・64 KiB(`wes/app.py:75`)。**本文全体・添付の数と大きさ・CreateTask の配列の数には上限が無い** |
| 10 | 文字列どうしの `compare_digest` | `StaticBearerAuthenticator` は bytes で比べる(`common/fakes.py`)。直っている |

## 予想

確からしさ: 高・中・低。予想した時点のレビューの担当の見立て(コードは直さない)も添える。

| # | ファイル | 予想する指摘 | 確からしさ | 見立て |
| --- | --- | --- | --- | --- |
| P-01 | tes/runner.py `LocalProcessRunner.run` | executor の `stdout`・`stderr`・`stdin` を、タスクの根 `fs/` の下で**ふつうの `open()`**(リンクを辿る)で開く。前の executor が `fs/` の中にリンクを置けば、次の executor の stdout が根の外へ書かれる・stdin が根の外を読む。出力の写し(`copy_beneath`)だけがリンクを辿らない | 高 | 直す側(`open_beneath` を使う)。ただし D-024 で隔離が無いと決めているので、重さは「根の外」の意味しだい |
| P-02 | tes/runner.py `PathMap.rewrite` | 宣言したパスの**部分文字列**をそのまま置き換える。`/in` を宣言すると `/input/x` の中も置き換わる。env の値・利用者の文字列の引数も置き換わる | 中〜高 | 直す側(要素の境目で置き換える)か、D-024 の「写し方」に境目の決まりを書き足す |
| P-03 | tes/runner.py・worker.py(再起動) | 再起動のとき、保存したプロセスの番号で `pid_alive`・`kill_group` する。**番号が別のプロセスに使い回されていると、知らないプロセスを待つ・止める**。`Popen` と `record_start` の間で落ちると子が残り、もう一度起動される | 中〜高 | D-024 で「番号と終了コードのファイル」と決めた。弱いところとして記録(直すならプロセスの開始時刻も保存して照らす) |
| P-04 | tes/worker.py `upload_outputs` | `copy_beneath` のあと `record_output` の前に落ちると、上げ直したときに写し先がもう有る(O_EXCL)ので SYSTEM_ERROR になる(成功したタスクが失敗に)。途中まで書いた写しも残る。障害の差し込み点 `after_output_copy` は `record_output` の**あと**なので試験されていない | 中 | 直す側(一時の名前に写して rename、または「自分が書いた途中のもの」を見分ける) |
| P-05 | tes/runner.py `wait`・worker.py `resume` | `max_runtime_seconds` の時計が、上げ直すと 0 から数え直しになる(`int(time.time())` を渡す)。executor ごとの上限で、タスク全体の上限が無い | 中 | 直す側(開始時刻を保存して使う) |
| P-06 | common/fetch.py `download` | `total_timeout` が 1 回の取得ごとで、再試行(最大 4 回)と待ち時間を足すと全体は数倍になる。期限は塊が届いたときにしか見ないので、読み取り 30 秒の待ちの分は越える | 中 | 直す側か、tes.md の書き方を「1 回ごと」に |
| P-07 | wes/engine.py `drive`・`cancel_all`・`_find` | `_find` は TES に届かないときも `None`(見つからない)を返す。再起動の直後に TES が止まっていると「出していない」と見なし、**新しい nonce で出し直して TES のタスクが 2 つ**になる。取り消しでも `task_id` が `None` のまま CANCELED にし、TES のタスクが動き続ける | 高 | 直す側(「分からない」と「無い」を分ける) |
| P-08 | wes/engine.py `cancel_all`・`submit` | `cancel_all` の輪に終わりが無い。TES が届かない・タスクが 404・PREEMPTED(WES の `TERMINAL` に無い)のとき、スレッドが回り続ける。`watch` の「TES unreachable」で `cancel_all(final=False)` を呼ぶと、TES が戻らない限り抜けない。`submit` の「届いたか分からない」の再試行にも上限が無い | 高 | 直す側(全体の期限) |
| P-09 | wes/tes_client.py `get` | GetTask の 404(タスクが無い)も「届かない」と同じ扱いで、30 分待ってから SYSTEM_ERROR | 低〜中 | 直す側 |
| P-10 | wes/resolver.py・engine.py | `resolve` が `httpx` の通信の失敗・`resp.json()` の失敗・形の違う応答(`obj` が dict でない・`access_id` が文字列でない)を `ResolveError` にしない。`drive` の `except (ResolveError, CWLError)` を抜けて「internal error in engine」になる(状態は SYSTEM_ERROR で同じだが、理由が消える)。DRS の応答の大きさに上限が無い | 中 | 直す側 |
| P-11 | wes/app.py `run_workflow`・cwl.py | 受け付けで確かめず、あとで SYSTEM_ERROR になるもの: File の `location` の種類(`file:` など)、https の入力の許すホスト、`attachment:` を利用者が直に書いたとき(1 MiB の上限を通らない、無い名前)、添付が UTF-8 でない、CWL の入力・出力の名前(`id`)に `/`・`..`(TES のパス・`file://` の URL に入る)。WES-RUN の「受け付けで 400」と合わない | 高 | 直す側(受け付けで同じ検査) |
| P-12 | wes/app.py `run_workflow` | multipart の本文全体・添付の数・1 つの大きさに上限が無い(`await part.read()` で全部メモリへ)。添付を検査の前に書くので、400 のときも `runs/<run_id>/` が残る。同じ名前の添付は上書き、`a` と `a/b` があると 500 | 中〜高 | 直す側 |
| P-13 | wes/app.py・cwl.py | 型の違う値: CWL の `label` が文字列でない(`step_name` → SQLite で 500)、workflow_url のパスの最後が `..`(`doc_name` が `safe_name` を通らない)、`exp` が bool(`isinstance(True, int)`) | 中 | 直す側 |
| P-14 | tes/model.py `validate_task` | CreateTask の配列(executors・inputs・outputs・volumes)の数・本文の大きさに上限が無い。入力どうしのパスが同じ・入力と出力のパスが同じ・stdout と出力が同じは受け付けて、実行のときに失敗する | 中 | 直す側 |
| P-15 | wes/engine.py・app.py | run ごとにスレッドを 1 本立て、上限が無い(QUEUED の run を並べると全部同時に TES へ) | 低〜中 | 段階 1 の割り切りか、上限を設定に |
| P-16 | cwl.py `parse` | `yaml.safe_load` に別名(alias)の爆発への備えが無い(1 MiB でも大きく膨らむ) | 低 | 直す側(JSON だけにする・別名を禁止) |
| P-17 | tes/app.py `list_tasks` | 1 ページのたびに見えるタスクを全部読み、タグの絞り込みをあとでする | 低 | 段階 1 は直さない寄り |
| P-18 | 試験 | 予想した弱いところの試験が無い(リンク越しの stdout、出力の写しの途中で落ちる、TES が止まっているときの再起動と取り消し、受け付けで落とすべき CWL) | 中 | 直す側(試験を足す) |

## 予想しなかったら外しそうなところ(前の回の学び)

- 前の回は「コードの境目の値」が中心で、予想に無かった指摘が 13 件中 8 件。今回も TES・WES の OpenAPI の細部(`tesTask` の必須項目、`RunLog`・`TaskLog` の形、`system_state_counts` など)と、`docs/data/*-states.yaml` と `TRANSITIONS` の写しの違いで来ると見る(どれが来るかは予想できないので、個別には書かない)
