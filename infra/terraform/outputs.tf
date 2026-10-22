output "backend_service_id" {
  description = "Render backend service ID."
  value       = render_web_service.backend.id
}

output "frontend_service_id" {
  description = "Render frontend static-site service ID."
  value       = render_static_site.frontend.id
}

output "backend_health_url" {
  description = "Backend health endpoint."
  value       = format("https://%s/api/health", var.backend_host)
}
