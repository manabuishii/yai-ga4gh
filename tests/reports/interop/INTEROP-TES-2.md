# INTEROP-TES-2: yai-wes(TES のクライアント)→ Funnel v0.11.14

- 相手: Funnel(calypr/funnel)。像 `quay.io/ohsu-comp-bio/funnel:v0.11.14@sha256:8e10ba4de436b058bed95427110d9fe947142162078274a6a91cfe3b3eb8d15a`(Release v0.11.14 の像、作成 2026-06-11。git タグ v0.11.14 = 2e54ad7)。研究ノートの「Release とタグの版の食い違い」: 像の `funnel version` は空(version・git commit とも "")で、service-info の `type.version` も `""`、`version` も `""`。像は Release v0.11.14 のものと判断(作成日が Release 公開日 2026-06-11 と一致)
- 派生の像: 公式の像は docker CLI を持たない(nerdctl のみ)ので `deploy/interop/funnel/Dockerfile` で docker CLI 27.3.1 を足した(環境の不備 [3])
- Funnel の設定(`deploy/interop/funnel/funnel.yaml.in`): Compute local、Worker.WorkDir は本体から見たパスと同じ場所(`$YAI_LOCAL_DIR/interop/funnel-work`、Funnel は `docker run -v` にこのパスをそのまま渡す)、`LocalStorage.AllowedDirs` に `/yai/var/tes/files`(yai-wes の出力 `file:///yai/var/tes/files/wes/…` を、yai-tes と同じ volume `yai-tes-files` に書く)、`HTTPStorage` 既定。Funnel のコンテナには `SSL_CERT_FILE=/certs/ca.pem`(Go の crypto/x509 が読む)、rootless の本体のソケットのフォルダを ro で
- 向き: yai-wes の `tes.url` を `https://funnel.yai.test/ga4gh/tes/v1`(第 2 の Caddy、同じ *.yai.test の証明書)に替えて(`local/interop/config/wes-funnel.yaml`、`sh tools/interop.sh wes-funnel`)、compose の E2E-01 を流す
- 結果: **通過**。`tests/test_e2e.py::test_E2E_01[compose]` が 1 passed(2026-10-17 06:3x UTC)。1 回目(06:27)は yai の不具合 [2] で SYSTEM_ERROR、直して 2 回目で通過

## 通ったこと

- yai-wes → Funnel: CreateTask(tags に `yai.submit_nonce`、`Authorization: Bearer <yai の AT>` を Funnel は無視)、GetTask view=FULL、ListTasks(tag_key/tag_value)。Funnel の記録: `Running command /usr/local/bin/docker run -i --read-only --rm --name db9h…-0 --workdir /yai/work --volume …/funnel-work/db9h…/yai/work:/yai/work:rw … alpine:3.20 cat /yai/inputs/infile/o01m…`、`EXECUTOR_EXIT_CODE 0`、`EXECUTOR_STDOUT "controlled data for E2E\n"`、`upload finished … url: file:///yai/var/tes/files/wes/r01m…/1/out/out.txt size 24`
- **INTEROP-TES-HTTPS**: Funnel の HTTP storage が yai-drs の署名付き URL(`https://drs.yai.test/yai/blobs/<id>?exp=…&sig=…`、手元の CA)を GET で取れた(E2E-01 の cat の出力が `controlled data for E2E` と一致)。Funnel の `storage/http.go` は Get では HEAD を打たない(Stat だけ HEAD)ので、yai-drs の blob の口は GET だけで足りた
- E2E-01 の `GET <tes_uri>`(alice の yai-tes の AT で Funnel のタスクを読む)も 200(Funnel は認証無し、TaskAccess All)

## 通信できなかった相手とエンドポイント(1 回目)

yai-wes → Funnel `GET /ga4gh/tes/v1/tasks/{id}?view=FULL` は 200 で届いたが、yai-wes の engine が応答の `logs[0].start_time: ""` を読めず落ちた。

## 原因の見立てと根拠

Funnel は protobuf の JSON 化の既定で、未設定の文字列の項目を `""` で返す(FULL view の `logs[].start_time`・`end_time` が、まだ始まっていない瞬間は空)。yai-wes の `engine.record_step` の `ts()` が `parse_rfc3339("")` を呼び `ValueError: Invalid isoformat string: ''`(yai-wes のログの traceback)。TES 1.1 の `tesTaskLog.start_time` は「`type: string`、RFC 3339」とだけで、無いときの表し方(省く / null / "")を決めていない。

## 解決の案(実施済み)

- **yai 側で直した**: `yai/wes/engine.py` の `ts()` は、文字列でない・空・読めないものを「無い」(null)にする。手元の試験 `tests/test_e2e.py::test_INT_TIME_EMPTY`(中継役で `start_time: ""`・`end_time: "not a time"` に書き換えても run が COMPLETE、時刻は null)を足し通過。直したあとの E2E-01(Funnel 相手)は通過
- 分類: yai の不具合(直して 0)。環境の不備 [3] も派生の像で解消

## 仕様との食い違い

TES 1.1: 時刻の項目が未設定のときの表し方が無い(G-TES-13 に足した)。

## 追加の情報

compose の E2E 全部を Funnel 相手に流した結果は README.md の「追加の情報」(通過 10・落ちた 9 は全部 yai-tes を観測する yai 固有の試験)。Funnel の service-info は `type.version: ""`・`storage: [file:///path/to/local/funnel-storage, s3://ohsu-compbio-funnel/storage]`(設定の写しでなく既定の例文のまま)で、yai-wes はどちらも読まないので影響なし。
