# プール・怪獣と初期全量配置（2026-10-04）

利用者の追加指示により、小学校のプールと怪獣2件を追加する。選択可能な全17件を最大水量の昇順に並べ、選択時と「新しい解析」の給水方式を初期配置にする。開始時点で全量を配置し、その後は追加給水しない。継続給水は手動で選択できる。既存保存結果は元の給水方式を保持し、省略された旧API設定の継続給水・初速0という互換性も維持する。

|追加項目|水量|範囲|初期運動|
|---|---:|---|---|
|小学校のプール（全量）|300.0 m³|25.0 × 12.0 mの長方形|静止|
|ゴジラ・上陸波|推定最大100000.0 m³|進行方向80.0 m、横幅100.0 m|直進2.0 m/s|
|チタノザウルス・大渦|推定最大23000.0 m³|半径30.0 mの円|時計回り、最大2.0 m/s、最大流速半径15.0 m|

初速は編集可能な比較用モデルの値で、作品の公式速度ではない。Rainも初期配置・静止にする。プールは水を地表に置く比較であり、プールの壁や槽を作る機能ではない。怪獣自体、海洋全域、建物破壊はモデル化しない。

## 根拠と推定の境界

- プールは[吹田市立南山田小学校の記録](https://blog.suita.ed.jp/es/25-m-yamada/shinmidorinoniji/2014/08/post-169.html)の25 m × 12 m × 平均1 m = 300 m³を採用する。
- ゴジラは2014年作品の[ホノルル上陸時の波](https://wikizilla.org/wiki/Honolulu)を参考にした局所水塊。横幅100 m × 奥行80 m × 高さ25 m × 三角断面係数0.5 = 100000 m³という推定上限であり、公式体積でも都市全体の浸水量でもない。
- チタノザウルスは[公式Monsterpedia](https://godzilla.com/blogs/monsterpedia/titanosaurus)の尾びれによる大渦と身長60 mを参考にする。半径30 m、水深8 mを仮定し、π × 30² × 8 ≈ 22619 m³を23000 m³に丸めた推定上限。公式情報は渦の能力を支持するが、半径・水深・水量を規定しない。

## 同梱画像とGIF

プールは寸法と水量を示す自作説明図・アイコン。怪獣は作品映像の短いGIFと公式サイトの識別画像を同梱する。GIFは自動再生する。

- ゴジラ: [映像](https://www.youtube.com/watch?v=WFEvriJDjYc)の165〜169秒。投稿者は非公式の映像紹介チャンネル。夜間映像の視認性を上げるためgamma 1.6を適用し、出典欄に明示する。
- チタノザウルス: [東宝公式予告編](https://www.youtube.com/watch?v=ZfbTB7bu6aI)の19〜23秒。キャラクターの登場映像であり、海中の渦そのものを映した映像ではないことをUIに明示する。
- 切出し定義: `web/public/magic-assets/pool-kaiju-footage-sources.json`。`scripts/extract_magic_gifs.py --clips`で再生成可能。プールは`prepare_magic_assets.pool_illustration` / `pool_icon`で生成する。

追加3本のGIFは各フレームのバイナリブロックを検査し、別途デコードした。プール24、ゴジラ48、チタノザウルス48の計120フレーム。黒一色に近いフレームは0、各GIF内の全フレームが異なる。証拠は`artifacts/pool-kaiju-validation/gif-validation.json`と各`*-frames/frame-binary-report.json`。

## 実解析とブラウザ確認

固定SFINCS 2.4.0 Galibierで、±100 m、400 × 400のネイティブ0.5 m格子を実計算した。

```powershell
python -m scripts.validate_magic_catalog --initial --grid-m 0.5 --output-dir artifacts/pool-kaiju-validation/engine --spell-id school-pool --spell-id kaiju-godzilla-wave --spell-id kaiju-titanosaurus-vortex --spell-id dos2-rain
```

平坦・閉境界の比較モデルで、プール300、ゴジラ100000、チタノザウルス23000 m³は初期・最終とも同量、Rain 6.3 m³の相対誤差は4.54 × 10⁻⁸。全4件で継続給水ファイルなし、給水による追加質量なし。証拠は`artifacts/pool-kaiju-validation/engine/engine_validation.json`。

ブラウザで4件の選択時設定と全画像読込を確認し、プールの実地形解析を開始・完了した。run ID `ae3ada01-5a24-4096-8090-a0294b84cef0`、0.5 m格子、0〜10秒の51出力。ネイティブデータの水量は開始300.000183、終了300.000153 m³、最大相対誤差5.96 × 10⁻⁷。開始・終了時刻の水深表示も確認した。証拠は`browser-presets.json`、`browser-run-proof.json`、`browser-pool-0.png`、`browser-pool-10.png`（同じ検証ディレクトリ）。

Python299テスト、フロント78テスト、最終の既定値変更後のReviewApp20テスト、画像登録調整後のカタログ5テストが成功。Ruff0.16.5、対象モデルのmypy2.3.1、TypeScript5.9.2、API型一致、製品ビルドも成功。既存の依存ライブラリ非推奨警告とバンドル容量警告は残る。CodebaseMemoryは別作業が利用中のため競合起動せず、対象ソース・呼出元・テストをrgで確認した。Import Linter / deptry / ty / Oxlintの未導入は既存のツール不足として記録する。検証集計は`artifacts/pool-kaiju-validation/checks.json`。
