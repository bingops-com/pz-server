# Project Zomboid container image

The image contains SteamCMD, the runtime scripts and Restic. Game files are
installed into the persistent volume by the chart init container rather than
embedded in the OCI image.

The default Steam branch is the current stable branch (Build 42). Set
`steam.beta` in Helm values only when an explicit beta branch is required.

The runtime expects server configuration under `/config`, persistent data at
`/data`, and passwords through environment variables backed by Kubernetes
Secrets.
