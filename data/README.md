# data/

デモ・実験用の人流データを作るための入力。生成のしかたは [docs/demo-data-design.md](../docs/demo-data-design.md)。

| 置き場所 | 中身 | 出典・ライセンス |
|---|---|---|
| `bodik/432024_public_facility.csv` | 八代市 公共施設一覧（公園・公民館・学校・団地など 331件） | [BODIK 八代市公共施設一覧](https://data.bodik.jp/dataset/07951092-e9be-49d2-acc3-3fa6b0f183a4) CC BY 4.0 |
| `bodik/432024_tourism.csv` | 八代市 観光施設一覧（物産館の位置に使う） | [BODIK 八代市観光施設一覧](https://data.bodik.jp/dataset/d0f61846-c6e6-4dc5-b6ce-c96836520043) CC BY 4.0 |
| `bodik/shelters.geojson` | 令和8年熊本地震の避難所（開設・閉鎖日時つき） | [BODIK 令和8年熊本地震オープンデータ](https://odcs.bodik.jp/kumamoto-r8/data/portal/shelters.geojson)。取得日 2026-11-04 |
| `osm/poi_yatsushiro.csv` | 八代市中心部の店・職場（スーパー・コンビニ・飲食店・工場など 509件）。`scripts/synth/extract_osm_poi.py` で抽出 | © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright) |
| `addresses/yatsushiro.json` | 八代市の小字の代表点。e-Stat のメッシュ人口が無いときに家を置く代用 | デジタル庁 アドレス・ベース・レジストリ由来（別プロジェクトで整形したもの） |
| `estat/tblT0011*.txt`（例 `tblT001102Q4830.txt`）（**各自で取得・コミットしない**） | 国勢調査2020 250m メッシュ人口（1次メッシュ 4830）。家の重みに使う | [e-Stat 統計地理情報システム](https://www.e-stat.go.jp/gis) → 統計データダウンロード → 国勢調査 2020年 → 5次メッシュ（250m）→ 人口及び世帯（JGD2000）→ 第1次地域区画 4830 |
| `out/`（**コミットしない**） | 生成した人流データ（実験用の台本） | — |
| `demo/`（**コミットしない**） | デモ動画用の台本（`scripts/synth/demo_scenario_video.json`）で生成した人流データ。画面の素材は `web/demo/` に入れる | — |
