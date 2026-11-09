# INTEROP-TES-1: py-tes 1.1.4(相手のクライアント)→ yai-tes

- 相手: py-tes 1.1.4(PyPI、git タグ 1.1.4 = 880dc5a、TES 1.1 用のモデル)。コンテナ(`deploy/interop/clients/Dockerfile`)の中で `pip install py-tes==1.1.4`、台本 `pytes_check.py`
- 向き: 相手のクライアント → yai-tes `https://tes.yai.test`(手元の CA は `REQUESTS_CA_BUNDLE`、alice の AT を `token=` で)
- 結果: 1 回目(06:3x UTC)**8/9**(service-info だけ通らない、相手の未対応 [1])→ ユーザー決定(15:51 JST、D-040)で yai-tes の service-info から `tags` を外し、再試験(06:53 UTC = 15:53 JST)**9/9 通過**(`data/INTEROP-TES-1.json` は再試験のもの)

| 確かめ | 結果 | 抜き書き |
| --- | --- | --- |
| service-info を py-tes の型で読む | 1 回目 **通らない** [1] → `tags` を外して **通った** | `UnmarshalError(… could not be unmarshalled to type: ServiceInfo / TypeError: ServiceInfo.__init__() got an unexpected keyword argument 'tags')`。生の応答は 200 で `id・name・type・description・organization・version・environment・storage・tesResources_backend_parameters・tags` |
| CreateTask(1.1 の項目 `backend_parameters`・`ignore_error` 付き) | 通った | id が返る。yai のログ `backend_parameters ignored: ['yai.note']`(C-05 の無視) |
| GetTask view=MINIMAL / BASIC / FULL を py-tes の型で読む | 通った | MINIMAL は `id`・`state` だけ、BASIC・FULL は executors・logs を持つ |
| wait → 終わりの状態 | 通った | COMPLETE、stdout `hello from py-tes` |
| ListTasks page_size=1 → next_page_token → 次のページ | 通った | 2 ページの id が違う |
| ListTasks view=FULL | 通った | 全部に executors |
| CancelTask → CANCELED | 通った | sleep 120 のタスクを取り消し、CANCELED |

## 通信できなかった相手とエンドポイント

py-tes → yai-tes `GET /ga4gh/tes/v1/service-info`(応答は 200。クライアント側の型で読めない)。ほかの呼び出しは全部通った。

## 原因の見立てと根拠

py-tes の `tes.models.ServiceInfo` は attrs の厳密なクラス(`tes/models.py` 509 行〜)で、知らない鍵を受けない。yai の TES の service-info には `tags: {"yai.runner": "docker (D-034)"}` が入る(`yai/tes/app.py` 122 行)。TES 1.1 の `tesServiceInfo`(`specs/tes/task_execution_service.openapi.yaml` 661 行)は service-info 1.0.0 の `Service` に `storage` と `tesResources_backend_parameters` を足したもので、`tags` は無いが `additionalProperties: false` も無い(JSON Schema の既定で追加の項目は許される)。

## 解決の案

- 相手の設定: 無い(py-tes に寛容な読み方の設定は無い)
- 仕様の解釈: yai は仕様に反していない(追加の項目)。ただし **厳密なクライアントは TES の service-info に `tags` を期待しない**。**ユーザー決定(2026-10-17 15:51、D-040)**: 相互運用を優先し、yai-tes の service-info から `tags` を外す(実行役の種類は `description` に)。`tests/test_tes.py::test_TES_SI_01` は `tags` が無いことを見る形に直した
- 分類: 最初は相手の未対応 → yai 側で直した(ユーザー決定)。直したあとは 9/9

## 仕様との食い違い

G-X-07 に足した: 同じ service-info 1.0.0 の拡張なのに WES 1.1.0 の ServiceInfo は `tags` を required とし(`workflow_execution_service.openapi.yaml:638`・`:651`)、TES 1.1 の tesServiceInfo には無い(`task_execution_service.openapi.yaml:660`、`tags` は Task の `:801` だけ)
