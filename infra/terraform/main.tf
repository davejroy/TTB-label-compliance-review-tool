terraform {
  required_version = ">= 1.6.0"

  required_providers {
    render = {
      source  = "render-oss/render"
      version = "~> 1.9"
    }
  }
}

provider "render" {
  api_key  = var.render_api_key
  owner_id = var.render_owner_id
}

resource "render_web_service" "backend" {
  name              = var.backend_service_name
  plan              = var.backend_plan
  region            = var.render_region
  root_directory    = "backend"
  health_check_path = "/api/health"
  start_command     = "uvicorn app.main:app --host 0.0.0.0 --port $PORT"

  runtime_source = {
    native_runtime = {
      repo_url      = var.repository_url
      branch        = var.branch
      runtime       = "python"
      build_command = "pip install -r requirements.txt"
      auto_deploy   = var.auto_deploy
    }
  }
}

resource "render_static_site" "frontend" {
  name           = var.frontend_service_name
  repo_url       = var.repository_url
  branch         = var.branch
  root_directory = "frontend"
  build_command  = "npm ci && npm run build"
  publish_path   = "dist"
  auto_deploy    = var.auto_deploy

  env_vars = {
    VITE_API_HOST = {
      value = var.backend_host
    }
  }

  routes = [
    {
      source      = "/*"
      destination = "/index.html"
      type        = "rewrite"
    }
  ]
}
