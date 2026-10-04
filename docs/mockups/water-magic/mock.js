"use strict";
// Independent UI prototype: coordinates and the map are illustrative, no solver calls.
const spells = [{ id: "gw2-healingrain", game: "Guild Wars 2", name: "Healing Rain", minVolumeL: 39, maxVolumeL: 117, icon: "game-gw2.svg" }];
spells.sort((a, b) => a.minVolumeL - b.minVolumeL || a.maxVolumeL - b.maxVolumeL || a.id.localeCompare(b.id));
const byId = (id) => document.getElementById(id);
let selected = false;
let playing = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let visible = true;
const spellButtons = [];
for (const spell of spells) {
  const button = document.createElement("button");
  button.className = "spell";
  button.type = "button";
  button.setAttribute("aria-pressed", "false");
  const icon = document.createElement("img");
  icon.className = "game-icon";
  icon.src = spell.icon;
  icon.alt = `${spell.game}の識別アイコン（仮）`;
  const text = document.createElement("span");
  text.className = "spell-text";
  const name = document.createElement("strong");
  name.textContent = spell.name;
  const game = document.createElement("small");
  game.textContent = spell.game;
  text.append(name, game);
  const volume = document.createElement("span");
  volume.className = "spell-volume";
  volume.textContent = `${spell.minVolumeL}–${spell.maxVolumeL} L`;
  const caption = document.createElement("small");
  caption.textContent = "推定総水量";
  volume.append(caption);
  button.append(icon, text, volume);
  button.addEventListener("click", () => {
    selected = true;
    for (const item of spellButtons) item.setAttribute("aria-pressed", String(item === button));
    byId("settings").hidden = false;
    byId("anchor").hidden = false;
    byId("empty-state").hidden = true;
    for (const id of ["center", "play", "visibility"]) byId(id).disabled = false;
    centerAnchor();
    refreshImage();
    updateSettings();
  });
  spellButtons.push(button);
  byId("spell-list").append(button);
}
function centerAnchor() {
  byId("anchor").style.left = "50%";
  byId("anchor").style.top = "50%";
}
function refreshImage() {
  const frame = byId("rain-frame");
  frame.hidden = !visible;
  byId("rain").src = visible && playing ? "rain.gif" : "rain-still.png";
  byId("play").textContent = playing ? "停止" : "再生";
  byId("play").setAttribute("aria-label", playing ? "GIFを停止" : "GIFを再生");
  byId("visibility").textContent = visible ? "非表示" : "表示";
}
function updateSettings() {
  const ids = ["volume", "radius", "duration", "relax"];
  const valid = ids.every((id) => byId(id).checkValidity());
  byId("preview").disabled = !valid;
  if (!valid) {
    byId("message").textContent = "入力範囲内の値を指定してください。";
    return;
  }
  const [volume, radius, duration, relax] = ids.map((id) => Number(byId(id).value));
  const area = Math.PI * radius ** 2;
  byId("volume-value").textContent = `${volume} L`;
  byId("depth-value").textContent = `${(volume / area).toFixed(3)} mm`;
  byId("total-value").textContent = `${Number((duration + relax).toFixed(2))} 秒`;
  byId("radius-label").textContent = `半径 ${radius} m`;
  const size = 210 * radius / 12.2;
  byId("footprint").style.width = `${size}px`;
  byId("footprint").style.height = `${size}px`;
  byId("message").textContent = "";
}
for (const id of ["volume", "radius", "duration", "relax"]) byId(id).addEventListener("input", updateSettings);
byId("opacity").addEventListener("input", () => { byId("rain-frame").style.opacity = Number(byId("opacity").value) / 100; });
byId("center").addEventListener("click", centerAnchor);
byId("play").addEventListener("click", () => { playing = !playing; refreshImage(); });
byId("visibility").addEventListener("click", () => { visible = !visible; refreshImage(); });
byId("map").addEventListener("click", (event) => {
  if (!selected || event.target.closest("button")) return;
  const rect = byId("map").getBoundingClientRect();
  byId("anchor").style.left = `${(event.clientX - rect.left) / rect.width * 100}%`;
  byId("anchor").style.top = `${(event.clientY - rect.top) / rect.height * 100}%`;
});
byId("rain").addEventListener("error", () => {
  if (!byId("rain").src.endsWith("rain-still.png")) byId("rain").src = "rain-still.png";
  else byId("rain").hidden = true;
});
byId("preview").addEventListener("click", () => {
  byId("message").textContent = "条件を確認しました。このモックでは解析は実行しません。";
});
window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", (event) => {
  if (event.matches) { playing = false; refreshImage(); }
});
