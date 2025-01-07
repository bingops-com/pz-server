# `node.tf` values

node_name = "homelab"
node_pool = "Gaming"

nodes = {
  pzomboid = {
    host_ip         = "192.168.1.130/24"
    gw              = "192.168.1.1"
    vm_id           = 130
    cores           = 4
    memory          = 8192 # 8GB
    network_bridge  = "vmbr0"
    role            = "master"
  },
}

datastore = "local-lvm"
template_vm_id = "9999"
