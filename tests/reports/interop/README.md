# 段階 3: 他の実装との相互運用(test-plan.md 6 節)の結果

- 動かした日: 2026-10-17(06:2x〜06:4x UTC = 15:2x〜15:4x JST)、手元の rootless Docker(D-037)。環境の立て方は `deploy/interop/README.md`、結果の原本は `results.yaml`、台本の出力は `data/*.json`
- **終わりの条件**(計画 第 3 版 段階 3): INTEROP-TES-1・INTEROP-TES-2 が通る(最低)→ **通った**。続けて DRS-1・AAI-1 も実施。通らない項目は 4 つに分類し、**yai の不具合は 1 つ見つけて直したので 0**。やっていない組(「次」の 6 組)は数えない
- **再試験 2026-10-31**(14:37〜14:39 UTC = 23:37〜23:39 JST、手元の rootless Docker): 6cfacd0 のコード(作業ツリーは 18ebb72、6cfacd0 との差は docs・specs の注だけ。像 yai:dev・yai-test:dev の中身が 6cfacd0 のソースと一致するのを確認)で「最初」の 5 組を同じ相手の版・同じ手順(`deploy/interop/README.md`)で試し直し、**同じ結果**: TES-1 9/9、TES-2 E2E-01 が Funnel 相手で COMPLETE、WES-1 Sapporo 8/10(落ちるのは同じ [4][5])・yai-wes 10/10、DRS-1 5/5、AAI-1 8/8 の確かめが通って分類は同じ(相手の未対応)。10/17 以降の変更(D-053〜D-061)のために足した設定は無い(`deploy/setup.sh`・`make_configs.py` が書く設定のままで起動の検査を通った)。台本の出力は id・時刻だけが違うので `data/` は 10/17 のまま(Sapporo の一覧の `next_page_token` が今回は無いのは、作り直した環境で run が 1 つだけだったため)
- 図: `interop.d2` → `interop.svg`・`interop.png`(矢印の色: 緑=通った・黄=通ったが相手側の理由で項目が欠ける・赤=通らない・灰=やっていない。矢印の番号は下の理由の表)。図と表の一致は `tools/interop_diagram_check.py` が照らす(`make check`)

## 組ごとの結果(「最初」の 5 組)

| 組 | 向き | 相手と版 | 結果 | 通らなかった項目の分類 | 詳しく |
| --- | --- | --- | --- | --- | --- |
| INTEROP-TES-1 | py-tes(相手のクライアント)→ yai-tes | py-tes 1.1.4(PyPI) | **9/9 通過**(再試験 15:5x JST)。作成・取得(3 つの view)・一覧とページング・取り消し・service-info が py-tes の型で読めた。直す前は 8/9(service-info の `tags` を py-tes が拒む → 相手の未対応)→ ユーザー決定(15:51、D-040)で yai 側で `tags` を外して再試験 | [1] yai 側で直した(ユーザー決定): service-info の `tags`(追加の項目)を py-tes の型が拒む | [INTEROP-TES-1.md](INTEROP-TES-1.md) |
| INTEROP-TES-2 | yai-wes(TES のクライアント)→ Funnel | Funnel v0.11.14(quay.io の像) | **通過**: E2E-01 が Funnel 相手で COMPLETE。Funnel は yai-drs の署名付き URL(https・クエリ付き・手元の CA)を取れた(INTEROP-TES-HTTPS) | [2] **yai の不具合(直した)**: 空の時刻 "" で engine が落ちた。[3] 環境の不備(直した): 像に docker CLI が無い | [INTEROP-TES-2.md](INTEROP-TES-2.md) |
| INTEROP-WES-1 | yai の試験(共通)→ Sapporo | Sapporo 2.4.0(ghcr の像、エンジン cwltool) | **8/10 通過**。service-info・POST /runs(添付)・COMPLETE まで・RunLog・出力の中身・一覧・cancel・無い run(UUID)の 404。同じ台本は yai-wes で 10/10 | [4] 相手の未対応: `/runs/{id}/tasks` は 400。[5] 仕様の差: UUID でない id は 400。[6] 環境の不備(直した): run.sh が rootful のソケットを固定 | [INTEROP-WES-1.md](INTEROP-WES-1.md) |
| INTEROP-DRS-1 | yai-wes の解決役 → playground DRS | ga4gh/passport-playground-drs main 5eb7745(DRS 1.5.0) | **5/5 通過**: 直接の呼び出し(service-info・OPTIONS・GET・access・取得)と、本番の道(WES に `drs://pgdrs.yai.test/<id>` → 解決 → TES が取って COMPLETE) | [7] 仕様の差(相手): access_methods.type は https なのに access URL は http 固定 → TES の試験の設定 `insecure_http_hosts` で受けた | [INTEROP-DRS-1.md](INTEROP-DRS-1.md) |
| INTEROP-AAI-1 | 相手の Broker・Issuer → yai の Clearinghouse(yai-drs) | GA4GH Passport Playground: Broker = Ory Hydra v26.2.0、Issuer = bfb0232 | **通らない(相手の未対応)**。Hydra の AT(typ=JWT、Visa 無し)と Issuer の署名の無い JSON の Visa は Passport 1.2 の形でなく、yai-drs は 401 で正しく拒否(8/8 の確かめは通過) | [8] 相手の未対応: Passport 1.2 の形で署名した Passport・Visa を出す部品が Playground に無い | [INTEROP-AAI-1.md](INTEROP-AAI-1.md) |

通らなかった項目の数: yai の不具合 **1(直して 0)** / 相手の未対応 2([4][8]。[1] は最初は相手の未対応だったが、ユーザー決定で yai 側を直して解消)/ 環境の不備 2(どちらも直して進めた)/ 仕様の差 2。

## 理由の表(図の矢印の番号)

| # | 分類 | 中身 | どうしたか・案 |
| --- | --- | --- | --- |
| 1 | yai 側で直した(ユーザー決定) | py-tes 1.1.4 の `ServiceInfo`(attrs の厳密な型)が、yai の TES の service-info の `tags` を `unexpected keyword argument` で拒む。TES 1.1 の `tesServiceInfo` は service-info の `Service` + `storage` + `tesResources_backend_parameters` で `tags` は無いが、追加の項目を禁じてもいない | 仕様の読みでは yai は誤っていない(相手の未対応、8/9)。**ユーザー決定(2026-10-17 15:51、D-040)**: 相互運用を優先し yai-tes の service-info から `tags` を外した(実行役の種類は `description` に、`test_TES_SI_01` を合わせた)。再試験で service-info も py-tes の型で読め **9/9**。WES では `tags` が required・TES には無い食い違いは G-X-07 |
| 2 | yai の不具合(直した) | **Funnel は `logs[].start_time`・`end_time` が無いとき `""` を返す(protobuf の JSON の既定)。yai-wes の `engine.record_step` が `parse_rfc3339("")` で ValueError → run が SYSTEM_ERROR、タスクを取り消し** | 空・読めない時刻は「無い」(null)に(`yai/wes/engine.py`)。手元の試験 `test_INT_TIME_EMPTY` を足した。G-TES-13 に記録 |
| 3 | 環境の不備(直した) | Funnel の公式の像は docker CLI を持たず(nerdctl のみ、containerd 前提)、worker は `docker run …` を exec で呼ぶ | 公式の像に docker CLI 27.3.1 を足した派生の像(`deploy/interop/funnel/Dockerfile`)。本体 20.10.13 と話せる版 |
| 4 | 相手の未対応 | Sapporo は `GET /runs/{id}/tasks`・`/tasks/{task_id}` を「実装しない・予定も無い」(400、`docs/wes-compatibility.md` 4-1・4-2) | 相手の設計。yai の WES のクライアントが相手に `/tasks` を頼むことは無いので影響なし |
| 5 | 仕様の差 | UUID の形でない `run_id` に Sapporo は 400 `Invalid run_id format`(UUID の形の無い id には 404)。WES 1.1.0 は「無い run は 404」とだけで、形が違う id の扱いは書いていない | yai は 404。どちらも仕様の読みとして成り立つ。G-WES-13 に記録 |
| 6 | 環境の不備(直した) | Sapporo 2.4.0 の `run.sh` は中の cwltool のコンテナに `-v /var/run/docker.sock:/var/run/docker.sock -e DOCKER_API_VERSION=1.44` を固定で渡す(rootful 前提。本体 20.10.13 は API 1.41) | 設定 `run_sh`(`SAPPORO_RUN_SH`)で、rootless のソケットのフォルダ(ro)と API 1.41 に差し替えた写しを渡す(`deploy/interop/setup.sh`)。ホスト・開発用コンテナの設定は変えていない |
| 7 | 仕様の差(相手) | playground DRS は `access_methods[].type: https` を返すが、`/access` の URL は `http://<DRS_HOST>:<DRS_PORT>/files/…` を `main.py` が固定(`_public_base()`)。DRS 1.5.0 は type と URL のスキームが一致することを明文で求めていない | 平文の口 `pgdrs.yai.test:8645` も第 2 の Caddy で受け、TES の `fetch.insecure_http_hosts`(試験の設定)に足した。本番なら yai-tes は https 以外を 400 で断る(NEG-SSRF)。G-DRS-16 に記録 |
| 8 | 相手の未対応 | Broker README(1a29660)は Hydra に `ga4gh_passport_v1` スコープのクライアントを作る手順だけ(「Token exchange (soon)」)。Hydra の AT は `typ: JWT`・`scp: [ga4gh_passport_v1]`・`aud: [yai-drs]` だが `ga4gh_passport_v1` のクレームが無い。Issuer(bfb0232)は `{"ga4gh_passport_v1": [<JSON の dict> ×4]}` を返し、Visa は JWT でなく署名も無い | yai-drs は AT を Passport として受けると 401 `invalid passport`(typ が Passport でない)、Bearer として受けると 401 `invalid access token`(発行者が yai でない)。どちらも仕様どおりの拒否。本物の署名付き Visa の確かめは INTEROP-AAI-2(keycloak-ga4gh-visa-issuer・REMS)で |

## 追加の情報: compose の E2E 全部を Funnel 相手に流した結果

WES の TES の先を Funnel にしたまま `tests/test_e2e.py` の compose のケースを全部流した(`data/` には入れていない。junit に試験用の Bearer が写るため)。19 件のうち **通過 10**: E2E-01・03・04・08・11、INT-EXIT-CODE・INT-DRS-HOSTNAME・INT-DRS-COMPACT・AAI-AUD・ERR-SHAPE。**落ちた 9**(E2E-02・06・06_no_container_left、NEG-LIMITS-docker、INT-TES-DELEGATE・INT-TES-URI-READ・INT-CANCEL-RACE・INT-FILE-SAME-RUN、NEG-PASSPORT-NOT-IN-TES)は全部、試験が **yai-tes を観測する**(`tes_tasks_of_run` が yai-tes に tags で問い合わせる、`yai-<task>` の名前のコンテナを見る)前提のもので、相手には当てない yai 固有の観測(test-plan 冒頭の区分)。Funnel や yai の不具合ではない。

## やっていない組(「次」、終わりに数えない)

INTEROP-WES-2(wes-client 5.0 → yai-wes)、INTEROP-WES-3(yai → Toil 9.5.0・Galaxy v26.1.1 の WES)、INTEROP-DRS-2(nf-ga4gh 1.5.0 → yai-drs・yai-tes)、INTEROP-DRS-3(yai → Gen3 indexd 2026.10・Galaxy の DRS)、INTEROP-AAI-2(keycloak-ga4gh-visa-issuer v1.0.3・REMS v2.41 → yai)、INTEROP-EDGE。

## 守ったこと

- 相手の像・ソースは公式の配布元(quay.io・ghcr.io・PyPI・GitHub)から、版(タグ・ダイジェスト・コミット)を固定(`deploy/interop/README.md` の表)。相手のスクリプトは開発用コンテナの上では動かしていない(全部コンテナの中)
- `--privileged`・ホストの網・pid・rootful の本体のソケットは使っていない(`tests/test_deploy.py` が相手の compose にも同じ検査)。ホストへ公開していない。ホスト・開発用コンテナの設定は変えていない
- 秘密・CA の鍵は git にも像にも入れていない(`data/*.json` に残るのは task id・page token・Hydra の client_id だけ)
- 片付け: `sh tools/interop.sh down`(相手と本体のコンテナ・網・volume)。像は残す(下の表)
