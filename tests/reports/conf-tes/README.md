# CONF-TES: 公式の TES の試験道具の結果(test-plan.md 2 節)

## 道具の入れ方(2026-10-16)

- ga4gh/openapi-test-runner の **git のコミット `bc3eb911e877521409348e0225852d072d4de196`**(spec-pins・test-plan の固定どおり。タグ・PyPI は無い)を clone し、別の venv に `pip install -e` で入れる(依存は `requirements.txt` のとおりで足りた)
- 回し方: `python tools/conformance_tes.py --suite-dir <clone> --suite-python <venv>/bin/python --label <日付>`
  - 「全部公開」の仮の実装の設定(`auth.mode: anonymous`)の yai-tes と実行役(手元のプロセス、D-024)を立て、`--version 1.1.0` で当てる。認証ありの振る舞いは (b)(c) の試験で確かめる
  - **道具は `--server` の後ろに `v1/` を足す**ので、`http://<host>/ga4gh/tes/` まで渡す。`--output_path` はディレクトリ(中に `report.json` を書く)

## 2026-10-16(段階 1 の TES の区切り)

`2026-10-16/report.json`(道具の報告そのもの)、`2026-10-16-runner.log`(道具の出力)

- 結果: 183 項目中 passed 179・failed 4(試験の単位では 23 本中 19 本が通った)
- 分け方: **版の違い 0・道具の前提 4・yai の誤り 0**

| 落ちた試験 | 道具が送ったもの | 道具の期待 | yai の応答 | 分け方 | 理由 |
| --- | --- | --- | --- | --- | --- |
| `create_task_inputs.yml` | 入力の `url` が `s3://my-object-store/file-1` | 200 | 400 | 道具の前提 | 4 つとも `schema_validation_only` の印の試験で、「形が正しい作成の要求は受け付けられる」ことを前提にしている。yai は対応しない入出力の種類を**受付で 400** にする設計(`tes.md` 1 節の入力は `https`・`content`・決められた根の下の `file://`、NEG-SSRF)。対応する種類は service-info の `storage` で `file://…` と `https://` と示している(C-04) |
| `create_task_optional_filetype.yml` | 入力・出力の `url` が `s3://…` | 200 | 400 | 道具の前提 | 同上 |
| `create_task_outputs.yml` | 出力の `url` が `s3://my-object-store/outfile-1` | 200 | 400 | 道具の前提 | 同上(出力は根の下の `file://` だけ) |
| `create_task_streamable.yml` | 入力の `url` が `s3://…`、`streamable: true` | 200 | 400 | 道具の前提 | 同上(`streamable` は最小版では受けて無視、`tes.md` 1 節) |

- 通ったもの: service-info、CreateTask(`backend_parameters` の正と負・`ignore_error`)、GetTask・ListTasks の 3 つの view、名前・状態・tags での絞り込み、`page_size`・`page_token`、CancelTask(と、取り消しまでの実際の動き)、作成から終わりまでの見張り(`create_task_functional`)
- 4xx の扱い(G-TES-01): 道具は `backend_parameters_strict` の負の例で 400 を期待しており、yai の 400 で通った。誤りの本文の形は見ていない
