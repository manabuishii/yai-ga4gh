# 試験の計画(test-plan)

- 作成: 2026-10-15。試験は 3 層+2 種
  - (a) 公式の適合性試験(2 節)
  - (b) 仕様から起こした試験(3 節、`docs/spec-obligations.md` の全行)
  - (c) yai が必要だと思う試験(4 節、拒否・別の利用者・期限切れ・再起動・取り消しの競合など)
  - 4 つまとめて動くことの試験(5 節)
  - 他の実装との相互運用の試験(6 節)
- **操作は HTTP の外から叩く**(実装の中を import しない)。試験のデータは `tests/cases/*.yaml`。同じ試験を、別の言語の実装にも、他の実装にも当てられるようにする(`docs/design/language.md` 2 節)
- **観測は「試験用の観測役」を通してよい**: ログのファイル、SQLite の中身、`docker inspect`・`docker ps` の結果を読む役。実装の中には入らず、ファイル・SQL・docker の外から読むだけ。観測が要る試験は、他の実装に当てるときは観測の部分を飛ばす(その旨を試験のデータに書く)
- 試験の種類を分ける: **共通**(仕様の義務だけを見る。他の実装にもそのまま当てる)/**yai 固有**(yai の scope・tags・ページの印・障害の差し込み点を使う。yai だけ)/**相手しだい**(相手が対応しているときだけ)
- 道具(仮): pytest+httpx、形の検査は openapi-core(応答を OpenAPI に照らす。**ただし TES の 4xx・5xx は仕様に定義が無いので OpenAPI に照らさず、yai の誤りの形 `{msg, status_code}`(D-011)に照らす**)。lint は ruff。`make check` で全部(GitHub には出さない、D-002)

## 1. 各層をいつ通すか

| 段階(計画 第 3 版) | (a) | (b) | (c) | まとめ | 相互運用 |
| --- | --- | --- | --- | --- | --- |
| 1. 4 つの動く最小版(**横断の試験は入れない**:WES→TES・WES→DRS をまたぐもの、障害の差し込みで WES を止めるものは段階 2。義務の表の ◯ のうち、主語が WES の解決役(DRS のクライアント)の行 DRS-URI-01〜04・DRS-AUTH-07 は段階 2 で数える) | TES・DRS の公式の試験を回し、落ちた項目をすべて「版の違い」「道具の前提(認証など)」「yai の誤り」に分け、**「yai の誤り」が 0** | 最小版が ◯ の行がすべて通る | 4 節の「最小版」印の行が通る | — | — |
| 2. 4 つまとめた試験 | 同上(戻りの確認) | 同上 | 同上 | 5 節がすべて通る | — |
| 3. 相互運用 | 同上 | 同上 | 同上 | 同上 | **INTEROP-TES-1(相手のクライアント → yai-tes)と INTEROP-TES-2(yai-wes → 相手の TES)が通ること**が最低の条件。そのあと INTEROP-DRS-1(公開の DRS)、INTEROP-AAI-1(外で署名された Passport)の順。通らない組は「yai の不具合/相手の未対応/環境の不備/仕様の差」に分け、**yai の不具合は 0**。やっていない組は終わりに数えない |
| 4. 深める | 落ちる項目を減らす | △・後の行 | 残り | 長い run など | 残りの組 |

## 2. (a) 公式の適合性試験

調べ: `docs/research/2026-10-15_interop.md` 2 節(2026-10-15)。版とコミットは設計者が `git ls-remote`・PyPI で確かめ直した。

| API | 道具 | 使う版・コミット | 合わない部分 | yai での使い方 |
| --- | --- | --- | --- | --- |
| TES | ga4gh/openapi-test-runner | 既定ブランチ dev **`bc3eb911e877521409348e0225852d072d4de196`**(2026-07-24)。タグ・PyPI は無いので git のコミットで入れる | 試験は TES 1.0.0・1.1.0 の両方(`--version 1.1.0` を指定)。認証の扱いは未確認(G-TES-01 と一緒に確かめる) | まず「全部公開」の仮の実装の設定の yai-tes に当てる。認証ありの振る舞いは (b)(c) で(CONF-TES) |
| DRS | ga4gh/drs-compliance-suite | PyPI **1.0.3**、main **`ad9a48420ba818b71007e9fa046d11187ba068aa`**(2025-05-06) | **DRS 1.2.0 だけ**(1.3.0 の schema はあるが使われていない)。1.3 以降の POST・`passports`・OPTIONS・一括は試せない | 1.2.0 と共通の部分(GET・`/access`・service-info)だけ。落ちた項目を「版の違い」「道具の前提」「yai の誤り」に分ける(CONF-DRS) |
| DRS・TES・WES(補助) | SynapticFour/HelixTest | v0.1.3(2026-08-24) | 同梱の schema は DRS **1.4.0**・TES 1.1.0・WES 1.1.0。1 社の道具で、README 自身が「GA4GH の正式な認証ではない」 | 補助の確かめだけ。**合否の根拠にしない** |
| WES | 公式は空(ga4gh/compliance-tests-ga4gh-wes は LICENSE と README だけ) | — | — | (b)(c) と相互運用(6 節)で埋める |
| AAI・Passport | 公式は見つからない | — | — | (b)(c) で埋める(`aai.md` 3 節の表の全セル) |

- CONF-TES・CONF-DRS: 道具の出す報告をそのまま `tests/reports/` に保存し、落ちた項目ごとに理由を 1 行書く

## 3. (b) 仕様から起こした試験

- `docs/data/obligations.yaml` の 1 行 = 試験 1 つ以上。試験の名前 = 義務の ID(例 `test_DRS_ID_02`)
- 1 行につき **成り立つ例と、破れたら落ちる例(負の例)の両方**を書く。負の例が書けない行(「定義」だけの行)は、その旨を `tests/cases/<ID>.yaml` に書く
- **期待する結果は試験のデータ(`tests/cases`)に、表(`permissions.yaml`・状態の表)とは別に書く**。実装と試験が同じ表を読むだけだと、表そのものの誤りが見つからない
- **障害の差し込み点**(試験の設定でだけ有効): 外の操作(コンテナの起動・出力の転送・CreateTask・CancelTask・状態の書き込み)の直前と直後に、外から「ここで止まる」「ここで落ちる」を指定できるようにする。HTTP の外からの試験でも、落ちる場所を決めて再現できる
- 時刻・期限の試験は試験用の時計(サービスの設定で外から進められる)で測る。試験用の時計は手元の環境(local)だけで開く(compose では閉じる、D-054)。鍵の先・DRS の先は、受けた要求を数えられる試験用のサーバーにする
- 本文にだけある義務(場所=本文)を優先する。形(schema)の検査は openapi-core が全応答に自動でかける(行を増やさない)
- 例
  - `DRS-ID-02`: 登録した元のファイルを書き換えたあとも、同じ ID のチェックサムと中身が変わらない
  - `TES-ST-01`: 出力の置き場を書けなくした設定で、executor が 0 で終わっても `COMPLETE` にならず `SYSTEM_ERROR`
  - `WES-LST-03`: alice の AT で ListRuns したとき、bob の run が 1 つも入らない(ページをすべて辿って確かめる)
  - `AAI-CH-07`: 許可表に無い `jku` の Visa を含む Passport で、**`jku` の先に要求が 1 回も来ない**(偽の `jku` の先を立てて数える)
  - `PP-VISA-02`: 空でない `conditions` を持つ Visa だけの Passport では 403
- 数は `spec-obligations.md` 5 節(道具が数える)。2026-10-15 23:3x の時点で 114 行、最小版の ◯ は 93 行

## 4. (c) yai が必要だと思う試験

「段階」の列: その試験を終わりの条件にする段階(1=4 つの最小版、2=4 つまとめた試験、後=深める)。サービスをまたぐ試験は 2。

| ID | 対象 | 中身 | 段階 |
| --- | --- | --- | --- |
| NEG-AUTH-NONE | 全 API | 資格情報なしで、要るはずの操作が 401 | 1 |
| NEG-AUTH-WRONG-AUD | 全 API | 別のサービスあての AT(`aud=yai-tes` を WES に)が 401 | 1 |
| NEG-AUTH-KIND | 全 API | Passport を Bearer に、PSAT を Bearer に入れると 401(種類の取り違え) | 1 |
| NEG-AUTH-ALG | 全 API | `alg=none`・`HS256`(公開鍵を秘密として使う)の JWT が 401 | 1 |
| NEG-AUTH-EXPIRED | 全 API | 期限切れ(60 秒のずれを超えた)の AT・Passport・Visa | 1 |
| NEG-AUTH-NOSCOPE | 全 API | 有効だが権限なし(scope が足りない)が 403 | 1 |
| NEG-OTHER-USER | TES・WES | bob が alice のタスク・run を取得・取り消し・ログ・出力 → 404。一覧に出ない | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-TAG-SPOOF | TES | 利用者が `yai.on_behalf_of` を付けて CreateTask → 400 | 1 |
| NEG-PASSPORT-NOT-LOGGED | WES | Passport が `RunLog.request`・DB・ログのどこにも出ない(ログの全文を探す) | 1 |
| NEG-PASSPORT-NOT-IN-TES | WES→TES | TES の task_json・tags に Passport が入らない | 2 |
| NEG-DRS-ACCESSID-REUSE | DRS | 別のオブジェクトの `access_id`・署名を流用 → 404・403 | 1 |
| NEG-DRS-URL-TAMPER | DRS | 署名付き URL の `exp`・オブジェクト ID を書き換え → 403 | 1 |
| NEG-DRS-URL-EXPIRED | DRS | 期限を 2 秒にした設定で、3 秒後の取得が 403 | 1 |
| NEG-DRS-BACKDOOR | DRS | `dataset` の方針のオブジェクトを、署名なしで配信口から取れない(ファイル置き場への別の道が無い) | 1 |
| NEG-DRS-REVOKE | DRS | Visa の期限が切れた利用者は新しい `/access` を取れない。出し済みの Visa は `exp` まで使える。URL は Visa より先に切れ、流している途中の取得も URL の期限で切れる(`aai.md` 4 節)。Option B の境目(`now + 60 分 + 60 秒` と `exp` の前後 1 秒)も試す。試験用の時計を進めて測る | 1 |
| NEG-DRS-REVOKE-AT-ISSUER | DRS | 試験用の Visa 発行役の利用者表から権限を外したあとも、出し済みの Visa・Passport では Option B の窓(Visa の `exp` − URL の期限 − 60 秒)まで `/access` が通る(仕様の限界、AAI-FMT-06)。新しく発行した Passport では 403(ユーザー 2026-10-16 00:39) | 1 |
| NEG-DRS-REASSIGN | DRS | `yai drs set-dataset` でオブジェクトの dataset を変えた直後から、元の dataset の Passport での新しい `/access` は 403、新しい dataset の Passport では 200。出し済みの署名付き URL は自分の期限まで使え、期限の後は 403(同上) | 1 |
| NEG-DRS-MIXED-POLICY | DRS | `metadata_policy=public`・`access_policy=dataset` のオブジェクト: メタデータは誰でも、配信口は署名なしで 403、Passport なしの `/access` は 401 | 1 |
| NEG-PASSPORT-SEND-UNTRUSTED | WES→DRS | 入力に、許可していない DRS のホストの `drs://`、`passport_auth_issuers` が Passport の Visa と重ならない DRS、リダイレクトする DRS を混ぜる → その先に Passport が 1 回も届かない(偽の DRS で受信を数える)、run は SYSTEM_ERROR(DRS-AUTH-07) | 2 |
| NEG-JKU-REDIRECT | AAI | 許可した `jku` が別の先へリダイレクトする → 従わずに拒否。応答が 64 KiB を超える・5 秒を超える鍵の先も拒否 | 1 |
| NEG-VISA-KINDS | AAI | `jku` と `scope`(`openid` なし)を持つ Visa は受ける、`scope` に `openid` を含む Visa は無視、`jku` の無い Visa は無視、`typ` が無い・`JWT`・`vnd.ga4gh.visa+jwt` は受ける、`nbf` が未来なら無視、`asserted` が無い・整数でない・未来なら無視 | 1 |
| NEG-MULTI-CRED | DRS | Bearer と `passports` を同時に、また別人の Passport 2 つを送る → どちらか 1 つで満たせば通り、2 つの属性を合わせないと通らない方針は通らない。壊れた資格情報が 1 つでもあれば 401 | 1 |
| NEG-AAI-UNTRUSTED-ISS | AAI | 信頼しない Broker の Passport・信頼しない Visa Issuer の Visa | 1 |
| NEG-AAI-KEY-ROTATION | AAI | 鍵を入れ替えたあと、新しい鍵のトークンが通り、外した鍵のトークンが落ちる | 1 |
| NEG-AAI-MULTI-ID | AAI | Passport の `sub` と違う `sub` の Visa でも、1 つの Visa で判定できれば通る。2 つの身元の Visa を組み合わせる方針は無い(最小版) | 1 |
| NEG-AAI-OPTB | AAI | Visa の残りが署名付き URL の期限より短いとき URL を出さない | 1 |
| NEG-PATH | TES | `..`・相対パス・`/proc` などの入出力の `path` → 400 | 1 |
| NEG-SSRF | TES・WES | 許していないホスト・ポートの `https` の入力・`workflow_url` → 400。許したホストから許していない先へのリダイレクト → 従わない。大きすぎる・遅すぎる応答 → 打ち切り | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-ATTACH-NAME | WES | `../x`・`/etc/x`・空の名前の添付 → 400 | 1 |
| NEG-PASSPORT-BIG | WES | Visa 20 個の Passport(数十 KB)を `yai_passports` で渡しても受け付けられる | 1 |
| NEG-PASSPORT-BAD-SHAPE | WES | 形の壊れた Passport(JWT でない・`typ` が違う・`ga4gh_passport_v1` が無い)と、期限切れ(60 秒のずれを超えた)の Passport を `yai_passports` で渡す → RunWorkflow が 400 で断り、run が作られない(ListRuns に出ない)。`wes.md` 2 節の「形と期限だけ検査」に当たる(ユーザー了承 2026-10-16 00:2x) | 1 |
| NEG-SYMLINK-OUTPUT | TES | executor が出力の場所に、実行役の機械のファイルを指すリンクを作る → 置き場に写されず SYSTEM_ERROR | 1 |
| NEG-IMAGE-OPTION | TES・WES | `-` で始まる image(`--privileged` など)・CWL の `dockerPull` を受け付けで 400。検査をすり抜けても `docker run` の argv では `--` の後ろに置くのでオプションにならない(D-041 (1)) | 1(TES・WES の単体)・2(compose の Docker の実行役) |
| NEG-SYMLINK-STDOUT | TES | コンテナが stdout・stderr の宣言の場所にリンク・FIFO・ファイルを置いても、実行役はリンクの先に書かず FIFO を開かず、名前で消して普通のファイルを置く(D-041 (2)) | 1(単体)・2(compose の Docker の実行役) |
| NEG-SYMLINK-MOUNT | TES | 前の executor がマウント元の場所をリンクに差し替えても、次の executor に付けるディレクトリは変わらない(タスクの宣言だけで決める)。コンテナの中のパスの `:` は 400(D-041 (3)・(4)) | 1(単体)・2(compose の Docker の実行役) |
| NEG-CANCEL-NO-COPY | TES・WES | 取り消し・時間切れのとき、executor が書いた出力を置き場へ写さず(一時の名前も残さない)、宣言した stdout・stderr へも写さない。TES の作業ディレクトリと WES の `runs/<id>/` が残らない(D-041 (2)・D-030・D-043) | 1(単体・手元のプロセスの実行役)・2(compose の Docker の実行役) |
| NEG-FIFO | TES | 出力の場所・次の executor の stdin・stdout・stderr に FIFO を置いても、worker は開いて待たずに終わらせ(SYSTEM_ERROR か EXECUTOR_ERROR)、実行の枠が空いたままで次のタスクが動く。写す元・公開する出力が FIFO なら UnsafePath(D-042) | 1(単体)・2(compose の Docker の実行役) |
| NEG-YAML-ALIAS | WES | CWL の YAML の alias(merge の `<<` も)と深すぎる入れ子を受け付けで 400(展開しない。D-042) | 1(単体)・2(4 つまとめた試験) |
| NEG-BODY-LIMIT | TES・DRS・AAI | 本文を読みながら数え、上限(TES 64 MiB・DRS と AAI の /token 1 MiB)を超えたら解析より前に止める。**TES は認証の後に読む**(認証なしは本文を読まずに 401、D-056)。DRS と /token は認証より前。Content-Length の無い chunked の要求でも止まる(D-042) | 1(単体)・2(4 つまとめた試験) |
| NEG-REDOS-LOG | 全部 | ログの伏せ字が長い行で O(n²) にならない(`http://` を並べた 1 MiB の行で 2 秒以内。D-042) | 1 |
| NEG-JKU-SLOW | AAI | Visa の `jku` の鍵の取得元が遅い・少しずつ送り続ける → 取り始めから 5 秒ほどで打ち切り、その Visa を使わない(key_unavailable) | 1 |
| NEG-AUTH-CLIENT-ID | 全 API | AT の `client_id` が無い・空・文字列でない → 拒否(`aai.md` 3 節) | 1 |
| NEG-DRS-URL-EXPIRES-MID | DRS | 署名付き URL で流している途中で期限が来たら切る。期限後の Range は 403(NEG-DRS-URL-EXPIRED は始める前の期限切れ) | 1 |
| NEG-CONTENT-AFTER-END | TES・WES | 終わったタスク(4 つの終わり方のどれでも)の FULL に `inputs[].content` を返さない(WES の添付も)。動いている間は返す。起動のとき前の版で終わったタスクからも消す(D-043) | 1(単体)・2(compose の Docker の実行役) |
| NEG-EMPTY-SECRET | DRS・TES・WES・AAI | 空・短い(32 字未満)HMAC の鍵のファイル(`url-secret`・`page-secret`)と、AAI のクライアントの 32 字未満・見本の値(`CHANGE-ME`)の秘密で起動しない(D-055)。無いときに作る鍵は 64 字・0600(D-044) | 1 |
| NEG-CONFORMANCE-STOPS | TES(試験の道具) | `tools/conformance_tes.py` が立てた認証なしの TES と worker を、SIGTERM でも道具の失敗でも必ず止める(D-044) | 1 |
| NEG-WES-RUN-LIMIT | WES | 利用者ごとの終わっていない run は `max_active_runs_per_user` まで、超えたら 429。同時に出しても超えない。ほかの人は数えない。終わった run の見張りのスレッドは残らない(D-044) | 1 |
| NEG-TES-TASK-LIMIT | TES | 利用者ごとの終わっていないタスクは `max_active_tasks_per_user` まで、超えたら 429。同時に出しても超えない。ほかの人と、サービスが代わりに出した分(`yai.on_behalf_of` ごと)は別に数える。送り直し(同じ nonce)は上限でも前の id(D-053、点検 N-1) | 1 |
| NEG-URL-USERINFO | TES・WES | 記録用の URL・ログ・終わったタスクと run から URL の `user:pass@` を消す(壊れた URL も)。起動のとき前の版で終わったタスクからも消す(D-044) | 1 |
| NEG-DISK-LIMITS | TES | 入力・出力の個数の上限を超えたら 400、1 タスクの出力の合計の上限を超えたら SYSTEM_ERROR(超えた出力は公開しない・一時のものも残さない)(D-044) | 1 |
| NEG-OUTPUT-EXISTS | TES | 他人のファイルがある・無い・途中がファイルのどれでも受け付けは 200(有無で返事を変えない)。あれば上書きせず SYSTEM_ERROR、理由は同じ文。自分の出力と重なるときだけ 400(D-044) | 1 |
| NEG-CONFIG-REFUSE | DRS・TES・WES・AAI | 起動の検査(`yai/common/startup.py`)の「断る」の行が 1 つずつ起動しない(文が 1 行、traceback なし)。`auth.mode` が無い・aai で issuer 等が無い・設定ファイルが無いと起動しない(D-055) | 1 |
| NEG-CONFIG-WARN | DRS・TES・WES | 「WARNING」の行が 1 つずつ `yai.startup` に WARNING を出して起動する。起動の 1 行は決めた項目だけで、秘密が出ず、改行を含むパスでも 1 行(D-055) | 1 |
| NEG-TEST-ENDPOINTS | AAI・DRS | 既定で試験用の口(`/test/login`・`/test/users/{name}/visas`・`/untrusted-visa/jwks.json`・`/yai/test/clock`)が 404。`/test/clock` はどの設定でも 404。`test_visa_endpoint` が無ければ `test_endpoints: true` でも visas の口は 404。範囲の外の `ttl` は 400。本文は 1 MiB まで。`clock_offset_seconds` を書くと起動しない(D-054) | 1 |
| NEG-CONFIG-SHIPPED | 全部 | 配備(setup.sh の出力)・相互運用・例の設定が全部起動の検査を通る(例の aai だけは秘密 `""` で断る)(D-055) | 1 |
| NEG-INSECURE-HTTP-ONLY | TES・WES | `insecure_http_hosts` の先に https で来たら allow の表で判断する(allow に無ければ断る)(D-055) | 1 |
| NEG-BODY-AUTH-FIRST | TES | 認証なしの CreateTask は、本文を送り切る前に 401 が返る(本文を読まない)。401 の後に本文を送り切っても同じ接続の次の要求は正しく扱われるか閉じられる。static-bearer でも認証・scope が本文の解析より前(D-056) | 1 |
| NEG-WES-LIMIT-BEFORE-FETCH | WES | 上限に達した利用者の RunWorkflow は、workflow_url を取りに行かずに 429(D-057) | 1 |
| NEG-WORKFLOW-NAME | WES | 道の最後が `..` の workflow_url は取りに行かずに 400(500 にならない)(D-057、点検 N-4) | 1 |
| NEG-VISA-COUNT | AAI(Clearinghouse) | Visa が `max_visas_per_passport` を超える Passport は拒否され、Visa の署名を 1 つも確かめない。ちょうど上限なら通る。必要な Visa 1 つ+要らない Visa でも数が超えれば拒否(PP-CH-02 とのずれ、D-058) | 1 |
| NEG-RAM-MIN | TES | `ram_gb` が 6 MiB 未満(0.0005 など)は受け付けで 400。docker の実行役の argv に `--memory 0m` が出ない(D-060) | 1 |
| NEG-JSON-DEPTH | WES | 入れ子が 64 段を超える workflow_params・tags・workflow_engine_parameters は受け付けで 400(D-060) | 1 |
| NEG-CROSS-SITE | 全部 | 状態を変える要求(POST・PUT・PATCH・DELETE)に `Sec-Fetch-Site: cross-site`・`same-site`、または Host と違う `Origin`(`null` を含む)があれば、認証・本文より前に 403。見出しの無いクライアント・同じ出自・GET は変わらない(D-061) | 1 |
| NEG-LIMITS | TES | 上限を超える・0・負の `cpu_cores`・`ram_gb` → 400。省いたときも既定の上限が付く(`docker inspect` で確かめる)。プロセス数・ログ・作業ディレクトリ・実行時間の上限で SYSTEM_ERROR。同時実行数の上限で QUEUED のまま待つ | 1 |
| NEG-RESTART | TES・WES | **障害の差し込み点**(下)ごとに止めて上げる → タスク・run が迷子にならない(続くか SYSTEM_ERROR)、二重に起動しない、孤児のコンテナが残らない、決めた時間の中で終わりの状態に着く | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-WES-SUBMIT-DUP | WES→TES | CreateTask の応答を落とす差し込み点、および**作成を 30 秒遅らせる差し込み点**で、WES が `tag_key`+`tag_value` で探し直し、TES のタスクが 1 つだけになる。別の run の nonce に一致しない | 2 |
| NEG-FILE-CROSS-TASK | TES | alice のタスクの出力の `file://` を bob のタスクの入力にする → 400。既に有る出力の URL に出す → 400 か SYSTEM_ERROR(上書きしない)。根の下のリンクで外に出られない | 1 |
| TES-VOLUMES-2EXEC | TES | executor 1 が volume に書いたファイルを executor 2 が読める。入力も両方から見える(TES-RUN-02・04) | 1 |
| CWL-PARITY | WES | `tests/cases/cwl/` の各例を cwltool でも動かし、yai の argv と出力の中身が同じ | 1 |
| NEG-CANCEL-RACE | TES・WES | 差し込み点で「出力の転送の直後・状態を書く直前」「CreateTask を出している途中」に止めて取り消しを入れる → 終わりの状態は 1 つで、状態の巻き戻りが無い。CANCELING の間に新しいタスクが出ない | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-CANCEL-DONE | TES・WES | 終わったものへの取り消しが 200 で、状態が変わらない(D-020) | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-PAGING-SNAPSHOT | TES・WES | ページを辿る途中で新しいものを作っても、重複・抜けが無い。条件を変えた印は 400 | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-PAGING-TAMPER | TES・WES | 書き換えた `page_token` → 400 | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-SECRET-IN-LOG | 全部 | 各資格情報に目印の秘密(`CANARY-<乱数>`)を入れて流し、全サービス・前段の proxy のログと、資格情報を持たない人が見える応答に 1 つも出ない。終わった TES タスクの DB から署名付き URL の問い合わせ部分が消えている(`system.md` 8 節) | 1(TES・DRS・AAI の単体)・2(WES を含むもの) |
| NEG-CACHE-HEADERS | DRS・AAI | 秘密を含む応答に `Cache-Control: no-cache, no-store`・`Pragma: no-cache` | 1 |
| NEG-LONG-RUN | WES | Passport の期限より長い run → 期限後のステップで SYSTEM_ERROR、理由が `system_logs` に | 2 |
| ROB-LARGE-INPUT | TES | 1 GB の入力・128 KiB の `content` | 後 |

## 5. 4 つまとめて動くことの試験

環境: docker compose(`*.yai.test`、手元の CA の TLS、443。`system.md` 2 節)。試験は compose の外の試験のコンテナから HTTP で叩く。

| ID | 流れ | 確かめること |
| --- | --- | --- |
| E2E-01 | alice が、`dataset=ds1` のオブジェクト 1 つを入力にした 1 ステップの CWL を WES に出す | WES → DRS(Passport)→ TES(署名付き URL)と流れ、run が COMPLETE。出力の中身が期待どおり。TaskLog の `tes_uri` を alice の AT で読める |
| E2E-02 | 2 ステップ(1 の出力を 2 の入力に)。**直列の Workflow を足したあと**(`wes.md` 1 節) | ステップの順と、`file://` での受け渡し |
| E2E-03 | bob(ds2 だけ)が ds1 のオブジェクトで出す | DRS が 403 → run が SYSTEM_ERROR、理由が `system_logs` に。TES にタスクが 1 つも出ない |
| E2E-11 | 形は正しく、署名を書き換えた Passport で出す(ユーザー了承 2026-10-16 00:2x) | WES は受け付ける(WES は形と期限だけを見る)。DRS が 401 で拒否 → run が SYSTEM_ERROR、理由が `system_logs` に。TES にタスクが 1 つも出ない |
| E2E-04 | executor が非 0 で終わる | TES が EXECUTOR_ERROR → run が EXECUTOR_ERROR |
| E2E-05 | 出力の置き場を書けなくする | TES が SYSTEM_ERROR → run が SYSTEM_ERROR |
| E2E-06 | 実行中に CancelRun | run が CANCELING → CANCELED、TES のタスクも CANCELED、コンテナが残らない |
| E2E-07 | 署名付き URL の期限を 2 秒、TES の同時実行を 0 にして待たせる | TES が SYSTEM_ERROR(入力の取得の失敗)→ run が SYSTEM_ERROR |
| E2E-08 | compact identifier 型の入力(`drs://yaitest.drs:<id>`) | 手元の解決表で解決できる |
| E2E-09 | 実行中に yai-wes を再起動 | run が続く。Passport が消えたので次のステップは SYSTEM_ERROR(2 ステップのとき) |
| E2E-10 | `system.md` 9 節の段階 2 の各行(試験 ID と期待する結果の列) | 各行の「期待する結果」のとおり(段階 2 の 25 行) |

INT-… の試験の名前は `system.md` 9 節の表の「試験」の列と同じ。

**試験のコード**(D-036・D-038): `tests/test_e2e.py`(印 `e2e`)。同じ試験を 2 つの環境に当てる: **local**(4 つを手元のプロセスで立て、TES は手元のプロセスの実行役。WES と TES・DRS の間に記録・書き換えの中継役 `tests/proxy.py`、偽の DRS `tests/fakedrs.py` を置く)と **compose**(`deploy/`、`YAI_E2E_TARGET=compose`、試験のコンテナ `deploy/compose.e2e.yaml` から)。compose は `tools/compose_e2e.sh`(手元の rootless Docker、D-037)で動かし、その junit(`build/junit-compose.xml`)を `tools/test_status.py` が手元の junit に重ねて数える(D-038)。手元の pytest だけでは compose のケースが「保留(Docker 待ち)」で飛ぶ。観測役が要る試験は local だけ(compose では「local だけ」で飛ぶ。保留ではない)。本物の Docker が要る確かめ(E2E-06 の「コンテナが残らない」、INT-DRS-HOSTNAME、NEG-LIMITS の実行中の上限)は印 `docker`(`YAI_E2E_DOCKER=1`、compose の試験のコンテナで付く)

## 6. 他の実装との相互運用の試験

調べ: `docs/research/2026-10-15_interop.md`(相手の版・コミット・確かめ方はそちら)。**記憶ではなく 2026-10-15 の公式のリポジトリで確かめた**。段階 3 の最初に、相手が今の版で動くかを確かめ直す。

| 組 | 向き | 相手と版 | 確かめること | 順 |
| --- | --- | --- | --- | --- |
| INTEROP-TES-1 | 相手のクライアント → yai-tes | py-tes 1.1.4(PyPI、TES 1.1 用) | 作成・取得・一覧・取り消しが py-tes の型で読める。`view`・ページング | 最初 |
| INTEROP-TES-2 | yai-wes(TES のクライアントとして)→ 相手の TES | Funnel(calypr/funnel、タグ v0.11.14。Release とタグの版の食い違いは段階 3 で確かめる) | yai-wes の TES の先を Funnel に替えて E2E-01 を通す。**Funnel が yai-drs の署名付き URL(https・クエリ付き・手元の CA)を取れるか**(INTEROP-TES-HTTPS) | 最初 |
| INTEROP-WES-1 | yai の試験 → 相手の WES | Sapporo 2.4.0(WES 1.1.0 が土台、自分で「一部互換」と書く) | `tests/cases` の WES の試験を Sapporo に当て、違いを Sapporo の互換表と突き合わせる。同じ CWL を出す | 最初 |
| INTEROP-DRS-1 | yai-wes の解決役 → 相手の DRS | ga4gh/passport-playground-drs(main 5eb7745、DRS 1.5.0、FastAPI。Passport の POST は無い) | 公開オブジェクトの解決と取得。応答の細かい差 | 最初 |
| INTEROP-AAI-1 | 相手の Broker・Issuer → yai の Clearinghouse | GA4GH Passport Playground(broker 1a29660 ほか、タグなし。Passport 1.2 への対応は明記なし) | 相手の Passport・Visa を yai-drs が検査できるか。`typ`・`jku`・`aud` の違い | 最初 |
| INTEROP-WES-2 | 相手のクライアント → yai-wes | wes-service 5.0 の wes-client(WES 1.0) | 1.1 のサーバーが 1.0 のクライアントを受け入れるか(後方互換) | 次 |
| INTEROP-WES-3 | yai の試験 → 相手の WES | Toil の WES(1.0)、Galaxy v26.1.1 の WES(1.0.0 を名乗る、Galaxy のワークフローだけ) | 状態の写し方の違い(Galaxy は completed を COMPLETE に写す、`docs/research/2026-10-15_galaxy.md`) | 次 |
| INTEROP-DRS-2 | 相手のクライアント → yai-drs | nf-ga4gh 1.5.0(Nextflow の DRS・TES) | Nextflow から yai-drs の `drs://`、Nextflow から yai-tes | 次 |
| INTEROP-DRS-3 | yai の解決役 → 相手の DRS | Gen3 indexd(タグ 2026.10、1.5.0 を名乗る)、Galaxy v26.1.1 の DRS(1.2.0 を名乗る、公開だけ) | 一括の部分的な失敗(D-021)、`self_uri` の形の違い(Galaxy はホスト名の前に `drs.`) | 次 |
| INTEROP-AAI-2 | 相手の Issuer → yai | keycloak-ga4gh-visa-issuer v1.0.3、REMS v2.41 | 本物の Keycloak・REMS が署名した Visa を検査できるか | 次 |
| INTEROP-EDGE | 両向き | 上の相手 | 一括の部分的な失敗・ページの終わり・`page_size` の上限・誤りの形(G-X-04・G-TES-03・G-DRS-09) | 次 |

- **実環境での試験はこの相互運用で足りる**とする(ユーザーの決定 2026-10-16 00:2x)。外に公開して動かす試験は作らない(D-002)
- 相手に当てるのは**共通**の種類の試験だけ(冒頭の 3 種類)。yai 固有の試験(scope・tags・ページの印・障害の差し込み)は相手には当てない
- 相手を手元で立てるときは docker compose の同じネットワークに入れ、`*.yai.test` の手元の CA を相手にも渡す(DRS のホスト名型 URI を仕様どおりに解決するため、D-017)
- yai-tes・yai-wes の `https` の許可ホストに、相手のホストを足す(自己レビュー R-09)
- 結果は組ごとに「通った/通らない理由」を `tests/reports/interop/` に書き、仕様の食い違いが原因なら `spec-gaps.md` に足す
- **段階 3 の結果(2026-10-17)**: 「最初」の 5 組を実施。TES-1 9/9(tags を外した後、D-040)・TES-2 通過・WES-1 8/10・DRS-1 5/5・AAI-1 通らない(相手の未対応)。yai の不具合 1(空の時刻)を直して 0。詳しくは `tests/reports/interop/README.md`、置き方は D-039、相手の版は `deploy/interop/README.md`。「次」の 6 組は未実施

## 7. 試験のデータの形(どの言語の実装にも当てるため)

```yaml
# tests/cases/TES-LST-01.yaml
id: TES-LST-01
obligation: docs/data/obligations.yaml#TES-LST-01
setup: [{as: alice, create_tasks: 3}]
steps:
  - {as: alice, request: {method: GET, path: /tasks, query: {page_size: 2048}}, expect: {status: 400}}
  - {as: alice, request: {method: GET, path: /tasks, query: {page_size: 2}}, expect: {status: 200, json_len: {tasks: 2}}}
```

- `as:` は利用者の名前(試験用の AAI か、相手の実装の資格情報の表から引く)
- 相手が他の実装のときは、相手の資格情報の取り方だけを差し替える(`tests/targets/<相手>.yaml`)
