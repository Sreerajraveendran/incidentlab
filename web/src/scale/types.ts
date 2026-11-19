// Shape of GET /api/mgmt/cluster (mgmt-api/app/cluster.py).

export type PodStage = "Pending" | "ContainerCreating" | "Running" | "Ready" | "Terminating" | string;

export interface Pod {
  name: string;
  stage: PodStage;
  node: string | null;
  ip: string | null;
  created: string | null;
  conditions: Record<string, string | null>;
  restarts: number;
  cpu_m: number | null;
  cpu_request_m: number | null;
  cpu_pct_of_request: number | null;
  mem_mib: number | null;
}

export interface Hpa {
  name: string;
  min_replicas: number;
  max_replicas: number;
  target_cpu_pct: number | null;
  current_cpu_pct: number | null;
  current_replicas: number | null;
  desired_replicas: number | null;
  last_scale_time: string | null;
  scale_down_window_s: number;
  conditions: { type: string; status: string; reason: string | null; message: string | null }[];
}

export interface KNode {
  name: string;
  role: "control-plane" | "worker";
  ready: boolean;
  schedulable: boolean;
}

export interface KEvent {
  time: string;
  type: string;
  reason: string;
  object: string;
  message: string;
  count: number;
}

export interface Snapshot {
  as_of: string;
  namespace: string;
  metrics_available: boolean;
  nodes: KNode[];
  pods: Pod[];
  hpa: Hpa;
  load_level: number; // 0 Off, 1 Light, 2 Medium, 3 Heavy
  load_pods: number;
  load_rps: number;
  load_rate_per_pod: number;
  events: KEvent[];
}

export interface Sample {
  t: number;
  cpu: number | null;
  replicas: number;
  ready: number;
}
