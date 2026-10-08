// Shapes from specs/001-isc-onboarding-agent/contracts/session-api.openapi.yaml and live-events.md.

export type Role = 'iam_engineer' | 'application_owner';
export type StepState = 'not_started' | 'in_progress' | 'passed' | 'failed';
export type StepKey =
  | 'application_ready'
  | 'source_created'
  | 'configured'
  | 'connection_check'
  | 'aggregation'
  | 'test_connection';
export const STEP_KEYS: StepKey[] = [
  'application_ready',
  'source_created',
  'configured',
  'connection_check',
  'aggregation',
  'test_connection',
];

export interface Me {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  is_admin: boolean;
  csrf_token: string;
}

export interface ApiError {
  code: string;
  message: string;
  retry_after_seconds?: number;
}

export interface SessionField {
  name: string;
  label: string;
  type: 'string' | 'string_list' | 'region' | 'region_list' | 'aws_account_id' | 'aws_account_id_list';
  required: boolean;
  default?: unknown;
}

export interface ConnectorType {
  id: string;
  name: string;
  status: 'available' | 'planned';
  description: string;
  owner_label: string;
  application_label: string;
  first_step_label: string;
  owner_asks: string;
  agent_configures: string;
  session_fields: SessionField[];
}

export interface Person {
  id: string;
  display_name: string;
  username?: string;
  online?: boolean;
}

export interface SessionSummary {
  id: string;
  title: string;
  connector_type: string;
  status: 'open' | 'finished';
  steps: Record<StepKey, StepState>;
  created_at: string;
  finished_at?: string | null;
}

export interface Session extends SessionSummary {
  tenant: { id: string; name: string; api_host: string; external_id: string | null } | null;
  details: Record<string, unknown>;
  source: { id: string; name: string } | null;
  /** Who the agent waits for; information only, it never holds a message back (FR-006a). */
  waiting_on: Role | null;
  /** The awaited person's next step, shown in the waiting banner (FR-006g); null without a wait. */
  waiting_reason: string | null;
  /** The IAM engineer's standing order to run the checks (FR-016a). */
  check_order: { display_name: string; at: string } | null;
  /** Last live-event id when this snapshot was read: the stream resumes after it. */
  event_seq: number;
  participants: { iam_engineer: Person | null; application_owner: Person | null };
}

export interface Attachment {
  id: string;
  secret_check: 'pending' | 'passed' | 'held';
  url: string | null;
}

export interface ApplicationStep {
  index: number;
  text: string;
  read_only: boolean;
}

export type Speaker = Role | 'agent';
export type QueueState = 'queued' | 'processing' | 'answered' | null;

export type MessageKind = 'message' | 'relay_note';

export interface Message {
  id: string;
  seq: number;
  /** Whose thread it belongs to (FR-006); a participant's message is always in their own thread. */
  thread: Role;
  /** A relay note is the agent's one-line note about what it asked or passed on in the other thread (FR-006c). */
  kind: MessageKind;
  speaker: Speaker;
  speaker_name: string;
  relay_ref: string | null;
  relayed_from: Role | null;
  text: string;
  masked: boolean;
  queue_state: QueueState;
  attachments: Attachment[];
  application_steps?: ApplicationStep[];
  created_at: string;
  /** Client-side only: a reply still streaming in. */
  streaming?: boolean;
}

export interface Action {
  id: string;
  action: string;
  source: { id: string; name: string } | null;
  ordered_by: { id: string; display_name: string };
  /** Why it ran: the IAM engineer's order, or a check rerun after the application owner confirmed a fix (FR-016a). */
  trigger: 'order' | 'application_owner_confirmation';
  result: 'ok' | 'failed';
  error: string | null;
  task_ids: string[];
  at: string;
}

export interface Tenant {
  id: string;
  name: string;
  api_host: string;
  external_id: string | null;
  credential_hint: string;
  status: 'usable' | 'credential_rejected' | 'unchecked';
  last_checked_at: string | null;
}

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  is_admin: boolean;
  status: 'active' | 'locked' | 'disabled';
  locked_until: string | null;
  last_sign_in: string | null;
}

export type SuggestionKind = 'answer' | 'order' | 'question';

/** One suggested message for the viewer's own thread (FR-006e); picking it only fills the message box. */
export interface Suggestion {
  text: string;
  kind: SuggestionKind;
}

export interface Suggestions {
  thread: Role;
  for_event_id: number;
  items: Suggestion[];
}
