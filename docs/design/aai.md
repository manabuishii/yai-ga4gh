# yai-aai の設計(AAI 1.2・Passport 1.2)

- 作成: 2026-10-15。前提: `system.md`(呼び出しと資格情報の表)。義務の ID は `docs/spec-obligations.md` 4 節
- **本命は受け取って確かめる側(Clearinghouse の部品)**。発行する側(Broker・Passport Issuer・Visa Issuer)は試験用の最小限(D-004)
- **AAI に適合すると言えるのは Clearinghouse の部品だけ**。試験用 Broker は OIDC の認可コードの流れなどを持たないので、Broker としては適合しない(G-AAI-09)

## 1. 範囲

| | 最初の「動く最小版」 | あとで深める |
| --- | --- | --- |
| Clearinghouse の部品(ライブラリ。yai-drs が中に持つ) | Passport の検査、Visa Document Token の検査、ControlledAccessGrants による判定、Option B の期限、鍵の取得と保持、`jku` の許可表 | `conditions` の評価、LinkedIdentities、複数の Visa を組み合わせる方針、Visa Access Token(と Polling)、鍵の入れ替えの試験の自動化 |
| 試験用 Broker+Passport Issuer | 固定の利用者表でのログイン、PSAT の発行、Token Exchange で Passport、OIDC Discovery と JWKS | OIDC の認可コードの流れ(+PKCE)、UserInfo、同意の画面 |
| 試験用 Visa Issuer | 利用者表に書いた Visa を Visa Document Token として署名し、`jku` を公開 | 取り消し(失効の一覧)、`conditions` 付きの Visa |
| yai の AT の発行 | client_credentials(サービス)と、利用者用の発行口(試験用ログインから) | refresh token |
| 4 つの役(段階 0 の依頼文) | 形と、仮の実装 4 つ(全部公開・固定 Bearer・有効だが権限なし・別の利用者)を最小版の最初に作る | — |

## 2. 4 つの役の形(全サービス共通、言語に依らない形で書く)

段階 0 の依頼文の「4 つの役」を、yai の 4 サービスが同じ形で使う。中身の差し替えで、仮の実装 → 本物に移る。

| 役 | 入力 | 出力 | 最小版の本物 | 仮の実装(試験用) |
| --- | --- | --- | --- | --- |
| R1 資格情報を受け取って確かめる | HTTP の要求(ヘッダー・本文の `passports`) | 確かめ済みの資格情報の一覧(種類つき)か、401 の理由 | AT の検査、Passport の検査(3 節) | `anonymous`(全部公開)・`static-bearer`(固定の文字列) |
| R2 利用者と確かめ済みの属性 | R1 の出力 | `Principal {iss, sub, kind: user\|service, scopes, visas: [AcceptedVisa]}` | R1 の出力から作る | 固定の Principal(`alice`・`bob`・権限なしの `carol`) |
| R3 資源と操作ごとに許す | `Principal`・操作名・資源 | 許可 / 拒否(403)/ 見えない(404) | `docs/data/permissions.yaml` を読む判定 | 「全部許す」「全部拒否」 |
| R4 下流に渡す資格情報を用意する | `Principal`・呼ぶ先 | 下流の要求に付けるもの | WES: DRS へは預かった Passport、TES へはサービスの AT(`system.md` 4 節) | 固定の文字列 |

- 仮の実装の 4 つ(段階 0 の依頼文): 「全部公開」「固定 Bearer」「有効だが権限なし」「別の利用者」。**拒否の試験を最初から書けるようにするため**
- 各役の入出力の形は JSON Schema で `docs/data/principal.schema.json` に置く(実装の前に作る。言語を替えても同じ形、`language.md`)

## 3. 受け入れるトークンの種類と、種類ごとの検査

**種類の見分けは「どこから来たか」で決め、中身で推測しない。** 例: `Authorization: Bearer` に来たものは AT として検査し、Passport の形(`typ=vnd.ga4gh.passport+jwt`)なら 401(種類の取り違え)。

| 検査 | yai AT(Bearer) | Passport(DRS の本文 `passports`、WES の multipart `yai_passports`) | Visa Document Token(Passport の中) | Visa Access Token | PSAT |
| --- | --- | --- | --- | --- | --- |
| 受けるか | 受ける | 受ける | 受ける | **受けない**(無視、G-AAI-02) | **受けない**(401、G-AAI-01) |
| 種類の見分け(Visa) | — | — | **ヘッダーに `jku` が有れば Visa Document Token**。`scope` を持ってもよい(`openid` を含めば拒否、AAI-FMT-05) | **`jku` が無いもの**は Visa Access Token とみなして無視 | — |
| `typ` | `at+jwt` | `vnd.ga4gh.passport+jwt`(AAI-FMT-01) | 無い・`vnd.ga4gh.visa+jwt`・`JWT` のどれでも受ける。ほかの値は無視(G-AAI-05、Passport の `typ` の節) | — | — |
| `alg` | `ES256`・`RS256` だけ。`none`・HS 系は拒否(AAI-FMT-03) | 同じ | 同じ | — | — |
| 鍵 | 信頼する発行元(yai-aai)の JWKS。`kid` 必須(yai の決まり) | 信頼する Broker の JWKS(OIDC Discovery の `jwks_uri`)(AAI-CH-01)。`kid` は仕様で必須でない:無ければ、その取得元の鍵が 1 つのときだけそれを使い、複数なら拒否 | ヘッダーの `jku`。**取りに行く前に、`iss` ごとの許可表に `jku` が有るか確かめる**(AAI-CH-07)。`kid` の扱いは左と同じ | — | — |
| `iss` | 設定の発行元と完全一致 | 信頼する Broker の一覧 | 信頼する Visa Issuer の一覧(PP-CH-03) | — | — |
| `aud` | 自分のサービス名を含む(必須) | 有れば自分を含む。無いのは設定しだい(既定は受ける、G-AAI-03) | 見ない | — | — |
| `exp`・`iat` | 必須。ずれは 60 秒まで | 必須(AAI-FMT-02) | 必須(PP-VISA-01) | — | — |
| `nbf` | 有れば検査(必須にしない。仕様の必須の項目に無い) | 同じ | 同じ | — | — |
| `scope` | 操作の表(`permissions.yaml`)の scope を含む。`ga4gh_passport_v1` を含んでいたら PSAT として 401 | 見ない | 有ってよい。`openid` を含めば拒否(AAI-FMT-05)。**種類の見分けには使わない**(レビューで直した) | — | — |
| 中身 | `sub`・`client_id` | `ga4gh_passport_v1` が配列(空でもよい) | `ga4gh_visa_v1` に `type`・`asserted`・`value`・`source`・`by`(ControlledAccessGrants は `by` 必須、PP-VISA-04)。**`asserted` は必須の整数で、`now + 60 秒` より未来なら無視**(PP-VISA-05)。`asserted` からの最大経過時間は制限しない(`maxAuthzTTL` を使わない Option B、PP-EXP-01) | — | — |
| `conditions` | — | — | **空でなければ無視**(評価しない最小版、PP-VISA-02) | — | — |
| 身元 | `(iss, sub)` | `(iss, sub)` | `(iss, sub)` が Passport と違ってよい。**最小版は 1 つの判断に 1 つの Visa だけ**(G-AAI-06) | — | — |
| 失敗 | 401 | 401(PP-CH-01:検査に通らない Passport を含む要求は全体を拒否) | その Visa だけ無視(PP-CH-02・03) | — | — |

## 4. 判定(ControlledAccessGrants)

```
許可 ⇔ 次を満たす Visa が 1 つ以上ある
  ga4gh_visa_v1.type  == "ControlledAccessGrants"
  ga4gh_visa_v1.value == オブジェクトの dataset(URL を文字列として完全一致)
  ga4gh_visa_v1.source ∈ 信頼する source(設定)
  ga4gh_visa_v1.by    ∈ {"dac"}(設定)
  conditions が無いか空
  now + accessTokenTTL + 60 秒 < min(Visa.exp, Passport.exp)    ← Option B(PP-EXP-01)。時計のずれは厳しい側
  (accessTokenTTL = 署名付き URL の期限、既定 3600 秒)
```

- 要らない Visa は見ない(PP-CH-02)。独自の `type` は無視(PP-VISA-03)
- **権限を外してから止まるまでの上限**(レビューで直した): Visa Document Token には失効の仕組みが無い(AAI の Visa Document Token の節「失効の手続きなしに出してよい」)。Visa Issuer が権限を外しても、出し済みの Visa は `exp` まで使える。一方 Option B により署名付き URL の期限は必ず Visa・Passport の `exp` より前になる。だから **新しい取得を始められるのは「出し済みの Visa の `exp`」まで(最長 120 分)**。**始まった取得も URL の期限で配信口が打ち切る**(期限を過ぎたら新しい要求も `Range` の続きも 403、流している途中の応答も期限で切る)。つまりバイト列が出ていく上限は Visa の `exp`。時計のずれは厳しい側に使う: `now + URL の期限 + 60 秒 < min(Visa.exp, Passport.exp)` のときだけ URL を出す
- 判定の記録: 許可・拒否と、使った Visa の `iss`・`jti`(無ければ署名の SHA-256 の先頭 16 文字)だけを残す。トークンそのものは残さない

## 5. 設定(信頼の表)

`config/trust.yaml`(例。秘密は入れない)

```yaml
brokers:
  - iss: https://aai.yai.test/
    jwks_uri: https://aai.yai.test/.well-known/jwks.json   # Discovery で取れた値と一致することも確かめる
visa_issuers:
  - iss: https://aai.yai.test/visa/
    jku_allow: [https://aai.yai.test/visa/jwks.json]
trusted_sources: [https://yai.test/dac]
accepted_by: [dac]
clock_skew_seconds: 60
key_cache_seconds: 3600
key_refetch_min_interval_seconds: 60
passport_aud_required: false
max_visas_per_passport: 100   # 超えたら Passport ごと拒否(D-058 仮)
```

## 6. 試験用 Broker・Visa Issuer の口(yai-aai のサーバー)

| 口 | 中身 | 仕様 |
| --- | --- | --- |
| `GET /.well-known/openid-configuration` | `issuer`・`jwks_uri`・`token_endpoint` | OIDC Discovery(Broker の MUST の一部) |
| `GET /.well-known/jwks.json` | Broker の公開鍵(ES256) | |
| `POST /test/login` | 試験用。利用者名 → PSAT と AT(`aud` を指定)。**試験の設定でだけ有効**。compose で開くのはこの口だけ(D-054) | 独自 |
| `PUT /test/users/{name}/visas` | 試験用。Visa 発行役の利用者表の Visa を差し替える。**`test_visa_endpoint` のときだけ**(local の試験だけ、D-054) | 独自 |
| `POST /token`(`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`) | PSAT → Passport(AAI-ISS-01 の決まりどおり) | AAI 1.2・RFC 8693 |
| `POST /token`(`grant_type=client_credentials`) | サービスの AT | RFC 6749 |
| `GET /visa/jwks.json` | Visa Issuer の公開鍵(`jku` の先) | |

- 秘密を含む応答には `Cache-Control: no-cache, no-store` と `Pragma: no-cache`(AAI-CLI-01)
- 利用者表(`config/test-users.yaml`): `alice`(ds1 の Visa あり)・`bob`(ds2 の Visa あり)・`carol`(Visa なし)・`mallory`(信頼しない Visa Issuer の Visa)

## 7. データの持ち方

- 状態を持たない(鍵と設定だけ)。署名鍵は起動時にファイルから読む(`var/keys/`、git に入れない)。鍵の入れ替えの試験用に、古い鍵と新しい鍵を JWKS に並べる
- Clearinghouse の部品のキャッシュ: 鍵を **(取得元の URL, `kid`)** ごとにメモリに 1 時間(`iss` と `kid` だけで引くと、許可された別の `jku` の鍵と取り違えうる)
- 鍵の取得(JWKS・`jku`)の決まり: `https` だけ(試験環境は手元の CA)。**リダイレクトに従わない**(許可表を確かめた先から別の先へ飛ばされないように)。応答は 64 KiB まで、5 秒で打ち切り。知らない `kid` のときだけ取り直す(取得元ごとに 1 分に 1 回まで)

## 8. 誤りの返し方

- 試験用 Broker: OAuth2 の形(`{"error": "invalid_grant", "error_description": "..."}`)
- Clearinghouse の部品: 呼ぶ側のサービスに理由の種類(`expired`・`untrusted_issuer`・`untrusted_jku`・`bad_signature`・`wrong_token_kind`・`no_matching_visa`)を返す。サービスは 401・403 と短い `msg` にする(理由の細かい中身は外に出さない)

## 9. 仮・未決

- 仮: AT の長さ 15 分、Passport 120 分、試験用 Visa 120 分、署名付き URL 60 分。**Passport・Visa は URL より長くする**(同じだと Option B が成り立たない、自己レビュー R-01)(`system.md` 5 節、D-019)
- 仮: `passport_aud_required=false`(省くと yai-drs の起動で WARNING、D-055)
- 仮: Passport の中の Visa は 100 個まで、超えたら Passport ごと拒否(D-058、G-AAI-10)
- 決めてほしい: なし(D-004 で決まっている)
