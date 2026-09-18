export type BeverageType = "distilled_spirits" | "wine" | "beer";
export type Status = "pass" | "warning" | "fail";

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

export interface FieldResult {
  field: string;
  label_name: string;
  status: Status;
  application_value?: string;
  label_value?: string;
  message: string;
  type_size_details?: TypeSizeMeasurement;
}

export const MAX_IMAGES_PER_LABEL = 4;

export interface ReviewResult {
  filenames: string[];
  overall_status: Status;
  fields: FieldResult[];
  extracted: ExtractedLabelData;
  processing_time_ms: number;
  error?: string;
}

export interface LabelCheckResult {
  filenames: string[];
  overall_status: Status;
  beverage_type?: string;
  checks: FieldResult[];
  extracted: ExtractedLabelData;
  processing_time_ms: number;
  error?: string;
  /** True when check is paused waiting for agent to confirm beverage type. */
  needs_beverage_confirmation?: boolean;
  /** True when beverage type was explicitly confirmed by the agent. */
  beverage_type_confirmed?: boolean;
  /** Roles of photos merged to build extracted fields, e.g. ["front","back"]. */
  photo_sources?: string[];
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
}
