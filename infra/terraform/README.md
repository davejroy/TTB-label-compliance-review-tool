# Terraform: Render infrastructure

This directory manages the existing Render backend and frontend services for the prod environment.

## Safety model

- Terraform manages service configuration, but the Render provider also reads existing environment-variable values and secret-file contents into state during import and refresh.
- Never commit application secrets to Terraform configuration. Omission, sensitive attributes, and `lifecycle.ignore_changes` do not keep imported secrets out of state.
- Before importing production services, configure an encrypted, access-controlled remote state backend. Treat state, backups, and saved plans as secret-bearing artifacts; never commit them. Do not import if policy prohibits storing these secrets in state.
- Review `terraform plan` before every apply, including any proposed environment-variable or secret-file removals.
- Import the existing Render services before the first apply to avoid duplicates.
- Use local state only with non-production, non-secret services in a controlled single-operator dry run.

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


## Important state and secret warning

The Render provider can read existing dashboard-managed environment variables and secret-file contents into Terraform state during import. \`sensitive\` and \`ignore_changes\` help prevent accidental changes, but they do not guarantee that imported values are absent from state or plan artifacts.

Use protected, access-controlled state and plan storage. If policy forbids production secrets in Terraform state, do not import the production backend into Terraform; manage it through Render/Blueprint controls that keep secrets outside Terraform state. Never keep production state or plan files on an untrusted or compromised workstation.
