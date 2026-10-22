# Terraform: Render infrastructure

This directory manages the existing Render backend and frontend services for the prod environment.

## Safety model

- Terraform manages service configuration, not application secrets.
- Do not put \`ANTHROPIC_API_KEY\`, demo tokens, or other secrets in Terraform files or state.
- Review \`terraform plan\` before every apply.
- Import the existing Render services before the first apply. Importing prevents Terraform from attempting to create duplicates.
- This module does not erase data, rotate credentials, or perform destructive workstation operations.
- Use a protected remote Terraform state backend for team or legal/audit use. Local state is acceptable only for a controlled single-operator dry run.

The official Render provider is \`render-oss/render\`. The provider requires a Render API key and owner/workspace ID supplied outside Git.

## First-time setup

\`\`\`bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars; keep it uncommitted
export TF_VAR_render_api_key="$RENDER_API_KEY"
export TF_VAR_render_owner_id="$RENDER_OWNER_ID"
terraform init
terraform fmt -check
terraform validate
\`\`\`

If using environment variables, remove the corresponding placeholder values from the local tfvars file.

## Import the current services

\`\`\`bash
terraform import render_web_service.backend srv-d8l3mdrtqb8s73aosli0
terraform import render_static_site.frontend srv-d8l49qbtqb8s73apj0tg
terraform plan
\`\`\`

The service IDs above are the IDs observed during the infrastructure review. Re-check them in Render before importing if the services have been renamed or recreated.

## Apply

\`\`\`bash
terraform plan -out=tfplan
terraform apply tfplan
\`\`\`

Do not run apply from an untrusted or compromised workstation. Use a clean, authenticated operator or CI environment and retain the plan and resulting state according to the project’s change-control policy.
