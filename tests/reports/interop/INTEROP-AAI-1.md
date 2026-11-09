# INTEROP-AAI-1: 相手の Broker・Issuer(GA4GH Passport Playground)→ yai の Clearinghouse(yai-drs)

- 相手: GA4GH Passport Playground(Connect2026 ハッカソン用、「デモ用」と明記)
  - Broker: ga4gh/passport-playground-broker 1a29660 は README だけ(Ory Hydra v26.2.0 の設定手順。「Token exchange (soon)」)。手順どおり Hydra v26.2.0(`oryd/hydra:v26.2.0`、`serve all --dev`、DSN=memory、`STRATEGIES_ACCESS_TOKEN=jwt`、`URLS_SELF_ISSUER=https://broker.yai.test/`)を compose で立て、admin API(網の中の 4445)で `grant_types: [client_credentials], scope: ga4gh_passport_v1, audience: [yai-drs]` のクライアントを作った
  - Issuer: ga4gh/passport-playground-issuer bfb0232(2026-07-21、Flask。DUOS の Passport 生成を真似たもの)。`deploy/interop/issuer/Dockerfile` で固定
- yai の設定: drs の `trust.brokers` に `{iss: https://broker.yai.test/, jwks_uri: https://broker.yai.test/.well-known/jwks.json}` を足した(相手を信頼した状態で、形の違いだけで落ちることを見るため)
- 向き: 相手の Broker が出したトークンと Issuer の Visa を yai-drs(Clearinghouse)に送る。台本 `aai_check.py`
- 結果: **通らない(相手の未対応)**。確かめ 8/8 は全部予想どおりで、yai-drs は 401 で正しく拒否(`data/INTEROP-AAI-1.json`、2026-10-17 06:3x〜06:4x UTC)

| 確かめ | 結果 | 抜き書き |
| --- | --- | --- |
| Hydra の admin API でクライアントを作る | 201 | `grant_types: [client_credentials], scope: ga4gh_passport_v1, audience: [yai-drs], token_endpoint_auth_method: client_secret_post` |
| Broker(`https://broker.yai.test/oauth2/token`、手元の CA)から AT を取る | 200 | `{"expires_in": 3599, "scope": "ga4gh_passport_v1", "token_type": "bearer"}` |
| AT の中身 | JWT | header `{alg: RS256, kid: …, typ: JWT}`、claims `iss: https://broker.yai.test/, aud: [yai-drs], sub: <client_id>, scp: [ga4gh_passport_v1]`、**`ga4gh_passport_v1` のクレームは無い** |
| Broker の JWKS | 200 | 2 つの kid(yai-drs が trust.brokers から取れる先) |
| yai-drs に AT を Passport として送る(`POST /objects/{ds1}` `passports: [AT]`) | **401** `{"msg": "invalid passport", "status_code": 401}` | yai の `verify_passport` は最初に `typ == vnd.ga4gh.passport+jwt` を見る(AAI-FMT-01)。相手は `typ: JWT` |
| yai-drs に AT を Bearer で送る | **401** `{"msg": "invalid access token", "status_code": 401}` | AT の発行者が yai-aai でない(yai の AT だけを受ける) |
| Issuer の Visa の形(`GET /api/passport/dataset/DUOS-000001`) | 200 | `{"ga4gh_passport_v1": [<dict> ×4]}`。Visa は **JSON の辞書**(`iss: https://duos.org, sub: DUOS-000001, iat, exp, ga4gh_visa_v1: {type: ApprovedUsers, asserted, value, source, by: dac}`)で、JWT でなく署名も無い |
| Issuer の JSON をそのまま Passport として送る | **401** `invalid passport` | JWT でない |

## 通信できなかった相手とエンドポイント

通信は全部できた。**Passport 1.2 の形のトークンが相手から出ない**ので、yai-drs の Clearinghouse の検査(Passport の `typ`・Visa の `jku`・署名)に相手のトークンを通せない。

## 原因の見立てと根拠

- Broker: Hydra は OAuth2/OIDC のサーバーで、`ga4gh_passport_v1` はスコープとして付くだけ。Passport(`typ: vnd.ga4gh.passport+jwt`、`ga4gh_passport_v1` に Visa の JWT の配列)を組み立てる部品(トークン交換 `urn:ga4gh:params:oauth:token-type:passport`)は README が「soon」としている(1a29660)
- Issuer: `passport_service.py` の `visa_from_claim` は dict を返し、`main.py` は `jsonify(passport)`。署名(JWS)の工程が無い(DUOS の Java の実装では署名する部分を、プロトタイプは省いている)
- yai の拒否: `yai/aai/clearinghouse.py` `verify_passport`(typ → 発行者 → 署名 → aud → Visa)。typ の段で止まる。期待どおり

## 解決の案

- 相手: Playground は Passport 1.2 の形で署名したトークンを出す部品が揃っていない(デモ用)。本物の署名付き Visa の確かめは **INTEROP-AAI-2**(keycloak-ga4gh-visa-issuer v1.0.3 = Keycloak の realm の鍵で署名した Visa、REMS v2.41)で
- yai 側: 直すものは無い。`typ`・`aud` の違いは「Passport でないものを Passport として受けない」という yai の設計(G-AAI-05 は Visa の typ の寛容さ)のとおりに働いた
- 分類: 相手の未対応

## 仕様との食い違い

無し(相手が仕様の形に達していないだけ)。研究ノートの「Passport 1.2 への対応を明記した実装は無い」のとおり。
