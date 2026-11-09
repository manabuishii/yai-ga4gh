# CONF-DRS: 公式の DRS の試験道具の結果(test-plan.md 2 節)

## 道具の入れ方(2026-10-16)

- ga4gh/drs-compliance-suite の **git のコミット `ad9a48420ba818b71007e9fa046d11187ba068aa`**(spec-pins の固定どおり)を clone し、別の venv に `requirements.txt` と `werkzeug==2.2.2` を入れる
  - **PyPI の 1.0.3 は起動しない**: 依存(jsonschema など)が宣言されておらず、`supported_drs_versions` のモジュールも入っていない(`ModuleNotFoundError`)。2026-10-16 に確かめた
  - `requirements.txt` は Flask 2.2.0 を固定しているが werkzeug を固定していないので、新しい werkzeug では `url_quote` が無く起動しない → `werkzeug==2.2.2` を足す
- 回し方: `python tools/conformance_drs.py --suite-dir <clone> --suite-python <venv>/bin/python --label <日付>`
  - AAI と DRS を手元に立て、`public`(認証なし)・`bearer`(alice の AT)・`dataset`(alice の Passport、POST)の 3 つのオブジェクトを登録して当てる。`--drs_version 1.2.0`(道具が対応するのはこれだけ)

## 2026-10-16(段階 1 の DRS の区切り)

`2026-10-16-report.json`(道具の報告そのもの)、`2026-10-16-failed.json`(落ちた項目)

- 結果: 24 項目中 PASS 21・FAIL 3
- 分け方: **版の違い 3・道具の前提 0・yai の誤り 0**

| 落ちた項目 | 道具の言い分 | 分け方 | 理由 |
| --- | --- | --- | --- |
| service-info の schema の検査 | `drs`・`maxBulkRequestLength` は許されない追加の項目 | 版の違い | 道具の schema は DRS 1.2.0 で `additionalProperties: false`。`maxBulkRequestLength`(根と `drs` の下)は 1.5.0 の `DrsService.yaml` で required(DRS-SI-02)。1.2.0 には一括の口が無い |
| DRS オブジェクト(bearer)の schema の検査 | `authorizations` は許されない追加の項目 | 版の違い | `AccessMethod.authorizations` は 1.5.0 の `AccessMethod.yaml` にあり、yai は DRS-AM-03 のために出す。1.2.0 の schema には無い |
| DRS オブジェクト(passport)の schema の検査 | 同上 | 版の違い | 同上 |

- 1.2.0 と共通の部分(GET・`/access`・service-info の必須の項目・誤りの形・状態コード・Content-Type)はすべて通った
- 道具は Passport を `POST` の本文 `passports` で送る(1.3 以降の形)ので、`dataset` の方針のオブジェクトも試せた
