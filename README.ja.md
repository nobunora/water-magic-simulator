# 水魔法シミュレータ

実際の日本の地形に水魔法を配置し、水量・範囲・初速を指定して、水の広がり、水深、流速をSFINCSで計算するシミュレータです。

[English README](README.md) · [実装・検証記録](docs/specs/water-magic-implementation.md) · [初期配置と初速](docs/specs/water-magic-initial-momentum.md)

## 起動と使い方

Python環境は `environment.yml` が基準です。フロントエンドの再構築にはNode.js 22.12以上が必要です。

```bash
python -m scripts.bootstrap_local_review --run-review
```

利用を許可された既存のSFINCS 2.4.0 Galibier実行ファイルを使う場合は、`--sfincs-bin <実行ファイルのパス>` を指定します。詳しい環境構築は [ローカルレビュー手順](docs/local-review.md) を参照してください。

1. 起動時に表示されるURLの `/?mode=water-magic` を開きます。
2. 住所検索または緯度・経度で解析場所を指定します。
3. 魔法や比較シナリオを選び、水量、範囲、方向、初速、追跡・緩和時間を調整します。
4. 解析を実行し、水深、流速の矢印、粒子、時刻別の結果を確認します。結果はZIPで保存・再読込できます。

水魔法は0.5 m格子で計算し、初期配置と継続給水に対応します。ゲーム・怪獣の水量や寸法は解析用の推定値で、公式の測定値ではありません。地表の水の運動を近似するもので、戦闘効果、怪獣の移動、建物破壊は再現しません。Adaptive格子は無効です。

## 従来版との関係

このリポジトリは [従来の内水シミュレータ](https://github.com/nobunora/urban-pluvial-flood-simulator) から履歴を引き継いだ、独立した別リポジトリです。GitHubのForkネットワークには属しません。従来版の公開先は変更せず、水魔法版は `nobunora/water-magic-simulator` に公開します。

分岐時点は `3fea888773575b288033faa2f67e12ee645609cb`。2026年10月4日の独立公開以前は、別フォルダのGit worktreeとブランチによる分岐でした。旧仕様書の記述はその経緯を示しています。
