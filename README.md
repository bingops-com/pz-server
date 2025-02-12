# Project Zomboid Deployment with Terraform and Ansible

This documentation provides a high-level overview of setting up a Project Zomboid dedicated server using **Terraform** for VM provisioning and **Ansible** for server configuration.

![Project Zomboid Server](images/pz-server.png)

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Key Configuration Points](#key-configuration-points)
4. [Deployment Steps](#deployment-steps)
5. [Playit.gg Tunnel Setup](#playitgg-tunnel-setup)
6. [Troubleshooting](#troubleshooting)

---

## Overview

This project automates the deployment of a Project Zomboid server on a Proxmox Virtual Environment (PVE). It includes:

- **Terraform**: Provisions the virtual machine.
- **Ansible**: Configures the server, including SSH, firewall, and server-specific settings.
- **Playit.gg Tunnel**: Exposes the server to the internet using a secure tunnel.

---

## Project Structure

Here is an overview of the project directory:

```
project-zomboid/
│
│ # Ansible
├── site.yml                  # Main Ansible playbook
├── bootstrap.yml             # Playbook for initial setup
├── requirements.yml          # Ansible role dependencies
├── group_vars/
│   ├── all.yml               # General variables
│   └── vault.yml             # Encrypted sensitive variables
│
├── roles/
│   ├── ssh/                  # SSH configuration role
│   ├── firewall/             # Firewall setup role
│   ├── playit/               # Playit agent setup role
│   ├── node_exporter/        # Monitoring setup role
│   └── server/               # Server configuration role
│       ├── defaults/
│       └── main.yml          # Configure mods and global server settings
│
│ # Terraform
├── main.tf                   # Proxmox resource definitions
├── pool.tf                   # Proxmox pool settings
├── vms.tf                    # Virtual machine configuration
├── variables.tf              # Terraform variable definitions
├── terraform.tfvars          # User-specific variable values
├── credentials.auto.tfvars   # Sensitive credentials (git-ignored)
└── README.md                 # Documentation
```

---

## Key Configuration Points

### Terraform Variables

- `terraform.tfvars`: Define VM properties such as IP address, memory, and CPU.
- `credentials.auto.tfvars`: Store sensitive Proxmox API credentials securely.

### Ansible Variables

- `group_vars/all.yml`: Contains general variables like `new_admin_user`.
- `group_vars/vault.yml`: Encrypted sensitive variables, including:
    - `server_password`: The Project Zomboid server password.

### Playbooks and Roles

- `bootstrap.yml`: Sets up SSH and firewall.
- `site.yml`: Configures the server and Playit tunnel.
- `roles/server/defaults/main.yml`: Allows adding/removing mods and updating global server's settings.

---

## Deployment Steps

### Terraform

1. Initialize Terraform:

   ```bash
   terraform init
   ```

2. Apply the configuration:

   ```bash
   terraform apply
   ```

### Ansible

1. Run the bootstrap playbook:

   ```bash
   ansible-playbook bootstrap.yml -i inventories/main/hosts
   ```

2. Configure the server:

   ```bash
   ansible-playbook site.yml -i inventories/main/hosts
   ```

---

## Playit.gg Tunnel Setup

Playit.gg allows you to expose your server to the internet without requiring port forwarding. Here’s how to set it up:

### 1. Download the Playit.gg Agent and Service

Executing the `site.yml` playbook will automatically install the Playit agent and setup the service. The service will be restarted once the next parts are done.

### 2. Create a New Tunnel

1. Run the Playit agent on your server:

   ```bash
   sudo -u playit /usr/local/bin/playit
   ```

2. Follow the instructions to link your machine with your Playit.gg account. Once linked, you'll have an auto-generated configuration file set up in `~/.config/playit/`.

3. Use the Playit dashboard to create a tunnel:

    - Go to `All Agents` and select your agent.
    - Select `Add Tunnel`.
    - Choose `UDP` protocol.
    - Enter the port number your Project Zomboid server uses (default: 16261).
    - Assign a subdomain or custom domain to your tunnel.

---

## Troubleshooting

### Terraform Errors

- Verify API credentials and Proxmox connectivity.

### Ansible Vault Issues

- Ensure the vault file is encrypted and the password is correct.

### Zomboid Server Issues

- Ensure the Zomboid service is running:
  ```bash
  sudo systemctl status zomboid
  ```
- Check logs for errors:
  ```bash
  sudo journalctl -u zomboid
  ```

### Playit Tunnel Issues

- Ensure the Playit agent is running:
  ```bash
  sudo systemctl status playit
  ```
- Check logs for errors:
  ```bash
  sudo journalctl -u playit
  ```

