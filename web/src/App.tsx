import { useState } from "react";
import FlowView from "./flow/FlowView";
import AutoscaleView from "./scale/AutoscaleView";
import TrafficView from "./TrafficView";

const VIEWS = [
  { id: "flow", label: "Request flow" },
  { id: "scale", label: "Autoscaling" },
  { id: "traffic", label: "Traffic split" },
] as const;

type View = (typeof VIEWS)[number]["id"];

export default function App() {
  const [view, setView] = useState<View>("flow");
  return (
    <main>
      <header className="top">
        <div>
          <h1>MiniCloud</h1>
          <p className="sub">Every view shows real requests and real cluster state</p>
        </div>
        <nav className="tabs" aria-label="Views">
          {VIEWS.map((v) => (
            <button key={v.id} className={view === v.id ? "on" : ""} onClick={() => setView(v.id)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>
      {view === "flow" && <FlowView />}
      {view === "scale" && <AutoscaleView />}
      {view === "traffic" && <TrafficView />}
    </main>
  );
}
