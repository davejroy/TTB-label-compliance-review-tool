export type BeverageType = "distilled_spirits" | "wine" | "beer";
export type Status = "pass" | "warning" | "fail";

export interface ApplicationData {
  beverage_type: BeverageType;
  brand_name: string;
  class_type: string;
  alcohol_content: string;
  net_contents: string;
  name_and_address?: string;
  country_of_origin?: string;
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
  notes?: string;
}

export interface FieldResult {
  field: string;
  label_name: string;
  status: Status;
  application_value?: string;
  label_value?: string;
  message: string;
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

export const EMPTY_APPLICATION: ApplicationData = {
  beverage_type: "distilled_spirits",
  brand_name: "",
  class_type: "",
  alcohol_content: "",
  net_contents: "",
  name_and_address: "",
  country_of_origin: "",
};
