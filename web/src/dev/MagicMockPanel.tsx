import type { MagicPreview } from "./MagicMapPreview";
import { useId } from "react";
import type { AnalysisArea } from "../api/client";
import { magicCatalog, spellById, spellSettings } from "./magicCatalog";
import assets from "../../public/magic-assets/manifest.json";
import { magicAnimation } from "./magicAssets";
import "./magic-mock.css";
import { magicTimeLabel, magicTimeUnit } from "../result/magicTime";
import ParameterHelp from "./ParameterHelp";
import ClippedNumberInput from "./ClippedNumberInput";
import { magicInputRange, updateMagicNumber, type MagicNumericField } from "./magicInputLimits";

const parameterDescriptions: Record<string, string> = {
  volume: "1秒あたりに生成する体積です。単位はm³/s。魔法継続時間を掛けた総水量を、開始時に全量配置します。",
  radius: "円・扇形の中心から外縁までの距離です。解析範囲はこの半径の1.5倍以上の±範囲を選びます。",
  length: "配置点から矢印方向へ伸びる距離です。長さと幅の大きい方の1.5倍以上を解析範囲にします。",
  width: "矢印に直交する横幅です。長方形は横幅の半分ずつ左右へ広がります。",
  sectorAngle: "扇形の全開き角です。方向角を中心に左右へ半分ずつ開きます。",
  casting: "生成体積を積算する時間です。総水量＝m³/s×秒。開始時全量配置では、この時間中に追加給水しません。変更時に観測時間の初期値を10倍へ更新します。",
  relaxation: "魔法継続時間に続けて水の流れを観測する時間です。解析全体は魔法継続時間＋観測時間。初期値は継続時間の10倍で、自由に編集できます。",
};

export const initialMagic = spellSettings("dq7-maelstrom", 139.767125, 35.681236);

export function validMagic(magic: MagicPreview): boolean {
  return [magic.volume, magic.radius, magic.casting, magic.length, magic.width, magic.sectorAngle].every(value => value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) > 0)
    && magic.relaxation.trim() !== "" && Number.isFinite(Number(magic.relaxation)) && Number(magic.relaxation) >= 0
    && magic.bearing.trim() !== "" && Number.isFinite(Number(magic.bearing)) && Number(magic.bearing) >= 0 && Number(magic.bearing) <= 360
    && Number.isFinite(magic.lat) && Math.abs(magic.lat) <= 90 && Number.isFinite(magic.lon) && Math.abs(magic.lon) <= 180
    && Number.isInteger(Number(magic.casting)) && Number(magic.casting) <= 3600
    && Number.isInteger(Number(magic.relaxation)) && Number(magic.relaxation) <= 36000
    && Number(magic.volume) * Number(magic.casting) <= 1_000_000 && Number(magic.radius) <= 4000
    && Number(magic.length) <= 4000 && Number(magic.width) <= 4000 && Number(magic.sectorAngle) <= 360
    && magic.initialSpeed.trim() !== "" && Number.isFinite(Number(magic.initialSpeed)) && Number(magic.initialSpeed) >= 0 && Number(magic.initialSpeed) <= 4
    && magic.vortexCoreRadius.trim() !== "" && Number.isFinite(Number(magic.vortexCoreRadius)) && Number(magic.vortexCoreRadius) > 0 && Number(magic.vortexCoreRadius) <= 4000
    && (magic.releaseMode === "initial" || (magic.initialMotion === "none" && Number(magic.initialSpeed) === 0))
    && (magic.initialMotion !== "none" || Number(magic.initialSpeed) === 0);
}

type Props = { magic: MagicPreview | null; analysisArea: AnalysisArea | null; disabled: boolean;
  onSelect: (id: string) => void; onChange: (magic: MagicPreview) => void };

export default function MagicMockPanel({ magic, analysisArea, disabled, onSelect, onChange }: Props) {
  const inputId = useId();
  const valid = magic !== null && validMagic(magic);
  const numericInput = (key: MagicNumericField, decimals?: number) => {
    if (!magic) return null;
    const [min, max] = magicInputRange(magic, key);
    return <ClippedNumberInput id={`${inputId}-${key}`} value={magic[key]} min={min} max={max} disabled={disabled}
      integer={key === "casting" || key === "relaxation"} decimals={decimals}
      onChange={value => {
        const next = updateMagicNumber(magic, key, value);
        onChange(next);
        return next[key];
      }} />;
  };
  const spell = spellById(magic?.spellId ?? "healing-rain");
  const animation = magicAnimation(spell.id);
  const area = !magic ? 0 : magic.footprintKind === "domain" ? analysisArea?.area_m2 ?? 0
    : magic.footprintKind === "rectangle" ? Number(magic.length) * Number(magic.width)
    : Math.PI * Number(magic.radius) ** 2 * (magic.footprintKind === "sector" ? Number(magic.sectorAngle) / 360 : 1);
  const casting = Number(magic?.casting), volume = Number(magic?.volume) * casting;
  const unit = magicTimeUnit(casting);
  const fields = [["volume", "1秒あたりの水量 (m³/s)"], ["radius", "半径 (m)"], ["length", "長さ (m)"], ["width", "幅 (m)"], ["sectorAngle", "扇形の開き角 (°)"], ["casting", "魔法継続時間 (秒)"], ["relaxation", "観測時間 (秒)"]] as const;
  const icons = assets.icons as Record<string, {path:string}>;
  return <section className="magic-mock-panel" aria-label="水魔法の選択と設定">
    <h2>2. 魔法の設定</h2>
    <p className="magic-note">水量の少ない順 · {magicCatalog.length}件 · 5 m³以下は除外 · プールは実寸基準、魔法・怪獣は推定最大値</p>
    <div className="magic-catalog" aria-label="魔法リスト">
      {magicCatalog.map(entry => <button key={entry.id} type="button" className="magic-choice" disabled={disabled} aria-pressed={magic?.spellId === entry.id || (magic?.spellId === "healing-rain" && entry.id === "gw2-healingrain")} onClick={() => onSelect(entry.id)}>
        <img src={icons[entry.gameId]?.path} alt={`${entry.game}のアイコン`} />
        <span className="magic-choice-text"><strong><span className="magic-choice-name" title={entry.name}>{entry.name}</span><span className="magic-choice-volume">{entry.maxVolume.toFixed(1)} m³</span></strong><small title={entry.game}>{entry.game}</small></span>
      </button>)}
    </div>
    {magic && <>
      <p className="magic-note">{spell.note}</p>
      {"estimate" in spell && <p className="magic-note">推定根拠: {spell.estimate} <a href={spell.source} target="_blank" rel="noreferrer">出典資料</a></p>}
      <p className="magic-note">水の配置方法: 開始時に全量を配置</p>
      {magic.releaseMode === "initial" && <>
        <div className="compact-input-grid">
        <label>初期運動<select value={magic.initialMotion} disabled={disabled} onChange={event => onChange({...magic,
          initialMotion: event.target.value as MagicPreview["initialMotion"], initialSpeed: event.target.value === "none" ? "0" : Number(magic.initialSpeed) === 0 ? "2" : magic.initialSpeed})}>
          <option value="none">静止</option><option value="directional">直進</option><option value="radial">中心から放射</option><option value="vortex">渦巻き</option>
        </select></label>
        {magic.initialMotion !== "none" && <label>初速 (m/s){numericInput("initialSpeed")}</label>}
        </div>
        {magic.initialMotion === "vortex" && <div className="compact-input-grid">
          <label>渦の向き<select value={magic.vortexDirection} disabled={disabled} onChange={event => onChange({...magic, vortexDirection:event.target.value as MagicPreview["vortexDirection"]})}><option value="clockwise">時計回り</option><option value="counterclockwise">反時計回り</option></select></label>
          <label>最大流速になる半径 (m){numericInput("vortexCoreRadius", 1)}</label>
        </div>}
      </>}
      <label>効果範囲<select value={magic.footprintKind} disabled={disabled} onChange={event => onChange({...magic, footprintKind:event.target.value as MagicPreview["footprintKind"]})}>
        <option value="circle">円</option><option value="rectangle">帯状の長方形（指向性）</option><option value="sector">扇形（指向性）</option><option value="domain">解析範囲全体</option>
      </select></label>
      <div className="compact-input-grid">
        {fields.filter(([key]) => key !== "radius" || ["circle", "sector"].includes(magic.footprintKind))
          .filter(([key]) => !["length", "width"].includes(key) || magic.footprintKind === "rectangle")
          .filter(([key]) => key !== "sectorAngle" || magic.footprintKind === "sector")
          .map(([key, label]) => <div className="parameter-field" key={key}><div className="parameter-field-heading"><label htmlFor={`${inputId}-${key}`}>{label}</label><ParameterHelp name={label}>{parameterDescriptions[key]} 入力範囲: {magicInputRange(magic, key).map(value => Number(value.toFixed(6))).join("〜")}。範囲外は自動補正します。</ParameterHelp></div>{numericInput(key, ["radius", "length", "width", "sectorAngle"].includes(key) ? 1 : undefined)}</div>)}
        <label>方向 (°){numericInput("bearing")}</label>
      </div>
      <details className="magic-condition-explanation">
      <summary>条件についての解説</summary>
      <div className="parameter-help-list">
        <span>水の配置方法 <ParameterHelp name="水の配置方法">総水量と初速を初期条件として一度だけ与えます。開始後の追加給水はありません。</ParameterHelp></span>
        <span>初期運動 <ParameterHelp name="初期運動">静止・直進・中心から放射・渦巻きを選びます。開始後は地形・重力・摩擦によって運動が変化します。</ParameterHelp></span>
        <span>初速 <ParameterHelp name="初速">開始時だけ与える流速です。単位m/s。入力上限は4.0 m/sです。</ParameterHelp></span>
        <span>渦の向き <ParameterHelp name="渦の向き">地図を上から見た回転方向です。時計回り・反時計回りを切り替えます。</ParameterHelp></span>
        <span>最大流速になる半径 <ParameterHelp name="最大流速になる半径">渦の中心は静止し、この半径で指定初速に達します。外側では距離に応じて減速します。</ParameterHelp></span>
        <span>効果範囲 <ParameterHelp name="効果範囲">水を初期配置する形です。円・長方形・扇形・解析範囲全体から選びます。</ParameterHelp></span>
        <span>方向 <ParameterHelp name="方向">真北0°、東90°、南180°、西270°。長方形・扇形の向きと直進の初速度の向きに使います。矢印先端のドラッグでも変更できます。</ParameterHelp></span>
      </div>
      <details className="magic-note"><summary>ⓘ 初期運動の計算について</summary>全量と初速は開始時に一度だけ設定し、その後は地形・重力・摩擦に従って流れます。初速の初期設定2.0 m/sは評価用で、作品の公式速度ではありません。入力上限4.0 m/s。直進は方向角を使用し、渦は中心で静止、指定半径で最大、その外側で減速します。</details>
      <details className="magic-note"><summary>ⓘ 配置と方向の操作について</summary>配置は「1. 条件」の住所検索または緯度・経度入力で指定。方向は真北0°・東90°。矢印付近60pxで現れる丸い先端をドラッグできます。長方形と扇形は前方へ広がり、角度に従って回転します。</details>
      {valid ? <dl className="magic-metrics">
        <dt>総水量（計算結果）</dt><dd>{volume.toFixed(1)} m³（{Number(magic.volume).toFixed(2)} m³/s × {casting}秒）</dd>
        <dt>給水範囲の面積</dt><dd>{area.toFixed(1)} m²</dd>
        {magic.releaseMode === "continuous" && <><dt>平均給水量</dt><dd>{volume/casting < .05 ? "<0.1" : (volume/casting).toFixed(1)} m³/s · {(1000*volume/casting).toFixed(1)} L/s</dd></>}
        <dt>等価水深</dt><dd>{(1000*volume/area).toFixed(1)} mm</dd>
        {magic.releaseMode === "continuous" && <><dt>相当降雨強度</dt><dd>{(3600000*volume/casting/area).toFixed(1)} mm/h</dd></>}
        <dt>解析時間</dt><dd>{magicTimeLabel(casting+Number(magic.relaxation),unit)}（魔法継続 {magicTimeLabel(casting,unit)} + 観測 {magicTimeLabel(Number(magic.relaxation),unit)}）</dd>
        <dt>結果の表示単位</dt><dd>{unit === "seconds" ? "秒" : "分"}（魔法継続時間5分未満は秒、5分以上は分）</dd>
      </dl> : null}
      <details className="magic-note"><summary>ⓘ 水量と時間の計算・入力範囲について</summary>総水量は1秒あたりの水量×魔法継続時間で計算します。{magic.releaseMode === "initial" ? "その全量を開始時に置き、継続時間中も追加給水しません。" : "継続時間にわたって給水します。"}観測時間は魔法継続時間終了後の期間です。継続時間変更時は10倍を初期設定し、手動で変更できます。計算刻みは最大0.01秒。結果出力は原則0.2秒、継続5分以上は原則10秒（600フレーム上限で調整）。継続1〜3600秒・観測0〜36000秒。水量と範囲は比較用推定値です。</details>
      <p className="magic-note">GIF出典: <a href={animation.source} target="_blank" rel="noreferrer">{animation.credit}</a>（{animation.source_kind === "original" ? "説明図" : animation.source_kind === "official" ? "公式映像" : animation.source_kind === "film" ? "映画映像" : "プレイ映像"}）。{animation.caption} 映像・作品アイコンの権利は各権利者に帰属します。</p>
      <p className="magic-note">開始時に効果範囲の地表へ全量と初速を配置します。その後は追加給水・強制駆動せず、水の流れを追跡します。</p>
      </details>
    </>}
  </section>;
}
