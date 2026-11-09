# GA4GH 自前実装の言語比較(Python / Rust / Go、TS・Java 一言)

> 調べたのは yai の設計セッションのサブエージェント(2026-10-15)。主の設計者は個々の版を確かめ直していない。本文の「確認済み/推定/未確認」のとおりに読む

観測日: 2026-10-15(JST)。表記:
- 【確認済み(出典)】= 公式レジストリ・GitHub API・公式ドキュメント・実行で今日確かめた
- 【推定】= 経験や一般論にもとづく判断。確かめていない
- 【未確認】= 時間内に確かめられなかった

出典の略号: PyPI = `pypi.org/pypi/<name>/json`、crates = `crates.io/api/v1/crates/<name>`、goproxy = `proxy.golang.org/<module>/@latest`、GH = `api.github.com/repos/...`、raw = `raw.githubusercontent.com` の README・docs、npm = `registry.npmjs.org`、maven = `repo1.maven.org` の maven-metadata.xml、実行 = scratchpad で実際に生成を試した結果。

---

## 0. 先に結論に効く事実(TES/WES の外部 $ref はすべて HTTPS の URL)

- TES 1.1(`openapi: 3.0.1`)は `tesServiceType`・`tesServiceInfo` の `allOf` の中で `https://raw.githubusercontent.com/ga4gh-discovery/ga4gh-service-info/v1.0.0/service-info.yaml#/components/schemas/...` を参照(tes.yaml 651・662 行)。WES 1.1.0(`openapi: 3.0.0`)も 604 行で同じ URL を参照。【確認済み(raw、タグ v1.1 / 1.1.0 の yaml)】
  - つまり「外部 $ref 対応」とはここでは **リモート HTTP 取得**のこと。ローカルの別ファイル参照とは別問題。
- TES 1.1 の yaml は応答コードのキーが引用符なしの `200:`(82・176・197 行ほか)。YAML としては整数キーになる。【確認済み(raw)】→ これで落ちる生成器がある(下の openapi-python-client)。
- DRS 1.5.0 のタグ名は `drs-1.5.0`(`1.5.0` では 404)。`openapi: 3.0.3`、`$ref: ./tags/Introduction.md` のように **Markdown ファイルへの $ref** を説明文に使っている。【確認済み(GH tags、raw)】
- TES の `tesState` と WES の `State` はどちらも 11 値(UNKNOWN, QUEUED, INITIALIZING, RUNNING, PAUSED, COMPLETE, EXECUTOR_ERROR, SYSTEM_ERROR, CANCELED, CANCELING, PREEMPTED。TES は PREEMPTED→CANCELING の順)。【確認済み(raw)】

### 実行して確かめた結果(TES 1.1 の yaml をそのまま入力)

| 道具 | 版 | 結果 |
|---|---|---|
| datamodel-code-generator `[http]` | 0.83.0 | 成功。リモート $ref を取りに行き `Service`/`ServiceType`/`TesServiceInfo(Service)` を生成。ただし「`--allow-remote-refs` なしのリモート取得は deprecated」と FutureWarning。【確認済み(実行)】 |
| fastapi-code-generator `--allow-remote-refs` | 0.8.1 | 成功。`main.py`(`@app.get('/service-info', response_model=TesServiceInfo)` 等)と `models.py` を生成。【確認済み(実行)】 |
| openapi-python-client | 0.29.1 | 失敗 2 段。(1) 応答コードが整数キーで検証エラー。(2) 引用符を付けても「Remote references ... are not supported yet」で `tesServiceInfo`・`tesServiceType` が除外される。【確認済み(実行)】 |
| oapi-codegen `-generate types,std-http-server,strict-server` | v2.8.0 | 成功(rc=0)。リモート $ref が `allOf` の中にあり、既定の `schema-merging-behavior: v2` が取り込んで `TesServiceInfo` に Service のフィールドを展開したため、import-mapping は不要だった。`allOf` の外の素の外部 $ref は未試験。go.mod が無いという警告のみ。生成物のコンパイルは未確認。【確認済み(実行)】 |
| ogen | v1.24.0 | 既定では失敗「external references are disabled」。設定ファイル `parser: allow_remote: true` で成功(server/client/validators 一式)。【確認済み(実行)】 |
| datamodel-code-generator(DRS 1.5.0 を URL で) | 0.83.0 | 失敗「Models not found in the input data」。1 回試しただけで原因(.md への $ref か、paths/ の分割か)は切り分けていない。【確認済み(実行)/原因は未確認】 |
| progenitor / Rust 系 | — | cargo が無く実行していない。【未確認(実行)】 |

→ 設計上の含意: **どの言語でも、まず GA4GH の仕様を 1 ファイルに束ねて(bundle)手元に固定し、それを生成器に渡す**のが言語をまたいで一番安定する。【推定(上の実行結果からの判断)】束ねる道具(Redocly CLI 等)が DRS の `.md` への $ref をどう扱うかは【未確認】。

---

## 1. OpenAPI からのコード生成

### Python

| 名前 | 最新版・日付 | 保守 | 内容 |
|---|---|---|---|
| datamodel-code-generator | 0.83.0(2026-09-24)、Python>=3.10 | GH は `datamodel-code-generator/datamodel-code-generator` に移転(旧 koxudaxi は Moved)、pushed 2026-10-15、open 83 | モデル(pydantic v2 等)のみ。OpenAPI 3.0/3.1/3.2 対応と docs に記載。リモート $ref は `[http]` extra。【確認済み(PyPI・GH・raw docs)】 |
| fastapi-code-generator | 0.8.1(2026-07-01) | pushed 2026-10-12、open 48 | FastAPI のサーバー雛形。モデル層は datamodel-code-generator。`--allow-remote-refs` あり。【確認済み(PyPI・GH・README)】 open issue に「$ref parameter prevents code generation」(#269, 2022)、「$ref from local file」(#104, 2021)。【確認済み(GH search)】 |
| openapi-python-client | 0.29.1(2026-08-30)、Python>=3.11 | pushed 2026-10-14、open 124 | クライアント(attrs+httpx)。「3.0 と 3.1」対応、「still in development and does not support all OpenAPI features」と README 自身が書く。リモート $ref 非対応(上の実行)。【確認済み】 |
| openapi-generator `python-fastapi` | openapi-generator 7.26.0(maven、lastUpdated 2026-10-06) | 本体 pushed 2026-10-15、open 5,756 | 生成器の stability は **BETA**。python-fastapi の open issue は 62 件。【確認済み(maven・raw docs・GH search)】 |

### Rust

| 名前 | 最新版・日付 | 内容 |
|---|---|---|
| progenitor(Oxide) | 0.15.1(2026-10-09) | クライアントのみ(positional/builder、CLI・httpmock も)。README は「OpenAPI 3.0.x」。**外部 $ref 非対応の issue #228(2022-11、open)**、**3.1 対応 #1268(2025-12、open)**、循環 oneOf+discriminator でスタックオーバーフロー #1252(open)。【確認済み(crates・raw・GH search)】→ TES/WES をそのまま渡すと外部 $ref で止まると見られる【推定】 |
| typify(Oxide) | — | progenitor が使う JSON Schema→型生成。pushed 2026-10-14。【確認済み(GH)】 |
| dropshot(Oxide) | 0.17.1(2026-06-02) | 逆向き(Rust コード→OpenAPI を出す)サーバー。仕様を正とする今回とは向きが逆。【確認済み(crates)/向きの評価は推定】 |
| openapi-generator `rust-axum` | 7.26.0 | stability **BETA**。open issue 例: `additionalProperties: false` で未知フィールドを拒否しない #25255(2026-10-14)、整数 enum を文字列で直列化 #23450。【確認済み(raw docs・GH search)】 |
| oas3 | 0.22.0(2026-05-06) | 仕様を読むライブラリ(生成器ではない)。stars 80。【確認済み(crates・GH)】 |
| openapiv3 | 2.2.0(2025-06-02) | 3.0 用の型。更新が止まり気味。【確認済み(crates)】 |
| utoipa | 6.0.0(2026-09-22) | 逆向き(コード→OpenAPI)。【確認済み(crates)】 |
| axum | 0.8.9(2026-04-14) | 手書きサーバーの土台。【確認済み(crates)】 |

→ Rust は「仕様からサーバー」を作る道具が BETA の openapi-generator しかなく、型だけ typify/progenitor で作り、axum のハンドラーは手書きになると見られる。【推定】

### Go

| 名前 | 最新版・日付 | 内容 |
|---|---|---|
| oapi-codegen | v2.8.0(2026-07-17)、runtime v1.7.0 | 型・クライアント・サーバー(chi/echo/gin/fiber/gorilla/iris/`net/http`)。**strict server**(応答型まで型で縛る)あり。外部 $ref は `import-mapping` で別パッケージに対応付ける方式(URL もキーにできる)。strict server と import-mapping の組み合わせに制約(#2010)。open issue: nested external references #1808、外部 responses を参照できない #958 など。allOf/oneOf/anyOf は `schema-merging-behavior` v1/v2(既定)/v3 から選ぶ。README は 3.1 対応(#2336)を掲げるが、それが v2.8.0 に入っているかは【未確認】。【それ以外は確認済み(goproxy・raw README・GH search・実行)】 |
| ogen | v1.24.0(2026-08-07) | 型・クライアント・サーバー一式。リフレクションなし、`OptNilString` などの Optional/Nullable ラッパー、**oneOf を和型(sum type)で生成**、検証コードも生成。リモート $ref は設定 `allow_remote` が必要(既定で無効)。open issue に allOf 関連 12 件(例 #1571 allOf で min/max を再定義できない)。3.1 対応の明記は README で見つからず【未確認】。【確認済み(goproxy・raw・GH search・実行)】 |
| openapi-generator `go-server` | 7.26.0 | stability **STABLE**。【確認済み(raw docs)】 |

### TypeScript・Java(一言)
- TS: openapi-typescript 7.13.0 / openapi-fetch 0.17.0(どちらも 2026-02-11)。型とクライアントのみ。【確認済み(npm)】
- Java: openapi-generator 自体が Java 製で、Spring 系生成器が最も成熟していると言われる【推定】。GA4GH starter kit(DRS)は Java。【確認済み(GH)】

---

## 2. JWT・JOSE(AAI 1.2 / Passport 1.2 向け)

### Python

| 名前 | 最新版・日付 | 保守・特徴 |
|---|---|---|
| joserfc | 1.7.5(2026-08-29) | Authlib の作者による後継。open issue 0。**既定で "recommended" のアルゴリズムしか許さず**、それ以外は `algorithms=` か `registry=` で明示が必要。`none` は deprecated。クレーム検証は `JWTClaimsRegistry`(essential、exp/nbf/iat の既定検証)。【確認済み(PyPI・GH・jose.authlib.org)】JWKS の取得・キャッシュ機能は見つからず【未確認】 |
| Authlib | 1.9.1(2026-10-14) | `authlib.jose` は 1.7 で deprecated。docs には「1.8 で削除」とあるが、main のコードは `deprecate("... use joserfc instead.", version="2.0.0")` で、1.9.1 でもまだ残っている(docs とコードが食い違う)。【確認済み(PyPI・docs・raw)】→ 新規は joserfc を使う |
| PyJWT | 2.15.1(2026-09-28) | `PyJWKClient` が JWKS を取得・キャッシュし、kid が無ければ取り直す。`algorithms=` 指定で alg 固定。FastAPI 公式チュートリアルは PyJWT を使う。【確認済み(PyPI・pyjwt docs・fastapi docs)】 |
| jwcrypto | 1.6.1(2026-09-15) | Red Hat 系(latchset)。open 10。【確認済み(PyPI・GH)】 |
| python-jose | 3.5.0(**2025-05-28**) | 1 年以上リリースなし、open 125。【確認済み(PyPI・GH)】→ 新規採用は避ける【推定】 |

### Rust

| 名前 | 最新版・日付 | 特徴 |
|---|---|---|
| jsonwebtoken | 11.1.0(2026-09-16) | 10.0 から暗号バックエンド(`aws_lc_rs` か `rust_crypto`)の選択が必須。11.0 で破壊的変更多数。`Validation::new(Algorithm::RS256)` で alg を固定。`JwkSet`/`Jwk` 型はあるが **JWKS の HTTP 取得・キャッシュは無い**(README の例は n/e から鍵を作るだけ)。10.3.0 で「exp/nbf の型の取り違え」を修正。【確認済み(crates・raw README・CHANGELOG)】 |
| jwt-simple | 0.15.0(2026-09-25) | 【確認済み(crates)】中身は未調査 |
| josekit | 0.10.3(**2025-05-20**) | GH の push も 2025-05。止まり気味。【確認済み】 |
| jose-jwk(RustCrypto/JOSE) | 0.1.2(**2023-08-21**) | 止まり気味。【確認済み】 |
| biscuit | 0.8.0(2026-04-05) | 【確認済み(crates)】 |

→ Rust では JWKS 取得・キャッシュ・jku の許可リストを自分で書く量が一番多い。【推定(上の機能の有無からの判断)】

### Go

| 名前 | 最新版・日付 | 特徴 |
|---|---|---|
| lestrrat-go/jwx v3 | v3.3.0(2026-09-08)、v2 も v2.1.7(2026-06-29) | `jwk.Cache` で JWKS を自動更新。**`jku` での検証は `jws.VerifyAuto` + `WithFetchWhitelist` を明示したときだけ**、既定は全 URL 不許可、https 必須、kid 一致必須。`jwt.WithKey(jwa.RS256(), key)` で alg と鍵を組で渡す。open issue 6。【確認済み(goproxy・raw docs・pkg.go.dev・GH)】 |
| go-jose v4 | v4.1.6(2026-10-14T22:46Z = 10/15 早朝 JST) | `ParseSigned(s, []SignatureAlgorithm)` と、**許すアルゴリズムの列挙が引数で必須**。【確認済み(goproxy・pkg.go.dev)】 |
| golang-jwt/jwt v5 | v5.3.1(2026-01-28) | `WithValidMethods`・`WithExpirationRequired` を付けないと緩い。JWKS は別ライブラリ(MicahParks/keyfunc v3.8.2、2026-09-04)。【確認済み】 |

### TS・Java
- TS: `jose`(panva)6.2.12(2026-09-05)。【確認済み(npm)】
- Java: nimbus-jose-jwt 10.10(2026-09-17)。【確認済み(maven)】

### alg confusion・jku の観点(まとめ)
- 3 言語とも「alg を呼び出し側が固定する」API は主流ライブラリにある(PyJWT `algorithms=`、joserfc の既定制限、jsonwebtoken `Validation::new`、go-jose v4 は必須引数)。【確認済み】
- jku を安全に扱う道具が既製であるのは Go の jwx v3 だけ(確かめた範囲で)。Python・Rust は許可リスト付きの取得を自分で書く。【確認済み(jwx)/他言語に無いことは未確認寄りの推定】
- Passport の「Visa の jku を信頼済みブローカーの一覧と突き合わせる」部分はどの言語でも自前の仕様判断になる。【推定】

---

## 3. コンテナ操作

| 言語 | 名前 | 最新版・日付 | 備考 |
|---|---|---|---|
| Python | docker(docker-py) | 7.2.0(2026-07-09) | pushed 2026-10-14、open 575。同期 API。【確認済み(PyPI・GH)】 |
| Rust | bollard | 0.21.1(2026-08-16) | 非同期。Docker と Podman を first-class、rootless Podman のソケット自動検出、ssl/ssh feature。【確認済み(crates・README)】 |
| Go | `github.com/moby/moby/client` | v0.6.2(2026-10-08)、API 型は `github.com/moby/moby/api` v1.56.1(2026-10-01) | **今の置き場所はこちら**。旧 `github.com/docker/docker` は v28.5.2+incompatible(2025-11-05)が最後。docker CLI 自身がこの client を使う(README)。v0.x なので API 変更に注意【推定】。【確認済み(goproxy・raw README)】 |
| TS | dockerode 5.0.1(2026-06-24) | 【確認済み(npm)】 |
| Java | docker-java 3.7.1(maven lastUpdated 2026-03-18) | 【確認済み(maven)】 |

---

## 4. 既存の GA4GH 実装の言語

| 実装 | 言語(GH の language) | 状態 |
|---|---|---|
| Funnel(TES) | Go | 正本は **calypr/funnel**(ohsu-comp-bio/funnel は「Redirect repo」で language が HTML と出る)。最新 v0.11.14(2026-06-11)、pushed 2026-10-14。【確認済み(GH)】 |
| TESK(TES) | **Python**(Python 107KB、Shell・Dockerfile のみ) | pushed 2026-07-23。昔の Java 版からの書き換えは済んでいると見られる。【確認済み(GH languages)】 |
| Sapporo(WES) | Python(GH の language は HTML と出るが、内訳は HTML 1.19MB・Python 714KB で、HTML は生成物と見られる) | pushed 2026-10-14。【確認済み(GH)/HTML の正体は推定】 |
| Toil(WES サーバーを含む) | Python | pushed 2026-10-15。【確認済み】 |
| cwl-WES | Python | pushed 2026-07-22。【確認済み】 |
| drs-filer | Python | pushed 2026-07-25。【確認済み】 |
| ga4gh-starter-kit-drs | Java | pushed 2026-05-28。【確認済み】 |
| Gen3 indexd(DRS) | Python | pushed 2026-10-14。【確認済み】 |
| Microsoft ga4gh-tes | C# | **archived**(最終 push 2025-05-16)。【確認済み】 |
| GBA-BI/tes-api(Bio-OS TES) | Go | pushed 2025-10-07。tes-scheduler・tes-filer・tes-k8s-agent も Go。【確認済み】 |
| stjude-rust-labs/tes | Rust | TES 用 crate `tes` 0.11.0(2026-10-01)。【確認済み(GH topic・crates)】 |
| JaeAeich/poiesis(TES) | Python | pushed 2026-08-01。【確認済み(GH topic ga4gh-tes)】 |
| CESNET/tesp-api(TES) | Python | pushed 2026-05-12。【確認済み】 |

→ 数えた 13 件では **Python 8(TESK・Sapporo・Toil・cwl-WES・drs-filer・indexd・poiesis・tesp-api)、Go 2(Funnel・GBA-BI)、Java 1、C# 1(archived)、Rust 1**。【確認済み(上の数え上げ。母集団は依頼の列挙+GH topic で、網羅ではない)】

---

## 5. 状態機械・型での表現(すべて【推定】。ただし挙げた enum・仕様の値は上で確認済み)

### 言語ごとの表現力
- Rust: `enum TesState { Queued, Running, ... }` は網羅性検査(`match` の漏れがコンパイルエラー)。さらに typestate(`Task<Queued>` → `Task<Running>` のように型で遷移を表し、`cancel()` を許す状態の型にだけ実装する)で「不正な遷移」をコンパイル時に止められる。データ付きの enum(`Complete { exit_code }` など)で「この状態のときだけあるフィールド」も表せる。`Option<T>` は使う前に取り出しを強制する。
- Go: ogen も oapi-codegen も `type TesState string` + 定数(実行で確認、ogen 1656 行・oapi-codegen 607 行)。任意の文字列を `TesState("FOO")` で作れる(キャストは検査されない)。oapi-codegen v2.8.0 は `func (e TesState) Valid() bool` を生成するので実行時の 1 行ガードは書ける。`switch` の網羅検査は言語にない(linter の exhaustive で補う)。ゼロ値(空文字・nil)が「欠落」と区別しにくい。ogen の `OptNil*` は改善策。typestate は書けるが冗長。
- Python: pydantic + `Literal`/`Enum` で **実行時の入口検証**は強い(必須フィールド欠落・未知の state 文字列は 422 で弾く)。静的には mypy/pyright の `assert_never` で網羅検査ができ、`Optional` の未チェック参照も pyright strict なら警告。ただし型注釈は実行を止めないので、CI で型検査を必須にしないと効かない。typestate は Generic で真似られるが実用的でない。

### 型で防げる誤り(例)
1. 状態の取り違え: `"CANCELLED"`(綴り違い)や TES にない状態を返す → enum/Literal で防げる(3 言語とも、Go は弱い)。
2. 状態に依存する操作: QUEUED でない Task に `start()`、終了済みに `cancel()` → Rust の typestate なら型で防げる。Python・Go は実行時の検査。
3. トークンの種類の取り違え: Passport JWT と Visa JWT、アクセストークンを同じ `str` で回して、Visa を Passport の検証関数に渡す → `NewType`(Py)・newtype(Rust)・定義型(Go)で区別できる。「検証済み」と「未検証」を別の型にする(`UnverifiedJwt` → `verify()` → `VerifiedVisa`)のも同様。
4. 必須フィールドの欠落: `DrsObject` の `id`・`self_uri`・`size`・`created_time`・`checksums` → 生成モデルで防げる(Go は空値と欠落の区別が弱い)。
5. Option の扱い: `access_url` が無い `AccessMethod` で `access_url.url` を読む → Rust は強制、pyright strict は警告、Go は nil 参照で実行時 panic。

### 型では防げない解釈の誤り(例)
1. 状態遷移の意味: CANCELING の後に COMPLETE になってよいか、PREEMPTED が何を義務づけるか、WES の run が終了状態に入った後に state が変わってよいか → 型は「値の集合」は表せても、仕様本文の義務(MUST/SHOULD)は表せない。
2. AAI/Passport の `tokens[]` と passports の食い違い: DRS の `tokens`/`passports` で渡された内容と、検証済み Visa の内容が一致しないときにどちらを信じるか、拒否か無視か → 仕様の読み方の問題で、型は無力。
3. 時刻・期限の意味: `exp` を要求受付時刻で見るか、タスク開始・ファイル取得時刻で見るか。Visa の `asserted`(付与時刻)と `exp` の関係、leeway をどれだけ取るか。秒の UNIX 時刻と RFC 3339 文字列の取り違えは型で防げるが、「どの時点の時刻で判断するか」は防げない。
4. 署名の信頼の連鎖: jku の URL が信頼済みブローカーのものか、`iss` と jku のホストが一致すべきか → 型ではなく設定とテスト。
5. TES 1.1 の応答コードのように「仕様ファイル自体の癖」(整数キー)をどう扱うか → 型以前の問題。

→ 学習用には「型で防げる部分は生成モデル+enum で機械的に固め、解釈の部分は仕様の文を引用したテストケースにする」と分けるのが、どの言語でも同じ形で効く。【推定】

---

## 6. 学習用としての作りやすさ(すべて【推定】)

- Python(FastAPI): 仕様→サーバー雛形→モデルが実行で通った(datamodel-code-generator・fastapi-code-generator)。PyJWT の `PyJWKClient`・joserfc で JWKS と alg 固定がすぐ書ける。既存実装の多数派なので読む手本が多い。弱点は型検査が任意で、状態遷移の誤りを実行時まで見逃しやすいこと。
- Go: oapi-codegen の strict server か ogen で「仕様→型付きサーバー」が最も揃っている(実行で通った)。jwx v3 が JWKS キャッシュと jku 許可リストまで持つので AAI 部分が一番楽。enum の弱さ・ゼロ値の曖昧さは linter と ogen の Opt 型で補う。
- Rust: 型で防げる範囲は最大(typestate・網羅的 match・Option 強制)。ただしサーバー生成は BETA の rust-axum しかなく、progenitor は外部 $ref と 3.1 に未対応、JWKS 取得・キャッシュ・jku は自作。学習コストと手書き量が最大。「型で仕様を写す練習」には一番向く。
- TS: 型生成とクライアントは楽だがサーバー側生成と既存 GA4GH 実装の手本は少ない(確かめた範囲で 0 件)。
- Java: GA4GH starter kit がある。生成器は最も成熟と言われるが、学習用には重い。

---

## 7. 「主の言語を Python 以外にしたほうがよいか」の材料

**確認済みの事実**
1. 確かめた GA4GH 実装 13 件のうち Python が 8 件(TESK も今は Python)、Go 2、Rust・Java・C# 各 1(C# の Microsoft 版は archived)。(GH、2026-10-15)
2. TES/WES の service-info は HTTPS URL の外部 $ref。TES のみ実行(WES は未実行)で、そのままで通ったのは datamodel-code-generator・fastapi-code-generator・oapi-codegen、設定 1 行で ogen。openapi-python-client は不可、progenitor は外部 $ref 非対応の issue が open。(実行・GH、2026-10-15)
3. Python の JWT は python-jose が 2025-05 から止まり、FastAPI 公式は PyJWT、Authlib は joserfc へ移行中。PyJWT に JWKS キャッシュあり。(PyPI・docs、2026-10-15)
4. Go の jwx v3 は JWKS キャッシュと jku 許可リスト検証を内蔵。Rust の jsonwebtoken は JWKS 取得・キャッシュなし、josekit・jose-jwk は 2025-05・2023-08 で止まり気味。(goproxy・crates・docs、2026-10-15)
5. Rust のサーバー生成器(openapi-generator rust-axum)と python-fastapi は BETA、go-server は STABLE。(openapi-generator docs、2026-10-15)

**推定**
6. 主を Python のままにする理由は十分ある(手本の数・生成器が実際に通る・JWT ライブラリが揃う)。変える強い理由は見当たらない。
7. 「他言語でも作りやすい設計」にするには、言語選びより (a) 仕様を 1 ファイルに束ねて手元に固定、(b) 状態遷移表と検証規則をコードでなくデータ(表・テストベクトル)で持つ、(c) 解釈の部分は仕様の文を引用したテストにする、の 3 点が効く。
8. 2 本目の言語を足すなら、AAI と生成器の揃い方で Go、型で仕様を写す学びを重視するなら Rust。Rust は手書き量が最大。
9. Python で型の恩恵を取りに行くなら pyright strict と `assert_never` を CI で必須にする。それでも状態遷移は実行時検査になる。
10. DRS 1.5.0 の分割ファイル(.md への $ref を含む)は、そのまま渡すと datamodel-code-generator で失敗した(1 回のみ・原因未切り分け)。束ね方の確認が最初の作業になる。

---

### 未確認として残したもの
- progenitor・rust-axum を TES/WES に実際にかけた結果(cargo なし)
- oapi-codegen の 3.1 対応が v2.8.0 リリースに入っているか、ogen の 3.1 対応
- 生成した Go コードのコンパイル可否
- DRS 失敗の原因、Redocly 等で束ねたときに `.md` への $ref がどうなるか
- joserfc・Rust 系に JWKS 取得・キャッシュ機能があるか(見つけられなかっただけ)
- GH の search は認証なしで 1 分 10 回・core は 1 時間 60 回の制限があり、issue 調査は上位数件だけ
