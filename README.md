# tokyo-west-facility-master

東京都西部の一定範囲について、OpenStreetMap（OSM）から生活関連施設（スーパー・コンビニ・病院・薬局・保育園／幼稚園・学校・バス停）の位置データを抽出し、JSONとして公開するリポジトリです。

GitHub Actions が Geofabrik 配布の関東地方 OSM データ（`.osm.pbf`）をダウンロードし、対象範囲と対象タグだけを抽出して `data/` に出力します。

## データの出典とライセンス

- データ: © OpenStreetMap contributors — <https://www.openstreetmap.org/copyright>
- 入手元: Geofabrik GmbH が配布する OSM 抽出データ — <https://download.geofabrik.de/asia/japan.html>
- `data/` 以下のデータは **Open Database License (ODbL) 1.0** で提供されます — <https://opendatacommons.org/licenses/odbl/1-0/>
- 出力ファイル（`facilities.json` / `manifest.json`）にも `attribution` と `license` を記載しています。

## 出力ファイル

| ファイル | 内容 |
|---|---|
| `data/manifest.json` | 生成日時、元データの時刻・MD5、対象範囲、件数、`facilities.json` のバイト数と SHA-256、区境界チェックの結果 |
| `data/facilities.json` | 施設データ本体（`elements` 配列） |

`facilities.json` の各要素:

```json
{"type": "node", "id": "123456789", "lat": 35.6780062, "lon": 139.6724265, "tags": {"shop": "supermarket", "name": "..."}}
```

- `type`: `node` / `way` / `relation`
- `id`: OSM ID（文字列）
- `lat` / `lon`: node はその座標、way / relation は形状の外接矩形の中心（小数点以下7桁）
- `tags`: `shop` / `amenity` / `highway` / `name:ja` / `name` / `brand` / `operator` のうち値があるもののみ
- 並び順は `type`（node → way → relation）、`id` の昇順。元データが同じなら毎回同一のバイト列になります。
- SHA-256 は `facilities.json` のバイト列に対して計算しています。

## 抽出条件

`config/filters.txt` の7条件（`shop=supermarket` / `shop=convenience` / `amenity=hospital` / `amenity=pharmacy` / `amenity=kindergarten|childcare` / `amenity=school` / `highway=bus_stop`）。

## 対象範囲

`config/area.json` の `candidateBbox`（西 139.54 / 南 35.57 / 東 139.78 / 北 35.76）を **候補値** として使用しています。

ワークフロー内の区境界チェック（wardCheck）で、OSM の行政区境界から杉並区・中野区・新宿区・渋谷区・目黒区の実際の範囲を算出し、候補範囲を半径 1,800m 分内側に縮めた範囲に収まるかを検証します。検証に通らない場合はデータを出力しません。範囲の正式採用は、実データでの wardCheck 結果を確認した後に行います（`area.json` の `bboxStatus`）。

## Overpass API との既知の違い

- マルチポリゴン・境界以外の relation（`type=site` など）として登録された施設は含まれません（`osmium export` の仕様）。
- 形状が不正なポリゴンは出力されません（同上）。
- Geofabrik のデータは日次更新のため、Overpass API よりも反映が遅れます。

## 実行方法

GitHub の **Actions** タブ → **Build facility master** → **Run workflow**。

- `dry_run` = `true`（初期値）: コミットせず、生成物を Artifact として保存するだけ
- `dry_run` = `false`: すべての検証に合格した場合のみ `data/` にコミット

ジョブ内の検証（MD5 照合、区境界チェック、カテゴリ別・合計件数、JSON 再読込、サイズ上限）のいずれかに失敗した場合、コミットは行われません。

## 構成

```
.github/workflows/build-facilities.yml  ワークフロー
config/area.json                        対象範囲・検証閾値・入手元URL
config/filters.txt                      osmium tags-filter の条件式
config/export-config.json               osmium export の設定
scripts/build_facilities.py             区境界チェック・整形・検証・出力（Python 標準ライブラリのみ）
```

このリポジトリには個人情報・顧客情報・物件情報は含まれません。
