# data/official/ — 公式の拠点（避難所・給水所）の CSV

八代市以外の地域でも、公式の拠点を CSV で足せる。取り込みは `scripts/import_official.py`（何度実行しても重複しない）。

```sh
docker compose exec api python scripts/import_official.py data/official/<file>.csv --source <取り込み元の名前>
```

| 列 | 必須 | 内容 |
|---|---|---|
| name | ○ | 名前。同じ source の中で一意（同じ名前の施設が複数あるなら key 列を使う） |
| kind | ○ | `shelter`（避難所）か `water`（給水所） |
| lat, lon | ○ | 緯度・経度（10 進。例 32.5072, 130.6016） |
| address | | 住所 |
| opened_at | | 開設日時（例 `2026-07-28T16:27+09:00`）。空なら取り込んだ時刻から開いている扱い |
| closed_at | | 閉鎖日時。空なら開設中 |
| key | | 一意のキー（自治体の施設 ID など）。空なら name を使う |

- 見出し行つき、UTF-8（Excel の BOM つきも可）
- `--source` は取り込み元ごとに変える（例 `yatsushiro-water-2026`、`kumamoto-city-shelters`）。同じ source で入れ直すと上書き、別の source は別の行になる
- 八代市の避難所は BODIK の GeoJSON から `scripts/import_bodik.py` で取り込む（source=`bodik-kumamoto-r8`）。CSV を使うのは、それ以外の地域や、給水所のように GeoJSON がないもの
- 実際のデータは出典と利用条件を確かめてから置く。このフォルダの CSV をコミットするなら、出典をこの README に書く

ひな型: `template.csv`（見出しだけ）。例:

```
name,kind,lat,lon,address,opened_at,closed_at,key
○○小学校,shelter,32.5095,130.6018,熊本県八代市○○町1-1,2026-07-28T16:27+09:00,,
○○コミュニティセンター,water,32.5071,130.5973,,2026-07-29T08:30+09:00,2026-08-25T20:00+09:00,
```
