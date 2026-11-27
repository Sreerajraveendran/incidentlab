import { useState } from "react";
import FlowView from "./flow/FlowView";
import IncidentsView from "./incidents/IncidentsView";
import AutoscaleView from "./scale/AutoscaleView";
import TrafficView from "./TrafficView";

const VIEWS = [
  { id: "incidents", label: "Incidents" },
  { id: "flow", label: "Request flow" },
  { id: "scale", label: "Autoscaling" },
  { id: "traffic", label: "Traffic split" },
] as const;

type View = (typeof VIEWS)[number]["id"];

export default function App() {
  const [view, setView] = useState<View>("incidents");
  return (
    <main>
      <header className="top">
        <div>
          <h1>IncidentLab</h1>
          <p className="sub">Incident drills on a real Kubernetes cluster · every view shows real state</p>
        </div>
        <nav className="tabs" aria-label="Views">
          {VIEWS.map((v) => (
            <button key={v.id} className={view === v.id ? "on" : ""} onClick={() => setView(v.id)}>
              {v.label}
            </button>
          ))}
        </nav>
      </header>
      {view === "incidents" && <IncidentsView />}
      {view === "flow" && <FlowView />}
      {view === "scale" && <AutoscaleView />}
      {view === "traffic" && <TrafficView />}
    </main>
  );
}
