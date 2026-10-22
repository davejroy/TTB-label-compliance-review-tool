variable "render_api_key" {
  description = "Render API key. Supply through an environment variable or an uncommitted tfvars file."
  type        = string
  sensitive   = true
}

variable "render_owner_id" {
  description = "Render owner/workspace ID, for example tea-... ."
  type        = string
}

variable "repository_url" {
  description = "GitHub repository URL used by both Render services."
  type        = string
  default     = "https://github.com/davejroy/TTB-label-compliance-review-tool"
}

variable "branch" {
  description = "Git branch that Render should deploy."
  type        = string
  default     = "main"
}

variable "render_region" {
  description = "Render region for the backend service."
  type        = string
  default     = "oregon"
}

variable "backend_service_name" {
  description = "Existing Render backend service name. Import the service before applying."
  type        = string
  default     = "ttb-label-backend"
}

variable "frontend_service_name" {
  description = "Existing Render static site name. Import the site before applying."
  type        = string
  default     = "ttb-label-frontend"
}

variable "backend_plan" {
  description = "Render backend plan. Keep dev inexpensive; use a paid plan for production reliability."
  type        = string
  default     = "standard"
}

variable "backend_host" {
  description = "Public Render hostname used by the frontend."
  type        = string
  default     = "ttb-label-backend.onrender.com"
}

variable "auto_deploy" {
  description = "Whether Render automatically deploys successful repository changes."
  type        = bool
  default     = true
}
