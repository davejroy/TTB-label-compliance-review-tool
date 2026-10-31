export type BeverageType = "distilled_spirits" | "wine" | "beer";
export type Status = "pass" | "warning" | "fail" | "retake" | "error";
export type FieldCheckStatus = "pass" | "warning" | "fail";
export type FormatStatus = "pass" | "advisory" | "fail";
export type FieldPresence = "present" | "absent" | "unreadable";
export type RetakeArea = "front" | "back" | "neck";
export type RetakeCause =
  | "blur"
  | "glare"
  | "blank"
  | "dark"
  | "angle"
  | "curvature"
  | "cropped"
  | "low_resolution";

export type ErrorCode =
  | "PHOTO_QUALITY_LOW"
  | "PHOTO_UNREADABLE_FIELD"
  | "UNSUPPORTED_FILE"
  | "FILE_TOO_LARGE"
  | "RATE_LIMITED"
  | "UPSTREAM_TIMEOUT"
  | "UPSTREAM_ERROR"
  | "INTERNAL_ERROR"
  | "INVALID_REQUEST"
  | "AUTH_REQUIRED"
  | "VISION_UNAVAILABLE"
  | "CONFIG_ERROR"
  | "MODEL_UNAVAILABLE"
  | string;

export interface RetakeGuidance {
  field: string;
  area: RetakeArea;
  cause: RetakeCause;
  tip: string;
}

export type CalibrationMethod =
  | "scale_marker_aruco"
  | "scale_marker_card_id1"
  | "scale_marker_ruler"
  | "container_geometry"
  | "uncalibrated_estimate"
  | "none";

export interface TypeSizeMeasurement {
  method: CalibrationMethod;
  pixels_per_mm?: number;
  measured_capital_height_mm?: number;
  required_min_height_mm: number;
  uncertainty_mm?: number;
  skew_angle_deg?: number;
  state: "pass" | "fail" | "warning" | "cannot_measure";
  verification_note: string;
  estimated_characters_per_inch?: number;
  max_characters_per_inch?: number;
}

export interface ApplicationData {
  beverage_type: BeverageType;
  brand_name: string;
  class_type: string;
  alcohol_content: string;
  net_contents: string;
  name_and_address?: string;
  country_of_origin?: string;
}

export type Confidence = "high" | "medium" | "low";

export interface FieldLocation {
  field: string;
  image_index: number;
  confidence: Confidence;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ExtractedLabelData {
  brand_name?: string;
  class_type?: string;
  alcohol_content?: string;
  net_contents?: string;
  name_and_address?: string;
  country_of_origin?: string;
  government_warning_header?: string;
  government_warning_body?: string;
  government_warning_present: boolean;
  government_warning_header_appears_bold?: boolean | null;
  beverage_type_guess?: string;
  origin_guess?: "domestic" | "imported" | "unknown";
  field_locations: FieldLocation[];
  notes?: string;
  sulfite_declaration?: string;
  allergen_statements?: string;
  age_statement?: string;
  commodity_statement?: string;
  is_alcohol_beverage_label?: boolean;
}

export interface WarningWordChange {
  kind: "missing" | "extra" | "changed";
  expected?: string | null;
  observed?: string | null;
  expected_index?: number | null;
  observed_index?: number | null;
}

export interface WarningTextDiff {
  exact_match: boolean;
  expected_words: string[];
  observed_words: string[];
  missing_words: WarningWordChange[];
  extra_words: WarningWordChange[];
  changed_words: WarningWordChange[];
}

export interface WarningFormatFinding {
  code: "type_size" | "characters_per_inch" | "header_bold";
  status: FormatStatus;
  citation: string;
  message: string;
  estimated: boolean;
}

export interface WarningFormatResult {
  status: FormatStatus;
  required_min_height_mm: number;
  max_characters_per_inch: number;
  estimated_characters_per_inch?: number | null;
  findings: WarningFormatFinding[];
}

export interface FieldResult {
  field: string;
  label_name: string;
  status: FieldCheckStatus;
  application_value?: string;
  label_value?: string;
  message: string;
  type_size_details?: TypeSizeMeasurement;
  warning_text_diff?: WarningTextDiff;
  warning_format?: WarningFormatResult;
}

export const MAX_IMAGES_PER_LABEL = 4;

export interface ImageQualityAdvisory {
  blur_score: number;
  exposure: "dark" | "ok" | "bright";
  glare_fraction: number;
  resolution: { width: number; height: number };
  warnings: string[];
}

export interface ReviewResult {
  filenames: string[];
  overall_status: Status;
  fields: FieldResult[];
  extracted: ExtractedLabelData;
  processing_time_ms: number;
  error?: string;
  error_code?: ErrorCode | null;
  request_id?: string | null;
  retake?: RetakeGuidance | null;
  field_status?: Partial<Record<string, FieldPresence>>;
  quality?: ImageQualityAdvisory | null;
  timings_ms?: StageTimings | null;
}

export interface StageTimings {
  decode: number;
  pre_gate: number;
  resize: number;
  model: number;
  compliance: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
  model_ttft_ms?: number;
  model_generation_ms?: number;
}

export interface LabelCheckResult {
  filenames: string[];
  overall_status: Status;
  beverage_type?: string;
  checks: FieldResult[];
  extracted: ExtractedLabelData;
  processing_time_ms: number;
  error?: string;
  error_code?: ErrorCode | null;
  request_id?: string | null;
  retake?: RetakeGuidance | null;
  field_status?: Partial<Record<string, FieldPresence>>;
  /** True when check is paused waiting for agent to confirm beverage type. */
  needs_beverage_confirmation?: boolean;
  /** True when beverage type was explicitly confirmed by the agent. */
  beverage_type_confirmed?: boolean;
  /** Roles of photos merged to build extracted fields, e.g. ["front","back"]. */
  photo_sources?: string[];
  quality?: ImageQualityAdvisory | null;
  timings_ms?: StageTimings | null;
}

export const BEVERAGE_TYPE_LABELS: Record<string, string> = {
  distilled_spirits: "Distilled Spirits",
  wine: "Wine",
  beer: "Beer / Malt Beverage",
  unknown: "Unknown",
};

export const EMPTY_APPLICATION: ApplicationData = {
  beverage_type: "distilled_spirits",
  brand_name: "",
  class_type: "",
  alcohol_content: "",
  net_contents: "",
  name_and_address: "",
  country_of_origin: "",
};

export interface ColaPreset {
  id: string;
  name: string;
  description: string;
  application: ApplicationData;
  /** Optional metadata for small, opt-in synthetic QA presets. */
  category?: "demo" | "synthetic_qa";
  fixture_id?: string;
  expected_result?: string;
}
