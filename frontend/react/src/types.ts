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

export interface Progress {
  case_id: string;
  status: string;
  status_updated_at: string | null;
  sighting_count: number;
  jobs: {
    id: string;
    video_source_id: string | null;
    worker_id: string;
    status: string;
    attempts: number;
    updated_at: string;
  }[];
}

export interface Sighting {
  id: string;
  case_id: string;
  video_source_id: string;
  occurred_at: string | null;
  video_offset_seconds: number;
  vehicle_type: string;
  vehicle_color: string | null;
  subject_type: string;
  detector_confidence: number;
  review_status: string;
  frame_url: string | null;
}
