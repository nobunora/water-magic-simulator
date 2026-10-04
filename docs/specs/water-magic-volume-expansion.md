# 大水量魔法の追加と水深表示高速化（2026-10-03）

選択肢は推定最大総水量が **5 m³を超える14件**。5 m³以下の8件を選択肢から除外し、最大総水量の昇順、同量はID順に並べる。保存結果を読めるよう旧IDは解析契約・素材に残す。初期選択はDQVIIのメイルストロム（2500 m³）。水量を増やすだけで一般に数値安定性が保証されるとは扱わない。

以下の6件を追加した。Wiki・公式の作品情報とプレイ映像で技・水の演出を確認したが、公式のメートル・m³データは得られていない。全ての寸法、水深、体積、給水時間は解析用の仮定であり、公式最大値ではない。映像の人体比は遠近法・カメラ移動の影響を受けるため、幾何学式に置いた値にも大きな不確かさがある。渦の回転運動量、移動波、召喚体の物質量、ダメージ・即死効果を再現しない。地表への静止した給水近似を計算する。

| 作品・魔法 | 推定最大水量 | 解析用形状 | 上限推定の仮定 | 技の資料・映像 |
|---|---:|---|---|---|
| Dragon Quest VII Reimagined / メイルストロム | 2500.0 m³ | 半径20.0 mの円 | 半径20m×有効水深2m×π = 2513m³を2500m³に丸める。体積・長さは演出からの仮定。 | [資料](https://dragon-quest.org/wiki/Maelstrom)・[映像](https://www.youtube.com/watch?v=Ik652uO2Ncs) 55.5〜59.8秒 |
| Final Fantasy VI Advance / Flood / フラッド | 1500.0 m³ | 前方25.0×幅30.0 mの長方形 | 戦闘面25×30m×有効水深2m = 1500m³。演出の人体比から仮定。 | [資料](https://finalfantasy.fandom.com/wiki/Flood_(Final_Fantasy_VI))・[映像](https://www.youtube.com/watch?v=YbrP10396f4) 1017〜1021.2秒 |
| Final Fantasy XV / Leviathan · Tsunami | 100000.0 m³ | 前方100.0×幅80.0 mの長方形 | 幅80×進行長100×高さ25m×波形係数0.5 = 100000m³。都市全水没量とは別の局所水壁上限推定。 | [資料](https://finalfantasy.fandom.com/wiki/Leviathan_(Final_Fantasy_XV))・[映像](https://www.youtube.com/watch?v=9C6rc5MDkBg) 47.5〜54.5秒 |
| Chrono Cross / Deluge / デルージュ | 3000.0 m³ | 前方30.0×幅40.0 mの長方形 | 30×40mの戦闘面×有効水深2.5m = 3000m³。演出からの仮定。 | [資料](https://chrono.fandom.com/wiki/Deluge)・[映像](https://www.youtube.com/watch?v=k5lPcjAvruE) 4.5〜8秒 |
| Golden Sun: The Lost Age / Neptune / ネプチューン | 5000.0 m³ | 前方40.0×幅50.0 mの長方形 | 幅50×長さ40×高さ5m×波形係数0.5 = 5000m³。召喚体そのものの体積は除く。 | [資料](https://goldensunwiki.net/wiki/Neptune)・[映像](https://www.youtube.com/watch?v=iy2xi2nRk-w) 49〜56.5秒 |
| Romancing SaGa 3 / メイルシュトローム | 8000.0 m³ | 半径25.0 mの円 | 半径25m×有効水深4m×π = 7854m³を8000m³に丸める。演出上限仮定。 | [資料](https://romancing-saga-3.blogspot.com/p/rs3-walkthrough-3.html)・[映像](https://www.youtube.com/watch?v=G2ULKrIoPHg) 166〜170.5秒 |

FFVIはAdvance版のフラッド映像であり、Pixel Remasterの通常習得魔法とは扱わない。ロマサガ3はフォルネウスの敵側の技も候補に含めた。FFXVは都市全域の総水量を算出せず、選択領域内の局所水壁を近似する。元のクロノトリガー Water II（80 m³）は引き続き選択でき、クロノ・クロスのDelugeを追加する。

ゲーム識別画像はSteam公式ストア／コミュニティの作品画像、黄金の太陽は[Nintendo公式作品ページ](https://www.nintendo.co.jp/n08/agfj/index.html)のタイトル画像 main18.jpg。切り出し時刻・取得元・権利者・SHA-256・サイズは web/public/magic-assets/manifest.json と expansion-footage-sources.json に記録。全6GIF、計372フレームはバイナリ画像ブロックとPillowの独立デコードで照合。各GIFの全フレームは異なる画像で、黒一色に近いフレームなし。検査証跡は artifacts/magic-expansion/frame-proof と asset-frame-proof.json。

再生成（元動画は取得後、ignored artifactsに置く）:

```powershell
& C:\Users\nobun\miniforge3\envs\urban-pluvial-flood-phase0\python.exe -m scripts.extract_magic_gifs --ffmpeg artifacts/media-tools/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe --source-dir artifacts/magic-expansion --clips web/public/magic-assets/expansion-footage-sources.json
```

水深専用の高速経路は、選択した h(time,n,m) だけを読み、静的な有効マスクと全解析期間の最大水深から作った色分けを再利用する。汎用アダプターでの標高・流速読込みと、hmax欠損時の全351フレーム再構築を避ける。PNGは32枚のLRU、静的な色分けは8件のLRU。Native NetCDFが対象で、旧NPZ結果の読込み契約は保持する。新しい画像も従来と同じRGBA・上下方向・色分けである。

実際の0.5 m・16万セル・351時刻の結果112be7d7-4aff-43b9-a797-3b432be175e6を同じPythonプロセスで比較。28.2秒は従来3.927秒→0.087秒、28.4秒は1.859秒→0.065秒。PNGはバイト一致。IPv4 HTTP APIで未キャッシュ53〜88 ms、再読込み5 ms。証跡: depth-comparison.json、depth-api-ipv4.json。localhostでは環境のIPv6接続フォールバックが約2秒加わったため、サーバ処理時間との比較には使っていない。

現在も選択時刻の空間全域を読む。現行の±100 mでは十分短くなったため表示範囲だけの切り出しは導入していない。より大きな範囲で再び遅くなる場合は、流速で使っている可視範囲ウィンドウ読込みに合わせて、水深のPNG範囲・地理座標・HTTPキーを一緒に拡張する。解析期間全体の色分けは可視範囲が変わっても固定する。


実画面の追加確認: DQメイルストロム2500 m³を東京駅の地形・建物で実行し、400×400の0.5 m格子、0〜64秒の321出力を得た。SFINCS13.24秒、全工程51.62秒。ブラウザ経由の水深画像は64秒53.48 ms、57.6秒58.37 ms（HTTP200）。全画面で固定した地図の6.4秒と64秒は43019画素が変化。57.6秒と64秒の水深PNGは同じ固定色階級内に収まるため色が一致しており、欠落した時間とは扱わない。GPU粒子は0.5 m流速場、WebGL、60粒子のまま。追加6件の平坦地0.5 m実解析は最大水量誤差0.087305%、緩和中の水量変化は要求量の0.1%未満。Python291件、フロントエンド73件、型・OpenAPI整合性・ビルドを確認済み。
