# Ansible: build and operations host

Ansible is intentionally separate from Terraform:

- Terraform owns Render service configuration.
- Ansible prepares a clean Debian-family build/operations host with repeatable packages, a non-root deployment user, application directories, and optional firewall rules.
- Ansible does not SSH into or manage Render’s managed runtime. Use Render’s API/provider for that.
- Firewall enablement is opt-in and requires an explicit trusted SSH CIDR.

## Install and validate

\`\`\`bash
cd infra/ansible
ansible-galaxy collection install -r requirements.yml
ansible-playbook --syntax-check -i inventory/hosts.example.yml playbooks/site.yml
\`\`\`

For a real host, copy the inventory example to an ignored \`inventory/hosts.yml\`, replace the host, and run:

\`\`\`bash
ansible-playbook -i inventory/hosts.yml playbooks/site.yml --ask-become-pass
ansible-playbook -i inventory/hosts.yml playbooks/verify.yml
\`\`\`

Review the package list and distribution assumptions before use. The default playbook does not enable a firewall and does not change SSH configuration.
