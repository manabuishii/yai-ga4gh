# yai-tes の設計(TES 1.1)

- 作成: 2026-10-15。前提: `system.md`・`aai.md`。義務の ID は `docs/spec-obligations.md` 2 節
- 学習用で自分だけが使う(D-002)ので、実行の隔離は最小限。ただし下の「最小の安全」は最小版から入れる

## 1. 範囲

| | 最初の「動く最小版」 | あとで深める |
| --- | --- | --- |
| 操作 | service-info、CreateTask、ListTasks(絞り込み・ページング・view)、GetTask(view)、CancelTask | — |
| 実行先 | 手元の Docker(1 台)。同時実行は 2(設定)。**段階 1 は手元のプロセス(D-024、ユーザー 2026-10-16 00:15)**: 実行役の差し込み口 `Runner`(`yai/tes/runner.py`)の先に置き、Docker の実行役はあとで同じ口に差し込む。パスの写し方・はずれる点・保留する試験は D-024。**Docker の実行役は段階 2 の下準備で書いた**(`yai/tes/docker_runner.py`、設定 `runner.kind: docker`、既定は手元のプロセスのまま。つなぐ先・パス・持ち主は D-034。段階 2 から手元の rootless Docker で動かす、D-037) | 別の実行先(Slurm など)、相互運用の相手の TES へ流す |
| 入力 | `https`(署名付き URL を含む)と `content`。`file://` は決められた根の下だけ | `drs://` を TES 自身が解決する(独自の拡張、D-007 の案 (b))、`streamable` |
| 出力 | `file://` の決められた根の下。ワイルドカードなし | ワイルドカードと `path_prefix`(TES-CRT-06) |
| executor | 複数可、順に実行、`ignore_error`、`workdir`・`env`・`stdin`・`stdout`・`stderr` | — |
| 資源 | `cpu_cores`・`ram_gb` を Docker の上限に写す。`disk_gb`・`zones`・`preemptible` は受けて無視(service-info に書く) | `disk_gb` の上限 |
| `backend_parameters` | 対応するキーは 0 個。来たら警告し、保存も返却もしない。`strict` なら 400(TES-CRT-07) | — |
| 状態 | `PAUSED`・`PREEMPTED` は使わない(G-TES-10) | — |

## 2. 操作と義務

| 操作 | 最小版の中身 | 義務(ID) |
| --- | --- | --- |
| GetServiceInfo | `type.artifact=tes`、`storage=["file:///…/outputs", "https://"]`、`tesResources_backend_parameters=[]` | TES-SI-01 |
| CreateTask | 形の検査 → 絶対パスの検査 → `yai.` の tags の検査 → 保存(QUEUED)→ `id` を返す。読み取り専用の項目は捨てる。同じ主体から同じ `yai.submit_nonce` の tags が来たら、新しく作らず前の `id` を返す(WES の送り直しのため) | TES-CRT-01〜05・07・08、G-TES-04 |
| ListTasks | 見えるタスクだけ。`name_prefix`・`state`・`tag_key`/`tag_value`(本文の表どおり)、`page_size`(既定 256、2048 以上は 400)、`view` | TES-LST-01〜03、TES-VIEW-01、G-TES-03 |
| GetTask | 見えなければ 404。`view` 既定 `MINIMAL` | TES-VIEW-01 |
| CancelTask | 本人だけ。終わったものは 200 で何もしない。それ以外は CANCELING にして実行役に知らせる | TES-CAN-01、G-TES-02、D-020 |

**最小の安全(学習用でも入れる。計画レビューで隔離を対象外にした範囲)**

- 入出力の `path` は絶対パスで、`..` を含まない。`/` 直下の重要なパス(`/proc`・`/sys`・`/dev`・`/etc`)には置かせない
- `file://` の入力・出力は設定の根(`var/tes/files/`)の下だけ。さらに
  - **出力の URL は、まだ無いファイルだけ**(上書きしない)。置き場へ写すときも「無ければ作る」(`O_EXCL`)で開き、有れば SYSTEM_ERROR
  - 出力の元のファイルが作業ディレクトリに無いときも SYSTEM_ERROR(TES-ST-02)。このときだけ `tesTaskLog.metadata` の `yai.missing_outputs` にその出力の `path` を書く(「,」区切り)。WES が「CWL の出力が無い」を EXECUTOR_ERROR に読み替えるための印(`wes.md` 1 節、D-026)
  - **`file://` の入力は、同じ持ち主のタスクの出力として記録されたファイルだけ**(`output_files` の表で確かめる)。WES のサービスが代理で出すタスクは、**同じ `yai.on_behalf_of` かつ同じ `yai.run_id`** の出力だけ(`system.md` C-25)。WES のステップの間の受け渡しはこれで通る
  - パスは要素ごとにリンクを辿らずに開く(根の下にリンクを置かれても外に出ない)。ホスト側のパスとコンテナの中のパスを別の型で扱う
  - **利用者の値を docker の引数・ホスト側のパスにするところ(D-041)**: `image` は Docker の参照の文法だけ(`yai/common/imageref.py`)で、argv では `--` の後ろ。コンテナの中のパスに `:` は使えない。`docker logs` は付けていない `logs/<i>/` に書き、宣言の `stdout`・`stderr` へは先にある物を名前で消してから写す(リンクの先に書かない)。付けるディレクトリはタスクの宣言だけから決めて executor の間で変えない(前の executor が置いたリンクで次の mount 元が変わらない)
  - 同じ OS の利用者がホストのファイルを直接いじることは、分離の対象外(D-002)
- **外から取る道はすべて共通の取り役を通す**(TES の入力の `https`、WES の `workflow_url`、DRS の解決、鍵の取得): 許すホストとポートの表(既定: `drs.yai.test:443`。単体の試験だけ `127.0.0.1` の決めたポート)、**リダイレクトに従わない**、TLS の検証あり(手元の CA)、接続 5 秒・読み取り 30 秒・全体の期限(入力は 1 時間、ワークフローの文書は 10 秒)、大きさの上限(入力 10 GB、ワークフローの文書 1 MiB)。DNS の答えを書き換えられる攻撃(DNS rebinding)は、ホストの名前を手元の DNS で固定するので対象外(D-002)
- Docker のソケットをコンテナに渡さない。`--privileged` を使わない。ネットワークは既定で切る(入力は実行役が取ってから渡す)
- `cpu_cores`・`ram_gb` → `--cpus`・`--memory`。**省いたときも有限の既定(1 CPU・2 GB)を必ず付ける**。0・負・上限(2 CPU・4 GB)超えは 400。ほかに、プロセス数 256(`--pids-limit`)、コンテナのログ 10 MB、作業ディレクトリ(入力+volumes+出力)20 GB、実行時間 24 時間を固定の上限にする(超えたら SYSTEM_ERROR)。`disk_gb` を守る作りは後でも、ホストを守るこの上限は最小版に入れる
- **出力の取り出しでシンボリックリンクを辿らない**。コンテナの中で作られたリンク(例 `/data/out -> /etc/passwd`)を辿って置き場へ写すと、実行役の機械のファイルが漏れる。出力は作業ディレクトリの中の普通のファイルだけを、リンクを辿らずに開いて写す(リンクなら SYSTEM_ERROR)
- 入力の取得は大きさの上限(既定 10 GB、設定)を超えたら止めて SYSTEM_ERROR
- **volumes**(TES-RUN-02・04): `work/<task_id>/volumes/<n>/` を空で作り、全 executor の同じパスに付ける。入力も全 executor に同じパスで付ける。volume のパスと入力・出力のパスが重なる(片方がもう片方の下にある)ときは 400。タスクが終わったら消す

**プロセスの形(最小版)**: API のプロセス(FastAPI)と実行役のプロセス(`yai-tes worker`)の 2 つ。**実行役は 1 つだけ**(起動時にロックのファイルを取り、取れなければ止まる)。API は `--workers 1`、自動の再読み込みなし。同じ SQLite(WAL)を使い、API は QUEUED の行を書くだけ、実行役が QUEUED を拾って進める。取り消しは API が CANCELING を書き、実行役が見て止める。状態の書き込みは両方とも条件付き更新(`version` の列)。

## 3. 認可の点(4 つの役との対応)

| 点 | R1 | R2 | R3 | R4 |
| --- | --- | --- | --- | --- |
| CreateTask | AT(`aud=yai-tes`) | 利用者かサービス | `tes.submit`。`yai.` の tags はサービス+`tes.delegate` だけ | — |
| List・Get | AT | 同上 | 本人か `yai.on_behalf_of` の本人(`permissions.yaml`) | — |
| Cancel | AT | 同上 | 本人だけ(代理で出されたものは出したサービスだけ) | — |
| 実行役の入力の取得 | — | — | — | URL だけ(資格情報を持たない、D-007) |

## 4. 状態

- 移り方の表: `docs/data/tes-states.yaml`(設計の正。実装は読むかコードに写す、D-006)
- 書き込みは「今の状態が `from` のときだけ」の条件付き更新。取り消しと完了が重なったら、先に書けた方が勝つ(負けた方は今の状態を読み直す)
- **外の操作の前に、意図を保存する**。実行役はコンテナを起動する前に `executions(task_id, attempt, executor_index, phase, container_name)` に「起動する」を書き、コンテナの名前は決まった形 `yai-<task_id>-<attempt>-<executor_index>` にする(同じ名前は 2 度作れないので、二重起動しない)。出力の転送は 1 ファイルずつ「写した」を記録する
- **再起動のとき**(G-TES-09): 終わっていないタスクを、保存した意図と、決まった名前のコンテナで突き合わせる
  - QUEUED → コンテナが無いのが普通なので、そのまま拾い直す(SYSTEM_ERROR にしない)
  - INITIALIZING → 入力の取得をやり直す(作業ディレクトリを消して最初から)
  - RUNNING → 「起動する」を書いた executor について、コンテナが動いていれば見張る/終わっていれば終了コードで進める/無ければ(起動の前に落ちた)その executor を起動し直す
  - 出力の転送の途中 → 「写した」と記録の無いファイルから続ける
  - CANCELING → コンテナを止めて消し、CANCELED
  - 意図も無く、上のどれにも当たらない → SYSTEM_ERROR(`lost_on_restart`)
- 子プロセス(入力の取得・出力の転送)は実行役の中のスレッドかプロセスで、終わったら必ず回収する。起動時に孤児のコンテナ(ラベル付きで、DB に無い)を消す

## 5. データの持ち方

| 物 | 場所 | 中身 |
| --- | --- | --- |
| タスク | SQLite `var/tes/tes.sqlite` | `tasks(id, owner_iss, owner_sub, state, name, created_at, task_json, version)`。`task_json` は受け取った tesTask(読み取り専用の項目を除く)、`version` は条件付き更新用 |
| 実行の意図 | 同上 | `executions(task_id, attempt, executor_index, phase, container_name, updated_at)`、`output_transfers(task_id, path, url, done)`。段階 1 の手元のプロセス(D-024)は `container_name` の代わりに `pid, exit_file` と結果 `exit_code, error`(`finished` と同時に保存。再起動のあとはこれで判定し、終了コードが分からないのを成功にしない)。二重に起動しない印は作業ディレクトリの `logs/<i>/started`(wrap が子を動かす前に O_EXCL で書く PID・プロセスの開始時刻・起動の時刻。止める・待つのは印と同じプロセスだけ) |
| ログ | 同上 | `task_logs(task_id, attempt, start_time, end_time, metadata_json, system_logs_json)`、`executor_logs(task_id, attempt, index, exit_code, start_time, end_time, stdout_tail, stderr_tail)`(各 64 KiB まで、仮) |
| 出力の記録 | 同上 | `output_files(task_id, url, path, size_bytes)` |
| 作業ディレクトリ | `var/tes/work/<task_id>/` | 入力・volumes。終わったら消す(出力は置き場へ写したあと) |
| 表の定義 | `schema/tes.sql` | 言語に依らない形 |

## 6. 誤りの返し方

- 仕様に無い(G-TES-01)ので、DRS・WES と同じ `{msg, status_code}`(D-011)
- 400: 形・パス・資源の上限・`yai.` の tags・`page_size`。401・403・404 は `system.md` 7 節

## 7. 仮・未決

- 仮: 同時実行 2、資源の上限、ログの切り詰め 64 KiB、`https` の許可ホスト
- 試験で確かめる: openapi-test-runner(TES 1.1 に合う公式の試験道具)が 4xx・認証をどう扱うか。認証を切った設定でも回せるようにする(`test-plan.md` CONF-TES)
