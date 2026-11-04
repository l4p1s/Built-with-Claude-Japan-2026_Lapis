# Built-with-Claude-Japan-2026_Lapis

## 起動方法

Docker（Compose）だけあれば動きます。

```sh
docker compose up -d --build --wait   # API と PostgreSQL + PostGIS を起動
curl localhost:18080/healthz           # {"ok":true}
docker compose run --rm api pytest    # テスト
```

- Web 画面: http://localhost:18080 （スマホ実機は HTTPS が必要なので `ngrok http 18080` で公開する）
- API 仕様書: http://localhost:18080/docs （OpenAPI: `/openapi.json`）
- 公式の避難所データ（BODIK 令和8年熊本地震）の取り込み: `docker compose run --rm api python scripts/import_bodik.py`
- 構成と設計の理由: [docs/architecture.md](docs/architecture.md) / 画面: [docs/web-ui-design.md](docs/web-ui-design.md)
- DB を初期化し直す: `docker compose down -v`（取り込んだデータも消えるので、取り込みをやり直す）
- デモ・実験用の人流データ（八代市 1000人 × 1分刻み × 17日）: `python scripts/make_demo_data.py --out data/out`（numpy が要る）。作り方と仮定は [docs/demo-data-design.md](docs/demo-data-design.md)、入力の出典は [data/README.md](data/README.md)

## 参考文献

※「本文確認済み」と書いていない文献は、要旨のみ確認。

### 場所ごとの平常比較は災害時に崩れる

- **Jiang, R., Song, X., Huang, D., Song, X., Xia, T., Cai, Z., Wang, Z., Kim, K.-S., Shibasaki, R. (2019). DeepUrbanEvent: A System for Predicting Citywide Crowd Dynamics at Big Events.** Proceedings of the 25th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining, 2114–2122. https://doi.org/10.1145/3292500.3330654
  - 地震・台風・大きな祭りなどの「大きな出来事」のときは、人の動きが日常と大きく変わるため、街全体の人の動きの予測が難しいと指摘
  - 直近1時間の街全体の人の動きを入力にして、次の1時間を予測するシステムを提案。動画の予測と同じ考え方で、深層学習（再帰型ニューラルネットワーク）を使う
  - 企画との関係: 「災害時は平常時の比較が通用しない」ことを支持

- **Fan, Z., Song, X., Shibasaki, R., Adachi, R. (2015). CityMomentum: An Online Approach for Crowd Behavior Prediction at a Citywide Level.** Proceedings of the 2015 ACM International Joint Conference on Pervasive and Ubiquitous Computing (UbiComp 2015), 559–569. https://doi.org/10.1145/2750858.2804277
  - 過去のデータを使わず、直近の人の動きだけで街全体の短期の人の動きを予測する方法（似た動きの人をまとめて予測する）
  - 大規模災害への対応を目的とした研究の一部
  - 企画との関係: 「災害時は過去の平常データに頼れない」ことを支持

### 個人の移動は規則的で、逸脱が信号になる

- **Song, C., Qu, Z., Blumm, N., Barabási, A.-L. (2010). Limits of Predictability in Human Mobility.** Science, 327, 1018–1021. https://doi.org/10.1126/science.1177170
  - 匿名の携帯電話利用者5万人（1,000万人から無作為に抽出）の3か月分の移動を分析
  - 移動の予測可能性は利用者全体で93%。移動距離が人によって大きく違っても、予測可能性はあまり変わらない
  - 企画との関係: 人の動きは規則的なので、「いつもと違う場所に行った」こと自体が信号になる

- **Song, C., Koren, T., Wang, P., Barabási, A.-L. (2010). Modelling the scaling properties of human mobility.** Nature Physics, 6, 818–823. https://doi.org/10.1038/nphys1760
  - 匿名の携帯電話利用者300万人の移動を分析
  - 人の移動を「新しい場所の探索」と「よく行く場所に戻る」の2つの要素で表すモデルを提案。単純なランダムな動きとはどれだけ違うかを示した
  - 企画との関係: 同上（個人ごとの「いつもの場所」を基準にする考え方の裏付け）

### 探索型の人がいる → 人ごとの補正が必要

- **Pappalardo, L., Simini, F., Rinzivillo, S., Pedreschi, D., Giannotti, F., Barabási, A.-L. (2015). Returners and explorers dichotomy in human mobility.** Nature Communications, 6, 8166. https://doi.org/10.1038/ncomms9166
  - いつもの場所を行き来する「リターナー（戻る型）」と、新しい場所へ広く動く「エクスプローラー（探索型）」の2種類の人がいることを発見
  - 既存のモデルではこの2種類を説明できないため、新しいモデルを作った
  - 企画との関係: 探索型の人を軽く数える補正の根拠

### 個人単位でイベント参加を判定

- **Traag, V. A., Browet, A., Calabrese, F., Morlot, F. (2011). Social Event Detection in Massive Mobile Phone Data Using Probabilistic Location Inference.** 2011 IEEE Third International Conference on Privacy, Security, Risk and Trust and 2011 IEEE Third International Conference on Social Computing (PASSAT/SocialCom), 625–628. https://doi.org/10.1109/PASSAT/SocialCom.2011.133
  - 大量の携帯電話データから、普段と違う大きな人の集まり（イベント）を見つける方法。位置を確率的に推定する（ベイズ推定）
  - 「誰がそのイベントに参加しているか」を判定する枠組みも作った。いくつかの例で方法を示した
  - 企画との関係: 個人単位で集まりへの参加を判定する、最も近い先例

### 災害時の人流で避難・支援の実態を把握

- **Yabe, T., Tsubouchi, K., Sudo, A., Sekimoto, Y. (2016). A framework for evacuation hotspot detection after large scale disasters using location data from smartphones: case study of Kumamoto earthquake.** Proceedings of the 24th ACM SIGSPATIAL International Conference on Advances in Geographic Information Systems. https://doi.org/10.1145/2996913.2997014
  - 2016年4月16日の熊本地震（M7.3）では1万棟以上が大きく壊れ、10万人以上が避難した。指定避難所以外に避難した人もいて、行政が避難先を把握するまで数日かかり、物資や救助が遅れた
  - 従来の把握方法は徒歩の現地調査で、時間がかかり、発災直後は実施が難しい
  - Yahoo Japan が集めたスマホの GPS から、避難が集中した場所（避難ホットスポット）を推定する枠組みを提案。施設の位置や新聞報道と照らし合わせて検証し、場所ごとの人数の推移と、拠点として機能した期間を示した
  - 企画との関係: 熊本での最も近い先例。対象は避難先

- **瀬戸寿一・樫山武浩・関本義秀 (2016). 平成28年4月熊本地震における携帯電話位置情報を用いた避難所の混雑度推計.** 地理情報システム学会 講演論文集. https://www.gisa-japan.org/content/files/conferences/2016/papers/C32.pdf （本文確認済み）
  - データ: ゼンリンデータコム「混雑統計®」（ドコモ地図ナビのオート GPS 利用者の位置情報を統計加工したもの、250m メッシュ）。熊本県内、2016年4月7日〜17日。4月7日〜13日を平時とした
  - 避難所データ: 学生 IT ボランティア「Youth Action for Kumamoto」の避難所データベース（903件）。このうち259件（約28.7%）は国土数値情報に載っていない自主避難所など
  - 結果: 平時より混雑したメッシュは15日10、16日38、17日22。グランメッセ熊本（当初は指定避難所でない）周辺では16日2〜4時に約3,200人以上。江原中学校周辺はピーク（17日17時）で約2,700人以上
  - 限界: 著者自身が「この手法だけでは実際の避難状況の把握や対策には不完全」と述べている
  - 企画との関係: 場所ごとの平常比較の国内先例

- **菊池健太・円山琢也 (2023). 複数の位置情報データを利用した令和2年7月豪雨熊本県人吉市の避難と支援の実態把握.** 土木学会論文集, 79(1). https://doi.org/10.2208/jscejj.22-00243
  - データ: 基地局ベースの「モバイル空間統計」と、GPS ベースの点群型の人流データ
  - 浸水した地区では、夜間の人口が平常時より大きく減った
  - GPS データからボランティアの活動の様子を捉え、ボランティアセンターでの滞在時間を算出した
  - 企画との関係: 災害時の人流で支援の実態を把握した、国内で最も近い研究

### 現地の人に聞いて確かめる

- **Liu, Y., Alexandrova, T., Nakajima, T. (2013). Using Stranger as Sensors: Temporal and Geo-sensitive Question Answering via Social Media.** Proceedings of the 22nd International Conference on World Wide Web (WWW 2013), 803–814. https://doi.org/10.1145/2488388.2488458 （本文確認済み）
  - MoboQ: 「今、あの店の行列はどれくらい？」のような、場所と時間に関する質問に、その場にいそうな人が答えるサービス。中国で実際に運用
  - 中国の SNS「新浪微博（Sina Weibo）」の投稿から、その場所に今いそうな人を見つけ、その人に質問を送る（答える人は MoboQ の利用者でなくてよい）
  - 2012年初めから10月までに、登録者35,214人が15,224件の質問をし、29,491件の回答が集まった。74.6%の質問に1件以上の回答があり、28%は10分以内、51%は20分以内に最初の回答が届いた
  - 企画との関係: 「ここに何がある？」を現地の人に聞いて確かめる部分の先例

## ライセンス

[MIT](LICENSE)。公式の避難所データ（BODIK）と地図（OpenStreetMap など）は、それぞれの提供元の利用条件に従います。
