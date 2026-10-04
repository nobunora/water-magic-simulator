import SmokeApp from "./dev/SmokeApp";

export default function App() {
  const parameters = new URLSearchParams(window.location.search);
  return <SmokeApp magicMock={parameters.get("mode") === "water-magic" || parameters.get("mock") === "water-magic"} />;
}
