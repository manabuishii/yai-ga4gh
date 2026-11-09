# INTEROP-DRS-1: yai-wes の解決役 → ga4gh/passport-playground-drs(DRS 1.5.0)

- 相手: ga4gh/passport-playground-drs main 5eb7745d743cfde837dcb07ef6cc2467120a01d7(2026-06-12、FastAPI、`main.py` 242 行)。`deploy/interop/pgdrs/Dockerfile` が clone してコミットを固定。`DRS_HOST=pgdrs.yai.test`・`DRS_PORT=8645`。相手の API は第 2 の Caddy が `https://pgdrs.yai.test`(443)で受け、相手が返す平文の access URL(下の [7])のために `http://pgdrs.yai.test:8645` も受ける
- yai の設定(`deploy/interop/make_configs.py`、R-09): wes・tes の `fetch.allow` に `pgdrs.yai.test:443`、tes の `fetch.insecure_http_hosts` に `pgdrs.yai.test:8645`(試験の設定)。`resolver.passport_hosts` は `drs.yai.test` のまま(相手には Passport を送らない。相手は Passport を見ない)
- 向き: (1) 台本 `drs_compare.py` が相手に直接(service-info・OPTIONS・GET object・GET access・取得)、yai-drs の同じ応答と並べる。(2) 本番の道: alice が WES に `drs://pgdrs.yai.test/ee7b2550…7850`(README の表の `data/alice.tsv`)の cat を出す → yai-wes の解決役 → yai-tes が取る
- 結果: **5/5 通過**(`data/INTEROP-DRS-1.json`、2026-10-17 06:3x UTC)

| 確かめ | 結果 | 抜き書き |
| --- | --- | --- |
| 相手に直接: service-info・OPTIONS・GET object・access・取得 | 通った | service-info `type.version 1.5.0`、OPTIONS **204**(本文なし)、object の鍵 `access_methods・checksums・created_time・description・id・name・self_uri・size・updated_time・version`、`self_uri: drs://pgdrs.yai.test/ee7b…`、access `{"url": "http://pgdrs.yai.test:8645/files/ee7b…/alice.tsv", "headers": {}}`、取得 200・17 バイト `name num / alice 1` |
| yai-drs に直接(比較) | 通った | OPTIONS **200** `{"drs_object_id": …, "supported_types": ["None"]}`、公開のオブジェクトは `access_url` を直に返す(access_id 無し)、`created_time` は `…Z` |
| WES に drs://pgdrs.yai.test/<id> の cat を出す | 通った | run_id |
| run が COMPLETE | 通った | system_logs `input infile: drs://pgdrs.yai.test/ee7b… resolved to http://pgdrs.yai.test:8645/files/ee7b…/alice.tsv`、outputs `out.txt size 17` |
| TES のタスクの入力 URL が相手の access URL | 通った | `http://pgdrs.yai.test:8645/files/ee7b…/alice.tsv`、state COMPLETE |

## 応答の細かい差(yai-drs との比較)

| 項目 | playground DRS | yai-drs | 読み |
| --- | --- | --- | --- |
| OPTIONS /objects/{id} | 204、本文なし(middleware が全部の OPTIONS に 204) | 200、`supported_types: ["None"]` | yai の解決役は 204・405 を「Passport は要らない」と読む(C-26)ので通る。DRS 1.5.0 の OPTIONS は `supported_types` を返す定義 |
| `created_time` | `2026-10-17T06:22:57.386351+00:00`(小数・`+00:00`) | `2026-10-17T06:25:21Z` | どちらも RFC 3339。yai の解決役は読まない |
| `checksums[].type` | `sha-256`・`md5` | `md5`・`sha-256` | 同じ名前(順だけ違う) |
| `access_methods` | `{type: https, access_id: local-https, region: local}` → `/access` が必要 | 公開は `{type: https, access_url: {url}}` | どちらも仕様の形。yai の解決役は両方を扱う(INT-AM-BOTH) |
| access URL のスキーム | **http**(`main.py` の `_public_base()` が `http://` 固定、`type` は https) | https | [7] |
| 仕様に無い口 | `GET /objects`(一覧、README が「非標準」と明記) | 無し(POST /objects は一括) | 使わない |

## 通信できなかった相手とエンドポイント

無し(全部通った)。ただし相手の access URL が http 固定のため、yai-tes の `fetch` の既定(https だけ)では取れない。試験の設定 `insecure_http_hosts` で受けた。

## 原因の見立てと根拠

`main.py`: `def _public_base(): return f"http://{DRS_HOST}:{DRS_PORT}"`、`url = f"{_public_base()}/files/{object_id}/{path.name}"`。`access_methods` は `{"type": "https", "access_id": "local-https"}` を固定で返す。相手は手元・デモ用の実装で TLS を前段に置く前提が無い。

## 解決の案

- 相手の設定: `DRS_HOST`・`DRS_PORT` は変えられるがスキームは変えられない。前段の TLS を置いても access URL は http のまま
- yai 側: 本番の yai-tes は https 以外を 400 で断る設計(tes.md の最小の安全、NEG-SSRF)なので、こういう相手は**試験の設定(`insecure_http_hosts`)でだけ**受ける。直すものは無い
- 分類: 仕様の差(相手)

## 仕様との食い違い

DRS 1.5.0: `AccessMethod.type`(https など)と `AccessURL.url` のスキームが一致することを求める文が無い(G-DRS-16 に足した)。
