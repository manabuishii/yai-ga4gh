# AAI・DRS のコードへの外部レビュー:写し・確かめ・仕分け・予想の評価

- 依頼: ユーザー依頼 10/16 00:39。レビューの担当。**読むだけで yai のコードは直していない**(直しは実装の担当に渡す)
- 対象: `23bb75d` の AAI(`yai/aai/` の jwtutil・keyfetch・clearinghouse・service_auth・keys・app)、DRS(`yai/drs/` の store・access・app、`schema/drs.sql`)、試験(`tests/test_aai_clearinghouse.py`・`test_aai_broker.py`・`test_drs.py`・`keyserver.py`・`tokens.py`)。基準の設計 `docs/design/aai.md`・`drs.md`・`system.md`、`docs/data/permissions.yaml`、義務 `docs/spec-obligations.md`
- 予想(送る前にコミット): `2026-10-16_review-prediction-aai-drs.md`(b8210ab)

## 0. 呼び出しの記録

| 回 | 時刻(JST) | 結果 |
| --- | --- | --- |
| 起動の失敗 | 10/16 00:43 | 呼び出しの設定の誤りでレビューが始まらなかった。依頼のとおり回数に数えない |
| ① | 10/16 00:44 | 13 件。**前回と同じく、対象 20 ファイルの全文(`git show 23bb75d:<file>` に行番号)を依頼文に入れ**、仕様の原文はタグ固定の raw URL(DRS drs-1.5.0、AAI AAIv1.2、Passport 82546d5)を読んでもらった。依頼文は約 204 KB。レビューは DRS の Authorizations.yaml・一括の access の道・200OkAccesses.yaml を raw で読んだ |

注: 依頼文は再現できる(上の 20 ファイルを `23bb75d` から行番号つきで並べ、前置きに対象・基準・見る観点・返し方を書いたもの)。依頼文そのものは大きいので入れていない。レビューの引用の仕様の行番号は前回どおり `specs/` の行番号と合わないので、確かめは `specs/` の写し(`specs/SHA256SUMS`)で行番号を取り直した。

## 1. レビューの返事(写し)

提示内容の静的レビューです。コマンド実行・ファイル変更・試験実行はしていません。

1. **高 | yai/drs/access.py:67・83 | 【コード】| `static-bearer` を指定すると保護対象が全部公開になる**  
   設計で認めている `static-bearer` でも `self.jwt` は `None` になり、資格情報を検査せず、認可も無条件で通ります。設定値の誤記でも同じです。メタデータだけでなく配信口も署名不要になります。**| 直し方:** モードを起動時に列挙検証し、`static-bearer` は対応する認証実装へ接続する。未知のモードは起動失敗にする。固定 Bearer の欠落・不一致と未知モードの試験を追加する。

2. **高 | yai/drs/app.py:135、yai/aai/service_auth.py:68、yai/aai/keyfetch.py:55 | 【コード】| 同期の鍵取得がイベントループを止める**  
   `async` ハンドラーから同期の `httpx.Client` を直接呼びます。キャッシュミスの要求が一つあるだけで、そのワーカーの別要求も鍵取得完了まで進めません。SQLite の同期処理も同じ経路にあります。**| 直し方:** 鍵取得を非同期化するか、検証と SQLite 処理をスレッドへ移す。同じ取得元への取得を一本化し、遅い鍵サーバーへの要求中も別要求が応答する試験を追加する。

3. **高 | yai/aai/keyfetch.py:57・64 | 【コード】| 「5 秒で打ち切り」が取得全体に適用されない**  
   HTTPX の `timeout=5.0` は各通信操作のタイムアウトで、応答全体の期限ではありません。5 秒未満の間隔で少量ずつ送る相手には、64 KiB に達するまで長時間待ち続けます。現在の `slow` 試験は最初の応答を遅らせるだけなので検出できません。**| 直し方:** 接続開始から本文取得完了までの期限を設け、期限時に通信を中断する。少量ずつ継続送信する試験を追加する。

4. **中 | yai/aai/keyfetch.py:103–107 | 【コード】| 鍵取得失敗時には再取得の間隔制限が働かない**  
   制限条件が `jwks is not None and recent` なので、初回取得失敗やキャッシュ失効後の失敗では、次の要求が直ちに再取得します。鍵サーバー停止時に全要求が外向き通信を起こし、障害を増幅します。**| 直し方:** キャッシュの有無に関係なく最終取得試行時刻で制限し、失敗も短期間保持する。初回失敗後とキャッシュ失効後の連続要求で取得回数を検証する。

5. **中 | yai/aai/clearinghouse.py:91・140 | 【コード】| 不正な JWT の型によって `TokenError` 以外が漏れる**  
   Passport の `iss=[]` は辞書検索で、Visa の `typ=[]` は集合検索で `TypeError` になります。前者は署名検証前に到達します。後者は、その Visa だけを無視するはずの処理を中断し、正常な Visa を含む Passport も処理できなくします。**| 直し方:** 検索前に `iss`・`typ`・`kid`・`jku` の型を検証し、不正値を `TokenError` に統一する。配列・オブジェクト型の負例を追加する。

6. **中 | yai/drs/store.py:45・95・114・130 | 【コード】| SQLite 接続を明示的に閉じていない**  
   `with sqlite3.Connection` はトランザクションを終了するだけで、接続を閉じません。各要求で作る接続の解放が GC 任せとなり、接続・ファイル記述子が一時的に蓄積します。各接続での `journal_mode=WAL` 実行も不要な要求時処理です。**| 直し方:** `contextlib.closing` または `finally: db.close()` で必ず閉じる。WAL の設定は初期化時に行う。

7. **中 | yai/aai/clearinghouse.py:224–234 | 【コード】| AT の必須 `client_id` を検査していない**  
   `aai.md` 3 節は AT の中身に `sub`・`client_id` を要求しますが、`client_id` が欠けた、または文字列でない署名済み AT も通ります。`service_auth.py:55` はその値を主体種別の判定に使うため、不正なトークンが利用者 AT として扱われます。**| 直し方:** 空でない文字列の `client_id` を必須にし、欠落・数値・配列の拒否試験を追加する。

8. **中 | yai/aai/clearinghouse.py:166–168 | 【原文確認】| ControlledAccessGrants の URL Claim 検証がない**  
   `value` と `source` は空でない文字列しか検査していません。設定・登録値も同じ不正文字列なら、URL でない値や 255 文字超の値でも認可できます。[Passport 固定版](https://raw.githubusercontent.com/ga4gh-duri/ga4gh-duri.github.io/82546d502c78ca341035a75c69f36edc8c50927a/researcher_ids/ga4gh_passport_v1.md)の「value」節（327–331 行）と「URL Claims」節（597–622 行）は URL の検証と長さ制限を要求しています。**| 直し方:** 対象 Claim の URL 形式・255 文字上限を検証する。比較は現在の完全一致を維持し、URL は取得しない。255／256 文字と相対文字列の負例を追加する。

9. **中 | yai/drs/app.py:151–160 | 【コード】| 一括アクセスの内側の配列に資源上限がない**  
   外側を100件に制限しても、一件の `bulk_access_ids` に大量の値を入れると、その数だけ同期処理と結果生成が走ります。また、本文・Passport 数・各トークン長にも提示範囲では上限がありません。外側の件数制限だけではメモリと処理時間を抑えられません。**| 直し方:** 本文バイト数、資格情報数・長さ、アクセス ID の総処理件数に上限を設け、検証・署名検査より先に拒否する。内側だけ巨大な要求の試験を追加する。

10. **中 | yai/drs/access.py:140 | 【コード】| 非 ASCII の署名で 403 ではなく例外になる**  
    `sig=あ` のような問い合わせ値では、文字列版 `hmac.compare_digest` が `TypeError` を送出します。設計の「改ざんは403」を満たしません。**| 直し方:** `sig` を64文字の16進文字列として検証してから比較する。非 ASCII・長さ違い・不正文字の試験を追加する。

11. **中 | tests/test_drs.py:456–464 | 【コード】| ログ漏洩試験が署名付き URL を配信口へ送っていない**  
    URL を取得しただけで、その URL に GET していません。そのため、配信口のアクセスログが問い合わせ部分を丸ごと記録していても、この試験は通ります。ログ設定の実装は提示されていないため、実際の漏洩の有無は判定できません。**| 直し方:** 署名付き URL で通常取得・Range・改ざん要求を実行してから、署名と問い合わせ部分が全ログにないことを検証する。

12. **中 | tests/test_drs.py:414–453、yai/drs/app.py:235–242 | 【コード】| 転送中の期限切れを検証していない**  
    既存試験は取得完了後の期限切れと、新しい要求の拒否だけです。設計が要求する「転送中も期限で打ち切る」を実際には確かめていません。送信待ちやバッファリングを含む挙動は、チャンク取得前の時刻検査だけでは確認できません。**| 直し方:** 大きいファイルをストリーミングで取得し、転送途中で時計を進めて完走しないことと、期限後の Range が403になることを検証する。

13. **低 | yai/drs/app.py:190–191・198–199 | 【コード】| 存在しない ID でも不正資格情報が先に 401 を返す**  
    設計 `drs.md:70` は「方針に関係なく、無いものは404」としていますが、単体 GET／POST／access は資格情報を先に検査します。不正 Bearer と存在しない ID の組み合わせでは401になります。現在の404試験はこの組み合わせを含みません。**| 直し方:** 明記された404優先に合わせて存在確認の順序を統一し、不正資格情報付きの未知 ID を試験する。

## 2. 確かめと仕分け

仕分け: (a) 直す / (b) ユーザーに決めてほしい / (c) 直さない。「確かめ」はレビューの担当がコード(`23bb75d`)・`specs/` で見たこと。

| # | 重さ | 要点 | 確かめ | 仕分け |
| --- | --- | --- | --- | --- |
| 1 | 高 | `static-bearer` だと DRS が全部公開になる | **一部正しい**。`23bb75d` の DRS は `static-bearer` を持たない(`config/drs.example.yaml:10` は `aai \| anonymous` だけ)ので前提は外れ。ただし `access.py:50` は `mode == "aai"` 以外を全部「仮の実装(全部公開)」にし、`authorize`(`access.py:83`)も `mode != "aai"` なら通す。**`AAI`・`static-bearer` などの書き違い・知らない値で、保護したオブジェクトが黙って公開になる**(配信口の署名も要らなくなる、`app.py:224`)。TES は実装の担当が `static-bearer` を足している最中なので、同じ形で DRS に入れるとレビューの言うとおりになる | (a) |
| 2 | 高 | `async` の受け口から同期の鍵取得(最長 5 秒×…)・SQLite | 正しい。`drs/app.py` の受け口は全部 `async def` で、`access.credentials` → `KeyFetcher._fetch`(同期の `httpx.Client`)・`store.get`(同期の sqlite3)を直に呼ぶ。`workers=1`(`cli.py:55`)なので、鍵を取りに行く間サーバー全体が止まる | (a) |
| 3 | 高 | 「5 秒で打ち切り」が取得全体に効かない | 正しい。`httpx.Client(timeout=5.0)` は接続・読み取りの 1 回ごとの期限で、全体の期限ではない。少しずつ送り続ける相手には 64 KiB まで待つ(`keyfetch.py:57・64`)。`NEG-JKU-SLOW` は最初の応答を遅らせるだけ | (a) |
| 4 | 中 | 取得に失敗したときは取り直しの間隔の制限が効かない | 正しい。`keyfetch.py:103-107` の条件は `jwks is not None and recent`。初めての取得の失敗・キャッシュが切れたあとの失敗では、次の要求がすぐ取りに行く(`_last_fetch` は記録しているのに使われない) | (a) |
| 5 | 中 | 型の違う `iss`・`typ` で `TokenError` 以外の例外 | 正しい(手元の Python で確かめた: `[] in {...}` は dict・set とも `TypeError`)。Passport の `iss=[]` は**署名を確かめる前に** `clearinghouse.py:91` で 500。Visa の `typ=[]` は `clearinghouse.py:140` で `TypeError` になり、`verify_passport` の `except TokenError`(`:114`)を抜けて **Passport 全体が 500**(その Visa だけ無視、の決まり PP-CH-02 に反する) | (a) |
| 6 | 中 | SQLite の接続を閉じていない | 正しい。`store.py:45` の `_db()` を `with` で使うのはトランザクションの終わりだけで、接続は閉じない。WAL の設定も接続のたびに出している | (a) |
| 7 | 中 | AT の `client_id` を確かめていない | 正しい。`aai.md` 3 節の表の「中身」は `sub`・`client_id`。`AccessTokenVerifier.verify`(`clearinghouse.py:224-234`)は `client_id` を見ず、`service_auth.py:55` は `client_id == sub` で種類を決めるので、`client_id` の無い AT は利用者の AT になる。発行元は yai-aai だけなので害は小さい | (a) |
| 8 | 中 | ControlledAccessGrants の `value`・`source` の URL の検査が無い | **正しい**(行番号は `specs/` で取り直した)。`value` の節(`specs/passport/ga4gh_passport_v1.md:370-375`)は「Visa Type が URL と定めるなら URL Claim の決まりで確かめる MUST」、ControlledAccessGrants の `value` は URL Claim(`:791`)、`source` も URL Claim(`:388`)、URL Claim は 255 文字以下の MUST(`:696`)・取りに行かない(`:699`)。コードは空でない文字列しか見ない(`clearinghouse.py:166-168`)。判定は設定の値との完全一致なので実害は小さいが、義務の MUST | (a) |
| 9 | 中 | 一括の `/access` の内側の配列(`bulk_access_ids`)に上限が無い。本文・Passport の数・長さも | 正しい。`ids_from` は外側だけ数える(`app.py:98-104`)。`passports` の数・長さの上限も無い(`json_body`、`verify_passports`)。予想 P-11 と同じ | (a) |
| 10 | 中 | `sig` に ASCII 以外が来ると 403 でなく例外 | 正しい(手元で `hmac.compare_digest('ab','あ')` が `TypeError` を確かめた)。`access.py:140`。未処理の例外なので 500 | (a) |
| 11 | 中 | ログの試験が署名付き URL を配信口に送っていない | 正しい(`tests/test_drs.py:456-464` は URL を取るだけ)。なお本番の起動は `access_log=False`(`cli.py:58`)なので、いま漏れているとは限らない。試験が確かめていない、という指摘 | (a)(試験を足す) |
| 12 | 中 | 流している途中の期限切れを試験していない | 正しい。`NEG-DRS-URL-EXPIRED`・`NEG-DRS-REVOKE`(`test_drs.py:414-453`)は取り終えたあとの要求だけ。`app.py:235-241` の途中で切る処理を通る試験が無い | (a)(試験を足す) |
| 13 | 低 | 無い ID でも、壊れた資格情報が先に 401 | 正しい(`get_object` などは `credentials` → `lookup` の順、`app.py:190-191・198-199`)。ただ `drs.md:70` の「方針に関係なく、無いものは 404」は方針の話で、資格情報の検査との順は書いていない。`permissions.yaml` の public の note「資格情報が付いていても検査に失敗したら 401」とは今の順が合う。どちらに寄せるかは決めの問題 | (b) |

数: 指摘 13 件。(a) 12 件(うち試験だけ 2 件)、(b) 1 件、(c) 0 件。誤り(外れ)は 0 件、一部外れ 1 件(1 の `static-bearer` の前提)。

## 3. (a) の直し方の案(実装の担当向け)

| # | ファイル・行(23bb75d) | 直し方の案 | 足す試験 |
| --- | --- | --- | --- |
| 1 | `yai/drs/access.py:49-50・83・108`(`DrsAccess.__init__`・`authorize`・`authorizations`) | 起動時に `auth.mode` を `{"aai", "anonymous"}` で確かめ、ほかは起動を失敗させる(TES に `static-bearer` を足すなら、DRS も同じ表で決める) | 知らない mode で起動が失敗する |
| 2 | `yai/drs/app.py` の受け口全部(`:119-214`)、`yai/aai/keyfetch.py:51-67` | 受け口は `await json_body(request)` を使うので、そのまま `def` にはできない。`async` のまま、同期の部分を `await run_in_threadpool(access.credentials, request, passports)`・`run_in_threadpool(store.get, oid)`(`fastapi.concurrency`)で包むのがいちばん小さい(本文を FastAPI の `Body` の引数で受けて `def` にする手もある)。同じ取得元を同時に取りに行かないよう `KeyFetcher` に URL ごとの lock | 遅い鍵の取得元を取りに行っている間に、別の要求(service-info など)が返る |
| 3 | `yai/aai/keyfetch.py:51-67`(`_fetch`) | 取り始めからの全体の期限(`monotonic` で `deadline = start + 5`)を `iter_bytes` の中で確かめて打ち切る | 1 バイトずつ間を空けて送り続ける鍵の取得元で、5 秒ほどで `key_unavailable` |
| 4 | `yai/aai/keyfetch.py:99-107`(`key`) | `jwks is not None` の条件を外し、最後に試した時刻(`_last_fetch`)だけで間隔を守る。失敗はキャッシュせずに `key_unavailable` を返す | 鍵の取得元が止まっているとき、続けて 10 回要求しても取りに行くのは 1 回 |
| 5 | `yai/aai/clearinghouse.py:90-91・136-141`、`jwtutil.py`(`unverified_parts` の後) | `iss`・`typ`・`kid`・`jku`・`alg` を「文字列か無し」で先に確かめ、違えば `TokenError("malformed")`。念のため `verify_passport` の Visa の輪で `TokenError` 以外も「その Visa の無視」にするかは実装の担当の判断 | `iss=[]` の Passport が 401、`typ=[]` の Visa を含む Passport でほかの Visa が使われる(NEG-PASSPORT-BAD-SHAPE に足す) |
| 6 | `yai/drs/store.py:48-51`(`_db`)と使う所 4 か所(`:45・95・114・130`) | `contextlib.closing(sqlite3.connect(...))` と `with db:` を重ねるか、`try/finally: db.close()`。`journal_mode=WAL` は `__init__` で 1 回だけ | (無くてよい。必要なら開いた接続の数) |
| 7 | `yai/aai/clearinghouse.py:232-234` | `client_id` が空でない文字列であることを確かめる(`sub` と同じ形) | `client_id` 無し・数・配列の AT が 401 |
| 8 | `yai/aai/clearinghouse.py:166-168` | `type == "ControlledAccessGrants"` のとき `value`、全部の Visa で `source` を「絶対 URL(scheme と host がある)かつ 255 文字以下」で確かめ、違えばその Visa を無視。取りに行かない・比べ方は今の完全一致のまま | 255 文字は通り 256 文字は無視、`ds1` のような URL でない `value` は無視(今の試験の値は `https://yai.test/datasets/ds1` などで URL なので、そのまま通る) |
| 9 | `yai/drs/app.py:74-88`(`json_body`)・`:98-104`(`ids_from`)・`:151-169`(一括 `/access`) | `passports` の数(例 10)と 1 つの長さ(例 64 KiB)、一括 `/access` の `bulk_access_ids` の合計(例 `max_bulk` まで)に上限、超えたら 413(一括)・400 | 内側だけ大きい一括 `/access` が 413、`passports` 11 個が 400 |
| 10 | `yai/drs/access.py:134-141`(`check_signature`) | 比べる前に `sig` が 64 文字の 16 進(`re.fullmatch("[0-9a-f]{64}", sig)`)か確かめ、違えば 403 | `sig=あ`・長さ違いが 403 |
| 11 | `tests/test_drs.py:456-464` | 署名付き URL で、ふつうの取得・`Range`・改ざんした URL を配信口に送ってから、ログに `sig` と問い合わせ部分が無いことを確かめる | (この行そのもの) |
| 12 | `tests/test_drs.py`(新しい試験) | 大きいファイル(数 MiB)を流して読み、途中で試験用の時計を期限の後へ進め、全部は届かないこと・期限後の `Range` が 403 を確かめる | (この行そのもの) |

## 4. ユーザーに決めてほしいこと

- **(b) 13**: DRS で「壊れた資格情報」と「無い ID」が重なったとき、どちらを返すか。
  - 案 A(おすすめ): **今のまま 401 を先に**し、`drs.md` 6 節に「資格情報の検査 → 有るか(404)→ 方針(401・403)」の順を書き足す。壊れた資格情報を黙って通さない、という `permissions.yaml` の決まりと合う。コードは変えない
  - 案 B: 404 を先に(ID を先に引く)。`drs.md:70` の字面に合わせる。存在を知られる情報は今と同じ(資格情報なしでも 404 が返る)

## 5. 予想の評価

| 予想 | 結果 | 対応する指摘 |
| --- | --- | --- |
| P-01 async の中の同期 I/O | 当たり | 2 |
| P-02 SQLite の接続を閉じない | 当たり | 6 |
| P-03 取得の失敗で間隔の制限が効かない・同時取得 | 当たり | 4(同時取得は 2 の直し方に) |
| P-04 `drs.read` だけで `/access` も通る | 外れ | — |
| P-05 一括 `/access` の `summary` の数え方 | 外れ | — |
| P-06 途中で切ると `Content-Length` と合わない | 外れ(同じ所を、試験が無いという別の角度で 12) | — |
| P-07 Token Exchange の結びつき・`except Exception` | 外れ | — |
| P-08 サービスの AT が PSAT として交換に通る | 外れ | — |
| P-09 Basic の URL デコード | 外れ | — |
| P-10 `client_id == sub` で種類を決める | 一部当たり(`client_id` の欠けとして) | 7 |
| P-11 `passports`・Visa の数・大きさに上限が無い | 当たり | 9 |
| P-12 Visa と Passport の `sub` の違い | 外れ(来なかった。設計どおりなので良い外れ) | — |
| P-13 解釈できない `Range` に 416 | 外れ | — |
| P-14 秘密のファイルの権限・空 | 外れ | — |
| P-15 OPTIONS が `metadata_policy` から | 外れ | — |
| P-16 試験の不足 | 一部当たり | 9・12(と、各指摘の試験の案) |

- 予想 16 件: 当たり 4、一部当たり 2、外れ 10
- 指摘 13 件のうち、予想に有ったもの 5 件(2・4・6・7・9)、**予想に無かったもの 8 件**(1・3・5・8・10・11・12・13)
- 外した型: **型の違う値で出る未処理の例外**(5・10)、**期限・上限の「1 回ごと」と「全体」の違い**(3)、**設定の値の確かめ**(1)、**試験が決まりを本当に通っているか**(11・12)。前回(設計)の学びの「仕様の原文にしかないこと」は今回 1 件(8)だけで、コードのレビューでは「コードの境目の値」が中心だった。次のコードのレビュー(TES・WES)の予想では、この 4 つの型を先に自分で探す

## 6. 直した結果(実装の担当、2026-10-16)

順番は指示どおり、安全に関わるもの(1・5・10・7・8・9)を先にした。**同じ型を TES・WES・AAI でも探して直した**(1 か所直したら同じ型を探す決まり)。

| # | 直したこと | 同じ型で一緒に直した所 | 足した試験 |
| --- | --- | --- | --- |
| 1 | `auth.mode` を起動時に確かめる共通の部品 `yai/common/config.py` の `auth_mode`。知らない値・書き違いは起動しない(DRS は `aai`・`anonymous` だけ) | TES(`aai`・`anonymous`・`static-bearer`)、WES(`aai`・`anonymous`)も同じく「aai 以外は全部公開」だった | `test_unknown_auth_mode_refuses_to_start`(DRS `AAI`・`static-bearer`、TES `aii`、WES `static-bearer`) |
| 2 | 受け口を `def`(同期)にし、FastAPI のスレッドで動かす。本文は `Body` で受ける。R1(`authenticate`)を同期の関数に変えた。`KeyFetcher` に URL ごとの lock(同じ取得元を同時に取りに行かない) | TES・WES の受け口も同じだった(WES の stdout の口は TES を同期の HTTP で呼んでいた)。WES の RunWorkflow は multipart のため async のまま、認証と workflow_url の取得を `run_in_threadpool` に | `test_slow_key_source_does_not_block_others`(鍵の取得元が 6 秒かかる間に service-info が 2 秒以内に返る)、`test_key_fetch_is_single_flight`(8 つ同時でも取得は 1 回) |
| 3 | 鍵の取得に、取り始めからの全体の期限(5 秒)。届いた分ずつ読んで確かめる | 共通の取り役 `yai/common/fetch.py` は元から全体の期限を持つ | `test_NEG_JKU_DRIP`(1 バイトずつ 0.5 秒あけて送る取得元を 8 秒未満で打ち切る) |
| 4 | 取り直しの間隔を、キャッシュの有無に関係なく最後に試した時刻で守る(失敗も含む) | — | `test_key_refetch_limited_when_source_is_down`(止まった取得元へ 10 回要求しても取得は 1 回) |
| 5 | 署名の前に読むヘッダーの `typ`・`kid`・`jku`・`alg` と中身の `iss`・`sub` は「文字列か無し」(`jwtutil.unverified_parts`)。Visa の輪では念のため `TypeError` なども「その Visa だけ無視」 | — | `test_wrong_types_are_token_errors`(`iss=[]` の Passport、`typ=["x"]` の Visa、`kid=["b1"]`) |
| 6 | SQLite の接続を `contextmanager` で必ず閉じる。WAL の設定は初期化の 1 回だけ | TES の `TesDb`・WES の `WesDb` も同じだった | (無し。既存の試験が全部通ることで確かめた) |
| 7 | AT の `client_id` は空でない文字列が必須 | — | `test_NEG_AUTH_CLIENT_ID`(無い・数・配列・空) |
| 8 | ControlledAccessGrants の `value` と全部の Visa の `source` を URL Claim として確かめる(RFC 3986 の URL の形・255 文字以下・空白なし。取りに行かない) | — | `test_PP_URL_CLAIMS`(255 文字は通り 256 文字は無視、`ds1`・空白入り・`source: dac` は無視、独自の type の `value` は URL でなくてよい) |
| 9 | 本文 1 MiB(一括は 413、単体は 400)、`passports` は 10 個・1 つ 64 KiB まで(400)、一括 `/access` は内側の件数の合計も `maxBulkRequestLength` まで(413)。上限は資格情報の検査より先 | WES の `yai_passports` にも同じ上限(10 個・64 KiB) | `test_DRS_limits` |
| 10 | `sig` は 64 文字の小文字 16 進、`exp` は 12 桁までの数字であることを比べる前に確かめ、bytes で比べる | ページの印(TES・WES)と試験用 AAI のクライアント認証も文字列どうしの `compare_digest` だった → bytes で比べる | `test_NEG_DRS_URL_TAMPER_shapes`(`あ`・1 文字短い・大文字・全角数字・負・40 桁) |
| 11 | (試験だけ)署名付き URL でふつうの取得・`Range`・改ざんを配信口に送ってから、ログに署名と問い合わせ部分が無いことを確かめる | — | `test_NEG_SECRET_IN_LOG` を直した |
| 12 | (試験だけ)8 MiB を流している途中で時計を期限の後へ進め、全部は届かないこと・期限後の `Range` が 403 を確かめる | — | `test_NEG_DRS_URL_EXPIRES_MID_TRANSFER` |
| 13 | (b) → **案 A に決定**(ユーザー 2026-10-16 01:00)。コードは変えず、`drs.md` 6 節に順を書き足し、D-025・G-DRS-15、GA4GH への質問の下書き `docs/questions/2026-10-16_drs-401-vs-404.md`(投稿していない) | — | `test_bad_credential_before_not_found`(今の振る舞いを固定) |
