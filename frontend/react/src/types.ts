export type VerificationStatus =
  | "draft"
  | "pending_review"
  | "additional_info_required"
  | "approved"
  | "rejected"
  | "suspended";

export type VerificationDocType =
  | "redacted_id"
  | "authorization_letter"
  | "posting_confirmation";

export type AdminDecisionType =
  | "approved"
  | "rejected"
  | "additional_info_required"
  | "suspended"
  | "reinstated";

export interface District {
  id: string;
  name: string;
}

export interface OfficerProfile {
  user_id: string;
  full_name: string;
  designation: string;
  police_station: string;
  claimed_district_id: string;
  approved_district_id: string | null;
  employee_ref: string | null;
  verification_status: VerificationStatus;
  status_reason: string | null;
  submission_version: number;
  submitted_at: string | null;
  approved_at: string | null;
  updated_at: string | null;
}

export interface VerificationDocument {
  id: string;
  document_type: VerificationDocType;
  original_filename: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  submission_version: number;
  uploaded_at: string;
}

export interface VerificationReview {
  id: string;
  applicant_user_id: string;
  admin_user_id: string;
  district_id: string;
  decision: AdminDecisionType;
  previous_status: VerificationStatus;
  new_status: VerificationStatus;
  reason: string | null;
  created_at: string;
}

export interface AuthMeResponse {
  user_id: string;
  email: string | null;
  email_verified: boolean;
  is_approved_officer: boolean;
  is_district_admin: boolean;
  admin_district_id: string | null;
  profile: OfficerProfile | null;
  documents: VerificationDocument[];
  reviews: VerificationReview[];
}

export interface AdminOverview {
  district_id: string;
  counts: Record<VerificationStatus, number>;
  total_reviews: number;
}

export interface AdminApplicationSummary extends OfficerProfile {
  document_count: number;
}

export interface AdminApplicationDetail {
  profile: OfficerProfile;
  documents: VerificationDocument[];
  reviews: VerificationReview[];
}

export interface CaseVideo {
  video_source_id: string;
  case_id: string;
  jurisdiction: string;
  camera_id: string;
  source_label: string | null;
  original_name: string;
  recorded_at: string | null;
  sha256: string;
  byte_size: number;
  duration_seconds: number;
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  media_status: string;
  created_at: string;
}

export interface CaseSummary {
  case_id: string;
  title: string | null;
  description: string;
  status: string;
  clues: { clue_notes?: string | null; incident_time_text?: string | null } | null;
  video_count: number;
  finding_count: number;
  pending_finding_count: number;
  authorized_at: string | null;
  created_at: string;
  updated_at: string | null;
  videos?: CaseVideo[];
  limits?: {
    max_upload_bytes: number;
    max_case_videos: number;
    supported_extensions: string[];
  };
}

export interface ProgressJob {
  id: string;
  run_id?: string | null;
  video_source_id: string | null;
  source_label?: string | null;
  worker_id: string;
  status: string;
  attempts: number;
  error_detail?: string | null;
  heartbeat_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at: string;
}

export interface ParsedClue {
  vehicle_color?: string | null;
  vehicle_type?: string | null;
  distinctive_vehicle_features?: string[];
  visual_targets?: { target_type: string; color?: string | null; attributes: string[] }[];
  unsupported_requests?: string[];
  event_time_text?: string | null;
  origin_zone?: string | null;
  parser?: string;
}

export interface Progress {
  case_id: string;
  title?: string | null;
  description?: string;
  clue?: ParsedClue | null;
  status: string;
  status_updated_at: string | null;
  run?: {
    id: string;
    status: string;
    input_video_ids: string[];
    error_code?: string | null;
    error_detail?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    updated_at: string;
  } | null;
  total_videos?: number;
  queued_jobs?: number;
  running_jobs?: number;
  retrying_jobs?: number;
  completed_jobs?: number;
  failed_jobs?: number;
  sighting_count: number;
  jobs: ProgressJob[];
}

export interface Sighting {
  id: string;
  case_id: string;
  job_id?: string | null;
  worker_id?: string | null;
  video_source_id: string;
  occurred_at: string | null;
  video_offset_seconds: number;
  track_id?: number | null;
  vehicle_type: string;
  vehicle_color: string | null;
  subject_type: string;
  visual_attributes?: string[];
  detector_confidence: number;
  clue_match_score?: number;
  evidence_sha256?: string;
  frame_sha256?: string;
  review_status: string;
  reviewer_user_id?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
  created_at?: string;
  clue?: Record<string, unknown> | null;
  bbox?: [number, number, number, number] | null;
  scaled_bbox?: [number, number, number, number] | null;
  frame_width?: number | null;
  frame_height?: number | null;
  saved_frame_width?: number | null;
  saved_frame_height?: number | null;
  has_frame?: boolean;
  frame_url: string | null;
  annotated_frame_url?: string | null;
}

export interface FindingReview {
  id: string;
  sighting_id: string;
  case_id?: string;
  reviewer: string;
  decision: "confirmed_candidate" | "not_relevant";
  note: string;
  request_key?: string;
  created_at: string;
}
