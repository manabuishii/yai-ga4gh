# AAI・DRS のコードへの外部レビューの前の予想(2026-10-16、レビューに送る前に書いた)

- 書いた日時: 2026-10-16 00:4x JST(レビューの担当)。**このファイルのコミットがレビューに送るより前であることが、予想を後から書き換えていない証拠**
- 依頼: ユーザー依頼 10/16 00:39。レビューの担当は読むだけで、yai のコードは直さない
- 対象: `23bb75d` の `yai/aai/`(clearinghouse.py・keyfetch.py・app.py と、使っている jwtutil.py・service_auth.py・keys.py)、`yai/drs/`(access.py・app.py・store.py)、`schema/drs.sql`、試験 `tests/test_aai_clearinghouse.py`・`test_aai_broker.py`・`test_drs.py`。基準は `docs/design/aai.md`・`drs.md`・`system.md`、`docs/spec-obligations.md`
- 回数: 1 回だけ。本文を依頼文に入れ、仕様の原文はタグ固定の raw URL で読んでもらう
- 評価のしかた: 返事のあとで各予想を「当たり / 一部当たり / 外れ」に分け、予想に無かった指摘も数える(`2026-10-16_external-review-aai-drs.md`)
- 材料: 10/15 の設計レビュー(①〜③ で 36 件、ほぼ全部受け入れ)。設計で直したところ(jku の許可表・リダイレクト・Option B・方針 2 本立て)はコードに入っているので、今回は「設計どおりに書けているか」「コードにしかない弱いところ」が中心になると見る

## 予想

確からしさ: 高・中・低。予想した時点のレビューの担当の見立て(コードは直さない)も添える。

| # | ファイル | 予想する指摘 | 確からしさ | 見立て |
| --- | --- | --- | --- | --- |
| P-01 | drs/app.py・aai/keyfetch.py | `async def` の受け口の中で同期の I/O をしている(鍵の取得は同期の httpx で最長 5 秒、SQLite も同期)。鍵を取りに行く間、サーバー全体が止まる | 高 | 直す側(`def` にして threadpool で動かすのが一番小さい) |
| P-02 | drs/store.py `_db()` | `with sqlite3.connect(...)` はトランザクションを閉じるだけで接続を閉じない。要求ごとに接続が残る | 中〜高 | 直す側 |
| P-03 | aai/keyfetch.py `key()` | 1 分に 1 回の取り直しの制限は「キャッシュが有るとき」だけ効く。取得に失敗した・キャッシュが切れた取得元には、要求ごとに取りに行く(遅さと相手への負荷が増える)。同じ URL を同時に何本も取る | 中 | 直す側(失敗も記録して間隔を守る) |
| P-04 | drs/access.py `BEARER_SCOPES` | `bearer` の方針で `drs.read` だけで `/access`(中身の取得)も通る。読む権限と取る権限を分けていない | 中 | `permissions.yaml` の `scope_any` どおり。分けるかはユーザーに決めてほしい側 |
| P-05 | drs/app.py `get_bulk_access` | 一括の `/access` の `summary` が、オブジェクトの数と `access_id` の数を混ぜて数える。`unresolved_drs_objects` に同じ ID が重なる | 中 | 仕様の原文を見てから |
| P-06 | drs/app.py `deliver` | 流している途中で URL の期限が来て切ると、`Content-Length` より短い応答になる(受け手には壊れた転送に見える) | 中 | 設計(aai.md 4 節)で決めたこと。直さない寄り |
| P-07 | aai/app.py `exchange` | Token Exchange で、PSAT を出した相手(`aud`・client)と交換を頼んだクライアントを結びつけていない。`audience` を誰でも好きに指定できる。`except Exception` が広い | 低〜中 | 試験用 Broker(G-AAI-09)。直さない寄り |
| P-08 | aai/app.py `token` | client_credentials で `openid ga4gh_passport_v1` を持てるクライアントがいれば、サービスの AT が PSAT として交換に通る | 低 | 設定で防げる。直さない寄り |
| P-09 | aai/app.py `client_auth` | Basic の client_id・secret を URL デコードしない(RFC 6749 2.3.1) | 低 | 小さく直せる |
| P-10 | aai/service_auth.py | `client_id == sub` でサービスか利用者かを決めている。client の `sub` を設定で変えると利用者の扱いになる | 低〜中 | 直す側(種類を明示の claim で) |
| P-11 | aai/clearinghouse.py・drs/app.py | `passports` の数・Passport の中の Visa の数・トークンの大きさに上限が無い。1 つの要求で多数の署名検査と鍵の取得を起こせる | 中 | 直す側 |
| P-12 | aai/clearinghouse.py | Visa の `sub` と Passport の `sub` が違っても受ける | 低 | 設計(G-AAI-06)で許した。直さない |
| P-13 | drs/app.py `parse_range` | 解釈できない `Range`(複数範囲など)に 416 を返す。RFC 9110 では無視して 200 で全体を返すのが普通 | 低〜中 | 直す側か、仕様との差に書く |
| P-14 | drs/access.py `_load_secret` | 既存の秘密のファイルの権限・中身(空)を確かめない。空なら空の鍵で HMAC | 低〜中 | 直す側 |
| P-15 | drs/access.py・app.py | `OPTIONS /objects/{id}` は `metadata_policy` から作る。中身の取得に要る認可(`access_policy`)と違うとき、利用者が誤解する | 低 | 設計(drs.md 2 節)どおり。直さない寄り |
| P-16 | 試験 | 予想した弱いところの試験が無い(鍵の取得の失敗が続くとき、`Range` の境目、一括の `summary`、大きな `passports`) | 中 | 直す側(試験を足す) |

## 予想しなかったら外しそうなところ(前回の学び)

- 前回の外れは「仕様の原文にしか書いていないこと」。今回は DRS の OpenAPI(一括の応答の形、`error_code`、OPTIONS の応答)と、AAI の Token Exchange の応答(`issued_token_type`・`token_type`)の細部で指摘が来ると見る(どれが来るかは予想できないので、個別には書かない)
