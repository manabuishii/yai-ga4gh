# INTEROP-WES-1: yai の試験(共通の種類)→ Sapporo 2.4.0

- 相手: Sapporo(sapporo-wes/sapporo-service)2.4.0。像 `ghcr.io/sapporo-wes/sapporo-service:2.4.0@sha256:0e0a153794c38fa0269531915fdc6c429e6235aa633058b429e2c1e83cc00aa0`(git タグ 2.4.0 = 14e6b5c)。service-info の `type.version` は `sapporo-wes-2.1.0`(自分の WES の版)、`supported_wes_versions: ["sapporo-wes-2.1.0"]`。エンジンは cwltool(像 `quay.io/commonwl/cwltool:3.3.20260925135507`、Sapporo が docker で起こす)
- 環境の差し替え [6]: Sapporo の `run.sh` は中の cwltool のコンテナに `-v /var/run/docker.sock:/var/run/docker.sock -e DOCKER_API_VERSION=1.44` を固定で渡す(rootful 前提)。設定 `run_sh`(`SAPPORO_RUN_SH`)で、rootless のソケットのフォルダ `<YAI_DOCKER_SOCK_DIR>:/run/yai-docker:ro` と `DOCKER_API_VERSION=1.41` に差し替えた写し(`deploy/interop/setup.sh` が sed で作る)を渡した。Sapporo 自身の docker CLI 29.9.0 は本体 20.10.13 に API 1.41 で降りて話せた(`docker version`: `API version: 1.41 (downgraded from 1.56)`)。`SAPPORO_RUN_DIR` は本体から見たパスと同じ場所に付けた(中のコンテナが host-path:host-path で付けるため、Sapporo の compose.yml と同じ考え)
- 向き: yai の「共通」の試験(仕様の義務だけ。yai 固有の scope・tags・ページの印・障害の差し込みは当てない)を台本 `wes_common_check.py` にして Sapporo `https://sapporo.yai.test`(認証なし)に当て、同じ台本を yai-wes(`https://wes.yai.test/ga4gh/wes/v1`、alice の AT)にも当てて並べた。CWL は `tests/cases/cwl/cat_file.cwl` + `input.txt`(添付、`workflow_params` は `{"infile": {"class": "File", "location": "input.txt"}}`)。Sapporo には `workflow_engine=cwltool` を付けた(2-2 の必須)
- 結果: **Sapporo 8/10、yai-wes 10/10**(`data/INTEROP-WES-1.sapporo.json`・`data/INTEROP-WES-1.yai.json`、2026-10-17 06:3x〜06:4x UTC)

| 確かめ | Sapporo | yai-wes | 互換表の番号(`docs/wes-compatibility.md`) |
| --- | --- | --- | --- |
| GET /service-info が WES 1.1.0 の ServiceInfo の鍵を持つ | 通った(`default_workflow_engine_parameters` は **dict**) | 通った(list) | 1-1(破壊的: dict)。鍵は全部ある |
| POST /runs(multipart、CWL と入力を添付)→ run_id | 通った | 通った | 2-1・2-2(`workflow_type`・`workflow_engine` 必須。付けたので問題なし) |
| /runs/{id}/status が COMPLETE まで | 通った | 通った | — |
| GET /runs/{id} が RunLog(run_id・request・state・run_log・outputs) | 通った(`outputs` は **list**、`task_logs_url` も)| 通った(dict)| 1-2(破壊的: `outputs` は FileObject の配列)。`run_log.stdout` は URL でなく cwltool の出力の中身(振る舞いの差の表) |
| 出力 copy.txt の中身が入力と同じ | 通った(`file_url` から取って一致) | (file:// なので E2E で確認) | 3-4 の FileObject |
| GET /runs?page_size=1 → runs は 1 件 | 通った(`next_page_token` あり) | 通った | 3-3 |
| GET /runs/{id}/tasks | **通らない** [4]: 400 `Sorry, this endpoint is not implemented and there are no plans to implement it.` | 通った(200) | 4-1 |
| POST /runs/{id}/cancel → CANCELED | 通った(sleep 300 を取り消し) | 通った | — |
| GET /runs/{無い id、UUID の形} → 404 ErrorResponse | 通った | 通った | — |
| GET /runs/{無い id、UUID でない} → 404 ErrorResponse | **通らない** [5]: 400 `Invalid run_id format: does-not-exist-0000` | 通った(404) | 2-4(ErrorResponse の形は `{msg, status_code}` で一致) |

## 通信できなかった相手とエンドポイント

- yai の試験 → Sapporo `GET /runs/{run_id}/tasks`: 400(実装しない)
- yai の試験 → Sapporo `GET /runs/does-not-exist-0000`: 400(404 を期待)

## 原因の見立てと根拠

- [4] Sapporo の設計(DinD の構造上、タスクの一覧を持たない。互換表 4-1・4-2「No plans to implement」)
- [5] Sapporo は run_id を UUID として検査し、形が違えば 400 を返す。WES 1.1.0 の `GET /runs/{run_id}` は 404 を「The requested workflow run not found」とだけ定義し、形が違う id の扱いは書いていない(`specs/wes/…openapi.yaml` 356 行)

## 解決の案

- yai の WES のクライアント(yai は WES のクライアントを持たない。相互運用では yai の試験がクライアント)としては、`/tasks` を 400 で返す相手を「未対応」と読めるよう、試験の側で `400 + msg に not implemented` を未対応として数える(台本はそのまま、報告で分類)。yai-wes 自身は 200 で返すので直すものは無い
- [5] は仕様の差として記録(G-WES-13)。yai は 404 のまま
- 分類: 相手の未対応 [4]、仕様の差 [5]、環境の不備(直した)[6]

## 仕様との食い違い

- WES 1.1.0: 形の違う run_id の状態コードが無い(G-WES-13 に足した)
- Sapporo の破壊的な差 1-1・1-2 は Sapporo 自身が文書化しているもので、yai の試験の鍵の確かめには引っかからなかった(型の違いは記録だけ)。yai が将来 WES のクライアントを持つなら、`outputs` が配列・`default_workflow_engine_parameters` が辞書の相手に耐える必要がある
