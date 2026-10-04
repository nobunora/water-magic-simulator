import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MagicMockPanel, { validMagic } from "./MagicMockPanel";
import { spellSettings } from "./magicCatalog";
import { clipObservation, updateMagicNumber } from "./magicInputLimits";
import ClippedNumberInput from "./ClippedNumberInput";

function EditablePanel() {
  const [magic, setMagic] = useState(spellSettings("dq7-maelstrom", 139, 35));
  return <MagicMockPanel magic={magic} analysisArea={null} disabled={false} onSelect={() => {}} onChange={setMagic} />;
}

function EditableCoordinate() {
  const [value, setValue] = useState("35.681236");
  return <label>座標<ClippedNumberInput type="text" value={value} min={-90} max={90} disabled={false} onChange={setValue} /></label>;
}

describe("bounded magic inputs", () => {
  it("allows unfinished signs and decimal points while typing coordinates", () => {
    render(<EditableCoordinate />);
    const input = screen.getByLabelText("座標");
    fireEvent.focus(input);
    fireEvent.change(input, {target:{value:"-"}});
    expect(input).toHaveValue("-");
    fireEvent.change(input, {target:{value:"-12."}});
    expect(input).toHaveValue("-12.");
    fireEvent.change(input, {target:{value:"-12.123456789"}});
    expect(input).toHaveValue("-12.123456789");
    fireEvent.change(input, {target:{value:"999"}});
    expect(input).toHaveValue("90");
  });
  it("clips dimensions, angle, speed and total volume together with duration", () => {
    let magic = spellSettings("dq7-maelstrom", 139, 35);
    magic = updateMagicNumber(magic, "radius", "9999");
    expect(magic.radius).toBe("166.6");
    magic = updateMagicNumber(magic, "bearing", "-1");
    expect(magic.bearing).toBe("0");
    expect(updateMagicNumber(magic, "sectorAngle", "999").sectorAngle).toBe("360");
    magic = updateMagicNumber(magic, "initialSpeed", "999");
    expect(magic.initialSpeed).toBe("4");
    magic = updateMagicNumber(magic, "volume", "999999999");
    expect(Number(magic.volume) * Number(magic.casting)).toBe(1_000_000);
    magic = updateMagicNumber(magic, "casting", "3601.8");
    expect(magic.casting).toBe("3600");
    expect(Number(magic.volume) * 3600).toBeLessThanOrEqual(1_000_000);
    expect(validMagic(magic)).toBe(true);
  });
  it("clips observation to a feasible saved-frame interval rather than just a scalar cap", () => {
    expect(clipObservation(4, 36000)).toBe(595);
    // 15+997 cannot align both boundaries in <=600 frames; 996 can.
    expect(clipObservation(15, 997)).toBe(996);
    expect(clipObservation(3600, 36000)).toBe(36000);
    expect(clipObservation(4, -1)).toBe(0);
  });
  it("retains a valid value during clearing, restores it on blur and removes validation alerts", () => {
    render(<EditablePanel />);
    const radius = screen.getByLabelText("半径 (m)");
    fireEvent.focus(radius);
    fireEvent.change(radius, {target:{value:""}});
    expect(radius).toHaveValue(null);
    expect(screen.getByText("2500.0 m³（625.00 m³/s × 4秒）")).toBeInTheDocument();
    fireEvent.blur(radius);
    expect(radius).toHaveValue(20);
    fireEvent.change(radius, {target:{value:"9999"}});
    expect(radius).toHaveValue(166.6);
    fireEvent.change(screen.getByLabelText("初速 (m/s)"), {target:{value:"99"}});
    expect(screen.getByLabelText("初速 (m/s)")).toHaveValue(4);
    fireEvent.change(screen.getByLabelText("魔法継続時間 (秒)"), {target:{value:"0"}});
    expect(screen.getByLabelText("魔法継続時間 (秒)")).toHaveValue(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
