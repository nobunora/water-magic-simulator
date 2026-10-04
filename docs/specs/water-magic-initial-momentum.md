# 初期水量・初速と渦巻き（2026-10-04）

利用者の「初期値だけでOK。渦巻きも出来るなら実装して」に対応する追加仕様。以前の「運動量なし」は継続給水方式に限る。初期配置方式では水量と水平流速を開始時に一度設定し、以後はSFINCSの移流・重力・粘性・摩擦によって変化する。継続的な駆動や給水水の運動量源は実装しない。

## 入力と画面

- `release_mode`: `initial`（開始時に全量配置）／`continuous`（従来の継続給水）。旧設定の省略時は `continuous`。
- `initial_motion`: `none`／`directional`／`radial`／`vortex`。旧設定の省略時は `none`。
- `initial_speed_mps`: 0～4 m/s。旧設定の省略時は0。継続給水と静止では0のみ。
- `vortex_direction`: `clockwise`／`counterclockwise`。`vortex_core_radius_m`: 最大流速となる正の半径（上限4000m）。
- 直進は既存の方向角（真北0°・東90°）を使う。放射は中心から外向き。渦は接線方向で、中心は静止、速度は指定半径まで比例増加し、その外側では距離に反比例して減少する。渦の角度入力は回転方向の指定には使わない。
- 有効な地表へ全水量を配分する。初期流束は両側が濡れた内部面のみ。建物・乾いた岸・制御境界に初期の流束を設定しない。
- メイルストロム2件とCataractは初期渦、長方形／扇形は初期直進、それ以外の水塊は初期放射を選択時の設定とする。2026-10-04の追加指示により、Rainも初期配置・静止に変更。全17件で初期配置を選択時の既定値とし、プールも静止、チタノザウルスは渦とする。初速2 m/sは編集可能な評価用初期設定で、ゲームの公式推定値ではない。
- 初期配置では「魔法継続時間」を「追跡時間」に変更する。追跡＋緩和が総解析時間となり、給水量/秒と降雨強度の指標は表示しない。追跡時間が300秒未満は秒、それ以上は分。内部は秒、格子は0.5m、結果表示密度・高速描画経路は従来どおり。
- 初速、回転方向、半径はAPI、結果メタデータ、ZIP保存と再読込で保持。旧ZIPは新しい初期渦の既定値を継承しない。旧結果の継続給水を保持した上で、「新しい解析」を押すと初期配置を既定とする（旧結果由来の初速は0）。

## SFINCSへの入力

エンジンを改造せず、固定版2.4.0 Galibierの `rstfile` を使用する。実装は `floodsim/sfincs/water_magic_initial.py`。モデル書込後の `sfincs.ind/msk/dep` を検査してセル順・地形を一致させる。

Type 1のFortran逐次非書式ファイルは4レコード（各レコードの前後に4バイトの長さ）: int32 type=1、float32水位、float32面流束q、float32制御境界uvmean。セルはFortran順、各セルの東面・北面を交互に並べ、q末尾のダミー1要素と境界専用uvmeanを保持する。面流束はエンジンと同じ面水深×初速。エンジンは初期速度成分を±4 m/sに制限するため入力速度も最大4 m/sにする（解析開始後の流速上限ではない）。

初期配置ではsrc/dis/降雨ファイルを指定せず、水量の二重加算を防ぐ。地形を含む水位のfloat32化後も水量を検査し、1%を超える誤差なら開始を拒否する。

根拠: [初期化処理](https://github.com/Deltares/SFINCS/blob/v2.4.0_Galibier_release/source/src/sfincs_initial_conditions.F90)、[面の配列構築](https://github.com/Deltares/SFINCS/blob/v2.4.0_Galibier_release/source/src/sfincs_domain.f90)、[再開ファイル出力](https://github.com/Deltares/SFINCS/blob/v2.4.0_Galibier_release/source/src/sfincs_output.f90)、[公式restart仕様](https://sfincs.readthedocs.io/en/latest/input.html#restart-file)。

## 検証

`python -m scripts.validate_magic_initial` は許可済みのSFINCS実行ファイルで0.5m格子を実計算する。静止、東、北、放射、時計回り、反時計回り、帯、扇、解析全域を検証。面の並びを独立に確認し、東/北の内側セルで約2.000003m/s、回転指標は時計回り−2.145511・反時計回り＋2.145511。20m³の初期水量を維持。静止の出力時刻0にある約0.000006m/sは初回計算の丸めで許容する。証拠は `artifacts/magic-initial-momentum/engine_validation.json`。

ブラウザ実行 `e1fd0421-1458-4bf3-9400-068bf87011e6`: 東京駅、±100m、400×400の0.5m格子、有効53984セル、DQメイルストロム2500m³、反時計回り2m/s、コア半径10m、追跡4秒＋緩和6秒。51出力、初期水量2499.999756m³、最終2500.0m³、初期最大流速1.994185m/s。SFINCS計算2.81秒、全工程14.03秒。初期時刻の矢印15本／WebGL粒子30個、10秒の粒子98個、速度場0.5m、既存のズーム依存密度と必要データだけの読込を保持。リロード後の編集画面で反時計回りを復元。ZIPエクスポート・API再インポートで全追加設定の一致を確認。

検査: Ruff0.16.5、mypy2.3.1（対象ファイル）、TypeScript5.9.2、OpenAPI型一致、Python296件、frontend76件。旧ZIP復元の追加テストを含むReviewApp19件とResultPanel22件も合格。production build合格。既存の依存ライブラリ警告とbundleサイズ警告は残る。

CodebaseMemoryは別操作が使用中のため競合起動せず、対象シンボル・呼出し・テストをrgとソースで確認。Import Linter/deptry/ty/Oxlintは既記録のツール不足として区別し、新しい依存は追加していない。
