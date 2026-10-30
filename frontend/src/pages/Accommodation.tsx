import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, ChevronDown, ChevronUp, Download, Eye,
  Plus, Printer, RefreshCw, Sparkles, Users, Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, Moment, PageHead, Pagination, Pill, QueryState,
  Tabs, fromInput, toInput, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatMoney, formatNumber, formatTime } from "../lib/format";

interface HostelSummary {
  code: string;
  name: string;
  kind: "on_campus" | "external";
  status: string;
  capacity: number;
  total_capacity: number;
  occupied: number;
  ready: number;
  checked_in: number;
  reserved: number;
  cleaning: number;
  maintenance: number;
  cost_per_bed_night: string | null;
  distance_km: string | null;
  transport_available: boolean | null;
  accessibility_status: "unknown" | "declared" | "confirmed_none";
  step_free_entry: boolean | null;
  ground_floor_rooms: number | null;
  accessible_bathrooms: number | null;
  has_lift: boolean | null;
  accessibility_confirmed_by: string | null;
  accessibility_confirmed_at: string | null;
}

interface QueuePerson {
  code: string;
  name: string;
  contingent: string;
  college: string;
  arrival_at: string | null;
  departure_at: string | null;
  age: number | null;
  is_minor: boolean;
  minor_supervision_status: string | null;
  gender: string | null;
  housing_preference: string | null;
  housing_outreach_sent_at: string | null;
  accessibility: string | null;
  restricted: boolean;
  reason: string;
}

interface DecisionQueueItem {
  key: string;
  cause: string;
  raw_cause: string;
  count: number;
  already_here_count: number;
  next_12h_count: number;
  earliest_arrival: string | null;
  contingents_count: number;
  contingents: { code: string; count: number }[];
  sub_breakdown: { label: string; count: number }[];
  primary_action: { key: string; label: string };
  people: QueuePerson[];
}

interface Summary {
  as_of: string;
  hostels: HostelSummary[];
  over_capacity: { room: string; capacity: number; occupied: number }[];
  unallocated: number;
  budget: string | null;
  pipeline: {
    requested_total: number;
    need_bed: number;
    blocked: number;
    ready_to_place: number;
    allocated: number;
    checked_in: number;
    total_active_allocations: number;
  };
  arrivals: {
    already_here: number;
    next_6h: number;
    next_12h: number;
    next_24h: number;
    within_12h_total: number;
  };
  bed_status: {
    on_campus: {
      total_capacity: number;
      ready: number;
      occupied: number;
      reserved: number;
      cleaning: number;
      maintenance: number;
      rooms_total: number;
      accessible_rooms: number;
      accessible_beds: number;
      accessible_free_beds: number;
      ground_floor_non_access_beds: number;
      near_washroom_confirmed_beds: number;
      visual_confirmed_beds: number;
      gender_neutral_rooms: number;
      gender_neutral_beds: number;
      gender_neutral_free_beds: number;
      gender_neutral_status: string;
    };
    external: {
      total_capacity: number;
      ready: number;
      occupied: number;
      reserved: number;
      cleaning: number;
      maintenance: number;
      properties_total: number;
      declared_accessible_properties: number;
      confirmed_none_properties: number;
      unknown_accessible_properties: number;
      declared_accessible_beds: number;
    };
  };
  budget_gauge: {
    approved: number;
    committed: number;
    remaining: number;
    source: string;
    is_simulated_base: boolean;
    budget_held_count: number;
    placeable_in_open_beds_count: number;
    cheapest_mix_for_open_beds_cost: number;
    extra_needed_for_open_beds: number;
    overflow_beyond_open_beds_count: number;
    cheapest_mix_all_budget_held_cost: number;
    extra_needed_all_budget_held: number;
    cheapest_external_rate: number;
    overrides: {
      id: number;
      actor: string;
      action: string;
      summary: string;
      reason: string | null;
      added_amount: number | null;
      approved_amount: number | null;
      created_at: string;
    }[];
  };
  decision_queue: DecisionQueueItem[];
  housing_summary: {
    requesters_blank_gender: number;
    requesters_prefer_not_to_say: number;
    requesters_non_binary: number;
    requesters_unmapped_total: number;
    unallocated_blank_gender: number;
    unallocated_unmapped_total: number;
    outreach_sent_count: number;
    preferences_recorded: Record<string, number>;
    gender_neutral_rooms: {
      code: string;
      hostel: string;
      floor: number | null;
      capacity: number;
      occupied: number;
      free: number;
      accessible: boolean | null;
      confirmed_by: string | null;
    }[];
    outreach_question: string;
  };
  accessibility_summary: {
    total_requesters_with_need: number;
    by_need: {
      need: string;
      priority_rank: number;
      total_requesters: number;
      allocated: number;
      unallocated: number;
      on_campus_matching_beds: number;
      on_campus_free_matching_beds: number;
      feature_rule: string;
      external_status: string;
    }[];
    external_call_sheets: HostelSummary[];
  };
  minors_summary: {
    policy: string | null;
    policy_set_by: string | null;
    total_minors_in_pack: number;
    minors_requiring_accommodation: number;
    minors_on_campus: number;
    minors_external_unconfirmed: number;
    minors_external_confirmed: number;
    minors_unallocated: number;
    people_needing_confirmation: {
      code: string;
      name: string;
      age: number;
      contingent: string;
      college: string;
      leader: string | null;
      escort: string | null;
      placement: string;
      arrival_at: string | null;
      minor_supervision_status: string;
      minor_supervision_note: string | null;
    }[];
  };
  date_errors: {
    code: string;
    name: string;
    contingent: string;
    college: string;
    arrival_at: string | null;
    departure_at: string | null;
    is_allocated: boolean;
  }[];
}

interface PlanSummary {
  people: number;
  placed: number;
  held: number;
  on_campus: number;
  external: number;
  external_cost: number;
  contingents_split: number;
  held_reasons: { reason: string; count: number }[];
  relaxed: { constraint: string; count: number }[];
  splits: { group: string; locations: number }[];
}

interface PlanAssignmentPreview {
  code: string;
  name: string;
  contingent: string;
  room: string;
  hostel: string;
  kind: string;
  relaxed: string[];
}

interface Plan {
  summary: PlanSummary;
  state_hash: string;
  budget_limit: string | null;
  approved_budget: string | null;
  already_committed: string;
  over_approved_budget: boolean;
  assignments_preview?: PlanAssignmentPreview[];
}

interface RoomRow {
  code: string;
  hostel: string;
  hostel_code: string;
  kind: string;
  floor: number | null;
  gender_policy: string | null;
  policy_confirmed_by: string | null;
  capacity: number;
  occupied: number;
  available: number;
  accessible: boolean | null;
  near_washroom: boolean | null;
  visual_assistance: boolean | null;
  accessibility_status: string;
  status: string;
}

interface AllocationRow {
  id: number;
  participant: string;
  name: string;
  contingent: string;
  college: string;
  age: number | null;
  is_minor: boolean;
  minor_supervision_status: string | null;
  room: string;
  hostel: string;
  kind: string;
  status: string;
  check_in_at: string | null;
  check_out_at: string | null;
  relaxed_constraints: string[];
  override_reason: string | null;
}

interface Unallocated {
  code: string;
  name: string;
  college: string;
  contingent: string;
  arrival_at: string | null;
  departure_at: string | null;
  age: number | null;
  is_minor: boolean;
  minor_supervision_status: string | null;
  gender: string | null;
  housing_preference: string | null;
  housing_outreach_sent_at: string | null;
  accessibility: string | null;
  held_reason: string;
  restricted: boolean;
}

interface MapRoom {
  code: string;
  floor: number | null;
  capacity: number;
  occupied: number;
  gender_policy: string | null;
  policy_confirmed_by: string | null;
  accessible: boolean | null;
  near_washroom?: boolean | null;
  visual_assistance?: boolean | null;
  status: string;
}

interface MapHostel {
  code: string;
  name: string;
  kind: string;
  status: string;
  cost_per_bed_night: string | null;
  distance_km: string | null;
  transport_available: boolean | null;
  accessibility_status: string;
  step_free_entry: boolean | null;
  ground_floor_rooms: number | null;
  accessible_bathrooms: number | null;
  has_lift: boolean | null;
  accessibility_confirmed_by: string | null;
  rooms: MapRoom[];
}

interface RoomDetail {
  code: string;
  hostel: string;
  hostel_code: string;
  kind: string;
  floor: number | null;
  gender_policy: string | null;
  policy_confirmed_by: string | null;
  capacity: number;
  occupied: number;
  available: number;
  accessible: boolean | null;
  near_washroom: boolean | null;
  visual_assistance: boolean | null;
  status: string;
  accessibility_status: string;
  occupants: {
    allocation_id: number;
    participant: string;
    name: string;
    college: string;
    contingent: string;
    status: string;
    check_in_at: string | null;
    check_out_at: string | null;
    is_minor: boolean;
  }[];
}

interface ContingentDetail {
  code: string;
  college: string;
  leader: string | null;
  escort: string | null;
  preferences: string | null;
  strength_declared: number | null;
  leader_sleep: string;
  escort_sleep: string;
  counts: {
    registered: number;
    requested: number;
    placed: number;
    checked_in: number;
    waiting: number;
    minors_total: number;
    minors_unconfirmed: number;
    unresolved_housing_count: number;
  };
  locations: { hostel: string; count: number }[];
  waiting_reasons: { reason: string; count: number }[];
  placed_members: {
    code: string;
    name: string;
    age: number | null;
    is_minor: boolean;
    minor_supervision_status: string | null;
    room: string;
    hostel: string;
    kind: string;
    floor: number | null;
    status: string;
    check_in_at: string | null;
    arrival_at: string | null;
    departure_at: string | null;
  }[];
  waiting_members: {
    code: string;
    name: string;
    age: number | null;
    is_minor: boolean;
    minor_supervision_status: string | null;
    arrival_at: string | null;
    departure_at: string | null;
    reason: string;
    gender: string | null;
    housing_preference: string | null;
    accessibility: string | null;
    restricted: boolean;
  }[];
}

const ROOM_STATES: [string, string][] = [
  ["free", "Empty"],
  ["part", "Part full"],
  ["full", "Full"],
  ["over", "Over capacity"],
  ["closed", "Closed"],
];

const RELAXED: Record<string, string> = {
  room_set_by_property: "Pool room set by property",
  minor_off_campus: "Under 18 off-campus (confirm escort)",
};

const HOUSING_PREF_LABELS: Record<string, string> = {
  boys_block: "Boys' block",
  girls_block: "Girls' block",
  gender_neutral: "Neutral / private",
  talk_to_organizer: "Talk to organizer",
};

function Meter({ label, value, total, detail }: { label: string; value: number; total: number; detail: string }) {
  const percent = total ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="meter">
      <div className="meter-head"><span>{label}</span><span className="muted">{detail}</span></div>
      <div className="meter-track" role="progressbar" aria-label={label} aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function roomState(room: MapRoom, hostel: MapHostel) {
  if (room.occupied > room.capacity) return "over";
  if (room.status !== "available" || hostel.status !== "available" || !room.capacity) return "closed";
  if (room.occupied === room.capacity) return "full";
  return room.occupied ? "part" : "free";
}

function arrivalUrgencyLabel(iso: string | null | undefined, asOfIso?: string) {
  if (!iso) return null;
  const ref = asOfIso ? new Date(asOfIso).getTime() : Date.now();
  const diffH = (new Date(iso).getTime() - ref) / 3600000;
  if (diffH <= 0) return { text: "Here", tone: "bad" as const };
  if (diffH <= 6) return { text: `In ${Math.max(1, Math.round(diffH))}h`, tone: "bad" as const };
  if (diffH <= 12) return { text: `In ${Math.round(diffH)}h`, tone: "warn" as const };
  if (diffH <= 24) return { text: `In ${Math.round(diffH)}h`, tone: "info" as const };
  return null;
}

function RoomMap({
  onSelectRoom,
  onOpenCallSheet,
}: {
  onSelectRoom: (code: string) => void;
  onOpenCallSheet: (hostelCode?: string) => void;
}) {
  const { base, can } = useFest();
  const write = can("accommodation.write");
  const query = useQuery({
    queryKey: ["room-map", base],
    queryFn: () => api<MapHostel[]>(`${base}/accommodation/room-map`),
  });

  return (
    <QueryState query={query}>
      {!query.data?.length ? (
        <div className="state-empty">
          <h2>No hostels or rooms registered</h2>
        </div>
      ) : (
        <>
          <div className="room-key" aria-label="Room map key">
            {ROOM_STATES.map(([key, label]) => (
              <span key={key}><i className={`room-swatch room-${key}`} />{label}</span>
            ))}
            <span><i className="room-swatch room-free access" />Accessible</span>
            <span><i className="room-swatch room-free" style={{ boxShadow: "inset 0 0 0 2px var(--cyan)" }} />Gender-neutral</span>
          </div>
          <div className="room-map">
            {query.data.map((hostel) => {
              const floors = [...new Set(hostel.rooms.map((r) => r.floor))];
              const totalCap = hostel.rooms.reduce((sum, r) => sum + r.capacity, 0);
              const totalOcc = hostel.rooms.reduce((sum, r) => sum + r.occupied, 0);
              const free = hostel.rooms.reduce(
                (sum, r) => sum + (roomState(r, hostel) === "closed" ? 0 : Math.max(0, r.capacity - r.occupied)),
                0
              );
              const isExternal = hostel.kind === "external";
              return (
                <section key={hostel.code} className="panel">
                  <header>
                    <h2>{hostel.name}</h2>
                    {isExternal ? (
                      <Pill
                        value={`Access: ${(hostel.accessibility_status || "unknown").toUpperCase()}`}
                        tone={hostel.accessibility_status === "declared" ? "ok" : hostel.accessibility_status === "confirmed_none" ? "bad" : "warn"}
                      />
                    ) : (
                      <Pill value={hostel.status === "available" ? `${formatNumber(free)} free` : hostel.status} tone={free ? "ok" : undefined} />
                    )}
                  </header>

                  {!hostel.rooms.length ? (
                    <p className="muted">No rooms registered for {hostel.name}.</p>
                  ) : isExternal ? (
                    <div className="meters">
                      <Meter
                        label={`${formatMoney(hostel.cost_per_bed_night ?? 0)}/night · ${hostel.distance_km ?? "?"} km`}
                        value={totalOcc}
                        total={totalCap}
                        detail={`${totalOcc} / ${totalCap} used (${free} free)`}
                      />
                      <div className="row-actions" style={{ marginTop: 4 }}>
                        {hostel.rooms.map((r) => (
                          <button
                            key={r.code}
                            className="button-ghost"
                            onClick={() => onSelectRoom(r.code)}
                          >
                            View pool ({r.occupied}/{r.capacity})
                          </button>
                        ))}
                        {write && (
                          <button className="button-ghost" onClick={() => onOpenCallSheet(hostel.code)}>
                            <Wrench size={14} /> Call sheet
                          </button>
                        )}
                      </div>
                    </div>
                  ) : (
                    floors.map((floor) => (
                      <div key={floor ?? "unknown"} className="room-floor">
                        <span className="muted" style={{ fontSize: 12 }}>
                          {floor === null ? "Pool" : floor === 0 ? "Ground" : `Floor ${floor}`}
                        </span>
                        <div className="room-grid">
                          {hostel.rooms
                            .filter((r) => r.floor === floor)
                            .map((r) => {
                              const state = roomState(r, hostel);
                              const stateName = ROOM_STATES.find(([key]) => key === state)?.[1]?.toLowerCase() ?? state;
                              const label = `${r.code}: ${r.occupied} of ${r.capacity} beds used, ${stateName}${r.gender_policy ? `, ${r.gender_policy}` : ""}${r.accessible ? ", accessible" : ""}`;
                              return (
                                <button
                                  key={r.code}
                                  tabIndex={-1}
                                  className={`room room-${state} ${r.accessible ? "access" : ""} ${r.gender_policy === "gender_neutral" ? "gender-neutral" : ""}`}
                                  title={label}
                                  aria-label={label}
                                  onClick={() => onSelectRoom(r.code)}
                                >
                                  {r.occupied}/{r.capacity}
                                </button>
                              );
                            })}
                        </div>
                      </div>
                    ))
                  )}
                </section>
              );
            })}
          </div>
        </>
      )}
    </QueryState>
  );
}

function RoomDetailDialog({
  roomCode,
  onClose,
  onFilterAllocations,
  onOpenContingent,
}: {
  roomCode: string;
  onClose: () => void;
  onFilterAllocations: (code: string) => void;
  onOpenContingent: (code: string) => void;
}) {
  const { base, can } = useFest();
  const write = can("accommodation.write");
  const query = useQuery({
    queryKey: ["room-detail", base, roomCode],
    queryFn: () => api<RoomDetail>(`${base}/accommodation/rooms/${roomCode}`),
  });
  const [confirmedBy, setConfirmedBy] = useState("");
  const [nearWashroom, setNearWashroom] = useState<string>("");
  const [visualAssist, setVisualAssist] = useState<string>("");

  const updateFeatures = useAction(
    () =>
      api(`${base}/accommodation/rooms/${roomCode}/accessibility-features`, {
        method: "POST",
        body: JSON.stringify({
          near_washroom: nearWashroom === "" ? null : nearWashroom === "true",
          visual_assistance: visualAssist === "" ? null : visualAssist === "true",
          confirmed_by: confirmedBy,
        }),
      }),
    { success: `Updated room ${roomCode}`, onDone: () => query.refetch() }
  );

  return (
    <Dialog title={`Room ${roomCode}`} onClose={onClose} side>
      <QueryState query={query}>
        {query.data && (
          <div style={{ display: "grid", gap: 14 }}>
            <Facts
              items={[
                ["Hostel", `${query.data.hostel} (${query.data.hostel_code})`],
                ["Floor", query.data.floor === null ? "External pool" : query.data.floor === 0 ? "Ground (0)" : `Floor ${query.data.floor}`],
                ["Occupancy", `${query.data.occupied} / ${query.data.capacity} (${query.data.available} free)`],
                ["Policy", query.data.gender_policy ? `${query.data.gender_policy}${query.data.policy_confirmed_by ? ` (${query.data.policy_confirmed_by})` : ""}` : "External pool"],
                ["Wheelchair", query.data.accessible === null ? "UNKNOWN" : query.data.accessible ? "Yes" : "No"],
                ["Near washroom", query.data.near_washroom === null ? "UNKNOWN" : query.data.near_washroom ? "Yes" : "No"],
                ["Visual assist", query.data.visual_assistance === null ? "UNKNOWN" : query.data.visual_assistance ? "Yes" : "No"],
              ]}
            />
            <div className="row-actions">
              <button
                className="button-secondary"
                onClick={() => {
                  onClose();
                  onFilterAllocations(roomCode);
                }}
              >
                Open in Allocations
              </button>
            </div>

            <h3>Occupants ({query.data.occupants.length})</h3>
            {!query.data.occupants.length ? (
              <p className="muted">Empty room.</p>
            ) : (
              <ul className="stack scroll">
                {query.data.occupants.map((o) => (
                  <li key={o.allocation_id}>
                    <div>
                      <strong>{o.name}</strong> <span className="mono">{o.participant}</span>
                      {o.is_minor && <> <Pill value="Under 18" tone="warn" /></>}
                    </div>
                    <div className="row-actions">
                      {o.contingent !== "none" && (
                        <button className="contingent-btn" onClick={() => onOpenContingent(o.contingent)}>
                          {o.contingent}
                        </button>
                      )}
                      <Pill value={o.status} tone={o.status === "checked_in" ? "ok" : "info"} />
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {write && query.data.kind === "on_campus" && (
              <form
                className="form result-card"
                onSubmit={(e) => {
                  e.preventDefault();
                  updateFeatures.mutate();
                }}
              >
                <h3>Confirm room features</h3>
                <div className="form-grid">
                  <Field label="Near washroom">
                    <select value={nearWashroom} onChange={(e) => setNearWashroom(e.target.value)}>
                      <option value="">Keep ({query.data.near_washroom === null ? "UNKNOWN" : String(query.data.near_washroom)})</option>
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  </Field>
                  <Field label="Visual assistance">
                    <select value={visualAssist} onChange={(e) => setVisualAssist(e.target.value)}>
                      <option value="">Keep ({query.data.visual_assistance === null ? "UNKNOWN" : String(query.data.visual_assistance)})</option>
                      <option value="true">Yes</option>
                      <option value="false">No</option>
                    </select>
                  </Field>
                </div>
                <Field label="Confirmed by">
                  <input required minLength={2} placeholder="Warden name" value={confirmedBy} onChange={(e) => setConfirmedBy(e.target.value)} />
                </Field>
                <ErrorNote error={updateFeatures.error} />
                <button className="button-primary small" disabled={updateFeatures.isPending}>Save features</button>
              </form>
            )}
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

function BudgetOverrideModal({
  gauge,
  onClose,
  onSuccess,
}: {
  gauge: Summary["budget_gauge"];
  onClose: () => void;
  onSuccess: () => void;
}) {
  const { base } = useFest();
  const [amount, setAmount] = useState(
    gauge.extra_needed_for_open_beds > 0 ? String(Math.ceil(gauge.extra_needed_for_open_beds)) : ""
  );
  const [reason, setReason] = useState("");
  const save = useAction(
    () =>
      api<{ approved_budget: number; added_amount: number }>(`${base}/accommodation/budget-override`, {
        method: "POST",
        body: JSON.stringify({ amount: Number(amount), reason }),
      }),
    {
      success: (r) => `Added ${formatMoney(r.added_amount)} override. Approved budget is now ${formatMoney(r.approved_budget)}.`,
      onDone: () => {
        onSuccess();
        onClose();
      },
    }
  );

  return (
    <Dialog title="Request extra Hospitality budget" onClose={onClose}>
      <div className="stat-row" style={{ marginBottom: 12 }}>
        <div><span>Approved</span><strong>{formatMoney(gauge.approved)}</strong></div>
        <div><span>Remaining</span><strong>{formatMoney(gauge.remaining)}</strong></div>
        <div><span>380 open beds need</span><strong>{formatMoney(gauge.extra_needed_for_open_beds)}</strong></div>
      </div>
      <div className="chips" style={{ marginBottom: 12 }}>
        {gauge.extra_needed_for_open_beds > 0 && (
          <button
            type="button"
            className="chip active"
            onClick={() => setAmount(String(Math.ceil(gauge.extra_needed_for_open_beds)))}
          >
            +{formatMoney(gauge.extra_needed_for_open_beds)} (fill 380 open beds)
          </button>
        )}
        {gauge.extra_needed_all_budget_held > gauge.extra_needed_for_open_beds && (
          <button
            type="button"
            className="chip"
            onClick={() => setAmount(String(Math.ceil(gauge.extra_needed_all_budget_held)))}
          >
            +{formatMoney(gauge.extra_needed_all_budget_held)} (all {formatNumber(gauge.budget_held_count)} held)
          </button>
        )}
      </div>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Extra budget amount (₹)">
          <input type="number" required min={1} step="1" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label="Reason for audit log">
          <textarea
            required
            minLength={5}
            rows={2}
            placeholder="Reason for authorizing additional external stay budget..."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>
          {save.isPending ? "Saving..." : "Authorize extra budget"}
        </button>
      </form>
    </Dialog>
  );
}

function HousingOutreachModal({
  summary,
  initialCode,
  onClose,
}: {
  summary: Summary;
  initialCode?: string;
  onClose: () => void;
}) {
  const { base, can } = useFest();
  const write = can("accommodation.write");
  const hs = summary.housing_summary;
  const [scope, setScope] = useState<"blank_only" | "all_unmapped">("blank_only");
  const [codesText, setCodesText] = useState(initialCode ?? "");
  const [pref, setPref] = useState("gender_neutral");
  const [note, setNote] = useState("");
  const [roomCodesText, setRoomCodesText] = useState("");
  const [confirmedBy, setConfirmedBy] = useState("");
  const [roomReason, setRoomReason] = useState("");

  const outreach = useAction(
    () =>
      api<{ count: number; question: string; privacy_guarantee: string; drafts_sample: { code: string; name: string; email: string; phone: string; message: string }[] }>(
        `${base}/accommodation/housing-outreach`,
        { method: "POST", body: JSON.stringify({ scope }) }
      ),
    { success: (r) => `Queued private outreach for ${formatNumber(r.count)} participants` }
  );

  const savePref = useAction(
    () => {
      const codes = codesText.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      return api<{ updated: number }>(`${base}/accommodation/housing-preference`, {
        method: "POST",
        body: JSON.stringify({ participant_codes: codes, housing_preference: pref, note: note || null }),
      });
    },
    {
      success: (r) => `Saved housing preference for ${r.updated} participant(s)`,
      onDone: () => setCodesText(""),
    }
  );

  const registerNeutral = useAction(
    () => {
      const roomCodes = roomCodesText.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      return api<{ registered_rooms: string[]; total_beds: number }>(`${base}/accommodation/gender-neutral-rooms`, {
        method: "POST",
        body: JSON.stringify({ room_codes: roomCodes, confirmed_by: confirmedBy, reason: roomReason }),
      });
    },
    {
      success: (r) => `Registered ${r.registered_rooms.length} gender-neutral room(s) (${r.total_beds} beds)`,
      onDone: () => setRoomCodesText(""),
    }
  );

  return (
    <Dialog title="Housing preference & neutral rooms" onClose={onClose} side>
      <div style={{ display: "grid", gap: 14 }}>
        <div className="stat-row">
          <div><span>Blank gender</span><strong>{formatNumber(hs.unallocated_blank_gender)}</strong></div>
          <div><span>Prefer not to say</span><strong>{formatNumber(hs.requesters_prefer_not_to_say)}</strong></div>
          <div><span>Non-binary</span><strong>{formatNumber(hs.requesters_non_binary)}</strong></div>
          <div><span>Neutral rooms</span><strong>{hs.gender_neutral_rooms.length || "0 (UNKNOWN)"}</strong></div>
        </div>

        <section className="result-card">
          <h3>1. Send 1-to-1 private inquiry</h3>
          <div className="form">
            <Field label="Target group">
              <select value={scope} onChange={(e) => setScope(e.target.value as "blank_only" | "all_unmapped")}>
                <option value="blank_only">Blank gender ({formatNumber(hs.unallocated_blank_gender)} waiting)</option>
                <option value="all_unmapped">All unmapped ({formatNumber(hs.unallocated_unmapped_total)} waiting)</option>
              </select>
            </Field>
            <div className="row-actions">
              {write && (
                <button className="button-primary small" disabled={outreach.isPending} onClick={() => outreach.mutate()}>
                  {outreach.isPending ? "Sending..." : "Send private inquiry"}
                </button>
              )}
              <a className="button-ghost" href={`${base}/accommodation/housing-outreach.csv?scope=${scope}`} download>
                <Download size={14} /> Drafts CSV
              </a>
            </div>
            <ErrorNote error={outreach.error} />
          </div>
        </section>

        {write && (
          <section className="result-card">
            <h3>2. Record housing preference (keeps gender_as_given)</h3>
            <form
              className="form"
              onSubmit={(e) => {
                e.preventDefault();
                savePref.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Participant code(s)">
                  <input required placeholder="e.g. P0004, P0010" value={codesText} onChange={(e) => setCodesText(e.target.value)} />
                </Field>
                <Field label="Comfort option">
                  <select value={pref} onChange={(e) => setPref(e.target.value)}>
                    <option value="boys_block">Boys' block</option>
                    <option value="girls_block">Girls' block</option>
                    <option value="gender_neutral">Private or gender-neutral room</option>
                    <option value="talk_to_organizer">Talk to an organizer</option>
                    <option value="clear">Clear preference</option>
                  </select>
                </Field>
              </div>
              <Field label="Note">
                <input placeholder="Optional note" value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <ErrorNote error={savePref.error} />
              <button className="button-primary small" disabled={savePref.isPending}>
                Save preference
              </button>
            </form>
          </section>
        )}

        {write && (
          <section className="result-card">
            <h3>3. Register gender-neutral rooms on campus</h3>
            {hs.gender_neutral_rooms.length > 0 && (
              <div className="chips" style={{ marginBottom: 8 }}>
                {hs.gender_neutral_rooms.map((r) => (
                  <span key={r.code} className="chip active">
                    {r.code} ({r.occupied}/{r.capacity}) · {r.confirmed_by}
                  </span>
                ))}
              </div>
            )}
            <form
              className="form"
              onSubmit={(e) => {
                e.preventDefault();
                registerNeutral.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Room code(s)">
                  <input required placeholder="e.g. B3-001, G2-001" value={roomCodesText} onChange={(e) => setRoomCodesText(e.target.value)} />
                </Field>
                <Field label="Confirmed by">
                  <input required minLength={2} placeholder="Warden / Organizer" value={confirmedBy} onChange={(e) => setConfirmedBy(e.target.value)} />
                </Field>
              </div>
              <Field label="Reason">
                <input required minLength={5} placeholder="Reason for audit log" value={roomReason} onChange={(e) => setRoomReason(e.target.value)} />
              </Field>
              <ErrorNote error={registerNeutral.error} />
              <button className="button-primary small" disabled={registerNeutral.isPending}>
                Register rooms
              </button>
            </form>
          </section>
        )}
      </div>
    </Dialog>
  );
}

function AccessibilityCallSheetModal({
  summary,
  initialHostelCode,
  onClose,
}: {
  summary: Summary;
  initialHostelCode?: string;
  onClose: () => void;
}) {
  const { base, can } = useFest();
  const write = can("accommodation.write");
  const acc = summary.accessibility_summary;
  const sheets = acc.external_call_sheets;
  const [selectedCode, setSelectedCode] = useState(initialHostelCode || sheets[0]?.code || "EXT06");
  const current = sheets.find((h) => h.code === selectedCode) ?? sheets[0];

  const [stepFree, setStepFree] = useState(current?.step_free_entry ?? true);
  const [groundRooms, setGroundRooms] = useState(String(current?.ground_floor_rooms ?? 15));
  const [bathRooms, setBathRooms] = useState(String(current?.accessible_bathrooms ?? 15));
  const [hasLift, setHasLift] = useState(current?.has_lift ?? true);
  const [confirmedBy, setConfirmedBy] = useState(current?.accessibility_confirmed_by ?? "");

  useEffect(() => {
    if (current) {
      setStepFree(current.step_free_entry ?? true);
      setGroundRooms(String(current.ground_floor_rooms ?? 15));
      setBathRooms(String(current.accessible_bathrooms ?? 15));
      setHasLift(current.has_lift ?? true);
      setConfirmedBy(current.accessibility_confirmed_by ?? "");
    }
  }, [selectedCode, current?.code]);

  const saveSheet = useAction(
    (mode: "save" | "none" | "unknown") =>
      api(`${base}/accommodation/hostels/${selectedCode}/accessibility-call-sheet`, {
        method: "POST",
        body: JSON.stringify(
          mode === "unknown"
            ? { confirmed_by: confirmedBy || "Organizer", reset_unknown: true }
            : mode === "none"
            ? { step_free_entry: false, ground_floor_rooms: 0, accessible_bathrooms: 0, has_lift: false, confirmed_by: confirmedBy }
            : {
                step_free_entry: stepFree,
                ground_floor_rooms: Number(groundRooms) || 0,
                accessible_bathrooms: Number(bathRooms) || 0,
                has_lift: hasLift,
                confirmed_by: confirmedBy,
              }
        ),
      }),
    { success: `Updated ${selectedCode} call sheet` }
  );

  return (
    <Dialog title="Accessibility call sheet" onClose={onClose} side>
      <div style={{ display: "grid", gap: 14 }}>
        <section className="result-card">
          <h3>Need-to-room matching ({formatNumber(acc.total_requesters_with_need)} requesters)</h3>
          <table className="bed-status-table">
            <thead>
              <tr>
                <th>Need</th>
                <th className="num">Placed</th>
                <th className="num">Waiting</th>
                <th>Supply</th>
              </tr>
            </thead>
            <tbody>
              {acc.by_need.map((n) => (
                <tr key={n.need}>
                  <td><strong>#{n.priority_rank} {n.need}</strong></td>
                  <td className="num">{formatNumber(n.allocated)}</td>
                  <td className="num over-text">{formatNumber(n.unallocated)}</td>
                  <td>{formatNumber(n.on_campus_matching_beds)} campus · {n.external_status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="result-card">
          <h3>External property call sheet</h3>
          <div className="chips" style={{ marginTop: 8 }}>
            {sheets.map((h) => (
              <button
                key={h.code}
                type="button"
                className={selectedCode === h.code ? "chip active" : "chip"}
                onClick={() => setSelectedCode(h.code)}
              >
                {h.code} · {(h.accessibility_status || "unknown").toUpperCase()}
              </button>
            ))}
          </div>

          {current && (
            <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
              <Facts
                items={[
                  ["Property", `${current.name} (${current.code})`],
                  ["Rate", `${formatMoney(current.cost_per_bed_night ?? 0)}/night · ${current.distance_km ?? "?"} km`],
                  ["Open beds", `${current.ready} / ${current.capacity}`],
                  ["Status", (current.accessibility_status || "unknown").toUpperCase()],
                ]}
              />
              {write && (
                <form
                  className="form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveSheet.mutate("save");
                  }}
                >
                  <div className="form-grid">
                    <label className="check">
                      <input type="checkbox" checked={stepFree} onChange={(e) => setStepFree(e.target.checked)} />
                      Step-free entrance
                    </label>
                    <label className="check">
                      <input type="checkbox" checked={hasLift} onChange={(e) => setHasLift(e.target.checked)} />
                      Passenger lift
                    </label>
                    <Field label="Ground-floor beds">
                      <input type="number" min={0} max={current.capacity} value={groundRooms} onChange={(e) => setGroundRooms(e.target.value)} />
                    </Field>
                    <Field label="Accessible bath beds">
                      <input type="number" min={0} max={current.capacity} value={bathRooms} onChange={(e) => setBathRooms(e.target.value)} />
                    </Field>
                  </div>
                  <Field label="Confirmed by">
                    <input
                      required
                      minLength={2}
                      placeholder="Manager / Coordinator name"
                      value={confirmedBy}
                      onChange={(e) => setConfirmedBy(e.target.value)}
                    />
                  </Field>
                  <ErrorNote error={saveSheet.error} />
                  <div className="row-actions">
                    <button type="submit" className="button-primary small" disabled={saveSheet.isPending}>
                      Save DECLARED
                    </button>
                    <button
                      type="button"
                      className="button-ghost danger"
                      disabled={saveSheet.isPending || confirmedBy.trim().length < 2}
                      onClick={() => saveSheet.mutate("none")}
                    >
                      Mark CONFIRMED_NONE
                    </button>
                    <button
                      type="button"
                      className="button-ghost"
                      disabled={saveSheet.isPending}
                      onClick={() => saveSheet.mutate("unknown")}
                    >
                      Reset UNKNOWN
                    </button>
                  </div>
                </form>
              )}
            </div>
          )}
        </section>
      </div>
    </Dialog>
  );
}

function FixStayDatesModal({
  dateErrors,
  initialCode,
  onClose,
}: {
  dateErrors: Summary["date_errors"];
  initialCode?: string;
  onClose: () => void;
}) {
  const { base } = useFest();
  const target = dateErrors.find((d) => d.code === initialCode) ?? dateErrors[0];
  const [code, setCode] = useState(initialCode || target?.code || "P1189");
  const [arrival, setArrival] = useState(toInput(target?.arrival_at || "2026-11-01T08:00:00+05:30"));
  const [departure, setDeparture] = useState(toInput(target?.departure_at || "2026-11-02T20:00:00+05:30"));
  const [reason, setReason] = useState("");

  const selectPerson = (d: Summary["date_errors"][number]) => {
    setCode(d.code);
    setArrival(toInput(d.arrival_at));
    setDeparture(toInput(d.departure_at));
  };

  const swapDates = () => {
    if (!target?.arrival_at || !target?.departure_at) {
      const tmp = arrival;
      setArrival(departure);
      setDeparture(tmp);
      return;
    }
    const aDate = toInput(target.departure_at).slice(0, 10) + "T08:00";
    const dDate = toInput(target.arrival_at).slice(0, 10) + "T20:00";
    setArrival(aDate);
    setDeparture(dDate);
  };

  const save = useAction(
    () =>
      api(`${base}/accommodation/fix-stay-dates`, {
        method: "POST",
        body: JSON.stringify({
          participant_code: code,
          arrival_at: fromInput(arrival),
          departure_at: fromInput(departure),
          reason,
        }),
      }),
    {
      success: `Fixed stay dates for ${code}`,
      onDone: onClose,
    }
  );

  return (
    <Dialog title="Fix stay dates" onClose={onClose}>
      {dateErrors.length > 0 && (
        <div className="chips" style={{ marginBottom: 12 }}>
          {dateErrors.map((d) => (
            <button
              key={d.code}
              type="button"
              className={code === d.code ? "chip active" : "chip"}
              onClick={() => selectPerson(d)}
            >
              {d.code} · {d.name}
            </button>
          ))}
        </div>
      )}
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Participant ID">
          <input required value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
        </Field>
        <div className="form-grid">
          <Field label="Arrival (IST)">
            <input type="datetime-local" required value={arrival} onChange={(e) => setArrival(e.target.value)} />
          </Field>
          <Field label="Departure (IST)">
            <input type="datetime-local" required value={departure} onChange={(e) => setDeparture(e.target.value)} />
          </Field>
        </div>
        <div>
          <button type="button" className="button-ghost" onClick={swapDates}>
            Swap reversed dates
          </button>
        </div>
        <Field label="Reason for audit log">
          <textarea
            required
            minLength={5}
            rows={2}
            placeholder="Confirmed arrival and departure dates with coordinator..."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>
          {save.isPending ? "Saving..." : "Save dates"}
        </button>
      </form>
    </Dialog>
  );
}

function MinorsModal({
  minors,
  initialCode,
  onClose,
  onOpenContingent,
}: {
  minors: Summary["minors_summary"];
  initialCode?: string;
  onClose: () => void;
  onOpenContingent: (code: string) => void;
}) {
  const { base, can } = useFest();
  const write = can("accommodation.write");
  const [policy, setPolicy] = useState(minors.policy || "require_escort_confirmation");
  const [policyReason, setPolicyReason] = useState("");
  const [codesText, setCodesText] = useState(initialCode || "");
  const [contingentCode, setContingentCode] = useState("");
  const [escortName, setEscortName] = useState("");
  const [note, setNote] = useState("");
  const [escortReason, setEscortReason] = useState("");

  const savePolicy = useAction(
    () =>
      api(`${base}/accommodation/minor-policy`, {
        method: "POST",
        body: JSON.stringify({ policy, reason: policyReason }),
      }),
    { success: "Updated under-18 policy" }
  );

  const confirmEscort = useAction(
    () => {
      const codes = codesText.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      return api<{ confirmed: number }>(`${base}/accommodation/minors/confirm-escort`, {
        method: "POST",
        body: JSON.stringify({
          participant_codes: codes,
          contingent_code: contingentCode || null,
          escort_name: escortName,
          note: note || null,
          reason: escortReason,
        }),
      });
    },
    {
      success: (r) => `Confirmed escort for ${r.confirmed} under-18 participant(s)`,
      onDone: () => {
        setCodesText("");
        setContingentCode("");
      },
    }
  );

  return (
    <Dialog title="Under-18 escort confirmation" onClose={onClose} side>
      <div style={{ display: "grid", gap: 14 }}>
        <div className="stat-row">
          <div><span>On campus</span><strong>{formatNumber(minors.minors_on_campus)}</strong></div>
          <div><span>External (unconfirmed)</span><strong className="over-text">{formatNumber(minors.minors_external_unconfirmed)}</strong></div>
          <div><span>External (confirmed)</span><strong>{formatNumber(minors.minors_external_confirmed)}</strong></div>
          <div><span>Waiting</span><strong>{formatNumber(minors.minors_unallocated)}</strong></div>
        </div>

        {write && (
          <section className="result-card">
            <h3>1. Confirm escort / guardian</h3>
            <form
              className="form"
              onSubmit={(e) => {
                e.preventDefault();
                confirmEscort.mutate();
              }}
            >
              <div className="form-grid">
                <Field label="Participant code(s)">
                  <input placeholder="e.g. P0021" value={codesText} onChange={(e) => setCodesText(e.target.value)} />
                </Field>
                <Field label="Or Contingent code">
                  <input placeholder="e.g. C01" value={contingentCode} onChange={(e) => setContingentCode(e.target.value.toUpperCase())} />
                </Field>
              </div>
              <div className="form-grid">
                <Field label="Escort name">
                  <input required minLength={2} placeholder="Faculty escort" value={escortName} onChange={(e) => setEscortName(e.target.value)} />
                </Field>
                <Field label="Note">
                  <input placeholder="Optional note" value={note} onChange={(e) => setNote(e.target.value)} />
                </Field>
              </div>
              <Field label="Reason for audit log">
                <input required minLength={5} placeholder="Verified escort undertaking" value={escortReason} onChange={(e) => setEscortReason(e.target.value)} />
              </Field>
              <ErrorNote error={confirmEscort.error} />
              <button className="button-primary small" disabled={confirmEscort.isPending}>
                Confirm escort
              </button>
            </form>
          </section>
        )}

        {write && (
          <section className="result-card">
            <h3>2. Festival minor policy</h3>
            <form
              className="form"
              onSubmit={(e) => {
                e.preventDefault();
                savePolicy.mutate();
              }}
            >
              <Field label="Policy">
                <select value={policy} onChange={(e) => setPolicy(e.target.value)}>
                  <option value="require_escort_confirmation">Allow off-campus with escort confirmation</option>
                  <option value="on_campus_only">On-campus only unless escort confirmed first</option>
                  <option value="escort_confirmed_contingent">Allow off-campus with contingent escort</option>
                </select>
              </Field>
              <Field label="Reason">
                <input required minLength={5} placeholder="Reason for policy" value={policyReason} onChange={(e) => setPolicyReason(e.target.value)} />
              </Field>
              <ErrorNote error={savePolicy.error} />
              <button className="button-secondary" disabled={savePolicy.isPending}>Save policy</button>
            </form>
          </section>
        )}

        <section>
          <h3>Needing confirmation ({minors.people_needing_confirmation.length})</h3>
          <ul className="stack scroll">
            {minors.people_needing_confirmation.map((m) => (
              <li key={m.code}>
                <div>
                  <strong>{m.name}</strong> <span className="mono">{m.code}</span> <Pill value={`Age ${m.age}`} tone="warn" />
                  <div className="muted" style={{ fontSize: 13 }}>{m.placement} · Escort: {m.escort || "None"}</div>
                </div>
                <div className="row-actions">
                  <button className="contingent-btn" onClick={() => onOpenContingent(m.contingent)}>{m.contingent}</button>
                  {write && (
                    <button
                      className="button-ghost"
                      onClick={() => {
                        setCodesText(m.code);
                        setEscortName(m.escort || m.leader || "");
                      }}
                    >
                      Select
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </Dialog>
  );
}

function ContingentModal({
  contingentCode,
  onClose,
  onSwitchContingent,
  allCodes,
}: {
  contingentCode: string;
  onClose: () => void;
  onSwitchContingent: (code: string) => void;
  allCodes: string[];
}) {
  const { base } = useFest();
  const query = useQuery({
    queryKey: ["contingent-housing", base, contingentCode],
    queryFn: () => api<ContingentDetail>(`${base}/accommodation/contingents/${contingentCode}`),
  });

  return (
    <Dialog title={`Contingent ${contingentCode}`} onClose={onClose} className="wide">
      <div className="filters no-print" style={{ justifyContent: "space-between" }}>
        <div className="row-actions">
          <select value={contingentCode} onChange={(e) => onSwitchContingent(e.target.value)}>
            {allCodes.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <button className="button-secondary" onClick={() => window.print()}>
          <Printer size={15} /> Print sheet
        </button>
      </div>

      <QueryState query={query}>
        {query.data && (
          <div style={{ display: "grid", gap: 14 }}>
            <div className="result-card">
              <h2>{query.data.code} — {query.data.college}</h2>
              <Facts
                items={[
                  ["Leader", `${query.data.leader ?? "—"} (${query.data.leader_sleep})`],
                  ["Escort", `${query.data.escort ?? "—"} (${query.data.escort_sleep})`],
                  ["Preferences", query.data.preferences ?? "None"],
                  ["Unresolved housing", `${query.data.counts.unresolved_housing_count} people (private)`],
                ]}
              />
            </div>

            <div className="stat-row">
              <div><span>Requested</span><strong>{formatNumber(query.data.counts.requested)}</strong></div>
              <div><span>Placed</span><strong>{formatNumber(query.data.counts.placed)}</strong></div>
              <div><span>Checked in</span><strong>{formatNumber(query.data.counts.checked_in)}</strong></div>
              <div><span>Waiting</span><strong className={query.data.counts.waiting ? "over-text" : ""}>{formatNumber(query.data.counts.waiting)}</strong></div>
              <div><span>Under 18s</span><strong>{formatNumber(query.data.counts.minors_total)}</strong></div>
            </div>

            <div className="chart-grid">
              <div className="result-card" style={{ marginTop: 0 }}>
                <h3>Locations</h3>
                <ul className="stack">
                  {query.data.locations.map((l) => (
                    <li key={l.hostel}><span>{l.hostel}</span><strong>{formatNumber(l.count)} beds</strong></li>
                  ))}
                </ul>
              </div>
              <div className="result-card" style={{ marginTop: 0 }}>
                <h3>Waiting reasons</h3>
                <ul className="stack">
                  {query.data.waiting_reasons.map((w) => (
                    <li key={w.reason}><span>{w.reason}</span><strong>{formatNumber(w.count)}</strong></li>
                  ))}
                </ul>
              </div>
            </div>

            {query.data.waiting_members.length > 0 && (
              <div>
                <h3>Waiting ({query.data.waiting_members.length})</h3>
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table>
                    <thead>
                      <tr><th>ID</th><th>Name</th><th>Arrival</th><th>Reason</th></tr>
                    </thead>
                    <tbody>
                      {query.data.waiting_members.map((m) => (
                        <tr key={m.code}>
                          <td className="mono">{m.code}</td>
                          <td><strong>{m.name}</strong> {m.is_minor && <Pill value="U18" tone="warn" />}</td>
                          <td>{m.arrival_at ? formatTime(m.arrival_at) : "—"}</td>
                          <td>{m.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {query.data.placed_members.length > 0 && (
              <div>
                <h3>Placed ({query.data.placed_members.length})</h3>
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table>
                    <thead>
                      <tr><th>ID</th><th>Name</th><th>Room</th><th>Hostel</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {query.data.placed_members.map((m) => (
                        <tr key={m.code}>
                          <td className="mono">{m.code}</td>
                          <td><strong>{m.name}</strong> {m.is_minor && <Pill value="U18" tone="warn" />}</td>
                          <td className="mono">{m.room}</td>
                          <td>{m.hostel}</td>
                          <td><Pill value={m.status} tone={m.status === "checked_in" ? "ok" : undefined} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

function PreviewReviewModal({
  plan,
  onClose,
  onApprove,
  canWrite,
  isApplying,
  applyError,
}: {
  plan: Plan;
  onClose: () => void;
  onApprove: (reason?: string) => void;
  canWrite: boolean;
  isApplying: boolean;
  applyError: unknown;
}) {
  const s = plan.summary;
  const needsReason = plan.over_approved_budget || s.relaxed.length > 0;
  const [reason, setReason] = useState("");

  return (
    <Dialog title="Allocation Preview" onClose={onClose} className="wide">
      <div style={{ display: "grid", gap: 14 }}>
        <div className="stat-row">
          <div><span>Places</span><strong>{formatNumber(s.placed)}</strong></div>
          <div><span>On campus</span><strong>{formatNumber(s.on_campus)}</strong></div>
          <div><span>External</span><strong>{formatNumber(s.external)}</strong></div>
          <div><span>External cost</span><strong>{formatMoney(s.external_cost)}</strong></div>
          <div><span>Still held</span><strong className={s.held ? "over-text" : ""}>{formatNumber(s.held)}</strong></div>
          <div><span>Splits</span><strong>{s.contingents_split}</strong></div>
        </div>

        <div className="chart-grid">
          <div className="result-card" style={{ marginTop: 0 }}>
            <h3>Still held</h3>
            <ul className="stack">
              {s.held_reasons.map((h) => (
                <li key={h.reason}><span>{h.reason}</span><strong>{formatNumber(h.count)}</strong></li>
              ))}
            </ul>
          </div>
          <div className="result-card" style={{ marginTop: 0 }}>
            <h3>Notes</h3>
            <ul className="stack">
              {s.relaxed.map((r) => (
                <li key={r.constraint}><span>{RELAXED[r.constraint] ?? r.constraint}</span><strong>{formatNumber(r.count)}</strong></li>
              ))}
            </ul>
          </div>
        </div>

        {plan.assignments_preview && plan.assignments_preview.length > 0 && (
          <div className="table-wrap" style={{ maxHeight: 220 }}>
            <table>
              <thead>
                <tr><th>Participant</th><th>Contingent</th><th>Room</th><th>Property</th></tr>
              </thead>
              <tbody>
                {plan.assignments_preview.map((a) => (
                  <tr key={a.code}>
                    <td><strong>{a.name}</strong> <span className="mono">{a.code}</span></td>
                    <td className="mono">{a.contingent}</td>
                    <td className="mono">{a.room}</td>
                    <td>{a.hostel}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {canWrite && s.placed > 0 && (
          <form
            className="form result-card"
            onSubmit={(e) => {
              e.preventDefault();
              onApprove(reason.trim() || undefined);
            }}
          >
            {needsReason && (
              <Field label="Approval reason">
                <textarea
                  required={plan.over_approved_budget}
                  minLength={plan.over_approved_budget ? 5 : undefined}
                  rows={2}
                  placeholder="Reason for audit log..."
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </Field>
            )}
            <ErrorNote error={applyError} />
            <div className="row-actions">
              <button type="submit" className="button-primary" disabled={isApplying}>
                {isApplying ? "Approving..." : `Approve ${formatNumber(s.placed)} beds`}
              </button>
              <button type="button" className="button-ghost" onClick={onClose}>Close</button>
            </div>
          </form>
        )}
      </div>
    </Dialog>
  );
}

function HostelClosureWhatIf({ hostels }: { hostels: Summary["hostels"] }) {
  const { base, can } = useFest();
  const [hostelCode, setHostelCode] = useState(hostels[0]?.code || "B1");
  const [extraBudget, setExtraBudget] = useState("0");

  interface SimOutcome {
    id: number;
    state_hash: string;
    report: {
      title: string;
      headline: string;
      before_after: { metric: string; before: string | number; after: string | number }[];
      actions: { title: string; detail: string; module: string }[];
      needs_reason: string | null;
    };
    impacts: { id: number; module: string; severity: string; certainty: string; explanation: string; recommended_action: string | null }[];
  }

  const runSim = useAction(() =>
    api<SimOutcome>(`${base}/simulations`, {
      method: "POST",
      body: JSON.stringify({
        scenario: "hostel_closure",
        change: { hostel_code: hostelCode, extra_budget: Number(extraBudget) || 0 },
      }),
    })
  );

  const sim = runSim.data;

  return (
    <section className="panel">
      <header>
        <h2>Hostel-closure what-if</h2>
        <Link className="button-ghost" to={`/ripple?scenario=hostel_closure&hostel_code=${hostelCode}`}>
          Open in Ripple Simulator
        </Link>
      </header>

      {can("simulation.run") && (
        <form
          className="inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            runSim.mutate();
          }}
        >
          <Field label="Hostel to close">
            <select value={hostelCode} onChange={(e) => setHostelCode(e.target.value)}>
              {hostels.map((h) => (
                <option key={h.code} value={h.code}>
                  {h.name} ({h.code}) — {h.occupied}/{h.capacity}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Extra budget (₹)">
            <input type="number" min={0} value={extraBudget} onChange={(e) => setExtraBudget(e.target.value)} />
          </Field>
          <button className="button-primary" disabled={runSim.isPending}>
            <Sparkles size={16} /> {runSim.isPending ? "Simulating..." : "Simulate"}
          </button>
        </form>
      )}

      <ErrorNote error={runSim.error} />

      {sim && (
        <div style={{ marginTop: 14, display: "grid", gap: 12 }}>
          <div className="result-card">
            <h3>{sim.report.title}</h3>
            <p>{sim.report.headline}</p>
          </div>
          <div className="chart-grid">
            <table className="bed-status-table">
              <thead>
                <tr><th>Metric</th><th>Current</th><th>If closed</th></tr>
              </thead>
              <tbody>
                {sim.report.before_after.map((row) => (
                  <tr key={row.metric}>
                    <td>{row.metric}</td>
                    <td>{row.before}</td>
                    <td className="changed">{row.after}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="impacts" style={{ maxHeight: 240 }}>
              {sim.impacts.map((imp) => (
                <li key={imp.id} className={`impact impact-${imp.severity}`}>
                  <div className="impact-tags">
                    <Pill value={imp.module} />
                    <span className={`certainty ${imp.certainty}`}>{imp.certainty}</span>
                  </div>
                  <div>{imp.explanation}</div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}

function ManualAllocate({ code, onClose }: { code: string; onClose: () => void }) {
  const { base } = useFest();
  const [room, setRoom] = useState("");
  const [reason, setReason] = useState("");
  const suggestions = useQuery({
    queryKey: ["suggest", code],
    queryFn: () => api<{ code: string; hostel: string; available: number }[]>(`${base}/accommodation/suggest/${code}`),
  });
  const save = useAction(
    () =>
      api(`${base}/accommodation/allocate`, {
        method: "POST",
        body: JSON.stringify({ participant_code: code, room_code: room, override_reason: reason || null }),
      }),
    { success: "Room allocated", onDone: onClose }
  );
  return (
    <Dialog title={`Allocate ${code}`} onClose={onClose}>
      <QueryState query={suggestions}>
        {suggestions.data?.length ? (
          <div className="chips">
            {suggestions.data.map((r) => (
              <button key={r.code} className={room === r.code ? "chip active" : "chip"} onClick={() => setRoom(r.code)}>
                {r.code} · {r.hostel} ({r.available} free)
              </button>
            ))}
          </div>
        ) : null}
      </QueryState>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Room"><input required value={room} onChange={(e) => setRoom(e.target.value)} /></Field>
        <Field label="Override reason">
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Allocate room</button>
      </form>
    </Dialog>
  );
}

function AddHostel({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: "", kind: "on_campus", cost_per_bed_night: "", distance_km: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(
    () =>
      api(`${base}/accommodation/hostels`, {
        method: "POST",
        body: JSON.stringify({
          name: form.name,
          kind: form.kind,
          cost_per_bed_night: form.cost_per_bed_night || null,
          distance_km: form.distance_km || null,
        }),
      }),
    { success: "Hostel added", onDone: onClose }
  );
  return (
    <Dialog title="Add hostel" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
        <Field label="Type"><select value={form.kind} onChange={set("kind")}><option value="on_campus">On campus</option><option value="external">External</option></select></Field>
        <Field label="Cost / night"><input type="number" min={0} value={form.cost_per_bed_night} onChange={set("cost_per_bed_night")} /></Field>
        <Field label="Distance (km)"><input type="number" min={0} step="0.1" value={form.distance_km} onChange={set("distance_km")} /></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save hostel</button></div>
      </form>
    </Dialog>
  );
}

function AddRoom({ hostels, onClose }: { hostels: Summary["hostels"]; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ hostel_code: hostels[0]?.code ?? "", code: "", floor: "", gender_policy: "", capacity: "2", accessible: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(
    () =>
      api(`${base}/accommodation/rooms`, {
        method: "POST",
        body: JSON.stringify({
          ...form,
          floor: form.floor ? Number(form.floor) : null,
          gender_policy: form.gender_policy || null,
          capacity: Number(form.capacity),
          accessible: form.accessible === "" ? null : form.accessible === "true",
        }),
      }),
    { success: "Room added", onDone: onClose }
  );
  return (
    <Dialog title="Add room" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Hostel"><select value={form.hostel_code} onChange={set("hostel_code")}>{hostels.map((h) => <option key={h.code} value={h.code}>{h.name}</option>)}</select></Field>
        <Field label="Room number"><input required minLength={2} value={form.code} onChange={set("code")} /></Field>
        <Field label="Floor"><input type="number" min={0} value={form.floor} onChange={set("floor")} /></Field>
        <Field label="Capacity"><input type="number" min={0} required value={form.capacity} onChange={set("capacity")} /></Field>
        <Field label="Gender policy">
          <select value={form.gender_policy} onChange={set("gender_policy")}>
            <option value="">Not gendered</option>
            <option value="boys">Boys</option>
            <option value="girls">Girls</option>
            <option value="gender_neutral">Gender-neutral / private</option>
          </select>
        </Field>
        <Field label="Accessible"><select value={form.accessible} onChange={set("accessible")}><option value="">Unknown</option><option value="true">Yes</option><option value="false">No</option></select></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save room</button></div>
      </form>
    </Dialog>
  );
}

const roomColumns: Column<RoomRow>[] = [
  { label: "Room", sort: "code", cell: (r) => <span className="mono">{r.code}</span> },
  { label: "Hostel", sort: "hostel", cell: (r) => r.hostel },
  { label: "Floor", sort: "floor", cell: (r) => r.floor ?? "Pool" },
  {
    label: "Policy",
    sort: "policy",
    cell: (r) =>
      r.gender_policy === "gender_neutral" ? (
        <Pill value="gender neutral" tone="info" />
      ) : (
        r.gender_policy ?? "—"
      ),
  },
  {
    label: "Accessible",
    sort: "accessible",
    cell: (r) =>
      r.accessible === null ? (
        <Pill value={r.accessibility_status} tone="warn" />
      ) : r.accessible ? (
        <Pill value="Yes" tone="ok" />
      ) : (
        "No"
      ),
  },
  { label: "Occupied", sort: "occupied", cell: (r) => `${r.occupied} / ${r.capacity}`, className: "num" },
  { label: "Free", sort: "free", cell: (r) => r.available, className: "num" },
];

export function Accommodation() {
  const { base, can } = useFest();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"map" | "rooms" | "allocations" | "unallocated" | "whatif">(
    params.get("tab") === "unallocated" ? "unallocated" : "map"
  );
  const [pipelineFilter, setPipelineFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [hostel, setHostel] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [room, setRoom] = useState("");
  const [allocStatus, setAllocStatus] = useState("");
  const [arrivalWindow, setArrivalWindow] = useState("");
  const [causeFilter, setCauseFilter] = useState("");
  const [contingentFilter, setContingentFilter] = useState("");
  const [under18Only, setUnder18Only] = useState(false);
  const [sort, setSort] = useState("");

  // Modals & drawers
  const [target, setTarget] = useState<string>();
  const [selectedMapRoom, setSelectedMapRoom] = useState<string>();
  const [adding, setAdding] = useState<"hostel" | "room" | null>(null);
  const [expandedCauses, setExpandedCauses] = useState<Record<string, boolean>>({});
  const [budgetModal, setBudgetModal] = useState(false);
  const [housingModalCode, setHousingModalCode] = useState<string | null>(null);
  const [callSheetHostel, setCallSheetHostel] = useState<string | null>(null);
  const [dateModalCode, setDateModalCode] = useState<string | null>(null);
  const [minorsModalCode, setMinorsModalCode] = useState<string | null>(null);
  const [contingentModalCode, setContingentModalCode] = useState<string | null>(null);
  const [reviewingPreview, setReviewingPreview] = useState(false);

  const q = useDebounced(search);
  useEffect(() => setPage(1), [tab, q, hostel, freeOnly, room, allocStatus, arrivalWindow, causeFilter, contingentFilter, under18Only, sort]);

  const summary = useQuery({
    queryKey: ["accommodation-summary", base],
    queryFn: () => api<Summary>(`${base}/accommodation/summary`),
  });

  const rooms = usePaged<RoomRow>(`${base}/accommodation/rooms`, { q, hostel, free_only: freeOnly, sort, page }, tab === "rooms");
  const allocations = usePaged<AllocationRow>(
    `${base}/accommodation/allocations`,
    { q, room, status: allocStatus, contingent: contingentFilter, under_18: under18Only, sort, page },
    tab === "allocations"
  );
  const unallocated = usePaged<Unallocated>(
    `${base}/accommodation/unallocated`,
    { q, arrival_window: arrivalWindow, cause: causeFilter, contingent: contingentFilter, under_18: under18Only, sort, page },
    tab === "unallocated"
  );

  const preview = useAction(
    () => api<Plan>(`${base}/accommodation/plan`, { method: "POST", body: JSON.stringify({}) }),
    {
      success: (p) =>
        p.summary.placed > 0
          ? `Preview ready: places ${formatNumber(p.summary.placed)} beds`
          : `0 can be placed until a blocker in 'Needs a decision' is resolved`,
    }
  );

  const applyPlan = useAction(
    (reason?: string) =>
      api<PlanSummary>(`${base}/accommodation/plan/apply`, {
        method: "POST",
        body: JSON.stringify({ state_hash: preview.data!.state_hash, reason: reason || null }),
      }),
    {
      success: (r) => `${formatNumber(r.placed)} beds allocated`,
      onDone: () => {
        setReviewingPreview(false);
        preview.reset();
      },
    }
  );

  const stay = useAction(
    ({ id, action }: { id: number; action: string }) =>
      api(`${base}/accommodation/allocations/${id}/${action}`, { method: "POST" }),
    { success: "Recorded" }
  );
  const cancel = useAction(
    (id: number) => api(`${base}/accommodation/allocations/${id}`, { method: "DELETE" }),
    { success: "Allocation cancelled" }
  );

  const write = can("accommodation.write");
  const canBudget = write || can("finance.approve");
  const allContingentCodes = Array.from({ length: 38 }, (_, i) => `C${String(i + 1).padStart(2, "0")}`);

  const switchTab = (next: typeof tab) => {
    setTab(next);
    setSort("");
  };

  const handlePipelineClick = (step: string) => {
    if (pipelineFilter === step) {
      setPipelineFilter("");
      setCauseFilter("");
      setAllocStatus("");
      return;
    }
    setPipelineFilter(step);
    if (step === "need_bed" || step === "blocked") {
      setTab("unallocated");
      setCauseFilter("");
    } else if (step === "proposed") {
      if (preview.data) {
        setReviewingPreview(true);
      } else {
        preview.mutate();
      }
    } else if (step === "allocated") {
      setTab("allocations");
      setAllocStatus("allocated");
    } else if (step === "checked_in") {
      setTab("allocations");
      setAllocStatus("checked_in");
    }
  };

  const triggerQueueAction = (actionKey: string, item: DecisionQueueItem) => {
    if (actionKey === "request_budget") {
      setBudgetModal(true);
    } else if (actionKey === "housing_outreach" || actionKey === "access_arrangement") {
      setHousingModalCode("");
    } else if (actionKey === "accessibility_call_sheet") {
      setCallSheetHostel("EXT06");
    } else if (actionKey === "fix_stay_dates") {
      setDateModalCode(item.people[0]?.code || "P1189");
    } else if (actionKey === "confirm_escort") {
      setMinorsModalCode("");
    } else {
      setTab("unallocated");
      setCauseFilter(item.key);
    }
  };

  const toggleExpand = (key: string) => {
    setExpandedCauses((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const allocationColumns: Column<AllocationRow>[] = [
    {
      label: "Participant",
      sort: "name",
      cell: (a) => (
        <>
          <strong>{a.name}</strong> <span className="mono">{a.participant}</span>
          {a.is_minor && (
            <>
              {" "}
              <Pill
                value={a.minor_supervision_status === "confirmed" ? "U18 OK" : "Under 18"}
                tone={a.minor_supervision_status === "confirmed" ? "ok" : "warn"}
              />
            </>
          )}
        </>
      ),
    },
    {
      label: "Contingent",
      cell: (a) =>
        a.contingent !== "none" ? (
          <button
            className="contingent-btn"
            onClick={(e) => {
              e.stopPropagation();
              setContingentModalCode(a.contingent);
            }}
          >
            {a.contingent}
          </button>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      label: "Room",
      sort: "room",
      cell: (a) => (
        <>
          <span className="mono">{a.room}</span> <span className="muted">· {a.hostel}</span>
        </>
      ),
    },
    { label: "Status", sort: "status", cell: (a) => <Pill value={a.status} tone={a.status === "checked_in" ? "ok" : undefined} /> },
    ...(write
      ? [
          {
            label: "Actions",
            cell: (a: AllocationRow) => (
              <div className="row-actions">
                {!a.check_in_at && (
                  <button className="button-ghost" disabled={stay.isPending} onClick={() => stay.mutate({ id: a.id, action: "check-in" })}>
                    Check in
                  </button>
                )}
                {a.check_in_at && !a.check_out_at && (
                  <button className="button-ghost" disabled={stay.isPending} onClick={() => stay.mutate({ id: a.id, action: "check-out" })}>
                    Check out
                  </button>
                )}
                {a.is_minor && a.minor_supervision_status !== "confirmed" && (
                  <button className="button-ghost" onClick={() => setMinorsModalCode(a.participant)}>
                    Escort
                  </button>
                )}
                <button className="button-ghost danger" disabled={cancel.isPending} onClick={() => cancel.mutate(a.id)}>
                  Cancel
                </button>
              </div>
            ),
          },
        ]
      : []),
  ];

  const unallocatedColumns: Column<Unallocated>[] = [
    { label: "ID", sort: "code", cell: (p) => <span className="mono">{p.code}</span> },
    {
      label: "Name",
      sort: "name",
      cell: (p) => (
        <>
          <strong>{p.name}</strong>
          {p.is_minor && (
            <>
              {" "}
              <Pill
                value={p.minor_supervision_status === "confirmed" ? "U18 OK" : `U18 (${p.age})`}
                tone={p.minor_supervision_status === "confirmed" ? "ok" : "warn"}
              />
            </>
          )}
        </>
      ),
    },
    {
      label: "Contingent",
      cell: (p) =>
        p.contingent !== "none" ? (
          <button
            className="contingent-btn"
            onClick={(e) => {
              e.stopPropagation();
              setContingentModalCode(p.contingent);
            }}
          >
            {p.contingent}
          </button>
        ) : (
          <span className="muted">—</span>
        ),
    },
    {
      label: "Arrival",
      sort: "arrival",
      cell: (p) => {
        const urg = arrivalUrgencyLabel(p.arrival_at, summary.data?.as_of);
        return (
          <span className="row-actions">
            <span>{p.arrival_at ? formatTime(p.arrival_at) : "—"}</span>
            {urg && <Pill value={urg.text} tone={urg.tone} />}
          </span>
        );
      },
    },
    {
      label: "Blocker",
      cell: (p) => p.held_reason,
    },
    {
      label: "Housing / Access",
      cell: (p) =>
        p.restricted ? (
          <span className="restricted">Restricted</span>
        ) : (
          <span className="row-actions">
            <Pill value={p.gender ?? "Blank gender"} tone={p.gender ? undefined : "warn"} />
            {p.housing_preference && (
              <Pill value={HOUSING_PREF_LABELS[p.housing_preference] ?? p.housing_preference} tone="ok" />
            )}
            {p.accessibility && <Pill value={p.accessibility} tone="info" />}
          </span>
        ),
    },
    ...(write
      ? [
          {
            label: "Actions",
            cell: (p: Unallocated) => (
              <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                <button className="button-ghost" onClick={() => setTarget(p.code)}>Allocate</button>
                {(!p.gender || p.gender === "Non-binary" || p.gender === "Prefer not to say") && (
                  <button className="button-ghost" onClick={() => setHousingModalCode(p.code)}>Pref</button>
                )}
                {p.held_reason.includes("Stay dates") && (
                  <button className="button-ghost" onClick={() => setDateModalCode(p.code)}>Dates</button>
                )}
              </div>
            ),
          },
        ]
      : []),
  ];

  const data = summary.data;
  const previewSummary = preview.data?.summary;
  const proposedCount = previewSummary ? previewSummary.placed : 0;
  const blockedCount = previewSummary ? previewSummary.held : (data?.pipeline.blocked ?? 0);

  return (
    <div className="accom-page">
      <PageHead title="Accommodation">
        <button className="button-ghost" onClick={() => setContingentModalCode("C01")}>
          <Users size={15} /> Contingent sheet
        </button>
        {write && (
          <>
            <button className="button-ghost" onClick={() => setAdding("room")}><Plus size={15} /> Add room</button>
            <button className="button-ghost" onClick={() => setAdding("hostel")}><Plus size={15} /> Add hostel</button>
          </>
        )}
        <button className="button-primary small" disabled={preview.isPending} onClick={() => preview.mutate()}>
          <Sparkles size={15} /> {preview.isPending ? "Running..." : "Preview plan"}
        </button>
      </PageHead>

      <QueryState query={summary}>
        {data && (
          <>
            {/* Signature FestOS Hero Moment */}
            <Moment
              eyebrow={
                <>
                  <Pill value={`As of ${formatTime(data.as_of)}`} />
                  <span>{formatNumber(data.arrivals.next_12h)} arrive in 12h ({formatNumber(data.arrivals.already_here)} here)</span>
                  <span>·</span>
                  <span>{formatMoney(data.budget_gauge.remaining)} budget left</span>
                  <span>·</span>
                  <span>{data.decision_queue.length} decisions need you</span>
                </>
              }
              title={`${formatNumber(data.unallocated)} people have no bed`}
            >
              <div className="row-actions" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => {
                    setTab("unallocated");
                    setArrivalWindow("12h");
                  }}
                >
                  Arriving in 12h ({formatNumber(data.arrivals.next_12h)})
                </button>
                <button
                  type="button"
                  className="button-ghost"
                  style={{ borderColor: "rgba(255,255,255,0.35)", color: "#fff" }}
                  onClick={() => queryClient.invalidateQueries()}
                >
                  <RefreshCw size={14} /> Refresh
                </button>
              </div>
            </Moment>

            {data.over_capacity.length > 0 && (
              <p className="form-error" role="alert" style={{ marginBottom: 12 }}>
                Over capacity: {data.over_capacity.map((r) => `${r.room} (${r.occupied}/${r.capacity})`).join(", ")}
              </p>
            )}

            {/* 5-Step Pipeline Ribbon (Native FestOS .ribbon) */}
            <section className="ribbon pipeline-ribbon" aria-label="Allocation pipeline">
              <button
                type="button"
                className={`ribbon-cell ${pipelineFilter === "need_bed" ? "active" : ""}`}
                onClick={() => handlePipelineClick("need_bed")}
              >
                <span className="ribbon-label">1 · Need a bed</span>
                <p className="kpi-value">{formatNumber(data.pipeline.need_bed)}</p>
              </button>

              <button
                type="button"
                className={`ribbon-cell ${pipelineFilter === "blocked" ? "active" : ""}`}
                onClick={() => handlePipelineClick("blocked")}
              >
                <span className="ribbon-label">2 · Blocked (needs human)</span>
                <p className="kpi-value" style={{ color: blockedCount ? "var(--critical)" : undefined }}>
                  {formatNumber(blockedCount)}
                </p>
              </button>

              <button
                type="button"
                className={`ribbon-cell ${pipelineFilter === "proposed" || preview.data ? "active" : ""}`}
                onClick={() => handlePipelineClick("proposed")}
              >
                <span className="ribbon-label">3 · Proposed (preview)</span>
                <p className="kpi-value" style={{ color: proposedCount ? "var(--cyan)" : undefined }}>
                  {formatNumber(proposedCount)}
                </p>
              </button>

              <button
                type="button"
                className={`ribbon-cell ${pipelineFilter === "allocated" ? "active" : ""}`}
                onClick={() => handlePipelineClick("allocated")}
              >
                <span className="ribbon-label">4 · Allocated</span>
                <p className="kpi-value">{formatNumber(data.pipeline.allocated)}</p>
              </button>

              <button
                type="button"
                className={`ribbon-cell ${pipelineFilter === "checked_in" ? "active" : ""}`}
                onClick={() => handlePipelineClick("checked_in")}
              >
                <span className="ribbon-label">5 · Checked in <CheckCircle2 size={12} style={{ verticalAlign: "-1px" }} /></span>
                <p className="kpi-value" style={{ color: "var(--ok)" }}>
                  {formatNumber(data.pipeline.checked_in)}
                </p>
              </button>
            </section>

            {/* Main 2/3 + 1/3 Operations Grid (Native FestOS .ops-grid) */}
            <div className="ops-grid" style={{ marginBottom: 18 }}>
              <section className="panel alerts-panel" aria-label="Needs a decision work queue">
                <header>
                  <h2>Needs a decision</h2>
                  <Pill value={`${data.decision_queue.length} causes`} tone="warn" />
                </header>

                {!data.decision_queue.length ? (
                  <p className="muted">No blockers remain. Preview and approve the plan.</p>
                ) : (
                  <ol>
                    {data.decision_queue.map((item, idx) => {
                      const isExpanded = !!expandedCauses[item.key];
                      const isUrgent = item.already_here_count > 0 || item.next_12h_count > 0;
                      return (
                        <li key={item.key} className={`decision-item ${isUrgent ? "alert-critical" : "alert-high"}`}>
                          <div className="decision-row">
                            <span className="decision-rank" aria-hidden="true">{idx + 1}</span>
                            <div>
                              <span className="alert-level">{formatNumber(item.count)} people</span>
                              <span className="muted">
                                {" · "}Earliest {item.earliest_arrival ? formatTime(item.earliest_arrival) : "—"}
                                {" · "}{item.contingents_count} contingents
                              </span>
                              <h3>{item.cause}</h3>
                              <div className="row-actions" style={{ marginTop: 6 }}>
                                {item.contingents.slice(0, 5).map((c) => (
                                  <button
                                    key={c.code}
                                    type="button"
                                    className="contingent-btn"
                                    onClick={() => setContingentModalCode(c.code)}
                                  >
                                    {c.code} ({c.count})
                                  </button>
                                ))}
                                <button
                                  type="button"
                                  className="button-ghost"
                                  style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                                  aria-expanded={isExpanded}
                                  onClick={() => toggleExpand(item.key)}
                                >
                                  {isExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                                  {isExpanded ? "Hide" : "People"}
                                </button>
                              </div>
                            </div>
                            <button
                              type="button"
                              className="button-primary small"
                              onClick={() => triggerQueueAction(item.primary_action.key, item)}
                            >
                              {item.primary_action.label}
                            </button>
                          </div>

                          {isExpanded && (
                            <div className="queue-expanded">
                              <div className="table-wrap" style={{ maxHeight: 230 }}>
                                <table>
                                  <thead>
                                    <tr>
                                      <th>ID</th>
                                      <th>Name</th>
                                      <th>Contingent</th>
                                      <th>Arrival</th>
                                      <th>Action</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {item.people.map((p) => {
                                      const urg = arrivalUrgencyLabel(p.arrival_at, data.as_of);
                                      return (
                                        <tr key={p.code}>
                                          <td className="mono">{p.code}</td>
                                          <td>
                                            <strong>{p.name}</strong>
                                            {p.is_minor && <> <Pill value="U18" tone="warn" /></>}
                                          </td>
                                          <td>
                                            {p.contingent !== "none" ? (
                                              <button
                                                type="button"
                                                className="contingent-btn"
                                                onClick={() => setContingentModalCode(p.contingent)}
                                              >
                                                {p.contingent}
                                              </button>
                                            ) : (
                                              "—"
                                            )}
                                          </td>
                                          <td>
                                            {p.arrival_at ? formatTime(p.arrival_at) : "—"}{" "}
                                            {urg && <Pill value={urg.text} tone={urg.tone} />}
                                          </td>
                                          <td>
                                            {write && (
                                              <div className="row-actions">
                                                {item.key === "invalid_dates" && (
                                                  <button className="button-ghost" onClick={() => setDateModalCode(p.code)}>Fix dates</button>
                                                )}
                                                {(item.key === "gender_blank" || item.key === "access_gendered") && (
                                                  <button className="button-ghost" onClick={() => setHousingModalCode(p.code)}>Pref</button>
                                                )}
                                                {p.is_minor && p.minor_supervision_status !== "confirmed" && (
                                                  <button className="button-ghost" onClick={() => setMinorsModalCode(p.code)}>Escort</button>
                                                )}
                                                <button className="button-ghost" onClick={() => setTarget(p.code)}>Allocate</button>
                                              </div>
                                            )}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>

              {/* Right Rail: Bed status + Read-only budget gauge */}
              <aside className="right-rail">
                <section className="panel" aria-label="Bed status">
                  <header>
                    <h2>Bed status</h2>
                    <Pill
                      value={`${formatNumber(data.bed_status.on_campus.ready + data.bed_status.external.ready)} free`}
                      tone="ok"
                    />
                  </header>

                  <div className="meters">
                    <Meter
                      label="On-campus (B1–B3, G1–G2)"
                      value={data.bed_status.on_campus.occupied + data.bed_status.on_campus.reserved}
                      total={data.bed_status.on_campus.total_capacity}
                      detail={`${data.bed_status.on_campus.ready} free / ${data.bed_status.on_campus.total_capacity}`}
                    />
                    <Meter
                      label="External partners (6 properties)"
                      value={data.bed_status.external.occupied + data.bed_status.external.reserved}
                      total={data.bed_status.external.total_capacity}
                      detail={`${data.bed_status.external.ready} free / ${data.bed_status.external.total_capacity}`}
                    />
                  </div>

                  <div className="stat-row" style={{ marginTop: 12 }}>
                    <div>
                      <span>Ready</span>
                      <strong>{formatNumber(data.bed_status.on_campus.ready + data.bed_status.external.ready)}</strong>
                    </div>
                    <div>
                      <span>Reserved</span>
                      <strong>{formatNumber(data.bed_status.on_campus.reserved + data.bed_status.external.reserved)}</strong>
                    </div>
                    <div>
                      <span>Checked in</span>
                      <strong>{formatNumber(data.bed_status.on_campus.occupied + data.bed_status.external.occupied)}</strong>
                    </div>
                    <div>
                      <span>Maint / Clean</span>
                      <strong>
                        {formatNumber(
                          data.bed_status.on_campus.cleaning +
                            data.bed_status.on_campus.maintenance +
                            data.bed_status.external.cleaning +
                            data.bed_status.external.maintenance
                        )}
                      </strong>
                    </div>
                  </div>

                  {write && (
                    <div className="row-actions" style={{ marginTop: 12 }}>
                      <button type="button" className="button-ghost" onClick={() => setHousingModalCode("")}>
                        Neutral rooms ({data.bed_status.on_campus.gender_neutral_rooms || "UNKNOWN"})
                      </button>
                      <button type="button" className="button-ghost" onClick={() => setCallSheetHostel("EXT06")}>
                        Access call sheet ({data.bed_status.external.declared_accessible_properties}/6)
                      </button>
                    </div>
                  )}
                </section>

                <section className="panel" aria-label="External stay budget gauge">
                  <header>
                    <h2>Hospitality budget</h2>
                    <span className="source source-pack">Approved</span>
                  </header>

                  {(() => {
                    const g = data.budget_gauge;
                    const inPreview = previewSummary ? previewSummary.external_cost : 0;
                    const remainingAfterPreview = g.remaining - inPreview;

                    return (
                      <>
                        <Meter
                          label="Committed + Preview"
                          value={g.committed + inPreview}
                          total={g.approved}
                          detail={`${formatMoney(remainingAfterPreview)} left`}
                        />

                        <div className="stat-row" style={{ marginTop: 12 }}>
                          <div>
                            <span>Approved</span>
                            <strong>{formatMoney(g.approved)}</strong>
                          </div>
                          <div>
                            <span>Committed</span>
                            <strong>{formatMoney(g.committed)}</strong>
                          </div>
                          <div>
                            <span>Preview</span>
                            <strong style={{ color: inPreview ? "var(--cyan)" : undefined }}>
                              {formatMoney(inPreview)}
                            </strong>
                          </div>
                          <div>
                            <span>Remaining</span>
                            <strong className={remainingAfterPreview < 0 ? "over-text" : ""}>
                              {formatMoney(remainingAfterPreview)}
                            </strong>
                          </div>
                        </div>

                        {g.budget_held_count > 0 && (
                          <div className="computed-engine-note">
                            Placing <strong>{formatNumber(g.placeable_in_open_beds_count)}</strong> budget-held people in open beds needs{" "}
                            <strong>+{formatMoney(g.extra_needed_for_open_beds)}</strong> ({formatMoney(g.extra_needed_all_budget_held)} for all{" "}
                            {formatNumber(g.budget_held_count)}).
                          </div>
                        )}

                        {canBudget && (
                          <button
                            type="button"
                            className="button-secondary"
                            style={{ width: "100%", marginTop: 10 }}
                            onClick={() => setBudgetModal(true)}
                          >
                            Request extra budget
                          </button>
                        )}
                      </>
                    );
                  })()}
                </section>
              </aside>
            </div>
          </>
        )}
      </QueryState>

      {/* Tabs */}
      <Tabs
        value={tab}
        onChange={switchTab}
        options={[
          ["map", "Room map"],
          ["unallocated", `Still need a bed${data ? ` (${formatNumber(data.unallocated)})` : ""}`],
          ["allocations", `Allocations${data ? ` (${formatNumber(data.pipeline.total_active_allocations)})` : ""}`],
          ["rooms", "Rooms"],
          ["whatif", "Hostel closure what-if"],
        ]}
      />

      {tab === "unallocated" && (
        <div className="filters">
          <input
            type="search"
            aria-label="Search participant"
            placeholder="Search ID, name, or college"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select aria-label="Arrival window" value={arrivalWindow} onChange={(e) => setArrivalWindow(e.target.value)}>
            <option value="">All arrival times ({formatNumber(data?.unallocated ?? 0)})</option>
            <option value="already_here">Already here ({formatNumber(data?.arrivals.already_here ?? 0)})</option>
            <option value="6h">Arriving in 6 h ({formatNumber(data?.arrivals.next_6h ?? 0)})</option>
            <option value="12h">Arriving in 12 h ({formatNumber(data?.arrivals.next_12h ?? 0)})</option>
            <option value="24h">Arriving in 24 h ({formatNumber(data?.arrivals.next_24h ?? 0)})</option>
          </select>
          <select aria-label="Filter by blocker cause" value={causeFilter} onChange={(e) => setCauseFilter(e.target.value)}>
            <option value="">All causes</option>
            <option value="budget">External budget limit reached</option>
            <option value="gender_blank">Gender is blank (unconfirmed)</option>
            <option value="access_gendered">Accessible rooms are gendered</option>
            <option value="access_exhausted">No suitable accessible bed is left</option>
            <option value="invalid_dates">Stay dates are invalid</option>
          </select>
          <select aria-label="Contingent" value={contingentFilter} onChange={(e) => setContingentFilter(e.target.value)}>
            <option value="">All contingents</option>
            {allContingentCodes.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={under18Only} onChange={(e) => setUnder18Only(e.target.checked)} />
            Under 18
          </label>
        </div>
      )}

      {(tab === "rooms" || tab === "allocations") && (
        <div className="filters">
          <input
            type="search"
            aria-label="Search"
            placeholder={tab === "rooms" ? "Search room" : "Search participant or college"}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {tab === "rooms" && (
            <>
              <select aria-label="Hostel" value={hostel} onChange={(e) => setHostel(e.target.value)}>
                <option value="">All hostels & properties</option>
                {summary.data?.hostels.map((h) => (
                  <option key={h.code} value={h.code}>{h.name}</option>
                ))}
              </select>
              <label className="check">
                <input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} /> Free only
              </label>
            </>
          )}
          {tab === "allocations" && (
            <>
              <select aria-label="Allocation status" value={allocStatus} onChange={(e) => setAllocStatus(e.target.value)}>
                <option value="">All statuses</option>
                <option value="allocated">Allocated</option>
                <option value="checked_in">Checked in</option>
              </select>
              <select aria-label="Contingent" value={contingentFilter} onChange={(e) => setContingentFilter(e.target.value)}>
                <option value="">All contingents</option>
                {allContingentCodes.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <label className="check">
                <input type="checkbox" checked={under18Only} onChange={(e) => setUnder18Only(e.target.checked)} /> Under 18
              </label>
              {room && (
                <button className="chip active" onClick={() => setRoom("")}>
                  Room {room} ×
                </button>
              )}
            </>
          )}
        </div>
      )}

      <ErrorNote error={stay.error || cancel.error || preview.error} />

      {tab === "map" && (
        <RoomMap
          onSelectRoom={(code) => setSelectedMapRoom(code)}
          onOpenCallSheet={(hCode) => setCallSheetHostel(hCode || "EXT06")}
        />
      )}

      {tab === "rooms" && (
        <QueryState query={rooms}>
          {rooms.data && (
            <>
              <DataTable
                rows={rooms.data.items}
                columns={roomColumns}
                rowKey={(r) => r.code}
                onRow={(r) => setSelectedMapRoom(r.code)}
                empty="No rooms match."
                sort={sort}
                onSort={setSort}
              />
              <Pagination page={rooms.data.page} pages={rooms.data.pages} total={rooms.data.total} onPage={setPage} />
            </>
          )}
        </QueryState>
      )}

      {tab === "allocations" && (
        <QueryState query={allocations}>
          {allocations.data && (
            <>
              <DataTable
                rows={allocations.data.items}
                columns={allocationColumns}
                rowKey={(a) => a.id}
                empty={room ? `Nobody is in ${room}.` : "No beds match."}
                sort={sort}
                onSort={setSort}
              />
              <Pagination page={allocations.data.page} pages={allocations.data.pages} total={allocations.data.total} onPage={setPage} />
            </>
          )}
        </QueryState>
      )}

      {tab === "unallocated" && (
        <QueryState query={unallocated}>
          {unallocated.data && (
            <>
              <DataTable
                rows={unallocated.data.items}
                columns={unallocatedColumns}
                rowKey={(p) => p.code}
                empty="No waiting participants match."
                sort={sort}
                onSort={setSort}
              />
              <Pagination page={unallocated.data.page} pages={unallocated.data.pages} total={unallocated.data.total} onPage={setPage} />
            </>
          )}
        </QueryState>
      )}

      {tab === "whatif" && data && <HostelClosureWhatIf hostels={data.hostels} />}

      {/* Sticky action bar appears ONLY when a preview exists */}
      {preview.data && previewSummary && (
        <div className="sticky-preview-bar" role="region" aria-label="Allocation preview actions">
          <div className="sticky-preview-summary">
            <Pill value="Preview" tone="info" />
            <span>
              Places <strong>{formatNumber(previewSummary.placed)}</strong>
              {" · "}external <strong>{formatMoney(previewSummary.external_cost)}</strong>
              {" · "}<strong>{formatNumber(previewSummary.held)}</strong> held
              {" · "}<strong>{previewSummary.contingents_split}</strong> split
            </span>
          </div>
          <div className="sticky-preview-actions">
            <button type="button" className="button-ghost" onClick={() => setReviewingPreview(true)}>
              <Eye size={15} /> Review
            </button>
            {write && previewSummary.placed > 0 && (
              <button
                type="button"
                className="button-primary small"
                disabled={applyPlan.isPending}
                onClick={() => {
                  if (preview.data!.over_approved_budget || previewSummary.relaxed.length > 0) {
                    setReviewingPreview(true);
                  } else {
                    applyPlan.mutate(undefined);
                  }
                }}
              >
                {applyPlan.isPending ? "Approving..." : `Approve (${formatNumber(previewSummary.placed)})`}
              </button>
            )}
            <button type="button" className="button-ghost danger" onClick={() => preview.reset()}>
              Discard
            </button>
          </div>
        </div>
      )}

      {/* Modals & Drawers */}
      {selectedMapRoom && (
        <RoomDetailDialog
          roomCode={selectedMapRoom}
          onClose={() => setSelectedMapRoom(undefined)}
          onFilterAllocations={(code) => {
            setTab("allocations");
            setRoom(code);
            setSort("");
          }}
          onOpenContingent={(cCode) => {
            setSelectedMapRoom(undefined);
            setContingentModalCode(cCode);
          }}
        />
      )}

      {budgetModal && data && (
        <BudgetOverrideModal
          gauge={data.budget_gauge}
          onClose={() => setBudgetModal(false)}
          onSuccess={() => {
            if (preview.data) preview.mutate();
          }}
        />
      )}

      {housingModalCode !== null && data && (
        <HousingOutreachModal
          summary={data}
          initialCode={housingModalCode || undefined}
          onClose={() => setHousingModalCode(null)}
        />
      )}

      {callSheetHostel !== null && data && (
        <AccessibilityCallSheetModal
          summary={data}
          initialHostelCode={callSheetHostel || undefined}
          onClose={() => setCallSheetHostel(null)}
        />
      )}

      {dateModalCode !== null && data && (
        <FixStayDatesModal
          dateErrors={data.date_errors}
          initialCode={dateModalCode || undefined}
          onClose={() => setDateModalCode(null)}
        />
      )}

      {minorsModalCode !== null && data && (
        <MinorsModal
          minors={data.minors_summary}
          initialCode={minorsModalCode || undefined}
          onClose={() => setMinorsModalCode(null)}
          onOpenContingent={(cCode) => {
            setMinorsModalCode(null);
            setContingentModalCode(cCode);
          }}
        />
      )}

      {contingentModalCode && (
        <ContingentModal
          contingentCode={contingentModalCode}
          allCodes={allContingentCodes}
          onSwitchContingent={setContingentModalCode}
          onClose={() => setContingentModalCode(null)}
        />
      )}

      {reviewingPreview && preview.data && (
        <PreviewReviewModal
          plan={preview.data}
          canWrite={write}
          isApplying={applyPlan.isPending}
          applyError={applyPlan.error}
          onApprove={(r) => applyPlan.mutate(r)}
          onClose={() => setReviewingPreview(false)}
        />
      )}

      {target && <ManualAllocate code={target} onClose={() => setTarget(undefined)} />}
      {adding === "hostel" && <AddHostel onClose={() => setAdding(null)} />}
      {adding === "room" && data && <AddRoom hostels={data.hostels} onClose={() => setAdding(null)} />}
    </div>
  );
}
