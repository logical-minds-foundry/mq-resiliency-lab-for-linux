# rhel-ha-repo

Offline package sources for the RHEL `pcmk-rhel` substrate: BaseOS + AppStream
from the attached install DVD, plus a local `file://` repo of the
HighAvailability packages (Pacemaker/Corosync/pcs/resource-agents/fence-agents).

## One-time host prerequisite (plan D1)

The HighAvailability packages are free OSS — **no subscription**. Download them
(and their deps) from a free, RHEL-compatible HighAvailability repo — AlmaLinux
9, Rocky 9, or CentOS Stream 9 — into `build/rhel-ha/` (gitignored). E.g. on an
EL9-compatible host (or container) with the HighAvailability repo enabled:

    dnf download --resolve --downloaddir=build/rhel-ha \
      --enablerepo=highavailability \
      pacemaker corosync pcs resource-agents fence-agents-all fence-agents-virsh

(The repo id is `highavailability` on AlmaLinux / Rocky / CentOS Stream 9. These
RPMs are binary-compatible with RHEL 9.) To use Red Hat's paid HA Add-On builds
instead, populate `build/rhel-ha/` from the entitled repo — the role is
unchanged.

`build/` is host-mounted and gitignored; nothing here enters git.
