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
  /** Field-level messages of a 422 (spec 002: session fields, the secret field). */
  errors?: Record<string, string>;
}

export interface SessionField {
  name: string;
  label: string;
  type:
    | 'string'
    | 'string_list'
    | 'region'
    | 'region_list'
    | 'aws_account_id'
    | 'aws_account_id_list'
    // spec 002 (Entra)
    | 'entra_tenant'
    | 'guid_list'
    | 'domain'
    | 'country_code'
    | 'capabilities';
  required: boolean;
  default?: unknown;
  /** Shown and validated only when this capability is chosen (spec 002). */
  show_if?: string;
  /** Allowed values (e.g. the source mode: new | extend). */
  choices?: string[];
}

/** An optional part of an onboarding (spec 002: directory, service principals, AI agents, provisioning). */
export interface Capability {
  id: string;
  label: string;
  summary?: string;
  tag: 'read_only' | 'writes';
  /** One line for the "what happens" panel, with the permission count already filled in. */
  owner_summary?: string;
  always?: boolean;
  enabled?: boolean;
  /** A risk the IAM engineer must accept before the session starts (provisioning). */
  warning?: string;
  requires_fields?: string[];
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
  capabilities?: Capability[] | null;
  /** The type needs an application secret, entered by the owner in the secret field (spec 002 FR-120). */
  secret?: { label: string; help: string; expires_help: string } | null;
  badge?: string | null;
  isc_api?: string | null;
  /** The starting plan, for the new-session "what happens" panel (spec 002 R15). */
  plan_preview?: { id: string; title: string; actor: Role | 'agent'; kind: string; capability?: string | null }[];
}

export type SecretState = 'missing' | 'received' | 'in_isc' | 'vault_deleted';

/** The application secret's metadata (spec 002): never the value. */
export interface SecretStatus {
  state: SecretState;
  provided_by?: string | null;
  provided_at?: string | null;
  expires_on?: string | null;
  expires_soon?: boolean;
  applied_at?: string | null;
  replaced_at?: string | null;
  vault_deleted_at?: string | null;
}

/** "What SailPoint now sees" (spec 002 R18), IAM engineer only. */
export interface Proof {
  users?: number | null;
  service_principals?: number | null;
  entitlements?: number | null;
  ai_agents?: number | null;
  ai_agents_state?: 'counted' | 'tenant_limitation' | 'not_chosen';
  updated_at?: string;
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
  /** `adopted`: created in an earlier session and only extended here (spec 002 FR-105). */
  source: { id: string; name: string; adopted?: boolean } | null;
  /** Who the agent waits for; information only, it never holds a message back (FR-006a). */
  waiting_on: Role | null;
  /** The awaited person's next step, shown in the waiting banner (FR-006g); null without a wait. */
  waiting_reason: string | null;
  /** The IAM engineer's standing order to run the checks (FR-016a). */
  check_order: { display_name: string; at: string } | null;
  /** Last live-event id when this snapshot was read: the stream resumes after it. */
  event_seq: number;
  participants: { iam_engineer: Person | null; application_owner: Person | null };
  /** The shared plan (FR-008a) with its count and next step. */
  plan?: PlanStep[];
  plan_done?: number;
  plan_total?: number;
  next_step_id?: string | null;
  finished_at?: string | null;
  reopened_at?: string | null;
  /** Spec 002: header chip order from the playbook; the application secret's metadata; proof counts (IAM only). */
  milestone_order?: StepKey[];
  mode?: 'new' | 'extend';
  application_secret?: SecretStatus | null;
  proof?: Proof | null;
  followups?: { plan_step: string | null; kind: string; started_at: string; state: string }[];
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

export type MessageKind = 'message' | 'relay_note' | 'system_note';

/** The agent reply's state while it waits or is being written (FR-006h, research R21). */
export type ReplyState = 'received' | 'working' | 'answered' | 'failed' | null;

export type PlanState = 'todo' | 'in_progress' | 'done' | 'failed' | 'skipped' | 'blocked';

/** One step of the shared plan (FR-008a, data-model PlanStep). */
export interface PlanStep {
  id: string;
  title: string;
  actor: Role | 'agent';
  kind: 'read_only' | 'change';
  state: PlanState;
  reason: string | null;
  milestone: StepKey | null;
  added_by: 'playbook' | 'agent';
  changed_at: string;
  /** Spec 002: the capability the step belongs to, and when a long-running step became pending. */
  capability?: string | null;
  pending_since?: string | null;
}

export interface Message {
  id: string;
  seq: number;
  /** Whose thread it belongs to (FR-006); a participant's message is always in their own thread. */
  thread: Role;
  /** A relay note is the agent's one-line note about what it asked or passed on in the other thread (FR-006c). */
  kind: MessageKind;
  speaker: Speaker;
  speaker_name: string;
  /** Who wrote it; after a handover (FR-033) the earlier holder's messages are not the viewer's own. */
  speaker_user_id?: string | null;
  relay_ref: string | null;
  relayed_from: Role | null;
  text: string;
  masked: boolean;
  queue_state: QueueState;
  /** On an agent reply: the participant message it answers, its state, messages ahead and the status text. */
  reply_to?: string | null;
  reply_state?: ReplyState;
  ahead?: number | null;
  status_text?: string | null;
  attachments: Attachment[];
  application_steps?: ApplicationStep[];
  created_at: string;
  /** Spec 002: a system note's tone (exposed secret, pending, finished) and code. */
  tone?: 'info' | 'success' | 'danger';
  code?: string | null;
  /** Client-side only: a reply still streaming in. */
  streaming?: boolean;
}

export interface Action {
  id: string;
  action: string;
  source: { id: string; name: string } | null;
  ordered_by: { id: string; display_name: string };
  /** Why it ran: the IAM engineer's order, or a check rerun after the application owner confirmed a fix (FR-016a). */
  trigger: 'order' | 'application_owner_confirmation' | 'followup' | 'secret_submitted';
  result: 'ok' | 'failed' | 'running' | 'tenant_limitation';
  /** Spec 002: one line under the action name, e.g. "clientSecret: [vaulted] · 13 fields". */
  summary?: string | null;
  /** One line, e.g. "passed · 3 accounts read" (FR-020a). */
  outcome?: string;
  order_message_id?: string | null;
  request?: Record<string, unknown>;
  request_missing?: boolean;
  response?: {
    task_ids?: string[];
    task_states?: Record<string, string>;
    counts?: { accounts?: number; entitlements?: number; users?: number; service_principals?: number; ai_agents?: number };
    error?: string | null;
  };
  response_missing?: boolean;
  diagnosis?: string | null;
  error: string | null;
  task_ids: string[];
  started_at?: string | null;
  duration_ms?: number | null;
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

/** One row of the admin's Sessions table (US8, FR-002). */
export interface AdminSession {
  id: string;
  title: string;
  connector_type: string;
  status: 'open' | 'finished';
  tenant_name: string;
  iam_engineer: { id: string; display_name: string; username: string; status: string } | null;
  application_owner: { id: string; display_name: string; username: string; status: string } | null;
  plan_done: number;
  plan_total: number;
  last_activity: string;
  expires_at: string | null;
  reopened_at: string | null;
  pending_handover: boolean;
}
