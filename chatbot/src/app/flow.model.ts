// Events streamed by the agent (agent/agent.py) plus the chatbot's own client-side hops.

export type NodeId = 'chatbot' | 'entra' | 'agent' | 'bedrock' | 'gateway' | 'weather' | 'hr-directory';
export type HopStatus = 'start' | 'ok' | 'denied' | 'error';

export interface HopEvent {
  type: 'hop';
  id: string;
  from: NodeId;
  to: NodeId;
  target?: NodeId | null;
  label: string;
  status: HopStatus;
  ms?: number;
  detail?: Record<string, unknown>;
}

export interface FinalEvent {
  type: 'final';
  answer: string;
  tools_used: string[];
  services_allowed: string[];
  services_denied: string[];
  services_unavailable: string[];
  ms: number;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
}

export type AgentEvent = HopEvent | FinalEvent | ErrorEvent;

/** One row of the step log: a hop's start and end events merged. */
export type Step = Omit<HopEvent, 'type'>;

/** Merge a hop event into the step list (start creates the step, the terminal status updates it). */
export function applyHop(steps: Step[], ev: HopEvent): Step[] {
  const { type: _type, ...step } = ev;
  const i = steps.findIndex((s) => s.id === ev.id);
  if (i < 0) return [...steps, step];
  const next = [...steps];
  next[i] = { ...next[i], ...step, detail: step.detail ?? next[i].detail };
  return next;
}
